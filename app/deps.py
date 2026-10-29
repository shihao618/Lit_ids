"""FastAPI 依赖：API Key → 用户 → 该用户的 CAS 会话/业务系统."""

from __future__ import annotations

from typing import Any

from fastapi import Header, HTTPException, Request

from .registry import SessionRegistry
from .session import SessionManager
from .systems import XsfwSystem




def get_registry(request: Request) -> SessionRegistry:
    return request.app.state.registry


async def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    """校验请求头里的 X-API-Key，并解析出对应网关用户.

    每个用户都有自己独立的 API Key；接口调用使用该用户自己的统一认证会话。
    """
    store: UserStore = request.app.state.store
    user = store.find_by_api_key(x_api_key)
    if not user:
        raise HTTPException(status_code=401, detail="无效的 API Key（请求头 X-API-Key）")
    request.state.api_user = user
    return user


def get_api_user(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "api_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="无效的 API Key（请求头 X-API-Key）")
    return user


def get_session(request: Request) -> SessionManager:
    """当前 API Key 对应用户的 CAS 会话."""
    user = get_api_user(request)
    return request.app.state.registry.session(user["id"])


def get_xsfw(request: Request) -> XsfwSystem:
    """当前 API Key 对应用户的 xsfw 实例（各自独立角色/会话）."""
    user = get_api_user(request)
    return request.app.state.registry.system(user["id"])
