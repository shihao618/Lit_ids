"""后台登录鉴权：HMAC 签名的 cookie（多用户版）.

* 后台登录：用户名 + 密码 → 校验 ``UserStore`` → 签发含 ``uid`` 的签名 cookie；
* 服务端签名密钥：``ADMIN_SECRET`` 环境变量优先，否则自动生成并保存到
  ``data/.gateway_secret``（权限 600），保证重启后 cookie 仍有效。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time

from fastapi import HTTPException, Request

from .config import Settings
from .users import UserStore

log = logging.getLogger(__name__)

COOKIE_NAME = "lit_admin"
DEFAULT_TTL = 8 * 3600


def _secret_file(settings: Settings) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(settings.users_file)),
                        ".gateway_secret")


def server_secret(settings: Settings) -> bytes:
    if settings.admin_secret:
        return settings.admin_secret.encode("utf-8")
    path = _secret_file(settings)
    try:
        with open(path, "r", encoding="ascii") as f:
            secret = f.read().strip()
        if secret:
            return secret.encode("ascii")
    except OSError:
        pass
    secret = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="ascii") as f:
            f.write(secret)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        log.info("已生成后台 cookie 签名密钥: %s", path)
    except OSError as e:  # noqa: BLE001
        log.warning("后台密钥保存失败（仅本次有效）: %s", e)
    return secret.encode("ascii")


def make_token(settings: Settings, user_id: str, ttl: int = DEFAULT_TTL) -> str:
    payload = {"uid": user_id, "exp": int(time.time()) + ttl}
    raw = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")).rstrip(b"=")
    sig = hmac.new(server_secret(settings), raw, hashlib.sha256).hexdigest()
    return raw.decode("ascii") + "." + sig


def parse_token(settings: Settings, token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    raw, sig = token.rsplit(".", 1)
    expect = hmac.new(server_secret(settings), raw.encode("ascii"),
                      hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except (ValueError, TypeError):
        return None
    if int(payload.get("exp", 0)) <= time.time():
        return None
    return str(payload.get("uid") or "") or None


def get_cookie_user(request: Request) -> dict | None:
    """从 cookie 解析当前后台用户（未登录返回 None）."""
    store: UserStore = request.app.state.store
    uid = parse_token(request.app.state.settings, request.cookies.get(COOKIE_NAME))
    if not uid:
        return None
    return store.get(uid)


async def require_login(request: Request) -> dict:
    """后台页面/API：必须已登录."""
    user = get_cookie_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录后台，请先访问 /admin 登录")
    return user


async def require_admin_user(request: Request) -> dict:
    """仅管理员可用."""
    user = await require_login(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user
