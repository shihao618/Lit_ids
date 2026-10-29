"""FastAPI 应用入口.

运行:
    uvicorn app.main:app --host 0.0.0.0 --port 10381
或使用 Docker:
    docker compose up -d

多用户 + 后台可配置：
  * 部署级配置（数据目录/初始管理员）来自环境变量；
  * 运行参数（系统名称/日志级别/会话保活/启动自动登录）在后台「系统设置」修改，
    保存到 data/settings.json，后台还提供「重启容器」按钮（SIGTERM + docker restart 策略）。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import Settings, get_settings
from .logbuf import log_buffer
from .registry import SessionRegistry
from .session import SessionError
from .settings_store import RuntimeSettings, apply_log_level
from .systems import SystemError
from .users import StoreError, UserStore

log = logging.getLogger("lit_api")


class UTF8JSONResponse(JSONResponse):
    """显式声明 charset=utf-8，避免中文在部分客户端（如 PowerShell）里乱码."""

    media_type = "application/json; charset=utf-8"


# ---------------------------------------------------------------- 会话后台任务
def _users_with_cas(store: UserStore) -> list[dict]:
    return [u for u in store.list()
            if u.get("cas_username") and u.get("cas_password")]


async def _session_worker(app: FastAPI) -> None:
    """启动时为已配置统一认证账号的用户自动登录 + 按后台设置的间隔保活."""
    store: UserStore = app.state.store
    registry: SessionRegistry = app.state.registry
    runtime: RuntimeSettings = app.state.runtime_settings

    async def ensure_all(reason: str) -> None:
        for user in _users_with_cas(store):
            try:
                await asyncio.to_thread(registry.session(user["id"]).ensure_login)
                log.info("[%s] 用户 %s 会话就绪", reason, user.get("username"))
            except Exception as e:  # noqa: BLE001
                log.warning("[%s] 用户 %s 登录失败: %s", reason, user.get("username"), e)

    if runtime.get("auto_login_on_start"):
        await ensure_all("启动")
    while True:
        interval = int(runtime.get("session_refresh_interval") or 0)
        if interval <= 0:
            log.info("会话保活已关闭（可在后台「系统设置」开启）")
            return
        await asyncio.sleep(interval)
        await ensure_all("保活")


# ---------------------------------------------------------------- 生命周期
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    # 运行参数（后台可改）：文件不存在时用环境变量/defaults 初始化
    runtime_settings = RuntimeSettings(settings.settings_file, defaults={
        "app_name": settings.app_name,
        "log_level": settings.log_level,
        "session_refresh_interval": settings.session_refresh_interval,
        "auto_login_on_start": settings.auto_login,
    })
    app.state.runtime_settings = runtime_settings

    logging.basicConfig(
        level=getattr(logging, str(runtime_settings.get("log_level", "INFO")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    )
    root_logger = logging.getLogger()
    log_buffer.setLevel(logging.DEBUG)
    log_buffer.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s"))
    if log_buffer not in root_logger.handlers:
        root_logger.addHandler(log_buffer)
    apply_log_level(str(runtime_settings.get("log_level", "INFO")))

    log.info("%s v%s 启动 | data_dir=%s | users_file=%s | settings_file=%s",
             runtime_settings.get("app_name"), settings.version, settings.data_dir,
             settings.users_file, settings.settings_file)

    store = UserStore(settings.users_file)
    bootstrap_user = store.bootstrap(
        admin_username=settings.admin_username,
        admin_password=settings.admin_password,
        api_key=settings.api_key,
        cas_username="", cas_password="", cas_fingerprint="",
    )
    if bootstrap_user:
        log.info("首次启动：已创建管理员用户 %s（统一认证账号请在后台配置）",
                 bootstrap_user.get("username"))
        if not settings.admin_password:
            log.info("随机管理员密码已写入: %s/admin-password.txt（登录后请在后台修改）",
                     settings.data_dir)
    registry = SessionRegistry(settings, store)
    app.state.store = store
    app.state.registry = registry

    tasks: list[asyncio.Task] = [
        asyncio.create_task(_session_worker(app), name="session-worker"),
    ]

    yield

    for t in tasks:
        t.cancel()
    log.info("服务已停止")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description=(
            "洛阳理工学院统一认证（CAS）多用户接口网关。\n\n"
            "每个网关用户拥有独立的统一认证账号、独立 CAS 会话和独立 API Key；\n"
            "后台：`/admin`（菜单式：概览/我的账号/统一认证/登录调试/接口自测/用户管理/系统设置/运行日志）。"
        ),
        lifespan=lifespan,
        default_response_class=UTF8JSONResponse,
    )
    app.state.settings = settings

    from .routers import admin, auth, docs, health, xsfw

    app.include_router(docs.router)      # 前台接口文档（GET / 、/api-docs）
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(xsfw.router)
    app.include_router(admin.router)

    # ------------------------------------------------------------ 异常处理
    @app.exception_handler(SessionError)
    async def _session_error(_req: Request, exc: SessionError) -> JSONResponse:
        log.error("会话错误: %s", exc)
        return UTF8JSONResponse(status_code=503, content={
            "code": "SESSION_ERROR", "msg": str(exc), "data": None,
            "mfa_required": exc.mfa_required,
            "hint": "请到后台界面 /admin 的「登录调试」输入企业微信验证码；"
                    "或调用 POST /api/v1/auth/login 重新登录并带 mfa_code",
        })

    @app.exception_handler(StoreError)
    async def _store_error(_req: Request, exc: StoreError) -> JSONResponse:
        log.error("用户数据存储错误: %s", exc)
        return UTF8JSONResponse(status_code=500, content={
            "ok": False, "code": "STORE_ERROR", "error": str(exc),
            "message": "数据保存失败：%s" % exc})

    @app.exception_handler(SystemError)
    async def _system_error(_req: Request, exc: SystemError) -> JSONResponse:
        log.error("业务系统错误: %s (code=%s)", exc.message, exc.code)
        return UTF8JSONResponse(status_code=exc.http_status, content={
            "code": exc.code, "msg": exc.message, "hint": exc.hint, "data": None})

    @app.get("/api/v1/meta", tags=["元信息"], summary="服务信息")
    def meta(request: Request) -> dict:
        runtime: RuntimeSettings = request.app.state.runtime_settings
        return {
            "name": runtime.get("app_name"),
            "version": settings.version,
            "docs": "/",
            "swagger": "/docs",
            "admin": "/admin",
            "openapi": "/openapi.json",
            "health": "/healthz",
            "auth": "每个用户在后台获得自己的 API Key（X-API-Key 请求头），调用各自统一认证账号的接口",
            "endpoints": [
                "GET  /                           前台接口文档页面",
                "GET  /api-docs                   前台接口文档页面（别名）",
                "GET  /admin                      后台菜单式调试界面（多用户）",
                "GET  /api/v1/auth/status         当前 API Key 用户的会话状态",
                "POST /api/v1/auth/login          登录当前 API Key 用户的统一认证",
                "GET  /api/v1/xsfw/roles",
                "GET  /api/v1/xsfw/menu",
                "GET  /api/v1/xsfw/sjkb/ssgq/students",
                "GET  /api/v1/xsfw/sjkb/getCqztDic",
                "GET  /api/v1/xsfw/sjkb/getCqztlxDic",
                "POST /api/v1/xsfw/call",
            ],
        }

    return app


app = create_app()
