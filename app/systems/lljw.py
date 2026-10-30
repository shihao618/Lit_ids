"""教务系统（lljw.lit.edu.cn）适配器.

已验证流程（2026-10 实测）:
  1. CAS SSO: GET https://ids.lit.edu.cn/authserver/login?service=<lljw /new/ssoLogin>
              -> 302 到 https://lljw.lit.edu.cn/new/welcome.page -> 下发 lljw JSESSIONID
  2. 业务接口: POST https://lljw.lit.edu.cn/new/student/xsgrkb/getCalendarWeekDatas
              form: xnxqdm=202601&zc=7&d1=2026-10-26+00:00:00&d2=2026-11-02+00:00:00
              返回: {"code":0,"data":[{课程/时间/地点/教师...}]}
              未登录返回: {"code":-401,"message":"尚未登录，请先登录"}
"""

from __future__ import annotations

import json
import logging
import urllib.parse

import lit_login as L

from ..session import SessionManager
from .base import BaseSystem, SystemError

log = logging.getLogger(__name__)


class LljwSystem(BaseSystem):
    key = "lljw"
    name = "教务系统（lljw.lit.edu.cn）"
    HOST = "https://lljw.lit.edu.cn"
    SSO_PATH = "/new/ssoLogin"
    WEEK_PAGE = HOST + "/new/student/xsgrkb/week.page"
    API_PATH = "/new/student/xsgrkb/getCalendarWeekDatas"
    #: 该系统通过 CAS 登录时的入口（service 参数）
    service_url = HOST + SSO_PATH

    def __init__(self, session: SessionManager):
        super().__init__(session)
        self._sso_ready = False
        self._generation = -1

    # ------------------------------------------------------------------ 会话
    def ensure_session(self, force: bool = False) -> None:
        """确保完成 CAS -> 教务系统 SSO（lljw 会话）."""
        with self.session.lock:
            self.session.ensure_login()
            if self._generation != self.session.generation:
                self._sso_ready = False
                self._generation = self.session.generation
            if self._sso_ready and not force:
                return
            client = self.session.client
            cas_url = L.BASE + "/authserver/login?service=" + urllib.parse.quote(
                self.service_url, safe="")
            status, final_url, raw = client.get(
                cas_url, headers={"User-Agent": L.UA,
                                  "Accept": "text/html,application/xhtml+xml,*/*"})
            text = raw.decode("utf-8", "replace")
            if "/authserver/login" in final_url and "ticket=" not in final_url:
                raise SystemError("CAS SSO 到教务系统失败（可能 CAS 会话已失效）",
                                  code="SSO_FAILED", http_status=502,
                                  hint=text[:160])
            self._sso_ready = True
            log.info("教务系统 SSO 完成: %s", final_url[:120])

    # ------------------------------------------------------------------ 基础请求
    def _headers(self, referer: str) -> dict:
        return {
            "User-Agent": L.UA,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": referer,
        }

    def _post_form(self, fields: dict, referer: str) -> tuple[int, str]:
        client = self.session.client
        body = urllib.parse.urlencode(fields).encode()
        status, _final_url, raw = client.post(
            self.HOST + self.API_PATH, body,
            "application/x-www-form-urlencoded; charset=UTF-8",
            headers=self._headers(referer), referer=referer)
        return status, raw.decode("utf-8", "replace")

    @staticmethod
    def _parse(status: int, text: str) -> dict:
        if status != 200 or text.lstrip().startswith("<"):
            raise SystemError("教务系统接口返回异常 HTTP %s" % status,
                              code="UPSTREAM_BAD_STATUS", http_status=502,
                              hint=text[:200])
        try:
            return json.loads(text)
        except ValueError as e:
            raise SystemError("教务系统返回非 JSON 数据: %s" % e,
                              code="UPSTREAM_BAD_JSON", http_status=502,
                              hint=text[:200]) from e

    # ------------------------------------------------------------------ 业务接口
    def _referer(self, xnxqdm: str, zc: str, d1: str, d2: str) -> str:
        query = urllib.parse.urlencode({
            "szxqdm": "", "xnxqdm": xnxqdm, "yxdm": "",
            "zc": zc, "d1": d1, "d2": d2,
        })
        return self.WEEK_PAGE + "?" + query

    def get_calendar_week_datas(self, xnxqdm: str, zc: int | str,
                                d1: str, d2: str) -> dict:
        """教务系统-校历周数据（官方 getCalendarWeekDatas），返回上游原始响应.

        :param xnxqdm: 学年学期代码，如 202601
        :param zc:     周次，如 7
        :param d1:     开始日期时间，如 2026-10-26 00:00:00
        :param d2:     结束日期时间，如 2026-11-02 00:00:00
        """
        xnxqdm = str(xnxqdm or "").strip()
        zc_s = str(zc or "").strip()
        d1 = str(d1 or "").strip().replace("+", " ")
        d2 = str(d2 or "").strip().replace("+", " ")
        if not xnxqdm or not zc_s or not d1 or not d2:
            raise SystemError("教务系统参数不完整：需要 xnxqdm/zc/d1/d2",
                              code="BAD_PARAMS", http_status=422)
        fields = {"xnxqdm": xnxqdm, "zc": zc_s, "d1": d1, "d2": d2}
        referer = self._referer(xnxqdm, zc_s, d1, d2)
        with self.session.lock:
            self.ensure_session()
            try:
                status, text = self._post_form(fields, referer)
            except Exception as e:  # noqa: BLE001
                raise SystemError("调用教务系统接口失败: %s" % e,
                                  code="UPSTREAM_ERROR", http_status=502) from e
            data = self._parse(status, text)
            if data.get("code") == -401:
                # lljw 会话失效（例如重启后），CAS 仍有效时重新 SSO 一次
                log.warning("教务系统会话失效，重新 SSO 后重试一次")
                self._sso_ready = False
                self.ensure_session(force=True)
                status, text = self._post_form(fields, referer)
                data = self._parse(status, text)
            return data
