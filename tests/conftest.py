"""pytest 公共配置：在导入 app 之前设置测试用环境变量.

测试环境不连真实 CAS / xsfw：
  * 使用临时数据目录，每个测试前清空用户表（lifespan 会用 env 引导创建管理员 admin/test-admin）；
  * 管理员 API Key = test-key；
  * 不自动登录。
"""

import os
import shutil
import tempfile

import pytest

TMP_DIR = os.path.join(tempfile.gettempdir(), "lit_api_gateway_test")
USERS_FILE = os.path.join(TMP_DIR, "users.json")
SESSION_DIR = os.path.join(TMP_DIR, "sessions")

shutil.rmtree(TMP_DIR, ignore_errors=True)
os.makedirs(SESSION_DIR, exist_ok=True)

os.environ["API_KEY"] = "test-key"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "test-admin"
os.environ["DATA_DIR"] = TMP_DIR
os.environ["USERS_FILE"] = USERS_FILE
os.environ["SESSION_DIR"] = SESSION_DIR
os.environ["SETTINGS_FILE"] = os.path.join(TMP_DIR, "settings.json")

SETTINGS_FILE = os.environ["SETTINGS_FILE"]


@pytest.fixture(autouse=True)
def clean_state():
    """每个测试前清空用户表/会话/系统设置，保证状态隔离."""
    for path in (USERS_FILE, USERS_FILE + ".tmp", SETTINGS_FILE, SETTINGS_FILE + ".tmp"):
        try:
            os.remove(path)
        except OSError:
            pass
    shutil.rmtree(SESSION_DIR, ignore_errors=True)
    os.makedirs(SESSION_DIR, exist_ok=True)
    yield
