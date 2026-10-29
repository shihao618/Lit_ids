"""后台调试界面（多用户）.

页面:
  GET  /admin                       后台首页（未登录显示登录页）
  POST /admin/login                 用户名 + 密码登录
  GET  /admin/logout                退出后台

后台 JSON API（登录后可用；标注"管理员"的仅 admin 角色）:
  GET  /admin/api/state             当前用户信息 + 自己的会话状态 + 自己的 API Key (+ 管理员时含用户列表)
  POST /admin/api/settings          保存自己的统一认证账号/密码/指纹
  POST /admin/api/account/password  修改自己的后台密码
  GET  /admin/api/apikey            查看自己的 API Key
  POST /admin/api/apikey/regenerate 重新生成自己的 API Key
  POST /admin/api/login             {force} 登录自己的统一认证
  POST /admin/api/mfa               {code} 提交企业微信验证码
  POST /admin/api/mfa/resend        重新发送企业微信验证码
  POST /admin/api/session/clear     清除自己的 CAS 会话
  GET  /admin/api/logs              最近日志
  GET  /admin/api/test/...          接口自测（使用自己的会话）
  GET  /admin/api/users             用户列表（管理员）
  POST /admin/api/users             创建用户（管理员）
  DELETE /admin/api/users/{uid}     删除用户（管理员）
  POST /admin/api/users/{uid}/password      重置用户密码（管理员）
  POST /admin/api/users/{uid}/apikey        重置用户 API Key（管理员）
  POST /admin/api/users/{uid}/cas           为用户配置统一认证账号（管理员）
  POST /admin/api/users/{uid}/role          设置用户角色（管理员）
"""

from __future__ import annotations

import html as html_lib
import logging
import os
import time
import urllib.parse
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..admin_auth import COOKIE_NAME, DEFAULT_TTL, get_cookie_user, make_token, require_admin_user, require_login
from ..deps import get_registry, get_xsfw
from ..logbuf import log_buffer
from ..restart import schedule_restart
from ..schemas import (
    AdminCasIn, AdminLoginIn, AdminMfaIn, AdminPasswordIn, AdminRoleIn,
    CasSettingsIn, GenericCallIn, SelfPasswordIn, SystemSettingsIn, UserCreateIn,
)
from ..session import SessionError
from ..settings_store import EDITABLE, apply_log_level
from ..systems import SystemError
from ..users import UserStore

log = logging.getLogger(__name__)

router = APIRouter(tags=["后台界面"])


# ============================================================ 页面
def _app_name(request: Request) -> str:
    runtime = getattr(request.app.state, "runtime_settings", None)
    name = runtime.get("app_name") if runtime is not None else None
    return name or request.app.state.settings.app_name


@router.get("/admin", response_class=HTMLResponse, summary="后台调试界面")
def admin_home(request: Request) -> HTMLResponse:
    no_store = {"Cache-Control": "no-store, max-age=0"}
    if not get_cookie_user(request):
        html = LOGIN_HTML.replace("__APP_NAME__", html_lib.escape(_app_name(request)))
        return HTMLResponse(html, headers=no_store)
    html = DASHBOARD_HTML.replace("__APP_NAME__", html_lib.escape(_app_name(request)))
    return HTMLResponse(html, headers=no_store)


@router.post("/admin/login", summary="后台登录（用户名+密码）")
async def admin_login(request: Request):
    raw = (await request.body()).decode("utf-8", "replace")
    params = urllib.parse.parse_qs(raw)
    username = (params.get("username") or [""])[0].strip()
    password = (params.get("password") or [""])[0]
    store: UserStore = request.app.state.store
    user = store.check_password(username, password) if username else None
    if not user:
        html = LOGIN_HTML.replace("<!--ERROR-->", '<div class="err">用户名或密码错误</div>')
        return HTMLResponse(html, status_code=401)
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(COOKIE_NAME, make_token(request.app.state.settings, user["id"]),
                    max_age=DEFAULT_TTL, httponly=True, samesite="lax", path="/")
    log.info("后台登录成功: %s", user.get("username"))
    return resp


@router.get("/admin/logout", summary="退出后台")
def admin_logout() -> RedirectResponse:
    resp = RedirectResponse("/admin", status_code=303)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


# ============================================================ 后台 JSON API
api = APIRouter(prefix="/admin/api", tags=["后台API"], dependencies=[Depends(require_login)])


def _err(e: Exception) -> dict[str, Any]:
    return {"ok": False, "error": str(e),
            "code": getattr(e, "code", None), "hint": getattr(e, "hint", None)}


def _self_public(store: UserStore, user: dict) -> dict:
    data = UserStore.public_user(user, mask_key=False)
    data["api_key"] = user.get("api_key") or ""
    return data


@api.get("/state", summary="当前用户 + 自己的会话 + 自己的 API Key")
def api_state(request: Request, user: dict = Depends(require_login)) -> dict[str, Any]:
    store: UserStore = request.app.state.store
    registry = get_registry(request)
    session = registry.session(user["id"])
    is_admin = user.get("role") == "admin"
    users = []
    if is_admin:
        users = [UserStore.public_user(u) for u in store.list()]
    return {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "user": _self_public(store, user),
        "is_admin": is_admin,
        "session": session.status(),
        "users": users,
        "env": {
            "data_dir": request.app.state.settings.data_dir,
            "users_file": request.app.state.settings.users_file,
            "session_dir": request.app.state.settings.session_dir,
            # 数据目录是否可写（fnOS 挂载权限不对时保存会失败，界面会提示）
            "data_writable": os.access(
                os.path.dirname(os.path.abspath(request.app.state.settings.users_file)) or ".",
                os.W_OK),
        },
    }


