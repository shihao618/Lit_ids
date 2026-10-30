"""智能查寝（xsfw.sec.lit.edu.cn / WIS 移动平台 zncqapp）适配器.

已验证流程（2026-10 实测）:
  1. CAS SSO:  GET https://ids.lit.edu.cn/authserver/login?service=<智能查寝首页>
                -> 302 到 /xsfw/sys/zncqapp/*default/index.do?ticket=ST-xxx -> 下发 JSESSIONID/_WEU/route
  2. 选择角色:  POST /xsfw/sys/swpubapp/NewMobileAPIController/getUserRoles.do   (data={"APPID":...})
                POST /xsfw/sys/swpubapp/NewMobileAPIController/setAppRole.do
                没选角色时 /xsfw/sys/zncqapp/modules/** 全部 403
  3. 业务接口:  POST /xsfw/sys/zncqapp/modules/sjkb/cqztbg.do
                body: querySetting=<JSON数组>&BJDM=B260714&pageSize=10&pageNumber=1
                返回: {"code":"0","datas":{"cqztbg":{"totalSize":39,"pageSize":10,
                        "pageNumber":1,"rows":[...]}}}

注意：zncqapp 接口的参数是平铺表单字段（不是 data=<JSON>）。
"""

from __future__ import annotations

import json
import logging
import urllib.parse

import lit_login as L

from ..session import SessionManager
from .base import BaseSystem, SystemError

log = logging.getLogger(__name__)


