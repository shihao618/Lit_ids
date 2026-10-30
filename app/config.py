"""应用配置：只保留"部署基础设施"配置（环境变量 / .env）.

可后台修改的运行参数（系统名称/日志级别/会话保活/启动自动登录）**只**在
``data/settings.json`` 中维护（后台「系统设置」修改），不再从环境变量/.env 读取。
本文件仅保留：数据目录、初始管理员引导、后台签名密钥等部署级信息。

首次启动（用户表为空）会创建管理员：
  * ADMIN_USERNAME / ADMIN_PASSWORD  —— 后台登录名/密码
  * API_KEY                           —— 管理员初始 API Key（不填自动生成）
之后所有账号/统一认证配置都在后台 /admin 里按用户维护。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

try:  # 本地开发便利：有 .env 就加载，容器里由 compose 注入环境变量
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


@dataclass(frozen=True)
class Settings:
    """部署级配置（不可在后台修改的部分）."""

    version: str
    api_key: str             # 首次引导的管理员 API Key（空=自动生成）
    admin_username: str
    admin_password: str
    admin_secret: str
    data_dir: str
    users_file: str
    session_dir: str
    settings_file: str


@lru_cache
def get_settings() -> Settings:
    from . import __version__

    data_dir = os.getenv("DATA_DIR", "data")
    return Settings(
        version=os.getenv("APP_VERSION", __version__),
        api_key=os.getenv("API_KEY", ""),
        admin_username=os.getenv("ADMIN_USERNAME", "admin"),
        admin_password=os.getenv("ADMIN_PASSWORD", ""),
        admin_secret=os.getenv("ADMIN_SECRET", ""),
        data_dir=data_dir,
        users_file=os.getenv("USERS_FILE", os.path.join(data_dir, "users.json")),
        session_dir=os.getenv("SESSION_DIR", os.path.join(data_dir, "sessions")),
        settings_file=os.getenv("SETTINGS_FILE", os.path.join(data_dir, "settings.json")),
    )

