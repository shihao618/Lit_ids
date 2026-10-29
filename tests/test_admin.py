"""后台界面测试（多用户）：登录/用户管理/API Key/接口自测（mock 上游，不联网）."""

import os
import sys

from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.deps import get_admin_xsfw, get_xsfw  # noqa: E402
from app.main import app  # noqa: E402

ADMIN_LOGIN = {"username": "admin", "password": "test-admin"}


class FakeXsfw:
    def __init__(self):
        self._role = {"ROLENAME": "班级管理员", "ROLEID": "r1", "DATAROLE": "DZDSW601"}

    def roles(self, refresh=False):
        return [{"ROLEID": "r1", "ROLENAME": "班级管理员", "DATAROLE": "DZDSW601"}]

    def set_role(self, role_name=None):
        return self._role

    def current_role(self):
        return dict(self._role)

    def ensure_session(self, force=False):
        return None

    def menu(self):
        return [{"GNMC": "数据看板"}]

    def get_ssgq_students(self, searchkey="", page_number=1, page_size=10,
                          cqzt="", cqztlx="", role=None):
        return {"code": "0", "msg": "成功", "data": [{"XSXM": "张三", "XH": "B26071401"}]}

    def get_cqzt_dic(self):
        return {"code": "0", "msg": "成功", "data": [{"DM": "1", "MC": "未归寝"}]}

    def get_cqztlx_dic(self, cqzt=""):
        return {"code": "0", "msg": "成功", "data": [{"DM": "21", "MC": "正常归寝"}]}

    def call(self, path, data=None):
        return {"code": "0", "msg": "成功", "data": []}


def _client() -> TestClient:
    fake = FakeXsfw()
    app.dependency_overrides[get_xsfw] = lambda: fake
    app.dependency_overrides[get_admin_xsfw] = lambda: fake
    return TestClient(app)


def teardown_function():
    app.dependency_overrides.clear()


def _login(c: TestClient, username: str = "admin", password: str = "test-admin") -> None:
    r = c.post("/admin/login", data={"username": username, "password": password},
               follow_redirects=False)
    assert r.status_code == 303


def _state(c: TestClient) -> dict:
    r = c.get("/admin/api/state")
    assert r.status_code == 200
    return r.json()


def _create_user(c: TestClient, username: str, password: str = "user-pass-1", role: str = "user",
                 cas_username: str = "", cas_password: str = "") -> dict:
    r = c.post("/admin/api/users", json={
        "username": username, "password": password, "role": role,
        "cas_username": cas_username, "cas_password": cas_password,
    })
    body = r.json()
    assert body["ok"] is True, body
    return body["user"]


def test_admin_page_requires_login():
    with TestClient(app) as c:
        r = c.get("/admin")
        assert r.status_code == 200
        assert "后台" in r.text
        assert "用户名" in r.text
        assert c.get("/admin/api/state").status_code == 401
        assert c.post("/admin/api/login", json={}).status_code == 401


def test_admin_login_flow_and_state():
    with TestClient(app) as c:
        assert c.post("/admin/login", data={"username": "admin", "password": "wrong"},
                      follow_redirects=False).status_code == 401
        _login(c)
        r = c.get("/admin")
        assert r.status_code == 200 and "登录调试" in r.text
        st = _state(c)
        assert st["user"]["username"] == "admin"
        assert st["is_admin"] is True
        assert st["user"]["api_key"] == "test-key"      # 引导时使用 env API_KEY
        logs = c.get("/admin/api/logs").json()
        assert "lines" in logs


def test_admin_api_key_regenerate():
    with _client() as c:
        _login(c)
        old_key = _state(c)["user"]["api_key"]
        r = c.post("/admin/api/apikey/regenerate").json()
        assert r["ok"] is True and r["api_key"] and r["api_key"] != old_key
        # 旧 key 立即失效，新 key 可用
        assert c.get("/api/v1/xsfw/sjkb/getCqztDic",
                     headers={"X-API-Key": old_key}).status_code == 401
        assert c.get("/api/v1/xsfw/sjkb/getCqztDic",
                     headers={"X-API-Key": r["api_key"]}).status_code == 200


def test_create_user_and_isolated_login():
    with _client() as admin:
        _login(admin)
        user = _create_user(admin, "studentA", "studentA-pass")
        assert user["username"] == "studentA"
        api_key = user["api_key"]
        uid = user["id"]
        assert api_key and api_key.startswith("lit_")

        # 普通用户用备用客户端登录
        with TestClient(app) as user_c:
            _login(user_c, "studentA", "studentA-pass")
            st = _state(user_c)
            assert st["user"]["username"] == "studentA"
            assert st["is_admin"] is False
            assert st["user"]["api_key"] == api_key
            # 普通用户不能管理其他用户
            assert user_c.get("/admin/api/users").status_code == 403
            r = user_c.post("/admin/api/users", json={"username": "x", "password": "123456"})
            assert r.status_code == 403

        # 管理员能看到用户列表
        users = admin.get("/admin/api/users").json()["users"]
        assert any(u["id"] == uid for u in users)