@api.post("/settings", summary="保存自己的统一认证账号/密码/指纹")
def api_settings(request: Request, body: CasSettingsIn,
                 user: dict = Depends(require_login)) -> dict[str, Any]:
    store: UserStore = request.app.state.store
    registry = get_registry(request)
    cas_username = (body.cas_username or "").strip()
    if not cas_username:
        return {"ok": False, "error": "统一认证账号不能为空（请输入学号/工号）"}
    cas_password = body.cas_password if body.cas_password else None
    updated = store.update_cas(user["id"], cas_username=cas_username,
                               cas_password=cas_password,
                               cas_fingerprint=body.cas_fingerprint)
    if not updated.get("cas_password"):
        return {"ok": True, "message": "统一认证账号已保存（尚未设置密码，请填写密码后再保存一次）",
                "session": get_registry(request).session(user["id"]).status()}
    # 账号/密码变化后清掉旧会话，避免继续用旧账号的登录态
    clear = bool(body.cas_username is not None or cas_password)
    registry.drop(user["id"], clear_cookies=clear)
    session = registry.session(user["id"])
    msg = "统一认证账号已保存" + ("（旧会话已清除，请到「🧪 登录调试」重新登录）" if clear else "")
    log.info("用户 %s 更新统一认证账号配置", user.get("username"))
    return {"ok": True, "message": msg, "session": session.status()}


@api.post("/account/password", summary="修改自己的后台密码")
def api_account_password(request: Request, body: SelfPasswordIn,
                         user: dict = Depends(require_login)) -> dict[str, Any]:
    store: UserStore = request.app.state.store
    checked = store.check_password(user.get("username", ""), body.old_password)
    if not checked:
        return {"ok": False, "error": "当前密码错误"}
    store.set_password(user["id"], body.new_password)
    log.info("用户 %s 修改后台密码", user.get("username"))
    return {"ok": True, "message": "后台密码已修改"}


@api.get("/apikey", summary="查看自己的 API Key")
def api_apikey(user: dict = Depends(require_login)) -> dict[str, Any]:
    return {"ok": True, "api_key": user.get("api_key") or ""}


@api.post("/apikey/regenerate", summary="重新生成自己的 API Key")
def api_apikey_regenerate(request: Request, user: dict = Depends(require_login)) -> dict[str, Any]:
    store: UserStore = request.app.state.store
    key = store.set_api_key(user["id"])
    log.info("用户 %s 重新生成 API Key", user.get("username"))
    return {"ok": True, "api_key": key, "message": "API Key 已重新生成，旧的 Key 已失效"}


@api.post("/login", summary="登录自己的统一认证（可 force）")
def api_login(request: Request, body: AdminLoginIn | None = None,
              user: dict = Depends(require_login)) -> dict[str, Any]:
    body = body or AdminLoginIn()
    session = get_registry(request).session(user["id"])
    try:
        res = session.start_login(force=body.force)
    except SessionError as e:
        return {"ok": False, "status": "error", "error": str(e), "session": session.status()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": "error", "error": "登录异常: %s" % e,
                "session": session.status()}
    return {"ok": res["status"] == "ok", **res}


@api.post("/mfa", summary="提交企业微信验证码")
def api_mfa(request: Request, body: AdminMfaIn,
            user: dict = Depends(require_login)) -> dict[str, Any]:
    session = get_registry(request).session(user["id"])
    try:
        res = session.complete_mfa(body.code)
    except SessionError as e:
        return {"ok": False, "status": "error", "error": str(e), "session": session.status()}
    return {"ok": res.get("status") == "ok", **res}


@api.post("/mfa/resend", summary="重新发送企业微信验证码")
def api_mfa_resend(request: Request, user: dict = Depends(require_login)) -> dict[str, Any]:
    session = get_registry(request).session(user["id"])
    try:
        res = session.resend_mfa()
    except SessionError as e:
        return {"ok": False, "status": "error", "error": str(e)}
    return {"ok": res.get("status") == "resent", **res}


@api.post("/session/clear", summary="清除自己的 CAS 会话")
def api_session_clear(request: Request, user: dict = Depends(require_login)) -> dict[str, Any]:
    registry = get_registry(request)
    session = registry.session(user["id"])
    session.clear_session()
    registry.drop(user["id"], clear_cookies=True)
    return {"ok": True, "status": "cleared", "session": session.status(),
            "message": "会话已清除（下次接口调用会重新登录）"}


@api.get("/logs", summary="最近运行日志")
def api_logs(limit: int = Query(200, ge=1, le=500)) -> dict[str, Any]:
    return {"lines": log_buffer.get(limit)}


# ------------------------------------------------------------ 接口自测（使用当前用户自己的会话）
@api.get("/test/roles", summary="自测：角色列表")
def test_roles(system=Depends(get_xsfw)) -> dict[str, Any]:
    try:
        return {"ok": True, "data": system.roles(refresh=True)}
    except (SystemError, SessionError) as e:
        return _err(e)


@api.get("/test/menu", summary="自测：当前角色菜单")
def test_menu(system=Depends(get_xsfw)) -> dict[str, Any]:
    try:
        system.ensure_session()
        if system.current_role() is None:
            system.set_role()
        return {"ok": True, "data": {"role": system.current_role(), "menu": system.menu()}}
    except (SystemError, SessionError) as e:
        return _err(e)


@api.get("/test/students", summary="自测：学生归寝查询（官方参数）")
def test_students(
    searchkey: str = Query("", alias="SEARCHKEY"),
    page_number: int = Query(1, alias="pageNumber", ge=1),
    page_size: int = Query(10, alias="pageSize", ge=1, le=200),
    cqzt: str = Query("", alias="CQZT"),
    cqztlx: str = Query("", alias="CQZTLX"),
    system=Depends(get_xsfw),
) -> dict[str, Any]:
    try:
        data = system.get_ssgq_students(searchkey=searchkey, page_number=page_number,
                                        page_size=page_size, cqzt=cqzt, cqztlx=cqztlx)
        return {"ok": True, "data": data}
    except (SystemError, SessionError) as e:
        return _err(e)


@api.get("/test/getCqztDic", summary="自测：查寝状态字典")
def test_cqzt_dic(system=Depends(get_xsfw)) -> dict[str, Any]:
    try:
        return {"ok": True, "data": system.get_cqzt_dic()}
    except (SystemError, SessionError) as e:
        return _err(e)


@api.get("/test/getCqztlxDic", summary="自测：查寝状态类型字典")
def test_cqztlx_dic(cqzt: str = Query("", alias="CQZT"), system=Depends(get_xsfw)) -> dict[str, Any]:
    try:
        return {"ok": True, "data": system.get_cqztlx_dic(cqzt=cqzt)}
    except (SystemError, SessionError) as e:
        return _err(e)


