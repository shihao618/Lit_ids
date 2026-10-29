"""内存日志缓冲：后台界面展示最近日志用."""

from __future__ import annotations

import logging
from collections import deque


class LogBuffer(logging.Handler):
    """把日志记录保存到内存环形缓冲区（默认 500 条）."""

    def __init__(self, capacity: int = 500):
        super().__init__()
        self.records: deque[str] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            self.records.append(self.format(record))
        except Exception:  # noqa: BLE001
            self.records.append(record.getMessage())

    def get(self, limit: int = 200) -> list[str]:
        data = list(self.records)
        return data[-limit:] if limit > 0 else data

    def clear(self) -> None:
        self.records.clear()


log_buffer = LogBuffer()
