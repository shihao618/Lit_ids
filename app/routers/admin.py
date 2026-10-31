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
from ..deps import get_admin_lljw, get_admin_xsfw, get_registry
from ..logbuf import log_buffer
from ..restart import schedule_restart
from ..schemas import (
    AdminCasIn, AdminLoginIn, AdminMfaIn, AdminPasswordIn, AdminRoleIn,
    CasSettingsIn, SelfPasswordIn, SystemSettingsIn, UserCreateIn,
)
from ..session import SessionError
from ..settings_store import DEFAULTS, EDITABLE, apply_log_level
from ..systems import SystemError
from ..users import UserStore

log = logging.getLogger(__name__)

router = APIRouter(tags=["后台界面"])


# ============================================================ 页面
def _app_name(request: Request) -> str:
    runtime = getattr(request.app.state, "runtime_settings", None)
    name = runtime.get("app_name") if runtime is not None else None
    return name or DEFAULTS["app_name"]


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
        html = html.replace("__APP_NAME__", html_lib.escape(_app_name(request)))
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
    try:
        session.check_valid()          # 30 秒缓存，CAS 掉线时界面会显示「已掉线」
    except Exception:                  # noqa: BLE001 网络异常不阻塞状态接口
        pass
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


@api.post("/session/check", summary="检测我的统一认证会话是否仍然有效")
def api_session_check(request: Request, user: dict = Depends(require_login)) -> dict[str, Any]:
    session = get_registry(request).session(user["id"])
    valid = session.check_valid(force=True)
    message = ("统一认证会话有效" if valid
               else "统一认证已掉线（TGC 失效），请到「🧪 登录调试」重新登录")
    log.info("用户 %s 检测会话: %s", user.get("username"), valid)
    return {"ok": True, "valid": valid, "message": message,
            "session": session.status()}


@api.get("/logs", summary="最近运行日志")
def api_logs(limit: int = Query(200, ge=1, le=500)) -> dict[str, Any]:
    return {"lines": log_buffer.get(limit)}


# ------------------------------------------------------------ 接口自测（智能查寝）
@api.get("/test/check-records", summary="自测：智能查寝-查寝记录（自有参数/易读返回）")
def test_check_records(
    class_code: str = Query("", alias="classCode"),
    check_status: str = Query("", alias="checkStatus"),
    check_status_type: str = Query("", alias="checkStatusType"),
    system=Depends(get_admin_xsfw),
) -> dict[str, Any]:
    try:
        data = system.check_records(class_code=class_code, check_status=check_status,
                                    check_status_type=check_status_type)
        return {"ok": True, "data": data}
    except (SystemError, SessionError) as e:
        return _err(e)


