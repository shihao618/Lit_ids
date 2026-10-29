"""学工系统 xsfw.sec.lit.edu.cn（WIS 移动平台）适配器.

已验证流程（2026-10 实测）:
  1. CAS SSO:  GET https://ids.lit.edu.cn/authserver/login?service=<xsfw 首页>
               -> 302 到 xsfw 首页?ticket=ST-xxx -> xsfw 下发 JSESSIONID/_WEU/route
  2. 选择角色:  POST /xsfw/sys/swpubapp/NewMobileAPIController/getUserRoles.do  (data={"APPID":...})
               POST /xsfw/sys/swpubapp/NewMobileAPIController/setAppRole.do
               没选角色时 /xsfw/sys/<app>/modules/** 全部 403
  3. 业务接口:  POST /xsfw/sys/swmzncqapp/modules/sjkb/getSsgqStudent.do
               body: data={"SEARCHKEY":"","pageNumber":1,"pageSize":10,"CQZT":"1"}

注意：WIS 移动端接口的参数统一为表单字段 ``data=<JSON 字符串>``，不是平铺字段。
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
    name = "学工系统（xsfw.sec.lit.edu.cn / WIS 移动平台）"
    HOST = "https://xsfw.sec.lit.edu.cn"
    INDEX = HOST + "/xsfw/sys/swmzncqapp/*default/index.do"
    SWROOT = HOST + "/xsfw/sys/swpubapp"
    APPID = "5405362541914944"
    APPNAME = "swmzncqapp"
    service_url = INDEX

    #: 可能拥有「数据看板」权限的角色（按优先级）
    PREFERRED_ROLES = ("班级管理员", "辅导员", "班主任", "教师")

    #: 数据看板-学生归寝（getSsgqStudent）用到的接口前缀
    SJKB = "/xsfw/sys/swmzncqapp/modules/sjkb/"

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
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": self.HOST,
            "Referer": referer or self.INDEX,
            "X-Requested-With": "XMLHttpRequest",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

    def _post_data(self, path: str, obj: dict, referer: str | None = None) -> tuple[int, str]:
        """按 WIS 规范 POST：表单字段 data=<JSON>."""
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
                                  hint="xsfw 需要「班级管理员」等有数据看板权限的角色，可用 role 参数指定")
            if status == 401 or "Not login" in text:
                raise SystemError("xsfw 会话未登录（401）: %s" % what,
                                  code="XSFW_NOT_LOGIN", http_status=502,
                                  hint="系统会自动重新 SSO；若持续失败请检查 CAS 会话是否失效")
            raise SystemError("xsfw 接口返回异常 HTTP %s: %s" % (status, what),
                              code="UPSTREAM_BAD_STATUS", http_status=502,
                              hint=text[:200])
        try:
            return json.loads(text)
        except ValueError as e:
            raise SystemError("xsfw 返回非 JSON 数据: %s" % e,
                              code="UPSTREAM_BAD_JSON", http_status=502,
                              hint=text[:200]) from e

    # ------------------------------------------------------------------ 会话 / 角色
    def ensure_session(self, force: bool = False) -> None:
        """确保 CAS 会话 + xsfw SSO + 角色上下文就绪."""
        with self.session.lock:
            self.session.ensure_login()
            # CAS 会话变了（重新登录/清除），本系统的 SSO 也要重建
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
                raise SystemError("CAS SSO 到 xsfw 失败（可能 CAS 会话已失效）",
                                  code="SSO_FAILED", http_status=502,
                                  hint=text[:160])
            # 2) 打开 app 首页，建立 xsfw 会话
            client.get(self.INDEX, headers={"User-Agent": L.UA, "Accept": "text/html,*/*"})

            self._sso_ready = True
            self._role = None
            self._roles = []
            self._menu = []
            log.info("xsfw SSO 完成: %s", final_url[:120])

    def roles(self, refresh: bool = False) -> list[dict]:
        self.ensure_session()
        if refresh or not self._roles:
            status, text = self._post_data(
                self.SWROOT + "/NewMobileAPIController/getUserRoles.do", {"APPID": self.APPID})
            data = self._parse_json(status, text, "getUserRoles")
            if str(data.get("code")) not in ("0", "200"):
                raise SystemError("获取 xsfw 角色失败: %s" % data.get("msg"),
                                  code=data.get("code"), http_status=502)
            self._roles = data.get("data") or []
        return self._roles

    def _choose_role(self, role_name: str | None) -> dict:
        roles = self.roles()
        if not roles:
            raise SystemError("该账号在 xsfw 没有任何角色", code="NO_ROLE", http_status=403)
        if role_name:
            for r in roles:
                if role_name in (r.get("ROLENAME"), r.get("DATAROLE"), r.get("ROLEID")):
                    return r
            raise SystemError("xsfw 没有角色 %r" % role_name, code="ROLE_NOT_FOUND",
                              http_status=400,
                              hint="可选角色: %s" % [r.get("ROLENAME") for r in roles])
        # 未指定：优先有数据看板权限的角色，其次第一个
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
            raise SystemError("设置 xsfw 角色失败: %s" % data.get("msg"),
                              code=data.get("code"), http_status=502)
        self._role = role
        # 菜单（顺带获取，便于调试/扩展）
        try:
            st, tx = self._post_data(
                self.SWROOT + "/NewMobileAPIController/getMenuInfo.do",
                {"APPID": self.APPID, "APPNAME": self.APPNAME, "ROLEID": role["ROLEID"]})
            menu = self._parse_json(st, tx, "getMenuInfo")
            self._menu = menu.get("data") or []
        except SystemError:
            self._menu = []
        log.info("xsfw 角色已设置: %s (%s), 菜单: %s", role.get("ROLENAME"),
                 role.get("DATAROLE"), [m.get("GNMC") for m in self._menu])
        return role

    def current_role(self) -> dict | None:
        return dict(self._role) if self._role else None

    def menu(self) -> list[dict]:
        return [dict(m) for m in self._menu]

    # ------------------------------------------------------------------ 业务调用
    def call(self, path: str, data: dict | None = None) -> dict:
        """调用 xsfw 任意接口. path 形如 /xsfw/sys/swmzncqapp/modules/sjkb/getSsgqStudent.do."""
        self._validate_path(path)
        with self.session.lock:
            self.ensure_session()
            if self._role is None:
                self.set_role()
            status, text = self._post_data(path, data or {})
            # 会话失效（401/302 到登录）时自动重建一次
            if status == 401 or "Not login" in text:
                log.warning("xsfw 会话失效，重建 SSO 后重试一次")
                self._sso_ready = False
                self.ensure_session(force=True)
                self.set_role(self._role.get("ROLENAME") if self._role else None)
                status, text = self._post_data(path, data or {})
            return self._parse_json(status, text, path)

    @staticmethod
    def _validate_path(path: str) -> None:
        if not path.startswith("/xsfw/sys/"):
            raise SystemError("path 必须以 /xsfw/sys/ 开头", code="BAD_PATH", http_status=400)
        if ".." in path or "://" in path or "?" in path:
            raise SystemError("path 非法", code="BAD_PATH", http_status=400)

    # ------------------------------------------------------------------ 数据看板（sjkb）
    def get_ssgq_students(self, searchkey: str = "", page_number: int = 1,
                          page_size: int = 10, cqzt: str = "",
                          cqztlx: str = "", role: str | None = None) -> dict:
        """数据看板-学生归寝查询（getSsgqStudent），参数与官方接口一致.

        :param searchkey:   官方参数 SEARCHKEY，搜索关键字
        :param page_number: 官方参数 pageNumber
        :param page_size:   官方参数 pageSize
        :param cqzt:        官方参数 CQZT：1=未归寝 2=已归寝 3=离校请假，空=全部
        :param cqztlx:      官方参数 CQZTLX：11=未联系上 12=已报备 21=正常归寝 22=晚归
        :param role:        角色名（网关内部使用，默认自动选择有数据看板权限的角色）
        :return: 上游原始响应（{"code":"0","msg":"成功","data":[...]}）
        """
        payload = {"SEARCHKEY": searchkey, "pageNumber": page_number, "pageSize": page_size}
        if cqzt:
            payload["CQZT"] = cqzt
        if cqztlx:
            payload["CQZTLX"] = cqztlx
        # 显式指定角色时按其执行；未指定时用默认角色（有数据看板权限的那个）
        self.set_role(role)
        try:
            return self.call(self.SJKB + "getSsgqStudent.do", payload)
        except SystemError as e:
            # 若当前角色无权限，而本身未指定角色，则切回默认角色重试一次
            if e.code == "FORBIDDEN" and role is None:
                preferred = self._choose_role(None)
                if not self._role or preferred.get("ROLEID") != self._role.get("ROLEID"):
                    self.set_role(preferred.get("ROLENAME"))
                    return self.call(self.SJKB + "getSsgqStudent.do", payload)
            raise

    def get_cqzt_dic(self) -> dict:
        """查寝状态字典（官方 getCqztDic.do），返回上游原始响应."""
        return self.call(self.SJKB + "getCqztDic.do", {})

    def get_cqztlx_dic(self, cqzt: str = "") -> dict:
        """查寝状态类型字典（官方 getCqztlxDic.do），返回上游原始响应.

        :param cqzt: 官方参数 CQZT，可只查某个状态下的类型
        """
        payload = {"CQZT": cqzt} if cqzt else {}
        return self.call(self.SJKB + "getCqztlxDic.do", payload)
