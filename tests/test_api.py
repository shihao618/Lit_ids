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
CHECK_RECORDS = "/api/v1/dorm/check-records"
OLD_CQZTBG = "/api/v1/xsfw/sjkb/cqztbg"
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

    def check_records(self, class_code="", check_status="", check_status_type=""):
        self.last_call.update({"class_code": class_code,
                               "check_status": check_status,
                               "check_status_type": check_status_type})
        return {"code": 0, "msg": "ok", "data": {
            "total": 1,
            "items": [{
                "studentId": "B2607xxxx", "name": "示例同学",
                "classCode": class_code, "className": "示例班级",
                "collegeName": "示例学院", "majorName": "示例专业",
                "dormBuilding": "示例公寓", "roomNumber": "218",
                "bedNumber": "2", "phone": "138****0000",
                "checkStatus": "returned", "checkStatusLabel": "已归寝",
                "checkStatusType": "normal", "checkStatusTypeLabel": "正常归寝"}]}}

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
        assert CHECK_RECORDS in r.text
        assert "智能查寝" in r.text
        assert "请求体参数" in r.text
        assert "classCode" in r.text
        assert "checkStatus" in r.text
        assert "checkStatusType" in r.text
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
        assert any(CHECK_RECORDS in e for e in body["endpoints"])


def test_api_key_required():
    with TestClient(app) as c:
        assert c.get("/api/v1/auth/status").status_code == 401
        assert c.get("/api/v1/auth/status", headers={"X-API-Key": "wrong"}).status_code == 401
        assert c.get("/api/v1/auth/status", headers=HEADERS).status_code == 200
        assert c.post(CHECK_RECORDS, json={}).status_code == 401


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


# ---------------------------------------------------------------- 智能查寝（自有接口）
def test_check_records_params():
    fake = FakeXsfw()
    app.dependency_overrides[get_xsfw] = lambda: fake
    with TestClient(app) as c:
        r = c.post(CHECK_RECORDS, headers=HEADERS, json={
            "classCode": "B260714", "checkStatus": "not_returned",
            "checkStatusType": "unreachable"})
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        assert body["data"]["items"][0]["checkStatusLabel"] == "已归寝"
        assert fake.last_call == {"class_code": "B260714",
                                  "check_status": "not_returned",
                                  "check_status_type": "unreachable"}


def test_check_records_default_params():
    fake = FakeXsfw()
    app.dependency_overrides[get_xsfw] = lambda: fake
    with TestClient(app) as c:
        r = c.post(CHECK_RECORDS, headers=HEADERS, json={})
        assert r.status_code == 200
        assert fake.last_call == {"class_code": "", "check_status": "",
                                  "check_status_type": ""}


def test_check_records_enum_and_mapping_unit():
    """枚举归一化 / 易读字段映射的单元测试（不联网）."""
    from app.systems.base import SystemError
    from app.systems.xsfw import (STATUS_LOOKUP, STATUS_OPTIONS, TYPE_LOOKUP,
                                  TYPE_OPTIONS, XsfwSystem)

    assert XsfwSystem._resolve_choice("未归寝", STATUS_LOOKUP,
                                      "checkStatus", STATUS_OPTIONS) == ("1", "未归寝")
    assert XsfwSystem._resolve_choice("returned", STATUS_LOOKUP,
                                      "checkStatus", STATUS_OPTIONS) == ("2", "已归寝")
    assert XsfwSystem._resolve_choice("12", TYPE_LOOKUP,
                                      "checkStatusType", TYPE_OPTIONS) == ("12", "已报备")
    try:
        XsfwSystem._resolve_choice("bad", STATUS_LOOKUP,
                                   "checkStatus", STATUS_OPTIONS)
    except SystemError as e:
        assert e.http_status == 422
    else:
        raise AssertionError("invalid enum should raise SystemError")

    row = {"XH": "B2607xxxx", "XM": "示例", "BJDM": "B260714",
           "BJDM_DISPLAY": "示例班级", "DWDM_DISPLAY": "示例学院",
           "ZYDM_DISPLAY": "示例专业", "SSLMC": "示例公寓",
           "FJH": "218", "CWH": "2", "SJH": "138****0000",
           "CQZT": "2", "CQZT_DISPLAY": "已归寝",
           "CQZTLX": "21", "CQZTLX_DISPLAY": "正常归寝"}
    mapped = XsfwSystem._map_student(row)
    assert mapped["checkStatus"] == "returned"
    assert mapped["checkStatusLabel"] == "已归寝"
    assert mapped["checkStatusType"] == "normal"
    assert mapped["roomNumber"] == "218"
    assert mapped["phone"] == "138****0000"


def test_old_native_endpoint_removed():
    with _client() as c:
        assert c.post(OLD_CQZTBG, headers=HEADERS, json={}).status_code == 404


def test_only_one_business_api():
    """只保留 check-records 与 calendar 两个业务接口，旧接口应全部移除."""
    with _client() as c:
        for method, path in (
            ("get", "/api/v1/xsfw/roles"),
            ("get", "/api/v1/xsfw/menu"),
            ("get", "/api/v1/xsfw/sjkb/ssgq/students"),
            ("get", "/api/v1/xsfw/sjkb/getCqztDic"),
            ("post", "/api/v1/xsfw/call"),
            ("post", "/api/v1/xsfw/sjkb/cqztbg"),
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
