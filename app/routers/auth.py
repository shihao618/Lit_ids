"""认证/会话路由：查看当前 API Key 用户的 CAS 会话状态、主动登录/强制刷新."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..deps import get_api_user, get_session, require_api_key
from ..schemas import AuthStatusOut, Envelope, LoginIn
from ..session import SessionManager

router = APIRouter(prefix="/api/v1/auth", tags=["认证/会话"],
                   dependencies=[Depends(require_api_key)])


@router.get("/status", response_model=AuthStatusOut,
            summary="查看当前 API Key 用户的 CAS 会话状态")
def auth_status(session: SessionManager = Depends(get_session),
                user: dict = Depends(get_api_user)) -> dict[str, Any]:
    try:
        session.check_valid()          # 30 秒缓存，掉线时 logged_in=false
    except Exception:                  # noqa: BLE001 网络异常不阻塞状态接口
        pass
    return session.status()


@router.post("/login", response_model=Envelope,
             summary="登录 / 刷新当前 API Key 用户的 CAS 会话")
def auth_login(body: LoginIn | None = None,
               session: SessionManager = Depends(get_session),
               user: dict = Depends(get_api_user)) -> dict[str, Any]:
    body = body or LoginIn()
    result = session.ensure_login(force=body.force, mfa_code=body.mfa_code)
    return {"code": 0, "msg": "ok", "data": result}