def test_user_configures_own_cas_and_admin_reset():
    with _client() as admin:
        _login(admin)
        user = _create_user(admin, "studentB", "studentB-pass")
        uid = user["id"]

        with TestClient(app) as user_c:
            _login(user_c, "studentB", "studentB-pass")
            r = user_c.post("/admin/api/settings", json={
                "cas_username": "B2607xxxx", "cas_password": "cas-secret",
                "cas_fingerprint": "a" * 32}).json()
            assert r["ok"] is True
            st = _state(user_c)
            assert st["user"]["cas_username"] == "B2607xxxx"
            assert st["user"]["cas_password_set"] is True

        # 管理员重置该用户后台密码
        r = admin.post("/admin/api/users/%s/password" % uid,
                       json={"password": "newpass-123"}).json()
        assert r["ok"] is True

        with TestClient(app) as user_c:
            # 旧密码失败，新密码成功
            assert user_c.post("/admin/login", data={"username": "studentB",
                                                     "password": "studentB-pass"},
                               follow_redirects=False).status_code == 401
            _login(user_c, "studentB", "newpass-123")

        # 管理员重置该用户 API Key
        r = admin.post("/admin/api/users/%s/apikey" % uid).json()
        assert r["ok"] is True and r["api_key"]

        # 普通用户的 key 也能调用接口（Fake 上游，仅验证鉴权隔离）
        with TestClient(app) as user_c:
            _login(user_c, "studentB", "newpass-123")
            assert user_c.get("/api/v1/xsfw/sjkb/ssgq/students",
                              headers={"X-API-Key": user["api_key"]}).status_code == 401
            assert user_c.get("/api/v1/xsfw/sjkb/ssgq/students",
                              headers={"X-API-Key": r["api_key"]}).status_code == 200


def test_delete_user_and_self_protection():
    with _client() as admin:
        _login(admin)
        user = _create_user(admin, "studentC", "studentC-pass")
        uid = user["id"]
        admin_st = _state(admin)
        # 不能删除自己
        r = admin.delete("/admin/api/users/%s" % admin_st["user"]["id"]).json()
        assert r["ok"] is False
        # 删除普通用户后其登录失效
        assert admin.delete("/admin/api/users/%s" % uid).json()["ok"] is True
        with TestClient(app) as user_c:
            assert user_c.post("/admin/login", data={"username": "studentC",
                                                     "password": "studentC-pass"},
                               follow_redirects=False).status_code == 401


def test_admin_interface_selftest():
    with _client() as c:
        _login(c)
        assert c.get("/admin/api/test/roles").json()["ok"] is True
        r = c.get("/admin/api/test/students?SEARCHKEY=&pageNumber=1&pageSize=5&CQZT=2&CQZTLX=21")
        assert r.json()["ok"] is True and r.json()["data"]["code"] == "0"
        assert c.get("/admin/api/test/getCqztDic").json()["ok"] is True
        assert c.get("/admin/api/test/getCqztlxDic?CQZT=2").json()["ok"] is True
        r = c.post("/admin/api/test/call",
                   json={"path": "/xsfw/sys/swmzncqapp/modules/sjkb/getSsgqStudent.do",
                         "data": {"pageNumber": 1}})
        assert r.json()["ok"] is True


def test_admin_system_settings_and_restart(monkeypatch):
    """系统设置（后台可改）+ 重启容器按钮（mock，不真的杀测试进程）."""
    import app.routers.admin as admin_mod

    restarted = []
    monkeypatch.setattr(admin_mod, "schedule_restart", lambda: restarted.append(True))

    with TestClient(app) as c:
        _login(c)
        r = c.get("/admin/api/system-settings").json()
        assert r["ok"] is True
        assert "values" in r["settings"] and "editable" in r["settings"]

        r = c.post("/admin/api/system-settings", json={
            "app_name": "测试网关",
            "log_level": "DEBUG",
            "session_refresh_interval": 60,
            "auto_login_on_start": False,
        }).json()
        assert r["ok"] is True
        assert r["values"]["app_name"] == "测试网关"
        assert "auto_login_on_start" in r["restart_required"]

        # 动态生效：meta / 前台文档标题
        assert c.get("/api/v1/meta").json()["name"] == "测试网关"
        assert "测试网关" in c.get("/").text

        vals = c.get("/admin/api/system-settings").json()["settings"]["values"]
        assert vals["log_level"] == "DEBUG"
        assert vals["session_refresh_interval"] == 60
        assert vals["auto_login_on_start"] is False

        r = c.post("/admin/api/restart").json()
        assert r["ok"] is True
        assert restarted, "应当调用 schedule_restart"


