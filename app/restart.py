"""一键重启容器（无 Docker socket 场景下的重启方式）.

原理：容器内进程向自己发送 SIGTERM，uvicorn 优雅退出；docker-compose 的
``restart: unless-stopped`` 会立刻把容器重新拉起，等同于"重启容器"。

注意：本地 `uvicorn` 直接运行时，这个操作会终止进程（不会自动拉起），
后台界面会有相应提示。
"""

from __future__ import annotations

import logging
import os
import signal
import threading

log = logging.getLogger(__name__)


def _kill_self() -> None:
    log.warning("收到后台『重启容器』指令，进程即将退出（容器将按 restart 策略自动拉起）")
    try:
        os.kill(os.getpid(), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        os._exit(1)


def schedule_restart(delay: float = 1.2) -> None:
    """延迟片刻后重启（留时间把 HTTP 响应返回给前端）."""
    threading.Timer(delay, _kill_self).start()