@api.post("/test/call", summary="自测：通用调用")
def test_call(body: GenericCallIn, system=Depends(get_xsfw)) -> dict[str, Any]:
    try:
        system.set_role(None)
        return {"ok": True, "data": system.call(body.path, body.data)}
    except (SystemError, SessionError) as e:
        return _err(e)


# ------------------------------------------------------------ 用户管理（管理员）
@api.get("/users", summary="用户列表（管理员）")
def list_users(request: Request, admin: dict = Depends(require_admin_user)) -> dict[str, Any]:
    store: UserStore = request.app.state.store
    return {"ok": True, "users": [UserStore.public_user(u) for u in store.list()]}


@api.post("/users", summary="创建用户（管理员）")
def create_user(request: Request, body: UserCreateIn,
                admin: dict = Depends(require_admin_user)) -> dict[str, Any]:
    store: UserStore = request.app.state.store
    try:
        user = store.create(
            username=body.username, password=body.password, role=body.role,
            cas_username=body.cas_username, cas_password=body.cas_password,
            cas_fingerprint=body.cas_fingerprint)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    log.info("管理员 %s 创建用户 %s", admin.get("username"), user.get("username"))
    return {"ok": True, "user": UserStore.public_user(user, mask_key=False),
            "message": "用户已创建，请把 API Key 交给该用户"}


@api.delete("/users/{user_id}", summary="删除用户（管理员）")
def delete_user(request: Request, user_id: str,
                admin: dict = Depends(require_admin_user)) -> dict[str, Any]:
    store: UserStore = request.app.state.store
    if user_id == admin.get("id"):
        return {"ok": False, "error": "不能删除当前登录的管理员自己"}
    target = store.get(user_id)
    if not target:
        return {"ok": False, "error": "用户不存在"}
    store.delete(user_id)
    get_registry(request).drop(user_id, clear_cookies=True)
    log.info("管理员 %s 删除用户 %s", admin.get("username"), target.get("username"))
    return {"ok": True, "message": "用户已删除"}


@api.post("/users/{user_id}/password", summary="重置用户密码（管理员）")
def reset_user_password(request: Request, user_id: str, body: AdminPasswordIn,
                        admin: dict = Depends(require_admin_user)) -> dict[str, Any]:
    store: UserStore = request.app.state.store
    if not store.get(user_id):
        return {"ok": False, "error": "用户不存在"}
    try:
        store.set_password(user_id, body.password)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    log.info("管理员 %s 重置用户 %s 的密码", admin.get("username"), user_id[:8])
    return {"ok": True, "message": "密码已重置"}


@api.post("/users/{user_id}/apikey", summary="重置用户 API Key（管理员）")
def reset_user_apikey(request: Request, user_id: str,
                      admin: dict = Depends(require_admin_user)) -> dict[str, Any]:
    store: UserStore = request.app.state.store
    if not store.get(user_id):
        return {"ok": False, "error": "用户不存在"}
    key = store.set_api_key(user_id)
    return {"ok": True, "api_key": key, "message": "API Key 已重新生成"}


@api.post("/users/{user_id}/cas", summary="为用户配置统一认证账号（管理员）")
def set_user_cas(request: Request, user_id: str, body: AdminCasIn,
                 admin: dict = Depends(require_admin_user)) -> dict[str, Any]:
    store: UserStore = request.app.state.store
    if not store.get(user_id):
        return {"ok": False, "error": "用户不存在"}
    store.update_cas(user_id, cas_username=body.cas_username,
                     cas_password=body.cas_password if body.cas_password else None,
                     cas_fingerprint=body.cas_fingerprint)
    get_registry(request).drop(user_id, clear_cookies=True)
    return {"ok": True, "message": "已更新该用户的统一认证账号"}


@api.post("/users/{user_id}/role", summary="设置用户角色（管理员）")
def set_user_role(request: Request, user_id: str, body: AdminRoleIn,
                  admin: dict = Depends(require_admin_user)) -> dict[str, Any]:
    store: UserStore = request.app.state.store
    target = store.get(user_id)
    if not target:
        return {"ok": False, "error": "用户不存在"}
    if user_id == admin.get("id") and body.role != "admin":
        return {"ok": False, "error": "不能取消当前管理员自己的管理员角色"}
    try:
        store.set_role(user_id, body.role)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "message": "角色已更新"}


