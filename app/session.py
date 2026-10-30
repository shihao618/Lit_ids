"""CAS 会话管理（单用户实例）：登录、复用、持久化、状态查询、二次验证(MFA)分步调试.

核心逻辑复用 ``lit_login.py``：
  * L.fetch_login_page / L.get_captcha_token / L.aes_cbc_encrypt_b64  —— 账号密码登录
  * L.is_mfa_page / L.extract_mfa_execution / L.parse_page_error     —— 企业微信二次验证
  * L.check_saved_session / L.client_with_cookies                    —— 会话复用

多用户场景下：每个网关用户持有一个独立的 SessionManager 实例（各自 cookie 文件、
各自的统一认证账号/密码/指纹、互不影响），由 ``app.registry.SessionRegistry`` 统一管理。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid

import lit_login as L

log = logging.getLogger(__name__)


class SessionError(RuntimeError):
    """会话不可用（未登录 / 登录失败 / 需要人工二次验证等）."""

    def __init__(self, message: str, *, mfa_required: bool = False):
        super().__init__(message)
        self.mfa_required = mfa_required


class SessionManager:
    """单个网关用户的 CAS 会话（线程安全）."""

    def __init__(self, credentials, cookie_file: str, label: str = ""):
        """
        :param credentials: 形如 UserCredentials 的对象（有 username/password/fingerprint 属性）
        :param cookie_file: 该用户的 CAS 会话文件路径
        :param label: 日志标识（一般是网关用户名）
        """
        self.credentials = credentials
        self.cookie_file = cookie_file
        self.label = label or "user"
        self._lock = threading.RLock()
        self._client: L.HttpClient | None = None
        self._cookies: dict[str, str] = {}
        self._meta: dict = {}
        self._last_error: str | None = None
        #: TGC 有效性缓存（None=尚未检测；True/False=最近一次检测结果）
        self._valid: bool | None = None
        self._valid_checked_at: float | None = None
        #: 会话版本号：每次登录成功/清除会话 +1（业务系统据此重建自己的会话）
        self.generation = 0
        #: 待完成的二次验证状态（后台界面输入验证码用）
        self._pending: dict | None = None
        #: 最近一次登录调试信息（后台界面展示）
        self.debug: dict = {"stage": None, "captcha": [], "result": None, "error": None, "at": None}
        self._load()

    # ------------------------------------------------------------------ 持久化
    def _load(self) -> None:
        path = self.cookie_file
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            log.info("[%s] 未找到已保存的会话文件: %s", self.label, path)
            return
        if not isinstance(data, dict):
            return
        self._cookies = data.get("cookies") or {}
        self._meta = {k: v for k, v in data.items() if k != "cookies"}
        if self._cookies:
            self._client = L.client_with_cookies(self._cookies)
            self._valid = None
            self._valid_checked_at = None
            log.info("[%s] 已加载会话文件 (login_time=%s)", self.label,
                     self._meta.get("login_time"))

    def _persist_login(self, client: L.HttpClient, final_url: str, mfa_used: bool) -> None:
        cookies = client.cookies_dict()
        payload = {
            "username": self.credentials.username,
            "login_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "final_url": final_url,
            "fingerprint": self.credentials.fingerprint,
            "mfa_used": mfa_used,
            "cookies": cookies,
        }
        self._client = client
        self._cookies = cookies
        self._meta = {k: v for k, v in payload.items() if k != "cookies"}
        self._pending = None
        self._last_error = None
        self._valid = True
        self._valid_checked_at = time.time()
        self.generation += 1
        self._save_session_file(payload)

    def _save_session_file(self, payload: dict) -> None:
        path = self.cookie_file
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, path)
            log.info("[%s] 会话已保存: %s", self.label, path)
        except OSError as e:  # noqa: BLE001
            log.warning("[%s] 会话保存失败: %s", self.label, e)

    # ------------------------------------------------------------------ 状态
    @property
    def lock(self) -> threading.RLock:
        return self._lock

    @property
    def client(self) -> L.HttpClient:
        if self._client is None:
            raise SessionError("尚未登录，请先登录统一认证")
        return self._client

    @property
    def cookies(self) -> dict[str, str]:
        return dict(self._cookies)

    def pending_mfa(self) -> bool:
        return self._pending is not None

    def status(self) -> dict:
        cookies = self._cookies
        tgc = cookies.get("TGC", "")
        return {
            # 有 client 且最近检测未失效；None（未检测）按已登录展示，后台会自动检测
            "logged_in": self._client is not None and self._valid is not False,
            "session_expired": self._valid is False,
            "tgc_valid": self._valid,
            "valid_checked_at": (time.strftime("%Y-%m-%d %H:%M:%S",
                                               time.localtime(self._valid_checked_at))
                                 if self._valid_checked_at else None),
            "username": self._meta.get("username") or self.credentials.username,
            "cas_username": self.credentials.username,
            "cas_configured": bool(self.credentials.username and self.credentials.password),
            "login_time": self._meta.get("login_time"),
            "final_url": self._meta.get("final_url"),
            "fingerprint_saved": bool(self._meta.get("fingerprint") or self.credentials.fingerprint),
            "fingerprint": self.credentials.fingerprint or "",
            "mfa_used": self._meta.get("mfa_used"),
            "cookie_names": sorted(cookies),
            "tgc_masked": (tgc[:18] + "..." + tgc[-8:]) if len(tgc) > 30 else tgc,
            "pending_mfa": self._pending is not None,
            "pending_since": (time.strftime("%Y-%m-%d %H:%M:%S",
                                            time.localtime(self._pending["created"]))
                              if self._pending else None),
            "session_generation": self.generation,
            "last_error": self._last_error,
            "debug": self.debug,
        }

    # ------------------------------------------------------------------ 登录
    def _tgc_valid(self) -> bool:
        if not self._cookies:
            self._valid = False
            self._valid_checked_at = time.time()
            return False
        try:
            ok = bool(L.check_saved_session(self._cookies))
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] 校验 TGC 失败: %s", self.label, e)
            # 网络异常时不判定失效，保留上次结果
            return self._valid is not False
        self._valid = ok
        self._valid_checked_at = time.time()
        if not ok:
            self._last_error = "统一认证会话已失效（TGC 无效），请重新登录"
            log.warning("[%s] 统一认证会话已失效（TGC 无效）", self.label)
        return ok

    def check_valid(self, force: bool = False, ttl: float = 30.0, timeout: int = 8) -> bool:
        """检测 CAS 会话是否仍有效（带缓存，默认 30 秒内不重复请求）.

        * force=True：忽略缓存立即检测（后台「检测会话状态」按钮用）；
        * 网络异常时不把会话判定为失效（避免误报掉线）。
        """
        with self._lock:
            if self._client is None:
                self._valid = False
                self._valid_checked_at = time.time()
                return False
            if (not force and self._valid is not None
                    and self._valid_checked_at
                    and time.time() - self._valid_checked_at < ttl):
                return bool(self._valid)
            cookies = dict(self._cookies)
        try:
            ok = bool(L.check_saved_session(cookies, timeout=timeout))
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] 检测统一认证会话失败（网络异常）: %s", self.label, e)
            return bool(self._valid) if self._valid is not None else True
        with self._lock:
            self._valid = ok
            self._valid_checked_at = time.time()
            if not ok:
                self._last_error = "统一认证会话已失效（TGC 无效），请重新登录"
                log.warning("[%s] 统一认证会话已失效（TGC 无效）", self.label)
        return ok


    def _debug(self, stage: str, result: str | None = None, error: str | None = None) -> None:
        self.debug.update({
            "stage": stage,
            "result": result,
            "error": error,
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })

    def start_login(self, force: bool = False) -> dict:
        """开始一次登录（后台界面/接口共用）.

        返回:
          * {"status": "ok"}             —— 直接登录成功（或复用有效会话）
          * {"status": "need_mfa"}       —— 需要企业微信验证码，调用 complete_mfa(code)
          * {"status": "error", "error"} —— 失败
        """
        with self._lock:
            if not force and self._client is not None and self._tgc_valid():
                self._debug("reuse", "ok")
                return {"status": "ok", "reused": True, "session": self.status()}
            if not self.credentials.username or not self.credentials.password:
                err = "未配置统一认证账号/密码"
                self._last_error = err
                self._debug("login", "error", err)
                return {"status": "error", "error": err}

            log.info("[%s] 开始 CAS 登录: username=%s", self.label,
                     self.credentials.username)
            self.debug = {"stage": "start", "captcha": [], "result": None, "error": None,
                          "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            self._last_error = None
            self._pending = None
            client = L.HttpClient()
            try:
                html, (execution, salt) = L.fetch_login_page(client)
            except Exception as e:  # noqa: BLE001
                self._last_error = "获取登录页失败: %s" % e
                self._debug("login_page", "error", self._last_error)
                return {"status": "error", "error": self._last_error}

            try:
                token = L.get_captcha_token(client, verbose=False, debug=self.debug["captcha"])
            except Exception as e:  # noqa: BLE001
                self._last_error = "滑块验证码识别失败: %s" % e
                self._debug("captcha", "error", self._last_error)
                return {"status": "error", "error": self._last_error}

            encrypted = L.aes_cbc_encrypt_b64(self.credentials.password, salt)
            fields = {
                "username": self.credentials.username,
                "password": encrypted,
                "execution": execution,
                "_eventId": "submit",
                "captcha": token,
                "browserFingerprint": self.credentials.fingerprint or uuid.uuid4().hex,
            }
            try:
                status, final_url, body = client.post_form(L.LOGIN_URL, fields)
            except Exception as e:  # noqa: BLE001
                self._last_error = "提交登录失败: %s" % e
                self._debug("submit", "error", self._last_error)
                return {"status": "error", "error": self._last_error}
            text = body.decode("utf-8", "replace")

            # 情况一：需要企业微信二次验证
            if L.is_mfa_page(text):
                try:
                    exec2 = L.extract_mfa_execution(text)
                except Exception as e:  # noqa: BLE001
                    self._last_error = "二次验证页面解析失败: %s" % e
                    self._debug("mfa_page", "error", self._last_error)
                    return {"status": "error", "error": self._last_error}
                self._pending = {"client": client, "execution": exec2, "created": time.time()}
                self._debug("mfa_required", "need_mfa")
                log.info("[%s] 账号密码验证通过，进入企业微信二次验证", self.label)
                return {"status": "need_mfa", "session": self.status()}

            # 情况二：直接登录成功
            if L.is_logged_in(final_url, text, client.cookies_dict()):
                self._persist_login(client, final_url, mfa_used=False)
                self._debug("success", "ok")
                log.info("[%s] 登录成功: %s", self.label, final_url)
                return {"status": "ok", "reused": False, "final_url": final_url,
                        "session": self.status()}

            # 情况三：账号密码错误等
            err = L.parse_page_error(text) or "账号密码错误或登录失败"
            self._last_error = err
            self._debug("submit", "error", err)
            log.warning("[%s] 登录失败: %s", self.label, err)
            return {"status": "error", "error": err}

    def complete_mfa(self, code: str) -> dict:
        """提交企业微信验证码（后台界面输入验证码用）."""
        with self._lock:
            if not self._pending:
                raise SessionError("当前没有等待验证码的登录（请先点击「开始登录」）")
            if not code or len(code.strip()) < 4:
                return {"status": "mfa_error", "error": "验证码格式不正确"}
            p = self._pending
            fields = {"execution": p["execution"], "_eventId": "submit", "code": code.strip()}
            status, final_url, body = p["client"].post_form(L.LOGIN_URL, fields)
            text = body.decode("utf-8", "replace")
            if L.is_mfa_page(text):
                try:
                    p["execution"] = L.extract_mfa_execution(text)
                except Exception:  # noqa: BLE001
                    pass
                err = L.parse_page_error(text) or "验证码错误"
                self._debug("mfa_submit", "error", err)
                self._last_error = err
                log.warning("[%s] 二次验证失败: %s", self.label, err)
                return {"status": "mfa_error", "error": err, "session": self.status()}
            if L.is_logged_in(final_url, text, p["client"].cookies_dict()):
                self._persist_login(p["client"], final_url, mfa_used=True)
                self._debug("success", "ok")
                log.info("[%s] 二次验证通过，登录成功: %s", self.label, final_url)
                return {"status": "ok", "mfa_used": True, "final_url": final_url,
                        "session": self.status()}
            err = L.parse_page_error(text) or "二次验证失败"
            self._debug("mfa_submit", "error", err)
            self._last_error = err
            return {"status": "error", "error": err}

    def resend_mfa(self) -> dict:
        """重新发送企业微信验证码."""
        with self._lock:
            if not self._pending:
                raise SessionError("当前没有等待验证码的登录")
            p = self._pending
            fields = {"execution": p["execution"], "_eventId": "sendAuth"}
            status, _final_url, body = p["client"].post_form(L.LOGIN_URL, fields)
            text = body.decode("utf-8", "replace")
            if L.is_mfa_page(text):
                try:
                    p["execution"] = L.extract_mfa_execution(text)
                except Exception:  # noqa: BLE001
                    pass
                self._debug("mfa_resend", "sent")
                log.info("[%s] 已重新发送企业微信验证码", self.label)
                return {"status": "resent"}
            return {"status": "error", "error": "重发验证码返回异常页面"}

    def ensure_login(self, force: bool = False, mfa_code: str | None = None) -> dict:
        """确保存在可用会话（供 API 调用）：必要时自动登录，可带 mfa_code 一步完成."""
        with self._lock:
            if not force and self._client is not None and self._tgc_valid():
                return {"ok": True, "reused": True, **self.status()}

            if not self.credentials.username or not self.credentials.password:
                self._last_error = "缺少统一认证账号/密码，无法自动登录"
                raise SessionError(self._last_error)
            res = self.start_login(force=True)
            if res["status"] == "need_mfa":
                if mfa_code:
                    r2 = self.complete_mfa(mfa_code)
                    if r2["status"] != "ok":
                        raise SessionError(r2.get("error") or "二次验证失败",
                                           mfa_required=self.pending_mfa())
                    return {"ok": True, "reused": False, "mfa_used": True, **self.status()}
                raise SessionError(
                    "登录需要企业微信二次验证码：请到后台界面输入验证码，"
                    "或调用 POST /api/v1/auth/login 并带上 mfa_code",
                    mfa_required=True)
            if res["status"] != "ok":
                raise SessionError(res.get("error") or "登录失败")
            return {"ok": True, "reused": bool(res.get("reused")),
                    "mfa_used": False, **self.status()}

    def clear_session(self) -> None:
        """清除当前会话（后台界面「退出登录/清除会话」）."""
        with self._lock:
            self._client = None
            self._cookies = {}
            self._meta = {}
            self._pending = None
            self._last_error = None
            self._valid = None
            self._valid_checked_at = None
            self.generation += 1
            try:
                os.remove(self.cookie_file)
            except OSError:
                pass
            self._debug("cleared", "ok")
            log.info("[%s] 会话已清除", self.label)
