"""API 层测试（不访问外网）：健康检查 / API Key（多用户）/ 会话 / 业务接口（mock 上游）."""

import os
import sys

from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.deps import get_xsfw  # noqa: E402
from app.main import app  # noqa: E402
from app.systems.xsfw import XsfwSystem  # noqa: E402

HEADERS = {"X-API-Key": "test-key"}


class FakeXsfw(XsfwSystem):
    """模拟 XsfwSystem，避免测试访问真实校园网（session=None，只用本地逻辑）."""

    def __init__(self):
        super().__init__(session=None)  # noqa: 仅测试用，方法都被 override
        self._role = {"ROLENAME": "班级管理员", "ROLEID": "r1", "DATAROLE": "DZDSW601"}
        self.last_call = {}

    def roles(self, refresh=False):
        return [{"ROLEID": "r1", "ROLENAME": "班级管理员", "DATAROLE": "DZDSW601"}]

    def set_role(self, role_name=None):
        self.last_call["role"] = role_name
        return self._role

    def current_role(self):
        return dict(self._role)

    def ensure_session(self, force=False):
        return None

    def menu(self):
        return [{"GNMC": "数据看板"}]

    def get_ssgq_students(self, searchkey="", page_number=1, page_size=10,
                          cqzt="", cqztlx="", role=None):
        self.last_call.update({"searchkey": searchkey, "page_number": page_number,
                               "page_size": page_size, "cqzt": cqzt,
                               "cqztlx": cqztlx, "role": role})
        return {"code": "0", "msg": "成功", "data": [
            {"XSXM": "张三", "XH": "B26071401", "BJMC": "B260714",
             "FJH": "412", "CQZT": cqzt or "1", "CQZT_DISPLAY": "未归寝"}]}

    def get_cqzt_dic(self):
        return {"code": "0", "msg": "成功", "data": [
            {"DM": "1", "MC": "未归寝"}, {"DM": "2", "MC": "已归寝"},
            {"DM": "3", "MC": "离校请假"}]}

    def get_cqztlx_dic(self, cqzt=""):
        self.last_call["cqztlx_dic_cqzt"] = cqzt
        return {"code": "0", "msg": "成功", "data": [
            {"DM": "21", "MC": "正常归寝", "CQZT": "2"}]}

    def call(self, path, data=None):
        return {"code": "0", "msg": "成功", "data": []}


def _client() -> TestClient:
    app.dependency_overrides[get_xsfw] = lambda: FakeXsfw()
    return TestClient(app)


def teardown_function():
    app.dependency_overrides.clear()


def test_healthz():
    with TestClient(app) as c:
        r = c.get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_readyz_multiuser():
    with TestClient(app) as c:
        r = c.get("/readyz")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["users"] >= 1          # 引导管理员
        assert "cached_sessions" in body


def test_root_info():
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "接口文档" in r.text
        assert "/api/v1/xsfw/sjkb/ssgq/students" in r.text
        assert "SEARCHKEY" in r.text
        # 页面不允许泄露任何密钥
        assert "test-key" not in r.text
        assert "test-admin" not in r.text
        r2 = c.get("/api-docs")
        assert r2.status_code == 200 and "接口文档" in r2.text


def test_meta_json():
    with TestClient(app) as c:
        body = c.get("/api/v1/meta").json()
        assert body["docs"] == "/"
        assert body["swagger"] == "/docs"
        assert body["admin"] == "/admin"


def test_api_key_required():
    with TestClient(app) as c:
        assert c.get("/api/v1/auth/status").status_code == 401
        assert c.get("/api/v1/auth/status", headers={"X-API-Key": "wrong"}).status_code == 401
        assert c.get("/api/v1/auth/status", headers=HEADERS).status_code == 200


def test_auth_status_and_login_without_cas_credentials():
    """管理员引导时未配置统一认证账号（测试 env 为空）→ 登录应返回 SESSION_ERROR."""
    with TestClient(app) as c:
        r = c.get("/api/v1/auth/status", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["logged_in"] is False
        assert body["cas_configured"] is False
        r = c.post("/api/v1/auth/login", headers=HEADERS, json={"force": True})
        assert r.status_code == 503
        assert r.json()["code"] == "SESSION_ERROR"


# ---------------------------------------------------------------- 学生归寝（官方参数/返回）
def test_ssgq_students_official_params():
    fake = FakeXsfw()
    app.dependency_overrides[get_xsfw] = lambda: fake
    with TestClient(app) as c:
        r = c.get("/api/v1/xsfw/sjkb/ssgq/students", headers=HEADERS,
                  params={"SEARCHKEY": "张三", "pageNumber": 2, "pageSize": 50,
                          "CQZT": "2", "CQZTLX": "21"})
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == "0" and body["msg"] == "成功"
        assert body["data"][0]["XSXM"] == "张三"
        assert fake.last_call["searchkey"] == "张三"
        assert fake.last_call["page_number"] == 2
        assert fake.last_call["page_size"] == 50
        assert fake.last_call["cqzt"] == "2"
        assert fake.last_call["cqztlx"] == "21"


def test_ssgq_students_default_params():
    fake = FakeXsfw()
    app.dependency_overrides[get_xsfw] = lambda: fake
    with TestClient(app) as c:
        r = c.get("/api/v1/xsfw/sjkb/ssgq/students", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["code"] == "0"
        assert fake.last_call["page_number"] == 1
        assert fake.last_call["page_size"] == 10
        assert fake.last_call["cqzt"] == ""
        assert fake.last_call["cqztlx"] == ""


def test_ssgq_students_validation():
    with _client() as c:
        assert c.get("/api/v1/xsfw/sjkb/ssgq/students", headers=HEADERS,
                     params={"pageSize": 500}).status_code == 422
        assert c.get("/api/v1/xsfw/sjkb/ssgq/students", headers=HEADERS,
                     params={"pageNumber": 0}).status_code == 422


def test_cqzt_dic_endpoint():
    with _client() as c:
        body = c.get("/api/v1/xsfw/sjkb/getCqztDic", headers=HEADERS).json()
        assert body["code"] == "0"
        assert [x["DM"] for x in body["data"]] == ["1", "2", "3"]


def test_cqztlx_dic_endpoint():
    fake = FakeXsfw()
    app.dependency_overrides[get_xsfw] = lambda: fake
    with TestClient(app) as c:
        r = c.get("/api/v1/xsfw/sjkb/getCqztlxDic", headers=HEADERS,
                  params={"CQZT": "2"})
        assert r.status_code == 200
        assert r.json()["data"][0]["DM"] == "21"
        assert fake.last_call["cqztlx_dic_cqzt"] == "2"


def test_generic_call_valid_path():
    with _client() as c:
        r = c.post("/api/v1/xsfw/call", headers=HEADERS,
                   json={"path": "/xsfw/sys/swmzncqapp/modules/sjkb/getSsgqStudent.do",
                         "data": {"pageNumber": 1}})
        assert r.status_code == 200
        assert r.json()["meta"]["path"].startswith("/xsfw/sys/")


def test_path_whitelist_blocks_ssrf():
    import pytest

    from app.systems import SystemError

    for bad in ("https://evil.com/x", "/api/other", "/xsfw/sys/../../etc/passwd",
                "/xsfw/sys/a?x=1"):
        with pytest.raises(SystemError):
            XsfwSystem._validate_path(bad)
    XsfwSystem._validate_path("/xsfw/sys/swmzncqapp/modules/sjkb/getSsgqStudent.do")