def test_system_settings_admin_only():
    with _client() as c:
        _login(c)
        _create_user(c, "settingsUser", "settings-pass")

    with _client() as user_c:
        _login(user_c, "settingsUser", "settings-pass")
        assert user_c.get("/admin/api/system-settings").status_code == 403
        assert user_c.post("/admin/api/system-settings", json={"log_level": "DEBUG"}).status_code == 403
        assert user_c.post("/admin/api/restart").status_code == 403


def test_admin_mfa_flow_plumbing(monkeypatch):
    from app.session import SessionManager

    def fake_start(self, force=False):
        return {"status": "need_mfa", "session": {"pending_mfa": True}}

    def fake_complete(self, code):
        assert code == "123456"
        return {"status": "ok", "mfa_used": True, "session": {"pending_mfa": False}}

    monkeypatch.setattr(SessionManager, "start_login", fake_start)
    monkeypatch.setattr(SessionManager, "complete_mfa", fake_complete)

    with TestClient(app) as c:
        _login(c)
        r = c.post("/admin/api/login", json={"force": True}).json()
        assert r["status"] == "need_mfa" and r["ok"] is False
        r = c.post("/admin/api/mfa", json={"code": "123456"}).json()
        assert r["ok"] is True and r["status"] == "ok"


def test_bootstrap_generates_random_admin_password(tmp_path):
    """未提供 ADMIN_PASSWORD 时：随机生成密码并写入 admin-password.txt."""
    from app.users import UserStore

    store = UserStore(str(tmp_path / "users.json"))
    user = store.bootstrap(admin_username="admin", admin_password="", api_key="",
                           cas_username="", cas_password="", cas_fingerprint="")
    assert user is not None and user["username"] == "admin"
    pw_file = tmp_path / "admin-password.txt"
    assert pw_file.exists()
    text = pw_file.read_text(encoding="utf-8")
    assert "username=admin" in text
    password = next(line.split("=", 1)[1].strip()
                    for line in text.splitlines() if line.startswith("password="))
    assert len(password) >= 12
    assert store.check_password("admin", password) is not None
    assert store.check_password("admin", "wrong-password") is None


def test_store_save_failure_returns_json_error(monkeypatch):
    """数据目录不可写时，保存统一认证账号应返回明确错误而不是假装成功."""
    from app.users import StoreError, UserStore

    def boom(self):
        raise StoreError("data 目录不可写（测试模拟）")

    with TestClient(app) as c:
        _login(c)
        monkeypatch.setattr(UserStore, "_save", boom)
        r = c.post("/admin/api/settings", json={"cas_username": "B2607xxxx"})
        assert r.status_code == 500
        body = r.json()
        assert body["ok"] is False
        assert "data 目录不可写" in body["message"]


def test_readyz_reports_data_writable():
    with TestClient(app) as c:
        assert c.get("/readyz").json()["data_writable"] is True


def test_cas_account_validation_and_password_kept():
    """统一认证：账号不能为空；只改账号（密码留空）时保留原密码."""
    with TestClient(app) as c:
        _login(c)
        r = c.post("/admin/api/settings",
                   json={"cas_username": "B2607xxxx", "cas_password": "cas-pass"}).json()
        assert r["ok"] is True
        # 只改账号，密码留空 → 保留已有密码
        r = c.post("/admin/api/settings", json={"cas_username": "B26071436"}).json()
        assert r["ok"] is True
        st = c.get("/admin/api/state").json()
        assert st["user"]["cas_username"] == "B26071436"
        assert st["user"]["cas_password_set"] is True
        # 账号为空 → 拒绝保存
        r = c.post("/admin/api/settings",
                   json={"cas_username": "", "cas_password": "x"}).json()
        assert r["ok"] is False and "不能为空" in r["error"]


def test_admin_selftest_uses_cookie_auth():
    """后台接口自测必须走 cookie 登录用户自己的会话，不能要求 X-API-Key（旧版 401 会导致前端跳概览）."""
    with TestClient(app) as c:
        _login(c)
        r = c.get("/admin/api/test/roles")
        assert r.status_code == 200          # 旧实现返回 401
        assert "ok" in r.json()
