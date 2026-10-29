"""健康检查路由（不需要 API Key）."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request

from .. import __version__
from ..schemas import HealthOut

router = APIRouter(tags=["健康检查"])


@router.get("/healthz", response_model=HealthOut, summary="存活检查")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "version": __version__}


@router.get("/readyz", summary="就绪检查（不发起网络请求）")
def readyz(request: Request) -> dict[str, Any]:
    store = request.app.state.store
    registry = request.app.state.registry
    users = store.list()
    return {
        "status": "ok",
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "users": len(users),
        "users_with_cas": sum(1 for u in users if u.get("cas_username") and u.get("cas_password")),
        "cached_sessions": len(registry.cached_users()),
    }
