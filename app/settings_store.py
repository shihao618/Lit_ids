"""运行时系统配置（后台「系统设置」可修改，落盘 data/settings.json）.

运行参数只在后台「系统设置」维护（落盘 data/settings.json），不从 .env 读取：
  * app_name                 界面/文档标题（即时生效；Swagger 标题需重启）
  * log_level                日志级别（即时生效）
  * session_refresh_interval 会话保活间隔秒（即时生效，后台任务动态读取）
  * auto_login_on_start      启动时自动登录（需要重启容器生效）
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

log = logging.getLogger(__name__)

#: 可后台修改的配置项元信息
EDITABLE: dict[str, dict[str, Any]] = {
    "app_name": {"label": "系统名称", "type": "string", "restart": False,
                 "description": "显示在后台/接口文档标题；Swagger 页标题需重启后更新"},
    "log_level": {"label": "日志级别", "type": "enum", "restart": False,
                  "options": ["DEBUG", "INFO", "WARNING", "ERROR"]},
    "session_refresh_interval": {"label": "会话保活间隔（秒）", "type": "int",
                                 "restart": False, "min": 0,
                                 "description": "0=关闭定时保活；修改后后台任务自动按新间隔执行"},
    "auto_login_on_start": {"label": "启动时自动登录", "type": "bool",
                            "restart": True,
                            "description": "是否为已配置统一认证账号的用户在启动时自动登录（重启容器后生效）"},
}

DEFAULTS = {
    "app_name": "LIT API Gateway",
    "log_level": "INFO",
    "session_refresh_interval": 0,
    "auto_login_on_start": True,
}


class RuntimeSettings:
    """运行参数存储（线程安全，JSON 落盘）."""

    def __init__(self, path: str, defaults: dict | None = None):
        self.path = path
        self._lock = threading.RLock()
        self._values: dict[str, Any] = dict(DEFAULTS)
        if defaults:
            self._values.update({k: v for k, v in defaults.items() if k in DEFAULTS})
        self._load()

    # ------------------------------------------------------------------ 读写
    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            log.info("系统设置文件不存在，使用默认值（保存后生成 %s）", self.path)
            return
        if isinstance(data, dict):
            for key in DEFAULTS:
                if key in data:
                    self._values[key] = data[key]
            log.info("已加载系统设置: %s", self._values)

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._values, f, ensure_ascii=False, indent=2)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)
        except OSError as e:  # noqa: BLE001
            log.warning("系统设置保存失败: %s", e)

    # ------------------------------------------------------------------ 读取
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._values.get(key, default)

    def all(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._values)

    def public(self) -> dict[str, Any]:
        return {
            "values": self.all(),
            "editable": EDITABLE,
            "path": self.path,
        }

    # ------------------------------------------------------------------ 更新
    def update(self, payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """更新配置；返回 (最新值, 需要重启的变更项)."""
        changed: list[str] = []
        restart_needed: list[str] = []
        with self._lock:
            for key, value in payload.items():
                if key not in EDITABLE or value is None:
                    continue
                value = self._validate(key, value)
                if self._values.get(key) != value:
                    changed.append(key)
                    if EDITABLE[key].get("restart"):
                        restart_needed.append(key)
                    self._values[key] = value
            if changed:
                self._save()
        return self.all(), restart_needed

    @staticmethod
    def _validate(key: str, value: Any) -> Any:
        if key == "app_name":
            text = str(value).strip()
            if not text:
                raise ValueError("系统名称不能为空")
            return text[:60]
        if key == "log_level":
            level = str(value).upper()
            if level not in EDITABLE["log_level"]["options"]:
                raise ValueError("日志级别不合法: %s" % value)
            return level
        if key == "session_refresh_interval":
            try:
                number = int(value)
            except (TypeError, ValueError) as e:
                raise ValueError("会话保活间隔必须是整数秒") from e
            if number < 0:
                raise ValueError("会话保活间隔不能为负数")
            return number
        if key == "auto_login_on_start":
            return bool(value)
        return value


def apply_log_level(level: str) -> None:
    """立即应用日志级别（root + uvicorn 相关 logger）."""
    numeric = getattr(logging, str(level).upper(), logging.INFO)
    logging.getLogger().setLevel(numeric)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "lit_api"):
        logging.getLogger(name).setLevel(numeric)
