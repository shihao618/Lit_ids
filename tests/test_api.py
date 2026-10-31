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
SCHEDULE = "/api/v1/lljw/schedule"
OLD_CALENDAR = "/api/v1/lljw/xsgrkb/getCalendarWeekDatas"


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

    def schedule(self, semester="", week=None, query="week", date_text=""):
        self.last_call.update({"semester": semester, "week": week,
                               "query": query, "date_text": date_text})
        return {"code": 0, "msg": "ok", "data": {
            "semester": "202601", "semesterName": "2026-2027-1",
            "query": query, "week": 8,
            "weekStart": "2026-10-26", "weekEnd": "2026-11-01",
            "date": "2026-10-29", "total": 1,
            "items": [{"courseName": "示例课程", "teacher": "示例教师",
                       "date": "2026-10-29", "weekday": "周四", "week": 8,
                       "startPeriod": "01", "endPeriod": "02",
                       "periodLabel": "第1-2节", "startTime": "08:00",
                       "endTime": "09:40"}]}}

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


# ---------------------------------------------------------------- 教务系统 课表查询
def test_schedule_params():
    fake = FakeLljw()
    app.dependency_overrides[get_lljw] = lambda: fake
    with TestClient(app) as c:
        r = c.post(SCHEDULE, headers=HEADERS, json={
            "semester": "202601", "query": "day", "date": "2026-10-29"})
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        assert body["data"]["total"] == 1
        assert fake.last_call == {"semester": "202601", "week": None,
                                  "query": "day", "date_text": "2026-10-29"}


def test_schedule_default_params():
    fake = FakeLljw()
    app.dependency_overrides[get_lljw] = lambda: fake
    with TestClient(app) as c:
        r = c.post(SCHEDULE, headers=HEADERS, json={})
        assert r.status_code == 200
        assert fake.last_call == {"semester": "", "week": None,
                                  "query": "week", "date_text": ""}


def test_schedule_requires_api_key():
    with TestClient(app) as c:
        assert c.post(SCHEDULE, json={}).status_code == 401


def test_schedule_old_endpoint_removed():
    with _client() as c:
        assert c.post(OLD_CALENDAR, headers=HEADERS, json={}).status_code == 404


def test_schedule_unit_helpers():
    """不联网测试：节次过滤 / 课程映射 / 学期解析."""
    from datetime import date

    from app.systems.lljw import LljwSystem

    morning = {"startPeriod": "01", "endPeriod": "02", "startTime": "08:00"}
    afternoon = {"startPeriod": "05", "endPeriod": "06", "startTime": "14:30"}
    evening = {"startPeriod": "09", "endPeriod": "10", "startTime": "19:10"}
    items = [morning, afternoon, evening]
    assert LljwSystem._filter_period(items, "morning") == [morning]
    assert LljwSystem._filter_period(items, "afternoon") == [afternoon]
    assert LljwSystem._filter_period(items, "evening") == [evening]

    sys_ = LljwSystem(session=None)
    row = {"kcmc": "示例课程", "kcdm": "123", "teaxms": "示例教师",
           "jxcdmc": "XC3", "jxbmc": "示例班级", "qsrq": "2026-10-29",
           "ps": "01", "pe": "02", "qssj": "08:00:00", "jssj": "09:40:00",
           "zc": "8", "bgcolor": "#009688"}
    mapped = sys_._map_course(row, 8, date(2026, 10, 26))
    assert mapped["courseName"] == "示例课程"
    assert mapped["weekday"] == "周四"
    assert mapped["startPeriod"] == "01"
    assert mapped["periodLabel"] == "第1-2节"
    assert mapped["startTime"] == "08:00"

    html = ("<select id='xnxqdm'>"
            "<option value='202601' selected>2026-2027-1</option></select>")
    opts = LljwSystem._parse_semester_options(html)
    assert opts == [{"code": "202601", "name": "2026-2027-1", "selected": True}]


def test_schedule_docs_and_meta():
    with TestClient(app) as c:
        docs = c.get("/")
        assert docs.status_code == 200
        assert SCHEDULE in docs.text
        assert "教务系统" in docs.text
        assert OLD_CALENDAR not in docs.text
        meta = c.get("/api/v1/meta").json()
        assert any(SCHEDULE in e for e in meta["endpoints"])
