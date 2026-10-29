"""网关用户存储：多用户、密码哈希、每人独立 API Key、每人独立统一认证账号.

数据文件默认 ``data/users.json``（权限 600），结构::

    {"users": {
        "<uuid>": {
            "id": "<uuid>",
            "username": "admin",                  # 后台登录名
            "role": "admin" | "user",
            "password": {"algo": "pbkdf2_sha256", "iter": N, "salt": hex, "hash": hex},
            "api_key": "lit_xxx",                  # 调用接口用的 Key
            "cas_username": "B2607xxxx",           # 统一认证账号（每个用户各自的）
            "cas_password": "***",
            "cas_fingerprint": "***",
            "created_at": "2026-10-29 21:00:00"
        }
    }}

注意：该文件包含统一认证账号密码，必须妥善保管（已设置 600 权限）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import uuid

log = logging.getLogger(__name__)

PBKDF2_ITER = 120_000
ROLES = ("admin", "user")


def _hash_password(password: str, salt: bytes | None = None) -> dict:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITER)
    return {"algo": "pbkdf2_sha256", "iter": PBKDF2_ITER,
            "salt": salt.hex(), "hash": dk.hex()}


def verify_password(password: str, stored: dict | None) -> bool:
    if not stored or not password:
        return False
    try:
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(stored["salt"]), int(stored["iter"]))
        return hmac.compare_digest(dk.hex(), str(stored["hash"]))
    except (KeyError, ValueError, TypeError):
        return False


def new_api_key() -> str:
    return "lit_" + secrets.token_hex(24)


class UserCredentials:
    """给 SessionManager 用的账号视图（用户名/密码/指纹）."""

    def __init__(self, store: "UserStore", user_id: str):
        self._store = store
        self.user_id = user_id

    def _user(self) -> dict:
        user = self._store.get(self.user_id)
        if not user:
            raise RuntimeError("用户不存在: %s" % self.user_id)
        return user

    @property
    def username(self) -> str:
        return self._user().get("cas_username") or ""

    @property
    def password(self) -> str:
        return self._user().get("cas_password") or ""

    @property
    def fingerprint(self) -> str | None:
        return self._user().get("cas_fingerprint") or None

    def configured(self) -> bool:
        return bool(self.username and self.password)



class UserStore:
    """多用户存储（线程安全，JSON 落盘）."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._users: dict[str, dict] = {}
        self._load()

    # ------------------------------------------------------------------ 读写
    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            log.info("用户文件不存在，将在引导时创建: %s", self.path)
            return
        users = data.get("users") if isinstance(data, dict) else None
        if isinstance(users, dict):
            self._users = users
            log.info("已加载 %d 个网关用户: %s", len(users),
                     [u.get("username") for u in users.values()])

    def _save(self) -> None:
        payload = {"users": self._users}
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)
        except OSError as e:  # noqa: BLE001
            log.warning("用户文件保存失败: %s", e)

    # ------------------------------------------------------------------ 查询
    def get(self, user_id: str) -> dict | None:
        with self._lock:
            user = self._users.get(user_id)
            return dict(user) if user else None

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(u) for u in self._users.values()]

    def find_by_username(self, username: str) -> dict | None:
        username = (username or "").strip().lower()
        with self._lock:
            for user in self._users.values():
                if str(user.get("username", "")).lower() == username:
                    return dict(user)
        return None

    def find_by_api_key(self, api_key: str | None) -> dict | None:
        if not api_key:
            return None
        with self._lock:
            for user in self._users.values():
                if user.get("api_key") and hmac.compare_digest(
                        str(user["api_key"]), str(api_key)):
                    return dict(user)
        return None

    def check_password(self, username: str, password: str) -> dict | None:
        user = self.find_by_username(username)
        if user and verify_password(password, user.get("password")):
            return user
        return None

    # ------------------------------------------------------------------ 增删改
    def create(self, username: str, password: str, role: str = "user",
               cas_username: str = "", cas_password: str = "",
               cas_fingerprint: str = "", api_key: str | None = None) -> dict:
        username = (username or "").strip()
        if not username:
            raise ValueError("用户名不能为空")
        if not password or len(password) < 6:
            raise ValueError("密码至少 6 位")
        if role not in ROLES:
            raise ValueError("角色只能是 admin/user")
        with self._lock:
            if self.find_by_username(username):
                raise ValueError("用户名已存在: %s" % username)
            uid = uuid.uuid4().hex
            user = {
                "id": uid,
                "username": username,
                "role": role,
                "password": _hash_password(password),
                "api_key": api_key or new_api_key(),
                "cas_username": (cas_username or "").strip(),
                "cas_password": cas_password or "",
                "cas_fingerprint": (cas_fingerprint or "").strip(),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._users[uid] = user
            self._save()
            log.info("创建网关用户: %s (role=%s)", username, role)
            return dict(user)

    def delete(self, user_id: str) -> bool:
        with self._lock:
            if user_id not in self._users:
                return False
            self._users.pop(user_id, None)
            self._save()
            log.info("删除网关用户: %s", user_id)
            return True

    def update_cas(self, user_id: str, cas_username: str | None = None,
                   cas_password: str | None = None,
                   cas_fingerprint: str | None = None) -> dict:
        with self._lock:
            user = self._users.get(user_id)
            if not user:
                raise ValueError("用户不存在")
            if cas_username is not None:
                user["cas_username"] = cas_username.strip()
            if cas_password is not None:
                user["cas_password"] = cas_password
            if cas_fingerprint is not None:
                user["cas_fingerprint"] = cas_fingerprint.strip()
            self._save()
            return dict(user)

    def set_password(self, user_id: str, password: str) -> None:
        if not password or len(password) < 6:
            raise ValueError("密码至少 6 位")
        with self._lock:
            user = self._users.get(user_id)
            if not user:
                raise ValueError("用户不存在")
            user["password"] = _hash_password(password)
            self._save()

    def set_role(self, user_id: str, role: str) -> None:
        if role not in ROLES:
            raise ValueError("角色只能是 admin/user")
        with self._lock:
            user = self._users.get(user_id)
            if not user:
                raise ValueError("用户不存在")
            user["role"] = role
            self._save()

    def set_api_key(self, user_id: str, api_key: str | None = None) -> str:
        with self._lock:
            user = self._users.get(user_id)
            if not user:
                raise ValueError("用户不存在")
            user["api_key"] = api_key or new_api_key()
            self._save()
            return user["api_key"]

    # ------------------------------------------------------------------ 引导
    def bootstrap(self, *, admin_username: str, admin_password: str,
                  api_key: str, cas_username: str, cas_password: str,
                  cas_fingerprint: str | None) -> dict | None:
        """用户表为空时，用环境变量创建第一个管理员用户."""
        with self._lock:
            if self._users:
                return None
            username = (admin_username or "admin").strip() or "admin"
            password = admin_password or ""
            if not password:
                password = "lit-" + secrets.token_urlsafe(9)
                log.warning("未配置 ADMIN_PASSWORD，已为管理员 %s 生成初始密码: %s",
                            username, password)
            user = self.create(
                username=username, password=password, role="admin",
                cas_username=cas_username or "", cas_password=cas_password or "",
                cas_fingerprint=cas_fingerprint or "",
                api_key=api_key or None,
            )
            log.info("已创建引导管理员用户: %s（API Key: %s%s）",
                     username, user["api_key"][:10], "..." if len(user["api_key"]) > 10 else "")
            return user

    # ------------------------------------------------------------------ 视图
    @staticmethod
    def public_user(user: dict, *, mask_key: bool = True) -> dict:
        api_key = str(user.get("api_key") or "")
        return {
            "id": user.get("id"),
            "username": user.get("username"),
            "role": user.get("role"),
            "api_key": (api_key[:8] + "..." + api_key[-6:]) if mask_key and api_key else api_key,
            "cas_username": user.get("cas_username") or "",
            "cas_password_set": bool(user.get("cas_password")),
            "cas_fingerprint": user.get("cas_fingerprint") or "",
            "cas_fingerprint_set": bool(user.get("cas_fingerprint")),
            "created_at": user.get("created_at"),
        }

    def credentials(self, user_id: str) -> UserCredentials:
        return UserCredentials(self, user_id)
