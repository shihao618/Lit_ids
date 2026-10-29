"""按用户管理 CAS 会话与业务系统实例（多用户隔离）."""

from __future__ import annotations

import logging
import os
import threading

from .config import Settings
from .session import SessionManager
from .systems import XsfwSystem
from .users import UserStore

log = logging.getLogger(__name__)


class SessionRegistry:
    """每个网关用户一个独立的 CAS 会话 + 业务系统实例."""

    def __init__(self, settings: Settings, store: UserStore):
        self.settings = settings
        self.store = store
        self.session_dir = settings.session_dir
        os.makedirs(self.session_dir, exist_ok=True)
        self._lock = threading.RLock()
        self._sessions: dict[str, SessionManager] = {}
        self._systems: dict[str, XsfwSystem] = {}

    # ------------------------------------------------------------------ 路径
    def cookie_file(self, user_id: str) -> str:
        return os.path.join(self.session_dir, "%s.json" % user_id)

    def _label(self, user_id: str) -> str:
        user = self.store.get(user_id) or {}
        return str(user.get("username") or user_id[:8])

    # ------------------------------------------------------------------ 获取
    def session(self, user_id: str) -> SessionManager:
        with self._lock:
            mgr = self._sessions.get(user_id)
            if mgr is None:
                mgr = SessionManager(
                    self.store.credentials(user_id),
                    cookie_file=self.cookie_file(user_id),
                    label=self._label(user_id),
                )
                self._sessions[user_id] = mgr
            return mgr

    def system(self, user_id: str) -> XsfwSystem:
        with self._lock:
            system = self._systems.get(user_id)
            if system is None:
                system = XsfwSystem(self.session(user_id))
                self._systems[user_id] = system
            return system

    def cached_users(self) -> list[str]:
        with self._lock:
            return sorted(set(self._sessions) | set(self._systems))

    # ------------------------------------------------------------------ 重置
    def drop(self, user_id: str, clear_cookies: bool = False) -> None:
        """丢弃缓存实例（用户删除/凭据修改时调用）；可选择删除会话文件."""
        with self._lock:
            self._sessions.pop(user_id, None)
            self._systems.pop(user_id, None)
        if clear_cookies:
            try:
                os.remove(self.cookie_file(user_id))
            except OSError:
                pass
        log.info("已重置用户会话缓存: %s (clear_cookies=%s)", user_id[:8], clear_cookies)

    def drop_all(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._systems.clear()