# ------------------------------------------------------------ 系统设置 / 重启容器（管理员）
@api.get("/system-settings", summary="系统设置（管理员）")
def get_system_settings(request: Request,
                        admin: dict = Depends(require_admin_user)) -> dict[str, Any]:
    runtime = request.app.state.runtime_settings
    settings = request.app.state.settings
    return {
        "ok": True,
        "settings": runtime.public(),
        "env": {
            "data_dir": settings.data_dir,
            "users_file": settings.users_file,
            "session_dir": settings.session_dir,
            "admin_username": settings.admin_username,
            "api_key_bootstrap": bool(settings.api_key),
        },
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


@api.post("/system-settings", summary="保存系统设置（管理员，部分需重启）")
def update_system_settings(request: Request, body: SystemSettingsIn,
                           admin: dict = Depends(require_admin_user)) -> dict[str, Any]:
    runtime = request.app.state.runtime_settings
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    if not payload:
        return {"ok": True, "message": "没有需要修改的配置"}
    try:
        values, restart_required = runtime.update(payload)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if "log_level" in payload:
        apply_log_level(str(values.get("log_level")))
    log.info("管理员 %s 修改系统设置: %s", admin.get("username"), sorted(payload))
    restart_now = [k for k in restart_required if EDITABLE[k].get("restart")]
    return {
        "ok": True,
        "values": values,
        "restart_required": restart_now,
        "settings": runtime.public(),
        "message": "设置已保存" + ("（部分配置需重启容器后生效，可点下方按钮重启）"
                                  if restart_now else ""),
    }


@api.post("/restart", summary="重启容器（管理员）")
def api_restart(request: Request, admin: dict = Depends(require_admin_user)) -> dict[str, Any]:
    log.warning("管理员 %s 触发容器重启", admin.get("username"))
    schedule_restart()
    return {"ok": True, "message": "容器即将重启，约 5~15 秒后恢复，请稍后刷新页面"}


# 把后台 JSON API 挂到页面路由上（main.py 只需 include admin.router）
router.include_router(api)


# ============================================================ HTML 模板
_CSS = """
*{box-sizing:border-box}
body{margin:0;font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:#f3f5f9;color:#222}
header{background:#1f2a44;color:#fff;padding:14px 22px;display:flex;justify-content:space-between;align-items:center}
header h1{font-size:17px;margin:0;font-weight:600}
header a{color:#9fc3ff;text-decoration:none;margin-left:14px}
main{max-width:1240px;margin:18px auto;padding:0 16px;display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:980px){main{grid-template-columns:1fr}}
.card{background:#fff;border-radius:10px;box-shadow:0 1px 4px rgba(20,40,80,.08);padding:16px 18px}
.card h2{font-size:15px;margin:0 0 12px;padding-bottom:8px;border-bottom:2px solid #eef2f8;color:#1f2a44}
.full{grid-column:1/-1}
label{display:block;font-size:13px;color:#556;margin:8px 0 4px}
input,select,textarea{width:100%;padding:7px 9px;border:1px solid #d5dce8;border-radius:6px;font:inherit;background:#fbfcfe}
textarea{min-height:64px;font-family:Consolas,monospace;font-size:12.5px}
button{padding:8px 16px;border:0;border-radius:6px;background:#2f6fed;color:#fff;font:inherit;cursor:pointer;margin:10px 8px 0 0}
button.gray{background:#6b7a93}
button.green{background:#15905a}
button.red{background:#c94040}
button:disabled{opacity:.55;cursor:not-allowed}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.kv{display:grid;grid-template-columns:110px 1fr;row-gap:6px;font-size:13px}
.kv b{color:#667}
pre{background:#0f172a;color:#d6e2f5;padding:12px;border-radius:8px;overflow:auto;max-height:340px;font-size:12.5px;margin:10px 0 0}
#logs{max-height:280px}
.msg{margin-top:10px;padding:8px 12px;border-radius:6px;background:#eef3ff;color:#1d4ed8;font-size:13px;display:none}
.msg.show{display:block}
.msg.err{background:#fdecec;color:#b91c1c}
.msg.ok{background:#e8f8ef;color:#15803d}
.ok-tag{color:#15803d;font-weight:600}
.bad-tag{color:#b91c1c;font-weight:600}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:8px}
th,td{padding:5px 6px;border-bottom:1px solid #eef2f8;text-align:left;vertical-align:top}
th{color:#667;font-weight:600}
input[readonly]{background:#f0f3f8;color:#445}
.login-wrap{max-width:390px;margin:110px auto;background:#fff;padding:30px;border-radius:12px;box-shadow:0 8px 30px rgba(20,40,80,.12)}
.login-wrap h1{font-size:19px;margin:0 0 6px}
.login-wrap p{color:#778;font-size:13px;margin:0 0 18px}
.login-wrap input{margin-bottom:12px}
.login-wrap button{width:100%;margin:2px 0 0}
.err{color:#b91c1c;background:#fdecec;padding:8px 10px;border-radius:6px;font-size:13px;margin-bottom:10px}
.note{font-size:12px;color:#889;margin-top:6px}
"""

_LOGIN_TMPL = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>__APP_NAME__ · 后台登录</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>__CSS__</style></head>
<body>
<div class="login-wrap">
  <h1>__APP_NAME__ · 后台</h1>
  <p>多用户版：每个用户登录后可配置自己的统一认证账号，并用各自的 API Key 调用接口。</p>
  <!--ERROR-->
  <form method="post" action="/admin/login">
    <label>用户名</label>
    <input type="text" name="username" autofocus autocomplete="username" placeholder="后台用户名">
    <label>密码</label>
    <input type="password" name="password" autocomplete="current-password" placeholder="后台密码">
    <button type="submit">登 录</button>
  </form>
  <p class="note" style="margin-top:16px">默认管理员由环境变量 ADMIN_USERNAME / ADMIN_PASSWORD 引导创建。</p>
</div>
</body></html>"""

_DASHBOARD_TMPL = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>__APP_NAME__ · 后台</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>__CSS__</style>
<style>
.shell{display:flex;max-width:1280px;margin:18px auto;padding:0 16px;gap:16px;align-items:flex-start}
.menu{width:190px;flex:none;background:#fff;border-radius:10px;box-shadow:0 1px 4px rgba(20,40,80,.08);padding:10px;position:sticky;top:72px}
.menu-item{display:block;padding:9px 12px;border-radius:7px;color:#2b3852;text-decoration:none;cursor:pointer;font-size:13.5px;margin-bottom:2px;user-select:none}
.menu-item:hover{background:#eef3fd}
.menu-item.active{background:#2f6fed;color:#fff}
.content{flex:1;min-width:0;display:flex;flex-direction:column;gap:16px}
.panel{display:none;flex-direction:column;gap:16px}
.panel.active{display:flex}
.card-row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:900px){.shell{flex-direction:column}.menu{width:100%;position:static;display:flex;flex-wrap:wrap}.card-row{grid-template-columns:1fr}}
.badge-restart{background:#fff3cd;color:#8a6d3b;border-radius:4px;padding:0 6px;font-size:11.5px;margin-left:6px;font-weight:400}
#overlay{display:none;position:fixed;inset:0;background:rgba(15,23,42,.74);color:#fff;z-index:99;align-items:center;justify-content:center;flex-direction:column;font-size:16px;gap:18px}
#overlay.show{display:flex}
.spinner{width:40px;height:40px;border:4px solid rgba(255,255,255,.25);border-top-color:#fff;border-radius:50%;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
</style></head>
<body>
<header>
  <h1>__APP_NAME__ · 后台</h1>
  <div><span id="who" style="color:#cbd8ee"></span><a href="/" target="_blank">接口文档</a><a href="/docs" target="_blank">Swagger</a><a href="/admin/logout">退出后台</a></div>
</header>
<div class="shell">
  <aside class="menu">
    <a class="menu-item active" data-panel="overview">📊 概览</a>
    <a class="menu-item" data-panel="account">👤 我的账号</a>
    <a class="menu-item" data-panel="cas">🔐 统一认证</a>
    <a class="menu-item" data-panel="login">🧪 登录调试</a>
    <a class="menu-item" data-panel="test">🔧 接口自测</a>
    <a class="menu-item admin-only" data-panel="users" style="display:none">👥 用户管理</a>
    <a class="menu-item admin-only" data-panel="settings" style="display:none">⚙️ 系统设置</a>
    <a class="menu-item" data-panel="logs">📜 运行日志</a>
  </aside>
  <main class="content">
    <!-- 概览 -->
    <section class="panel active" id="panel-overview">
      <div class="card">
        <h2>当前用户</h2>
        <div class="kv">
          <b>后台用户名</b><span id="u-name">-</span>
          <b>角色</b><span id="u-role">-</span>
          <b>统一认证账号</b><span id="u-cas">-</span>
          <b>API Key</b><span id="u-key">-</span>
        </div>
      </div>
      <div class="card">
        <h2>我的统一认证会话</h2>
        <div class="kv">
          <b>登录状态</b><span id="s-status">-</span>
          <b>登录时间</b><span id="s-time">-</span>
          <b>TGC</b><span id="s-tgc" style="word-break:break-all">-</span>
          <b>最近错误</b><span id="s-err">-</span>
        </div>
        <div class="row">
          <button onclick="startLogin(false)">登录（复用有效会话）</button>
          <button class="gray" onclick="startLogin(true)">强制重新登录</button>
          <button class="red" onclick="clearSession()">清除我的 CAS 会话</button>
        </div>
        <div id="overview-msg" class="msg"></div>
      </div>
      <div class="card" id="overview-admin" style="display:none">
        <h2>系统概况（管理员）</h2>
        <div class="kv"><b>用户数</b><span id="ov-users">-</span><b>已配置统一认证</b><span id="ov-users-cas">-</span></div>
        <div class="row"><button class="gray" onclick="gotoPanel('users')">用户管理</button><button class="gray" onclick="gotoPanel('settings')">系统设置</button></div>
      </div>
    </section>

    <!-- 我的账号 -->
    <section class="panel" id="panel-account">
      <div class="card">
        <h2>我的 API Key</h2>
        <div class="row"><input id="api-key" readonly><button class="gray" onclick="copyKey()">复制</button><button class="gray" onclick="regenKey()">重新生成</button></div>
        <div id="key-msg" class="msg"></div>
        <div class="note">调用接口时放在请求头 X-API-Key；重新生成后旧的 Key 立即失效。</div>
      </div>
      <div class="card">
        <h2>修改后台密码</h2>
        <label>当前密码</label><input id="old-pwd" type="password" placeholder="当前后台密码">
        <label>新密码（至少 6 位）</label><input id="new-pwd" type="password" placeholder="新后台密码">
        <div class="row"><button class="green" onclick="changePassword()">修改密码</button></div>
        <div id="pwd-msg" class="msg"></div>
      </div>
    </section>

    <!-- 统一认证 -->
    <section class="panel" id="panel-cas">
      <div class="card">
        <h2>我的统一认证账号</h2>
        <div id="data-warn" class="msg err" style="display:none"></div>
        <label>账号（学号/工号）</label>
        <input id="cas-username" placeholder="例如 B2607xxxx">
        <label>密码（留空 = 不修改）</label>
        <input id="cas-password" type="password" placeholder="输入新密码；留空保持不变">
        <label>设备指纹 browserFingerprint（可留空）</label>
        <input id="cas-fingerprint" placeholder="已受信任的设备指纹，留空则自动生成">
        <div class="row">
          <button class="green" onclick="saveCas()">保存账号</button>
          <button class="red" onclick="clearSession()">清除我的 CAS 会话</button>
        </div>
        <div id="cas-msg" class="msg"></div>
        <div class="note" id="cas-note">-</div>
        <div class="note">保存后仅影响当前用户自己的会话；若改了账号/密码，请到「登录调试」重新登录。</div>
      </div>
    </section>

    <!-- 登录调试 -->
    <section class="panel" id="panel-login">
      <div class="card">
        <h2>登录调试（账号密码 / 企业微信验证码）</h2>
        <div class="row">
          <button onclick="startLogin(false)">登录（复用有效会话）</button>
          <button class="gray" onclick="startLogin(true)">强制重新登录</button>
          <span class="note" id="login-stage"></span>
        </div>
        <div id="login-msg" class="msg"></div>
        <div id="mfa-area" style="display:none;border:1px dashed #f0b429;background:#fffbeb;padding:12px;border-radius:8px;margin-top:12px">
          <b>需要企业微信验证码</b>（请到该账号绑定的企业微信查看「统一身份认证」验证码）
          <div class="row" style="margin-top:8px">
            <input id="mfa-code" placeholder="请输入验证码" style="max-width:220px">
            <button class="green" onclick="submitMfa()">提交验证码</button>
            <button class="gray" onclick="resendMfa()">重新发送</button>
          </div>
        </div>
        <div class="note" id="captcha-debug"></div>
      </div>
    </section>

    <!-- 接口自测 -->
    <section class="panel" id="panel-test">
      <div class="card">
        <h2>接口自测（使用我自己的会话）</h2>
        <div class="row">
          <button class="gray" onclick="testRoles()">角色列表</button>
          <button class="gray" onclick="testMenu()">菜单</button>
          <button class="gray" onclick="testCqztDic()">CQZT 字典</button>
          <button class="gray" onclick="testCqztlxDic()">CQZTLX 类型</button>
        </div>
        <div class="row" style="margin-top:10px">
          <span>学生归寝（官方参数）：</span>
          <input id="test-searchkey" value="" placeholder="SEARCHKEY" style="width:130px">
          <input id="test-page-number" value="1" placeholder="pageNumber" style="width:100px">
          <input id="test-page-size" value="10" placeholder="pageSize" style="width:95px">
          <input id="test-cqzt" value="" placeholder="CQZT(1/2/3, 空=全部)" style="width:185px">
          <input id="test-cqztlx" value="" placeholder="CQZTLX(可空)" style="width:130px">
          <button onclick="testStudents()">查询</button>
        </div>
        <div class="row" style="margin-top:10px">
          <span>通用调用：</span>
          <input id="test-path" style="flex:1;min-width:280px" value="/xsfw/sys/swmzncqapp/modules/sjkb/getSsgqStudent.do">
          <button onclick="testCall()">调用</button>
        </div>
        <textarea id="test-data">{"SEARCHKEY":"","pageNumber":1,"pageSize":5,"CQZT":""}</textarea>
        <pre id="test-out">（接口自测结果）</pre>
      </div>
    </section>

    <!-- 用户管理（管理员） -->
    <section class="panel" id="panel-users">
      <div class="card">
        <h2>用户管理（管理员）</h2>
        <div class="row">
          <input id="new-username" placeholder="用户名" style="width:130px">
          <input id="new-password" type="password" placeholder="初始密码(≥6位)" style="width:150px">
          <select id="new-role" style="width:110px"><option value="user">普通用户</option><option value="admin">管理员</option></select>
          <input id="new-cas-username" placeholder="统一认证账号(可空)" style="width:170px">
          <input id="new-cas-password" type="password" placeholder="统一认证密码(可空)" style="width:170px">
          <button class="green" onclick="createUser()">创建用户</button>
        </div>
        <div id="users-msg" class="msg"></div>
        <table id="users-table">
          <thead><tr><th>用户名</th><th>角色</th><th>统一认证账号</th><th>API Key</th><th>创建时间</th><th>操作</th></tr></thead>
          <tbody id="users-tbody"></tbody>
        </table>
      </div>
    </section>

    <!-- 系统设置（管理员） -->
    <section class="panel" id="panel-settings">
      <div class="card">
        <h2>系统设置（管理员）</h2>
        <label>系统名称</label>
        <input id="set-app-name" placeholder="例如 洛阳理工学院统一认证接口服务">
        <label>日志级别</label>
        <select id="set-log-level">
          <option value="DEBUG">DEBUG</option><option value="INFO">INFO</option>
          <option value="WARNING">WARNING</option><option value="ERROR">ERROR</option>
        </select>
        <label>会话保活间隔（秒，0 = 关闭）</label>
        <input id="set-refresh" type="number" min="0" value="0">
        <label>启动时自动登录<span class="badge-restart">需重启</span></label>
        <select id="set-auto-login"><option value="true">开启</option><option value="false">关闭</option></select>
        <div class="row">
          <button class="green" onclick="saveSystemSettings()">保存设置</button>
          <button class="gray" onclick="loadSystemSettings()">重新读取</button>
        </div>
        <div id="sys-msg" class="msg"></div>
        <div class="note" id="sys-note">-</div>
      </div>
      <div class="card">
        <h2>重启容器</h2>
        <p class="note" style="margin-top:0">「启动时自动登录」等启动项需要重启容器后生效。点击后容器会在约 5~15 秒内自动重启并恢复，页面稍后会自动刷新。</p>
        <button class="red" onclick="restartContainer()">🔄 重启容器</button>
        <div id="restart-msg" class="msg"></div>
      </div>
    </section>

    <!-- 运行日志 -->
    <section class="panel" id="panel-logs">
      <div class="card">
        <h2>运行日志</h2>
        <div class="row">
          <button class="gray" onclick="loadLogs()">刷新日志</button>
          <label style="display:inline;margin:0"><input type="checkbox" id="auto-log" checked style="width:auto"> 自动刷新</label>
        </div>
        <pre id="logs">（日志）</pre>
      </div>
    </section>
  </main>
</div>
<div id="overlay"><div class="spinner"></div><div id="overlay-text">处理中…</div></div>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function show(el,text,type){el.textContent=text;el.className='msg show'+(type?(' '+type):'');}
async function api(path,opt){const r=await fetch(path,Object.assign({credentials:'same-origin'},opt||{}));if(r.status===401){location.href='/admin';throw new Error('未登录');}let d=null;try{d=await r.json();}catch(e){}return d;}
async function post(path,body){return api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});}
function fmt(v){return (v===undefined||v===null||v==='')?'-':esc(v);}
let IS_ADMIN=false, CURRENT='overview';
let casDirty=false;   // 统一认证表单是否被用户修改（避免 5s 自动刷新覆盖输入）

function gotoPanel(name){
  if(!name) name='overview';
  const panel=$('panel-'+name);
  if(!panel){name='overview';}
  const item=document.querySelector('.menu-item[data-panel="'+name+'"]');
  if(item&&item.classList.contains('admin-only')&&!IS_ADMIN){name='overview';}
  CURRENT=name;
  document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('active',p.id==='panel-'+name));
  document.querySelectorAll('.menu-item').forEach(a=>a.classList.toggle('active',a.dataset.panel===name));
  history.replaceState(null,'','#'+name);
  if(name==='logs') loadLogs();
  if(name==='settings'&&IS_ADMIN) loadSystemSettings();
  if(name==='users'&&IS_ADMIN) refresh();
}
document.querySelectorAll('.menu-item').forEach(a=>{a.addEventListener('click',()=>gotoPanel(a.dataset.panel));});
window.addEventListener('hashchange',()=>gotoPanel((location.hash||'#overview').slice(1)));
['cas-username','cas-password','cas-fingerprint'].forEach(id=>{const el=document.getElementById(id);if(el)el.addEventListener('input',()=>{casDirty=true;});});

async function refresh(){
  const st=await api('/admin/api/state');
  if(!st) return;
  IS_ADMIN=!!st.is_admin;
  $('who').textContent=st.user.username||'';
  $('u-name').textContent=fmt(st.user.username);
  $('u-role').innerHTML=st.user.role==='admin'?'<span class="ok-tag">管理员</span>':'普通用户';
  $('u-cas').textContent=fmt(st.user.cas_username);
  const key=st.user.api_key||''; $('u-key').textContent=key?key.slice(0,14)+'…':'-';
  $('api-key').value=key;
  if(!casDirty){
    $('cas-username').value=st.user.cas_username||'';
    $('cas-fingerprint').value=st.user.cas_fingerprint||'';
  }
  const s=st.session||{};
  $('s-status').innerHTML=s.logged_in?'<span class="ok-tag">已登录</span>':'<span class="bad-tag">未登录</span>';
  $('s-time').textContent=fmt(s.login_time);
  $('s-tgc').textContent=fmt(s.tgc_masked);
  $('s-err').textContent=fmt(s.last_error);
  $('cas-note').textContent='登录状态：'+(s.logged_in?'已登录':'未登录')+'；最近登录：'+fmt(s.login_time)+
    (s.mfa_used?'；已通过二次验证':'')+(s.last_error?('；最近错误：'+s.last_error):'')+
    '；密码：'+(st.user.cas_password_set?'已设置':'未设置')+
    '（用户数据：'+((st.env&&st.env.users_file)||'data/users.json')+'）';
  const env=st.env||{};
  if(env.data_writable===false){
    $('data-warn').textContent='⚠ 数据目录不可写（'+fmt(env.data_dir)+'），保存会失败。请在 NAS 上检查数据目录权限（如 chown -R 1000:1000 ./data）后重启容器。';
    $('data-warn').style.display='block';
  } else {
    $('data-warn').style.display='none';
  }
  $('mfa-area').style.display=s.pending_mfa?'block':'none';
  const dbg=s.debug||{}; $('login-stage').textContent=dbg.stage?('最近阶段: '+dbg.stage+(dbg.at?' @ '+dbg.at:'')):'';
  renderCaptcha(dbg.captcha||[]);
  document.querySelectorAll('.admin-only').forEach(el=>{el.style.display=IS_ADMIN?'block':'none';});
  $('overview-admin').style.display=IS_ADMIN?'block':'none';
  if(IS_ADMIN&&st.users){
    $('ov-users').textContent=st.users.length;
    $('ov-users-cas').textContent=st.users.filter(u=>u.cas_username).length;
    renderUsers(st.users);
  }
  if(!IS_ADMIN&&(CURRENT==='users'||CURRENT==='settings')) gotoPanel('overview');
}
function renderCaptcha(list){
  if(!list.length){$('captcha-debug').textContent='';return;}
  let h='<table><tr><th>#</th><th>模板</th><th>randomX</th><th>残差</th><th>峰值比</th><th>结果</th></tr>';
  for(const c of list){h+='<tr><td>'+c.attempt+'</td><td>'+fmt(c.style)+'</td><td>'+fmt(c.random_x)+'</td><td>'+fmt(c.residual)+'</td><td>'+fmt(c.margin)+'</td><td>'+(c.accepted?'✅ 采用':'跳过'+(c.reason?('（'+esc(c.reason)+'）'):''))+'</td></tr>';}
  $('captcha-debug').innerHTML='<div style="margin-top:12px"><b>滑块验证码识别过程</b>'+h+'</table></div>';
}
function renderUsers(users){
  let h='';
  for(const u of users){
    h+='<tr><td>'+esc(u.username)+(u.role==='admin'?' <span class="ok-tag">管理员</span>':'')+'</td><td>'+esc(u.role)+'</td><td>'+fmt(u.cas_username)+'</td><td><code>'+esc(u.api_key||'')+'</code></td><td>'+fmt(u.created_at)+
       '</td><td><button class="gray" data-id="'+esc(u.id)+'" onclick="resetUserPwd(this.dataset.id)">重置密码</button>'+
       '<button class="gray" data-id="'+esc(u.id)+'" onclick="resetUserKey(this.dataset.id)">重置Key</button>'+
       '<button class="red" data-id="'+esc(u.id)+'" onclick="deleteUser(this.dataset.id)">删除</button></td></tr>';
  }
  $('users-tbody').innerHTML=h||'<tr><td colspan="6">暂无用户</td></tr>';
}

async function copyKey(){try{await navigator.clipboard.writeText($('api-key').value);show($('key-msg'),'API Key 已复制到剪贴板','ok');}catch(e){show($('key-msg'),'复制失败，请手动选择复制','err');}}
async function regenKey(){if(!confirm('重新生成后，旧的 API Key 立即失效，确定？'))return;const r=await post('/admin/api/apikey/regenerate',{});show($('key-msg'),r.message||(r.ok?'已生成':'失败'),r.ok?'ok':'err');refresh();}
async function changePassword(){const old=$('old-pwd').value,newp=$('new-pwd').value;if(!old||!newp){show($('pwd-msg'),'请输入当前密码和新密码','err');return;}const r=await post('/admin/api/account/password',{old_password:old,new_password:newp});show($('pwd-msg'),r.message||(r.ok?'已修改':'失败'+(r.error?('：'+r.error):'')),r.ok?'ok':'err');$('old-pwd').value='';$('new-pwd').value='';}
async function saveCas(){
  const username=$('cas-username').value.trim();
  if(!username){show($('cas-msg'),'请先填写统一认证账号（学号/工号）','err');$('cas-username').focus();return;}
  const body={cas_username:username,cas_fingerprint:$('cas-fingerprint').value.trim()};
  const p=$('cas-password').value;if(p)body.cas_password=p;
  const r=await post('/admin/api/settings',body);
  $('cas-password').value='';casDirty=false;
  show($('cas-msg'),r.message||(r.ok?'已保存':'失败'+(r.error?('：'+r.error):'')),r.ok?'ok':'err');
  refresh();
}
async function clearSession(){if(!confirm('确定清除当前用户的 CAS 会话？'))return;const r=await post('/admin/api/session/clear',{});show($('overview-msg'),r.message||'已清除','ok');refresh();}
function applyLoginResult(r){
  if(!r) return;
  const target=$('login-msg'); target.classList.add('show');
  if(r.status==='need_mfa'){show(target,'账号密码验证通过，请输入企业微信验证码','');$('mfa-code').focus();}
  else if(r.status==='mfa_error'){show(target,'验证码错误：'+(r.error||''),'err');$('mfa-code').select();}
  else if(r.ok||r.status==='ok'){show(target,'登录成功'+(r.reused?'（复用有效会话）':'')+(r.mfa_used?'（已通过二次验证）':''),'ok');}
  else{show(target,'失败：'+(r.error||r.message||'未知错误'),'err');}
  refresh();
}
async function startLogin(force){show($('overview-msg'),'登录中，请稍候…','');show($('login-msg'),'登录中，请稍候（自动识别滑块验证码，约 3~15 秒）…','');const r=await post('/admin/api/login',{force:!!force});show($('overview-msg'),r&&r.status==='ok'?'登录成功':'见「登录调试」页结果',r&&r.status==='ok'?'ok':'');applyLoginResult(r);}
async function submitMfa(){const code=$('mfa-code').value.trim();if(!code){show($('login-msg'),'请输入验证码','err');return;}const r=await post('/admin/api/mfa',{code});$('mfa-code').value='';applyLoginResult(r);}
async function resendMfa(){const r=await post('/admin/api/mfa/resend',{});show($('login-msg'),r.ok?'验证码已重新发送':'重发失败：'+(r.error||''),r.ok?'ok':'err');}
async function testRoles(){const r=await api('/admin/api/test/roles');$('test-out').textContent=JSON.stringify(r,null,2);}
async function testMenu(){const r=await api('/admin/api/test/menu');$('test-out').textContent=JSON.stringify(r,null,2);}
async function testCqztDic(){const r=await api('/admin/api/test/getCqztDic');$('test-out').textContent=JSON.stringify(r,null,2);}
async function testCqztlxDic(){const q=new URLSearchParams({CQZT:$('test-cqzt').value||''});const r=await api('/admin/api/test/getCqztlxDic?'+q);$('test-out').textContent=JSON.stringify(r,null,2);}
async function testStudents(){const q=new URLSearchParams({SEARCHKEY:$('test-searchkey').value,pageNumber:$('test-page-number').value||1,pageSize:$('test-page-size').value||10,CQZT:$('test-cqzt').value,CQZTLX:$('test-cqztlx').value});const r=await api('/admin/api/test/students?'+q);$('test-out').textContent=JSON.stringify(r,null,2);}
async function testCall(){let data={};try{data=JSON.parse($('test-data').value||'{}');}catch(e){$('test-out').textContent='data JSON 解析失败: '+e;return;}const r=await post('/admin/api/test/call',{path:$('test-path').value.trim(),data});$('test-out').textContent=JSON.stringify(r,null,2);}
async function createUser(){
  const body={username:$('new-username').value.trim(),password:$('new-password').value,role:$('new-role').value,
              cas_username:$('new-cas-username').value.trim(),cas_password:$('new-cas-password').value};
  const r=await post('/admin/api/users',body);
  show($('users-msg'), r.ok?('用户已创建，API Key：'+r.user.api_key):('创建失败：'+(r.error||'')), r.ok?'ok':'err');
  if(r.ok){$('new-username').value='';$('new-password').value='';$('new-cas-username').value='';$('new-cas-password').value='';refresh();}
}
async function resetUserPwd(id){const pwd=prompt('输入该用户的新后台密码（≥6位）：');if(!pwd)return;const r=await post('/admin/api/users/'+id+'/password',{password:pwd});show($('users-msg'),r.message||(r.ok?'已重置':'失败'+(r.error?('：'+r.error):'')),r.ok?'ok':'err');}
async function resetUserKey(id){if(!confirm('重新生成该用户的 API Key？旧 Key 会立即失效。'))return;const r=await post('/admin/api/users/'+id+'/apikey',{});show($('users-msg'),r.ok?('新 API Key：'+r.api_key):('失败：'+(r.error||'')),r.ok?'ok':'err');refresh();}
async function deleteUser(id){if(!confirm('确定删除该用户？其会话/API Key 将立即失效。'))return;const r=await api('/admin/api/users/'+id,{method:'DELETE'});show($('users-msg'),r.message||(r.ok?'已删除':'失败'+(r.error?('：'+r.error):'')),r.ok?'ok':'err');refresh();}
async function loadSystemSettings(){
  const r=await api('/admin/api/system-settings'); if(!r||!r.ok) return;
  const v=r.settings.values||{};
  $('set-app-name').value=v.app_name||'';
  $('set-log-level').value=v.log_level||'INFO';
  $('set-refresh').value=(v.session_refresh_interval===0?0:(v.session_refresh_interval||0));
  $('set-auto-login').value=v.auto_login_on_start?'true':'false';
  const restartItems=Object.entries(r.settings.editable||{}).filter(([k,m])=>m.restart).map(([k,m])=>m.label);
  $('sys-note').textContent='配置文件：'+((r.settings.path)||'data/settings.json')+
    '；数据目录：'+(((r.env||{}).data_dir)||'-')+
    '；需要重启才能生效的项：'+(restartItems.length?restartItems.join('、'):'无');
}
async function saveSystemSettings(){
  const body={app_name:$('set-app-name').value.trim(),log_level:$('set-log-level').value,
              session_refresh_interval:parseInt($('set-refresh').value||'0',10),
              auto_login_on_start:($('set-auto-login').value==='true')};
  const r=await post('/admin/api/system-settings',body);
  show($('sys-msg'), r.message||(r.ok?'已保存':'失败：'+(r.error||'')), r.ok?'ok':'err');
  if(r.ok){ if(r.restart_required&&r.restart_required.length){show($('sys-msg'),'已保存；'+r.restart_required.join('、')+' 需要重启容器后生效（可点下方「重启容器」）','ok');} loadSystemSettings(); }
}
function overlayShow(text){$('overlay-text').textContent=text;$('overlay').classList.add('show');}
function overlayHide(){$('overlay').classList.remove('show');}
async function restartContainer(){
  if(!confirm('确定重启容器？约 5~15 秒后恢复，期间接口不可用。'))return;
  show($('restart-msg'),'正在通知容器重启…','');
  await post('/admin/api/restart',{});
  overlayShow('容器重启中，请稍候…');
  const start=Date.now();
  const timer=setInterval(async()=>{
    try{const r=await fetch('/healthz',{cache:'no-store'});if(r.ok){clearInterval(timer);overlayShow('容器已重启，即将刷新页面…');setTimeout(()=>location.reload(),900);return;}}catch(e){}
    if(Date.now()-start>120000){clearInterval(timer);overlayHide();show($('restart-msg'),'等待超时，请手动刷新页面','err');}
  },2000);
}
async function loadLogs(){const r=await api('/admin/api/logs?limit=200');$('logs').textContent=((r&&r.lines)||[]).join(String.fromCharCode(10));}
refresh().then(()=>gotoPanel((location.hash||'#overview').slice(1)));loadLogs();
function isEditing(){const a=document.activeElement;return !!a&&['INPUT','TEXTAREA','SELECT'].includes(a.tagName);}
setInterval(()=>{if(isEditing())return;refresh();if(CURRENT==='logs'&&$('auto-log').checked)loadLogs();},8000);
</script>
</body></html>"""

LOGIN_HTML = _LOGIN_TMPL.replace("__CSS__", _CSS)
DASHBOARD_HTML = _DASHBOARD_TMPL.replace("__CSS__", _CSS)
