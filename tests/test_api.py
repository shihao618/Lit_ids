"""API 层测试（不访问外网）：健康检查 / API Key（多用户）/ 会话 / 智能查寝接口（mock 上游）."""

import os
import sys

from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.deps import get_lljw, get_xsfw  # noqa: E402
from app.main import app  # noqa: E402
from app.systems.xsfw import XsfwSystem  # noqa: E402

HEADERS = {"X-API-Key": "test-key"}
CQZTBG = "/api/v1/xsfw/sjkb/cqztbg"
CALENDAR = "/api/v1/lljw/xsgrkb/getCalendarWeekDatas"


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

    def get_cqztbg(self, bjdm="", page_number=1, page_size=10,
                   query_setting=None, cqzt="", cqztlx="", dwdm="", gzxs="", role=None):
        self.last_call.update({"bjdm": bjdm, "page_number": page_number,
                               "page_size": page_size, "query_setting": query_setting,
                               "cqzt": cqzt, "cqztlx": cqztlx,
                               "dwdm": dwdm, "gzxs": gzxs, "role": role})
        return {"code": "0", "datas": {"cqztbg": {
            "totalSize": 1, "pageSize": page_size, "pageNumber": page_number,
            "rows": [{"XH": "B2607xxxx", "XM": "示例同学", "BJDM": bjdm,
                      "CQZT": "2", "CQZT_DISPLAY": "已归寝"}]}}}


class FakeLljw:
    """模拟 LljwSystem（不访问真实教务系统）."""

    def __init__(self):
        self.last_call = {}

    def get_calendar_week_datas(self, xnxqdm, zc, d1, d2):
        self.last_call.update({"xnxqdm": xnxqdm, "zc": zc, "d1": d1, "d2": d2})
        return {"code": 0, "data": [{
            "kcmc": "示例课程", "teaxms": "示例教师", "zc": str(zc),
            "xnxqdm": xnxqdm, "qsrq": "2026-10-26", "jsrq": "2026-10-26"}]}


def _client() -> TestClient:
    app.dependency_overrides[get_xsfw] = lambda: FakeXsfw()
    app.dependency_overrides[get_lljw] = lambda: FakeLljw()
    return TestClient(app)


def teardown_function():
    app.dependency_overrides.clear()


# ---------------------------------------------------------------- 基础
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
        assert CQZTBG in r.text
        assert "智能查寝" in r.text
        assert "BJDM" in r.text
        assert "请求体参数" in r.text
        assert "子参数" in r.text
        assert "linkOpt" in r.text
        assert "builder" in r.text
        assert "querySetting" in r.text
        assert "lessEqual" in r.text
        assert "CQZTLX" in r.text
        # 页面不允许泄露任何密钥
        assert "test-key" not in r.text
        assert "test-admin" not in r.text
        assert c.get("/api-docs").status_code == 404   # 旧别名已移除


def test_meta_json():
    with TestClient(app) as c:
        body = c.get("/api/v1/meta").json()
        assert body["docs"] == "/"
        assert body["swagger"] == "/docs"
        assert body["admin"] == "/admin"
        assert any(CQZTBG in e for e in body["endpoints"])


def test_api_key_required():
    with TestClient(app) as c:
        assert c.get("/api/v1/auth/status").status_code == 401
        assert c.get("/api/v1/auth/status", headers={"X-API-Key": "wrong"}).status_code == 401
        assert c.get("/api/v1/auth/status", headers=HEADERS).status_code == 200
        assert c.post(CQZTBG, json={}).status_code == 401


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


# ---------------------------------------------------------------- 智能查寝 cqztbg.do
def test_cqztbg_official_params():
    fake = FakeXsfw()
    app.dependency_overrides[get_xsfw] = lambda: fake
    with TestClient(app) as c:
        r = c.post(CQZTBG, headers=HEADERS, json={
            "BJDM": "B260714", "DWDM": "07", "CQZT": "2", "CQZTLX": "21", "GZXS": "0",
            "pageSize": 50, "pageNumber": 2,
            "querySetting": '[{"name":"BJDM","value":"B260714","linkOpt":"AND","builder":"equal"}]'})
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == "0"
        assert body["datas"]["cqztbg"]["pageSize"] == 50
        assert fake.last_call["bjdm"] == "B260714"
        assert fake.last_call["page_number"] == 2
        assert fake.last_call["page_size"] == 50
        assert "linkOpt" in fake.last_call["query_setting"]
        assert fake.last_call["cqzt"] == "2"
        assert fake.last_call["cqztlx"] == "21"
        assert fake.last_call["dwdm"] == "07"
        assert fake.last_call["gzxs"] == "0"


