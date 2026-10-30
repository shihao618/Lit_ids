"""业务系统适配层.

每个业务系统（智能查寝 zncqapp、教务、图书馆……）实现一个 ``BaseSystem`` 子类：
  * 负责用 CAS 会话完成 SSO；
  * 负责维护该系统自身的会话/角色上下文；
  * 对外暴露语义化的业务方法（如 get_cqztbg）。

新增系统示例::

    class NewSystem(BaseSystem):
        key = "new"
        name = "新业务系统"
        def ensure_session(self, force=False): ...
        def get_xxx(self, ...): ...
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..session import SessionManager


class SystemError(RuntimeError):
    """业务系统调用错误（可携带 HTTP 状态 / 上游 code / 提示）."""

    def __init__(self, message: str, *, code: Any = None, http_status: int = 502,
                 hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status
        self.hint = hint

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
        }


class BaseSystem(ABC):
    """业务系统适配器基类."""

    key: str = "base"
    name: str = "base"
    #: 该系统通过 CAS 登录时的入口（service 参数）
    service_url: str = ""

    def __init__(self, session: SessionManager):
        self.session = session

    @abstractmethod
    def ensure_session(self, force: bool = False) -> None:
        """确保该系统已完成 SSO / 角色初始化；失败抛 SystemError."""

    def status(self) -> dict:
        return {"system": self.key, "name": self.name}