class XsfwSystem(BaseSystem):
    key = "xsfw"
    name = "智能查寝（xsfw.sec.lit.edu.cn / zncqapp）"
    HOST = "https://xsfw.sec.lit.edu.cn"
    INDEX = HOST + "/xsfw/sys/zncqapp/*default/index.do"
    SWROOT = HOST + "/xsfw/sys/swpubapp"
    APPID = "5499550419006327"
    APPNAME = "zncqapp"
    service_url = INDEX

    #: 查寝表格接口路径
    CQZTBG_PATH = "/xsfw/sys/zncqapp/modules/sjkb/cqztbg.do"

    #: 可能拥有「数据看板」权限的角色（按优先级）
    PREFERRED_ROLES = ("班级管理员", "辅导员", "班主任", "教师")

    def __init__(self, session: SessionManager):
        super().__init__(session)
        self._sso_ready = False
        self._generation = -1
        self._roles: list[dict] = []
        self._role: dict | None = None
        self._menu: list[dict] = []

    # ------------------------------------------------------------------ 基础请求
    def _headers(self, referer: str | None = None) -> dict:
        return {
            "User-Agent": L.UA,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": self.HOST,
            "Referer": referer or self.INDEX,
            "X-Requested-With": "XMLHttpRequest",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

    def _post_form(self, path: str, fields: dict, referer: str | None = None) -> tuple[int, str]:
        """按 zncqapp 规范 POST 平铺表单字段（如 querySetting/BJDM/pageSize/pageNumber）."""
        client = self.session.client
        body = urllib.parse.urlencode(fields).encode()
        status, _final_url, raw = client.post(
            path if path.startswith("http") else self.HOST + path,
            body, "application/x-www-form-urlencoded",
            headers=self._headers(referer), referer=referer or self.INDEX)
        return status, raw.decode("utf-8", "replace")

    def _post_data(self, path: str, obj: dict, referer: str | None = None) -> tuple[int, str]:
        """swpubapp 的接口仍使用 WIS 规范：表单字段 data=<JSON>."""
        client = self.session.client
        body = urllib.parse.urlencode(
            {"data": json.dumps(obj, separators=(",", ":"), ensure_ascii=False)}).encode()
        status, _final_url, raw = client.post(
            path if path.startswith("http") else self.HOST + path,
            body, "application/x-www-form-urlencoded",
            headers=self._headers(referer), referer=referer or self.INDEX)
        return status, raw.decode("utf-8", "replace")

    @staticmethod
    def _parse_json(status: int, text: str, what: str) -> dict:
        if status != 200 or text.lstrip().startswith("<"):
            if status == 403:
                raise SystemError("无权访问 %s（403）: 当前角色没有该功能权限" % what,
                                  code="FORBIDDEN", http_status=403,
                                  hint="智能查寝需要「班级管理员」等有查寝权限的角色")
            if status == 401 or "Not login" in text:
                raise SystemError("智能查寝会话未登录（401）: %s" % what,
                                  code="XSFW_NOT_LOGIN", http_status=502,
                                  hint="系统会自动重新 SSO；若持续失败请检查 CAS 会话是否失效")
            raise SystemError("智能查寝接口返回异常 HTTP %s: %s" % (status, what),
                              code="UPSTREAM_BAD_STATUS", http_status=502,
                              hint=text[:200])
        try:
            return json.loads(text)
        except ValueError as e:
            raise SystemError("智能查寝返回非 JSON 数据: %s" % e,
                              code="UPSTREAM_BAD_JSON", http_status=502,
                              hint=text[:200]) from e

    # ------------------------------------------------------------------ 会话 / 角色
    def ensure_session(self, force: bool = False) -> None:
        """确保 CAS 会话 + 智能查寝 SSO + 角色上下文就绪."""
        with self.session.lock:
            self.session.ensure_login()
            if self._generation != self.session.generation:
                self._sso_ready = False
                self._role = None
                self._roles = []
                self._menu = []
                self._generation = self.session.generation
            if self._sso_ready and not force:
                return
            client = self.session.client

            # 1) CAS SSO
            cas_url = L.BASE + "/authserver/login?service=" + urllib.parse.quote(self.INDEX, safe="")
            status, final_url, raw = client.get(
                cas_url, headers={"User-Agent": L.UA,
                                  "Accept": "text/html,application/xhtml+xml,*/*"})
            text = raw.decode("utf-8", "replace")
            if "/authserver/login" in final_url and "ticket=" not in final_url:
                raise SystemError("CAS SSO 到智能查寝失败（可能 CAS 会话已失效）",
                                  code="SSO_FAILED", http_status=502,
                                  hint=text[:160])
            # 2) 打开应用首页，建立 xsfw 会话
            client.get(self.INDEX, headers={"User-Agent": L.UA, "Accept": "text/html,*/*"})

            self._sso_ready = True
            self._role = None
            self._roles = []
            self._menu = []
            log.info("智能查寝 SSO 完成: %s", final_url[:120])

    def roles(self, refresh: bool = False) -> list[dict]:
        self.ensure_session()
        if refresh or not self._roles:
            status, text = self._post_data(
                self.SWROOT + "/NewMobileAPIController/getUserRoles.do", {"APPID": self.APPID})
            data = self._parse_json(status, text, "getUserRoles")
            if str(data.get("code")) not in ("0", "200"):
                raise SystemError("获取角色失败: %s" % data.get("msg"),
                                  code=data.get("code"), http_status=502)
            self._roles = data.get("data") or []
        return self._roles

    def _choose_role(self, role_name: str | None) -> dict:
        roles = self.roles()
        if not roles:
            raise SystemError("该账号在智能查寝没有任何角色", code="NO_ROLE", http_status=403)
        if role_name:
            for r in roles:
                if role_name in (r.get("ROLENAME"), r.get("DATAROLE"), r.get("ROLEID")):
                    return r
            raise SystemError("没有角色 %r" % role_name, code="ROLE_NOT_FOUND",
                              http_status=400,
                              hint="可选角色: %s" % [r.get("ROLENAME") for r in roles])
        for pref in self.PREFERRED_ROLES:
            for r in roles:
                if pref in str(r.get("ROLENAME") or ""):
                    return r
        return roles[0]

    def set_role(self, role_name: str | None = None) -> dict:
        """选择并设置当前角色（WIS 平台必须有角色上下文，否则业务接口 403）."""
        self.ensure_session()
        role = self._choose_role(role_name)
        if self._role and self._role.get("ROLEID") == role.get("ROLEID"):
            return self._role
        status, text = self._post_data(
            self.SWROOT + "/NewMobileAPIController/setAppRole.do",
            {"APPID": self.APPID, "APPNAME": self.APPNAME, "ROLEID": role["ROLEID"]})
        data = self._parse_json(status, text, "setAppRole")
        if str(data.get("code")) not in ("0", "200"):
            raise SystemError("设置角色失败: %s" % data.get("msg"),
                              code=data.get("code"), http_status=502)
        self._role = role
        try:
            st, tx = self._post_data(
                self.SWROOT + "/NewMobileAPIController/getMenuInfo.do",
                {"APPID": self.APPID, "APPNAME": self.APPNAME, "ROLEID": role["ROLEID"]})
            menu = self._parse_json(st, tx, "getMenuInfo")
            self._menu = menu.get("data") or []
        except SystemError:
            self._menu = []
        log.info("智能查寝角色已设置: %s (%s), 菜单: %s", role.get("ROLENAME"),
                 role.get("DATAROLE"), [m.get("GNMC") for m in self._menu])
        return role

    def current_role(self) -> dict | None:
        return dict(self._role) if self._role else None

    def menu(self) -> list[dict]:
        return [dict(m) for m in self._menu]

    # ------------------------------------------------------------------ 业务接口
    @staticmethod
    def build_query_setting(filters: dict[str, str] | None = None) -> str:
        """按浏览器抓包格式生成 querySetting（数组元素：name/value/linkOpt/builder）."""
        items = []
        for name, value in (filters or {}).items():
            if value not in (None, ""):
                items.append({"name": name, "value": str(value),
                              "linkOpt": "AND", "builder": "equal"})
        return json.dumps(items, separators=(",", ":"), ensure_ascii=False)

    def get_cqztbg(self, bjdm: str = "", page_number: int = 1, page_size: int = 10,
                   query_setting: str | None = None, cqzt: str = "", cqztlx: str = "",
                   dwdm: str = "", gzxs: str = "", role: str | None = None) -> dict:
        """智能查寝-查寝状态表格（官方 cqztbg.do），返回上游原始响应.

        说明：服务端实际按 querySetting 过滤；直传的 BJDM/CQZT/CQZTLX/DWDM/GZXS
        在抓包中会出现，但实测不生效，因此网关在不传 querySetting 时会把它们
        合并成 querySetting（AND / equal）。
        """
        filters = {"BJDM": bjdm, "DWDM": dwdm, "CQZT": cqzt,
                   "CQZTLX": cqztlx, "GZXS": gzxs}
        fields = {
            "querySetting": query_setting or self.build_query_setting(filters),
            "BJDM": bjdm,
            "pageSize": str(page_size),
            "pageNumber": str(page_number),
        }
        for name, value in (("DWDM", dwdm), ("CQZT", cqzt),
                            ("CQZTLX", cqztlx), ("GZXS", gzxs)):
            if value not in (None, ""):
                fields[name] = str(value)
        self.set_role(role)
        try:
            status, text = self._post_form(self.CQZTBG_PATH, fields)
        except Exception as e:  # noqa: BLE001
            raise SystemError("调用智能查寝接口失败: %s" % e,
                              code="UPSTREAM_ERROR", http_status=502) from e
        # 会话失效时自动重建一次
        if status == 401 or "Not login" in text:
            log.warning("智能查寝会话失效，重建 SSO 后重试一次")
            self._sso_ready = False
            self.ensure_session(force=True)
            self.set_role(self._role.get("ROLENAME") if self._role else None)
            status, text = self._post_form(self.CQZTBG_PATH, fields)
        try:
            return self._parse_json(status, text, "cqztbg.do")
        except SystemError as e:
            if e.code == "FORBIDDEN" and role is None:
                preferred = self._choose_role(None)
                if not self._role or preferred.get("ROLEID") != self._role.get("ROLEID"):
                    self.set_role(preferred.get("ROLENAME"))
                    return self._parse_json(*self._post_form(self.CQZTBG_PATH, fields),
                                            "cqztbg.do")
            raise