def test_cqztbg_default_params():
    fake = FakeXsfw()
    app.dependency_overrides[get_xsfw] = lambda: fake
    with TestClient(app) as c:
        r = c.post(CQZTBG, headers=HEADERS, json={})
        assert r.status_code == 200
        assert fake.last_call["bjdm"] == ""
        assert fake.last_call["page_number"] == 1
        assert fake.last_call["page_size"] == 10
        assert fake.last_call["query_setting"] is None
        assert fake.last_call["cqzt"] == ""
        assert fake.last_call["cqztlx"] == ""
        assert fake.last_call["dwdm"] == ""
        assert fake.last_call["gzxs"] == ""


def test_cqztbg_validation():
    with _client() as c:
        assert c.post(CQZTBG, headers=HEADERS, json={"pageSize": 500}).status_code == 422
        assert c.post(CQZTBG, headers=HEADERS, json={"pageNumber": 0}).status_code == 422


def test_only_one_business_api():
    """只保留 cqztbg 与 calendar 两个业务接口，旧接口应全部移除."""
    with _client() as c:
        for method, path in (
            ("get", "/api/v1/xsfw/roles"),
            ("get", "/api/v1/xsfw/menu"),
            ("get", "/api/v1/xsfw/sjkb/ssgq/students"),
            ("get", "/api/v1/xsfw/sjkb/getCqztDic"),
            ("post", "/api/v1/xsfw/call"),
        ):
            resp = getattr(c, method)(path, headers=HEADERS)
            assert resp.status_code in (404, 405), (path, resp.status_code)


# ---------------------------------------------------------------- 教务系统 校历周数据
def test_calendar_official_params():
    fake = FakeLljw()
    app.dependency_overrides[get_lljw] = lambda: fake
    with TestClient(app) as c:
        r = c.post(CALENDAR, headers=HEADERS, json={
            "xnxqdm": "202601", "zc": 7,
            "d1": "2026-10-26 00:00:00", "d2": "2026-11-02 00:00:00"})
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        assert body["data"][0]["kcmc"] == "示例课程"
        assert fake.last_call["xnxqdm"] == "202601"
        assert fake.last_call["zc"] == 7
        assert "00:00:00" in fake.last_call["d1"]


def test_calendar_date_range_optional():
    """d1/d2 可省略（上游实测不需要），只传 xnxqdm + zc 也能查询."""
    fake = FakeLljw()
    app.dependency_overrides[get_lljw] = lambda: fake
    with TestClient(app) as c:
        r = c.post(CALENDAR, headers=HEADERS, json={"xnxqdm": "202601", "zc": 7})
        assert r.status_code == 200
        assert fake.last_call["xnxqdm"] == "202601"
        assert fake.last_call["zc"] == 7
        assert fake.last_call["d1"] == ""
        assert fake.last_call["d2"] == ""


def test_calendar_requires_api_key():
    with TestClient(app) as c:
        r = c.post(CALENDAR, json={"xnxqdm": "202601", "zc": 7,
                                   "d1": "2026-10-26 00:00:00",
                                   "d2": "2026-11-02 00:00:00"})
        assert r.status_code == 401


def test_calendar_validation():
    with _client() as c:
        # 必填：xnxqdm、zc（d1/d2 已改为可选）
        assert c.post(CALENDAR, headers=HEADERS, json={"zc": 7}).status_code == 422
        assert c.post(CALENDAR, headers=HEADERS,
                      json={"xnxqdm": "202601", "zc": 0}).status_code == 422


def test_calendar_in_docs_and_meta():
    with TestClient(app) as c:
        docs = c.get("/")
        assert docs.status_code == 200
        assert "/api/v1/lljw/xsgrkb/getCalendarWeekDatas" in docs.text
        assert "教务系统" in docs.text
        meta = c.get("/api/v1/meta").json()
        assert any("getCalendarWeekDatas" in e for e in meta["endpoints"])