@api.get("/test/schedule", summary="自测：教务系统-课表查询（自有参数/易读返回）")
def test_schedule(
    semester: str = Query("", alias="semester"),
    week: str = Query("", alias="week"),
    query: str = Query("week", alias="query"),
    date_text: str = Query("", alias="date"),
    system=Depends(get_admin_lljw),
) -> dict[str, Any]:
    try:
        data = system.schedule(semester=semester, week=week or None,
                               query=query, date_text=date_text)
        return {"ok": True, "data": data}
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
:root{
  --green:#07c160;--green-dark:#06ad56;--green-soft:#e8f8ef;--green-line:#c8eedb;
  --bg:#f5f7fa;--card:#fff;--text:#1f2937;--text2:#4b5563;--muted:#8b95a5;
  --border:#e5e7eb;--border2:#d1d5db;--danger:#e5484d;--danger-soft:#fdecec;
  --warning:#d97706;--warning-soft:#fff7e6;--info-soft:#eff6ff;
  --radius:10px;--radius-lg:14px;--shadow:0 1px 3px rgba(15,23,42,.06);
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.65 var(--sans);-webkit-text-size-adjust:100%}
a{color:inherit;text-decoration:none}
.skip-link{position:fixed;top:-60px;left:12px;z-index:200;padding:8px 14px;border-radius:8px;background:var(--green);color:#fff;font-size:13px}
.skip-link:focus{top:10px}
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius-lg);box-shadow:var(--shadow);padding:20px 22px;min-width:0}
.card h2{font-size:15px;font-weight:700;margin:0 0 14px;padding-bottom:10px;border-bottom:1px solid var(--border);color:var(--text)}
.card-subtitle{margin:4px 0 0;color:var(--text2);font-size:13px}
label,.form-label{display:block;font-size:13px;color:var(--text2);margin:10px 0 5px;font-weight:500}
input,select,textarea,.input{width:100%;padding:8px 10px;border:1px solid var(--border2);border-radius:8px;font:inherit;background:#fff;color:var(--text)}
input:focus,select:focus,textarea:focus,.input:focus{outline:2px solid var(--green-line);border-color:var(--green);outline-offset:0}
textarea{min-height:70px;font-family:var(--mono);font-size:12.5px}
button,.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:8px 14px;border:1px solid transparent;border-radius:8px;background:var(--green);color:#fff;font:inherit;font-size:13px;font-weight:600;cursor:pointer;margin:10px 8px 0 0;white-space:nowrap}
button:hover,.btn:hover{background:var(--green-dark)}
button.gray,.btn-secondary{border-color:var(--border);background:#fff;color:var(--text2);font-weight:500}
button.gray:hover,.btn-secondary:hover{background:#f9fafb;color:var(--text)}
button.green,.btn-primary{background:var(--green);color:#fff}
button.red,.btn-danger{background:var(--danger);color:#fff}
button.red:hover,.btn-danger:hover{background:#c23b40}
.btn-ghost{border-color:var(--border);background:#fff;color:var(--text2);font-weight:500}
.btn-ghost:hover{background:#f9fafb;color:var(--text)}
.btn-sm{padding:6px 10px;font-size:12px;margin:0}
.btn-block{width:100%;margin:12px 0 0}
button:disabled{opacity:.55;cursor:not-allowed}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.kv{display:grid;grid-template-columns:118px 1fr;row-gap:8px;font-size:13px;align-items:baseline}
.kv b{color:var(--text2);font-weight:600}
pre{background:#0f172a;color:#e2e8f0;padding:12px 14px;border-radius:10px;overflow:auto;max-height:360px;font:12.5px/1.6 var(--mono);margin:10px 0 0;white-space:pre-wrap;word-break:break-word}
#logs{max-height:340px}
.msg{margin-top:10px;padding:8px 12px;border-radius:8px;background:var(--info-soft);color:#1d4ed8;font-size:13px;display:none}
.msg.show{display:block}
.msg.err{background:var(--danger-soft);color:#b91c1c}
.msg.ok{background:var(--green-soft);color:#047a3d}
.ok-tag{color:#047a3d;font-weight:600}
.bad-tag{color:#b91c1c;font-weight:600}
.table-wrap{width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch;margin-top:10px;border:1px solid var(--border);border-radius:10px}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin:0}
th,td{padding:9px 11px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}
th{background:#f9fafb;color:var(--muted);font-weight:600;white-space:nowrap}
tr:last-child td{border-bottom:none}
input[readonly]{background:#f3f4f6;color:var(--text2)}
.note{font-size:12px;color:var(--muted);margin-top:8px}
.err{color:#b91c1c;background:var(--danger-soft);padding:8px 10px;border-radius:8px;font-size:13px;margin-bottom:12px}
.badge-restart{background:var(--warning-soft);color:#8a5a00;border-radius:4px;padding:0 6px;font-size:11.5px;margin-left:6px;font-weight:400}
#overlay{display:none;position:fixed;inset:0;background:rgba(15,23,42,.74);color:#fff;z-index:99;align-items:center;justify-content:center;flex-direction:column;font-size:16px;gap:18px}
#overlay.show{display:flex}
.spinner{width:40px;height:40px;border:4px solid rgba(255,255,255,.25);border-top-color:#fff;border-radius:50%;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.login-view{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}
.login-card{width:min(400px,100%);padding:30px 28px;border-radius:16px;box-shadow:0 18px 40px rgba(15,23,42,.10)}
.login-icon{font-size:26px;margin-bottom:8px}
.login-title{font-size:19px;font-weight:700;margin:0 0 6px}
.login-hint{font-size:12px;color:var(--muted);margin-top:14px;line-height:1.6}
.form-field{margin-bottom:12px}
.form-hint{font-size:12px;color:var(--muted);margin-top:8px}
@media(max-width:640px){
 .login-view{padding:16px}
 .login-card{padding:22px 18px}
 .card{padding:16px 14px;border-radius:12px}
 .kv{grid-template-columns:92px 1fr;font-size:12.5px;row-gap:6px}
 .row{gap:8px}
 .row input,.row select{flex:1 1 140px;width:auto!important}
 .row input[type=checkbox]{flex:0 0 auto;width:auto!important}
 .row button{flex:1 1 auto}
 pre{font-size:11.5px}
}
"""

_LOGIN_TMPL = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>__APP_NAME__ · 后台登录</title>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#07C160">
<style>__CSS__</style></head>
<body class="page-admin">
<div class="login-view">
  <div class="card login-card">
    <div class="login-icon" aria-hidden="true">🔐</div>
    <h1 class="login-title">__APP_NAME__ · 后台</h1>
    <p class="card-subtitle">多用户版：每个用户登录后可配置自己的统一认证账号，并用各自的 API Key 调用接口。</p>
    <!--ERROR-->
    <form method="post" action="/admin/login" class="form">
      <div class="form-field"><label class="form-label" for="username">用户名</label>
        <input class="input" id="username" type="text" name="username" autofocus autocomplete="username" placeholder="后台用户名"></div>
      <div class="form-field"><label class="form-label" for="password">密码</label>
        <input class="input" id="password" type="password" name="password" autocomplete="current-password" placeholder="后台密码"></div>
      <button class="btn btn-primary btn-block" type="submit">登 录</button>
    </form>
    <p class="login-hint">默认管理员由环境变量 ADMIN_USERNAME / ADMIN_PASSWORD 引导创建。</p>
  </div>
</div>
</body></html>"""

_DASHBOARD_TMPL = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>__APP_NAME__ · 后台</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>__CSS__</style>
<style>
.admin-shell{min-height:100vh}
.admin-sidebar{position:fixed;top:0;left:0;bottom:0;z-index:40;width:232px;display:flex;flex-direction:column;padding:14px 10px;background:#fff;border-right:1px solid var(--border);overflow-y:auto}
.sidebar-brand{display:flex;flex-direction:column;align-items:flex-start;gap:4px;padding:2px 6px 12px}
.brand{display:inline-flex;align-items:center;gap:8px;min-width:0;color:var(--text);font-weight:700}
.brand-icon{width:28px;height:28px;border-radius:8px;flex:none}
.brand-name{font-size:16px;font-weight:700;letter-spacing:-.01em;white-space:nowrap}
.brand-tag{display:inline-block;padding:1px 9px;border-radius:999px;background:var(--green-soft);color:#047a3d;font-size:11px;font-weight:600;margin-left:36px}
.admin-menu{display:flex;flex-direction:column;gap:2px;margin-top:8px}
.menu-item{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:8px;color:var(--text);font-size:13.5px;cursor:pointer;white-space:nowrap}
.menu-item:hover{background:#f9fafb}
.menu-item.active{background:var(--green-soft);color:#047a3d;font-weight:600}
.menu-icon{width:18px;text-align:center;flex:none}
.sidebar-footer{margin-top:auto;padding-top:12px;border-top:1px solid var(--border)}
.sidebar-link{display:flex;align-items:center;gap:9px;padding:9px 12px;border-radius:8px;color:var(--text2);font-size:13px}
.sidebar-link:hover{background:#f9fafb;color:var(--text)}
.sidebar-overlay{position:fixed;inset:0;z-index:110;background:rgba(15,23,42,.45)}
.sidebar-overlay[hidden]{display:none!important}
.admin-main{margin-left:232px;min-height:100vh;display:flex;flex-direction:column;min-width:0}
.admin-topbar-bar{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:12px;padding:13px 24px;background:rgba(255,255,255,.94);border-bottom:1px solid var(--border);backdrop-filter:blur(6px)}
.admin-menu-toggle{display:none;flex:none;align-items:center;justify-content:center;width:36px;height:36px;border:1px solid var(--border);border-radius:10px;background:#fff;color:var(--text);font-size:17px;line-height:1;cursor:pointer;margin:0;padding:0}
.admin-topbar-title{flex:1;min-width:0}
.page-title{font-size:20px;font-weight:700;margin:0;line-height:1.3}
.page-subtitle{font-size:12px;color:var(--muted);margin:2px 0 0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.topbar-actions{display:flex;align-items:center;gap:8px;flex:none}
.topbar-user{font-size:13px;color:var(--text2);margin-right:4px}
.admin-content{flex:1;width:100%;max-width:1240px;margin:0 auto;padding:20px 24px 40px}
.panel{display:none;flex-direction:column;gap:16px}
.panel.active{display:flex}
.card-row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.admin-footer{margin-top:28px;padding-top:16px;border-top:1px solid var(--border);color:var(--muted);font-size:12px;text-align:center}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px;margin-bottom:4px}
.stat-card{display:flex;flex-direction:column;gap:2px;padding:14px 16px;border:1px solid var(--border);border-radius:12px;background:#fff;box-shadow:var(--shadow);min-width:0}
.stat-head{display:flex;align-items:center;gap:8px;color:var(--text2);font-size:12px}
.stat-icon{font-size:15px}
.stat-value{font-size:20px;font-weight:700;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
@media(max-width:900px){
 .admin-main{margin-left:0}
 .admin-sidebar{width:min(300px,85vw);z-index:120;padding:14px 12px;transform:translateX(-100%);visibility:hidden;transition:transform .2s ease,visibility .2s ease;box-shadow:none}
 body.sidebar-open .admin-sidebar{transform:none;visibility:visible;box-shadow:0 18px 40px rgba(15,23,42,.2)}
 .admin-menu-toggle{display:inline-flex}
 .admin-topbar-bar{padding:10px 14px;gap:8px}
 .page-title{font-size:17px}
 .page-subtitle{display:none}
 .topbar-user{display:none}
 .topbar-actions .topbar-links{display:none}
 .admin-content{padding:14px 12px 28px}
 .card-row{grid-template-columns:1fr}
 .stats-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media(max-width:640px){
 .admin-content{padding:12px 10px 24px}
 .card{padding:14px 12px}
 .card h2{font-size:14px}
 .stats-grid{gap:8px}
 .stat-card{padding:12px}
 .stat-value{font-size:18px}
 #users-table{min-width:760px}
 #users-table th,#users-table td{white-space:nowrap}
 .table-wrap{overflow-x:auto}
 pre{font-size:11.5px;max-height:340px}
 #logs{max-height:360px}
}
</style></head>
<body class="page-admin">
<a class="skip-link" href="#main">跳到主要内容</a>
<div class="admin-shell">
  <aside id="admin-sidebar" class="admin-sidebar" aria-label="后台菜单">
    <div class="sidebar-brand">
      <a class="brand" href="/" aria-label="__APP_NAME__ 首页">
        <svg class="brand-icon" viewBox="0 0 64 64" aria-hidden="true" focusable="false">
          <rect width="64" height="64" rx="14" fill="#07C160"></rect>
          <path d="M22 16h20a10 10 0 0 1 10 10v8a10 10 0 0 1-10 10H34l-9 8v-8h-3a10 10 0 0 1-10-10v-8a10 10 0 0 1 10-10z" fill="#ffffff"></path>
          <circle cx="27" cy="30" r="3" fill="#07C160"></circle>
          <circle cx="38" cy="30" r="3" fill="#07C160"></circle>
        </svg>
        <span class="brand-name">LIT Gateway</span>
      </a>
      <span class="brand-tag">管理后台</span>
    </div>
    <nav id="admin-menu" class="admin-menu" aria-label="后台菜单">
      <a class="menu-item active" data-panel="overview" href="#overview"><span class="menu-icon">📊</span><span class="menu-label">概览</span></a>
      <a class="menu-item" data-panel="account" href="#account"><span class="menu-icon">👤</span><span class="menu-label">我的账号</span></a>
      <a class="menu-item" data-panel="cas" href="#cas"><span class="menu-icon">🔐</span><span class="menu-label">统一认证</span></a>
      <a class="menu-item" data-panel="login" href="#login"><span class="menu-icon">🧪</span><span class="menu-label">登录调试</span></a>
      <a class="menu-item" data-panel="test" href="#test"><span class="menu-icon">🔧</span><span class="menu-label">接口自测</span></a>
      <a class="menu-item admin-only" data-panel="users" href="#users" style="display:none"><span class="menu-icon">👥</span><span class="menu-label">用户管理</span></a>
      <a class="menu-item admin-only" data-panel="settings" href="#settings" style="display:none"><span class="menu-icon">⚙️</span><span class="menu-label">系统设置</span></a>
      <a class="menu-item" data-panel="logs" href="#logs"><span class="menu-icon">📜</span><span class="menu-label">运行日志</span></a>
    </nav>
    <div class="sidebar-footer">
      <a class="sidebar-link" href="/" target="_blank"><span aria-hidden="true">🌐</span> 接口文档</a>
      <a class="sidebar-link" href="/docs" target="_blank"><span aria-hidden="true">📘</span> Swagger</a>
      <a class="sidebar-link" href="/admin/logout"><span aria-hidden="true">🚪</span> 退出后台</a>
    </div>
  </aside>
  <div id="sidebar-overlay" class="sidebar-overlay" hidden></div>
  <div class="admin-main">
    <header class="admin-topbar-bar">
      <button id="btn-sidebar-toggle" class="admin-menu-toggle" type="button" aria-label="打开菜单" aria-controls="admin-sidebar" aria-expanded="false">☰</button>
      <div class="admin-topbar-title">
        <h1 id="view-title" class="page-title">概览</h1>
        <p id="view-subtitle" class="page-subtitle">当前用户与会话状态</p>
      </div>
      <div class="topbar-actions">
        <span id="who" class="topbar-user"></span>
        <button type="button" class="btn btn-secondary btn-sm" onclick="refresh()">🔄 刷新</button>
        <a class="btn btn-ghost btn-sm topbar-links" href="/" target="_blank">接口文档</a>
        <a class="btn btn-ghost btn-sm topbar-links" href="/docs" target="_blank">Swagger</a>
        <a class="btn btn-ghost btn-sm" href="/admin/logout">退出登录</a>
      </div>
    </header>
    <main id="main" class="admin-content">
    <!-- 概览 -->
    <section class="panel active" id="panel-overview">
      <div class="stats-grid">
        <div class="stat-card"><div class="stat-head"><span class="stat-icon">👤</span>后台用户</div><div class="stat-value" id="stat-user">-</div></div>
        <div class="stat-card"><div class="stat-head"><span class="stat-icon">🔐</span>统一认证会话</div><div class="stat-value" id="stat-session">-</div></div>
        <div class="stat-card"><div class="stat-head"><span class="stat-icon">👥</span>用户数</div><div class="stat-value" id="stat-users">-</div></div>
        <div class="stat-card"><div class="stat-head"><span class="stat-icon">✅</span>已配置统一认证</div><div class="stat-value" id="stat-cas">-</div></div>
      </div>
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
          <b>会话检测</b><span id="s-valid">-</span>
          <b>登录时间</b><span id="s-time">-</span>
          <b>TGC</b><span id="s-tgc" style="word-break:break-all">-</span>
          <b>最近错误</b><span id="s-err">-</span>
        </div>
        <div class="row">
          <button type="button" onclick="startLogin(false)">登录（复用有效会话）</button>
          <button type="button" class="gray" onclick="startLogin(true)">强制重新登录</button>
          <button type="button" class="gray" onclick="checkSession()">检测会话状态</button>
          <button type="button" class="red" onclick="clearSession()">清除我的 CAS 会话</button>
        </div>
        <div id="overview-msg" class="msg"></div>
      </div>
      <div class="card" id="overview-admin" style="display:none">
        <h2>系统概况（管理员）</h2>
        <div class="kv"><b>用户数</b><span id="ov-users">-</span><b>已配置统一认证</b><span id="ov-users-cas">-</span></div>
        <div class="row"><button type="button" class="gray" onclick="gotoPanel('users')">用户管理</button><button type="button" class="gray" onclick="gotoPanel('settings')">系统设置</button></div>
      </div>
    </section>

    <!-- 我的账号 -->
    <section class="panel" id="panel-account">
      <div class="card">
        <h2>我的 API Key</h2>
        <div class="row"><input id="api-key" readonly><button type="button" class="gray" onclick="copyKey()">复制</button><button type="button" class="gray" onclick="regenKey()">重新生成</button></div>
        <div id="key-msg" class="msg"></div>
        <div class="note">调用接口时放在请求头 X-API-Key；重新生成后旧的 Key 立即失效。</div>
      </div>
      <div class="card">
        <h2>修改后台密码</h2>
        <label>当前密码</label><input id="old-pwd" type="password" placeholder="当前后台密码">
        <label>新密码（至少 6 位）</label><input id="new-pwd" type="password" placeholder="新后台密码">
        <div class="row"><button type="button" class="green" onclick="changePassword()">修改密码</button></div>
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
          <button type="button" class="green" onclick="saveCas()">保存账号</button>
          <button type="button" class="red" onclick="clearSession()">清除我的 CAS 会话</button>
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
          <button type="button" onclick="startLogin(false)">登录（复用有效会话）</button>
          <button type="button" class="gray" onclick="startLogin(true)">强制重新登录</button>
          <span class="note" id="login-stage"></span>
        </div>
        <div id="login-msg" class="msg"></div>
        <div id="mfa-area" style="display:none;border:1px dashed #f0b429;background:#fffbeb;padding:12px;border-radius:8px;margin-top:12px">
          <b>需要企业微信验证码</b>（请到该账号绑定的企业微信查看「统一身份认证」验证码）
          <div class="row" style="margin-top:8px">
            <input id="mfa-code" placeholder="请输入验证码" style="max-width:220px">
            <button type="button" class="green" onclick="submitMfa()">提交验证码</button>
            <button type="button" class="gray" onclick="resendMfa()">重新发送</button>
          </div>
        </div>
        <div class="note" id="captcha-debug"></div>
      </div>
    </section>

    <!-- 接口自测（智能查寝） -->
    <section class="panel" id="panel-test">
      <div class="card">
        <h2>接口自测（智能查寝 · 使用我自己的会话）</h2>
        <div class="row">
          <span>参数：</span>
          <input id="test-class-code" value="" placeholder="classCode 班级代码" style="width:170px">
          <select id="test-check-status" style="width:140px">
            <option value="">全部状态</option>
            <option value="not_returned">未归寝</option>
            <option value="returned">已归寝</option>
            <option value="leave">离校请假</option>
          </select>
          <select id="test-check-status-type" style="width:150px">
            <option value="">全部类型</option>
            <option value="unreachable">未联系上</option>
            <option value="reported">已报备</option>
            <option value="normal">正常归寝</option>
            <option value="late">晚归</option>
          </select>
          <button type="button" onclick="testCheckRecords()">查询</button>
        </div>
        <pre id="check-records-out">（接口自测结果）</pre>
      </div>
      <div class="card">
        <h2>接口自测（教务课表 · 使用我自己的会话）</h2>
        <div class="row">
          <span>参数：</span>
          <input id="test-semester" value="" placeholder="semester（空=当前）" style="width:160px">
          <input id="test-week" value="" placeholder="week（空=本周/all）" style="width:170px">
          <select id="test-query" style="width:130px">
            <option value="week">本周/指定周</option>
            <option value="today">今天</option>
            <option value="morning">上午</option>
            <option value="afternoon">下午</option>
            <option value="evening">晚上</option>
            <option value="day">指定日期</option>
            <option value="next">最近一节</option>
          </select>
          <input id="test-schedule-date" value="" placeholder="date（YYYY-MM-DD）" style="width:170px">
          <button type="button" onclick="testSchedule()">查询</button>
        </div>
        <pre id="schedule-out">（接口自测结果）</pre>
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
          <button type="button" class="green" onclick="createUser()">创建用户</button>
        </div>
        <div id="users-msg" class="msg"></div>
        <div class="table-wrap"><table id="users-table">
          <thead><tr><th>用户名</th><th>角色</th><th>统一认证账号</th><th>API Key</th><th>创建时间</th><th>操作</th></tr></thead>
          <tbody id="users-tbody"></tbody>
        </table></div>
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
          <button type="button" class="green" onclick="saveSystemSettings()">保存设置</button>
          <button type="button" class="gray" onclick="loadSystemSettings()">重新读取</button>
        </div>
        <div id="sys-msg" class="msg"></div>
        <div class="note" id="sys-note">-</div>
      </div>
      <div class="card">
        <h2>重启容器</h2>
        <p class="note" style="margin-top:0">「启动时自动登录」等启动项需要重启容器后生效。点击后容器会在约 5~15 秒内自动重启并恢复，页面稍后会自动刷新。</p>
        <button type="button" class="red" onclick="restartContainer()">🔄 重启容器</button>
        <div id="restart-msg" class="msg"></div>
      </div>
    </section>

    <!-- 运行日志 -->
    <section class="panel" id="panel-logs">
      <div class="card">
        <h2>运行日志</h2>
        <div class="row">
          <button type="button" class="gray" onclick="loadLogs()">刷新日志</button>
          <label style="display:inline;margin:0"><input type="checkbox" id="auto-log" checked style="width:auto"> 自动刷新</label>
        </div>
        <pre id="logs">（日志）</pre>
      </div>
    </section>
      <footer class="admin-footer">LIT Gateway · 管理后台 · 当前视图每 8 秒自动刷新（页面隐藏时暂停） · <a href="/" target="_blank">接口文档</a> · <a href="/docs" target="_blank">Swagger</a></footer>
    </main>
  </div>
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

const PANEL_META={
  overview:['概览','当前用户与会话状态'],
  account:['我的账号','API Key 与后台密码'],
  cas:['我的统一认证账号','账号 / 密码 / 设备指纹'],
  login:['登录调试','CAS 登录与企业微信验证码'],
  test:['接口自测','使用当前登录用户自己的会话'],
  users:['用户管理','创建 / 重置 / 删除网关用户'],
  settings:['系统设置','运行参数修改与容器重启'],
  logs:['运行日志','最近运行日志']
};
function openMenu(){
  document.body.classList.add('sidebar-open');
  const overlay=$('sidebar-overlay');
  if(overlay) overlay.hidden=false;
  $('btn-sidebar-toggle')?.setAttribute('aria-expanded','true');
}
function closeMenu(){
  document.body.classList.remove('sidebar-open');
  const overlay=$('sidebar-overlay');
  if(overlay) overlay.hidden=true;
  $('btn-sidebar-toggle')?.setAttribute('aria-expanded','false');
}
$('btn-sidebar-toggle')?.addEventListener('click',()=>{
  document.body.classList.contains('sidebar-open')?closeMenu():openMenu();
});
$('sidebar-overlay')?.addEventListener('click',closeMenu);
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeMenu();});
window.addEventListener('resize',()=>{if(window.innerWidth>900)closeMenu();});

function gotoPanel(name){
  if(!name) name='overview';
  const panel=$('panel-'+name);
  if(!panel){name='overview';}
  const item=document.querySelector('.menu-item[data-panel="'+name+'"]');
  if(item&&item.classList.contains('admin-only')&&!IS_ADMIN){name='overview';}
  CURRENT=name;
  const meta=PANEL_META[name]||PANEL_META.overview;
  $('view-title').textContent=meta[0];
  $('view-subtitle').textContent=meta[1];
  document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('active',p.id==='panel-'+name));
  document.querySelectorAll('.menu-item').forEach(a=>a.classList.toggle('active',a.dataset.panel===name));
  history.replaceState(null,'','#'+name);
  if(name==='logs') loadLogs();
  if(name==='settings'&&IS_ADMIN) loadSystemSettings();
  if(name==='users'&&IS_ADMIN) refresh();
  closeMenu();
}
document.querySelectorAll('.menu-item').forEach(a=>{a.addEventListener('click',()=>gotoPanel(a.dataset.panel));});
window.addEventListener('hashchange',()=>gotoPanel((location.hash||'#overview').slice(1)));
['cas-username','cas-password','cas-fingerprint'].forEach(id=>{const el=document.getElementById(id);if(el)el.addEventListener('input',()=>{casDirty=true;});});

async function refresh(){
  const st=await api('/admin/api/state');
  if(!st) return;
  IS_ADMIN=!!st.is_admin;
  $('who').textContent=st.user.username||'';
  const _u=st.user||{}, _s=st.session||{};
  $('stat-user').textContent=fmt(_u.username||'-');
  $('stat-session').textContent=(_s.logged_in?'已登录':'未登录');
  $('stat-users').textContent=(st.users?st.users.length:1);
  $('stat-cas').textContent=(st.users?st.users.filter(u=>u.cas_username).length:(_u.cas_username?1:0));
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
  if(s.session_expired){
    $('s-status').innerHTML='<span class="bad-tag">已掉线（请重新登录）</span>';
  }else{
    $('s-status').innerHTML=s.logged_in?'<span class="ok-tag">已登录</span>':'<span class="bad-tag">未登录</span>';
  }
  $('s-valid').textContent=s.valid_checked_at
    ?(s.session_expired?'已失效':(s.tgc_valid===true?'有效':'-'))+'（'+s.valid_checked_at+'）'
    :'-';
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
  document.querySelectorAll('.admin-only').forEach(el=>{el.style.display=IS_ADMIN?'flex':'none';});
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
  $('captcha-debug').innerHTML='<div style="margin-top:12px"><b>滑块验证码识别过程</b><div class="table-wrap">'+h+'</div></div>';
}
function renderUsers(users){
  let h='';
  for(const u of users){
    h+='<tr><td>'+esc(u.username)+(u.role==='admin'?' <span class="ok-tag">管理员</span>':'')+'</td><td>'+esc(u.role)+'</td><td>'+fmt(u.cas_username)+'</td><td><code>'+esc(u.api_key||'')+'</code></td><td>'+fmt(u.created_at)+
       '</td><td><button type="button" class="gray" data-id="'+esc(u.id)+'" onclick="resetUserPwd(this.dataset.id)">重置密码</button>'+
       '<button type="button" class="gray" data-id="'+esc(u.id)+'" onclick="resetUserKey(this.dataset.id)">重置Key</button>'+
       '<button type="button" class="red" data-id="'+esc(u.id)+'" onclick="deleteUser(this.dataset.id)">删除</button></td></tr>';
  }
  $('users-tbody').innerHTML=h||'<tr><td colspan="6">暂无用户</td></tr>';
}

function copyTextFallback(text){
  const ta=document.createElement('textarea');
  ta.value=text; ta.setAttribute('readonly','');
  ta.style.position='fixed'; ta.style.top='-1000px'; ta.style.left='-1000px'; ta.style.opacity='0';
  document.body.appendChild(ta);
  ta.focus(); ta.select();
  let ok=false;
  try{ok=document.execCommand('copy');}catch(e){ok=false;}
  document.body.removeChild(ta);
  return ok;
}
async function copyText(text){
  let ok=false;
  if(window.isSecureContext && navigator.clipboard && navigator.clipboard.writeText){
    try{await navigator.clipboard.writeText(text);ok=true;}catch(e){ok=false;}
  }
  if(!ok) ok=copyTextFallback(text);
  return ok;
}
async function copyKey(){
  const text=$('api-key').value;
  if(!text){show($('key-msg'),'当前没有可复制的 API Key','err');return;}
  const ok=await copyText(text);
  if(ok){show($('key-msg'),'API Key 已复制到剪贴板','ok');return;}
  const el=$('api-key'); el.focus(); el.select();
  show($('key-msg'),'浏览器不允许自动复制（HTTP 非安全环境）：已选中内容，请按 Ctrl+C / Cmd+C 手动复制','err');
}
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
async function checkSession(){
  show($('overview-msg'),'正在检测统一认证会话（可能需要几秒）…','');
  const r=await post('/admin/api/session/check',{});
  show($('overview-msg'),r.message||(r.valid?'会话有效':'会话已失效'),r.valid?'ok':'err');
  refresh();
}
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
async function testSchedule(){
  const q=new URLSearchParams({
    semester:$('test-semester').value.trim(),
    week:$('test-week').value.trim(),
    query:$('test-query').value,
    date:$('test-schedule-date').value.trim()
  });
  const r=await api('/admin/api/test/schedule?'+q);
  $('schedule-out').textContent=JSON.stringify(r,null,2);
}
async function testCheckRecords(){
  const q=new URLSearchParams({
    classCode:$('test-class-code').value.trim(),
    checkStatus:$('test-check-status').value,
    checkStatusType:$('test-check-status-type').value
  });
  const r=await api('/admin/api/test/check-records?'+q);
  $('check-records-out').textContent=JSON.stringify(r,null,2);
}
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
