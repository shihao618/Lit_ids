"""智能查寝（xsfw.sec.lit.edu.cn / WIS 移动平台 zncqapp）适配器.

对外提供**自有契约** ``check_records``：
  * 请求只有：班级、查寝状态、查寝状态类型；
  * 内部自动组装官方 ``querySetting`` 并分页拉全；
  * 返回字段全部映射成易读名称/中文枚举（不再暴露官方原始字段）。

内部仍调用官方接口 ``POST /xsfw/sys/zncqapp/modules/sjkb/cqztbg.do``：
  body: querySetting=<JSON数组>&pageSize=200&pageNumber=N
  返回: {"code":"0","datas":{"cqztbg":{"totalSize":39,"rows":[...]}}}
"""

from __future__ import annotations

import json
import logging
import urllib.parse

import lit_login as L

from ..session import SessionManager
from .base import BaseSystem, SystemError

log = logging.getLogger(__name__)

#: 对外枚举 ↔ 官方 CQZT（查寝状态）
STATUS_OPTIONS: dict[str, tuple[str, str]] = {
    "not_returned": ("1", "未归寝"),
    "returned": ("2", "已归寝"),
    "leave": ("3", "离校请假"),
}

#: 对外枚举 ↔ 官方 CQZTLX（查寝状态类型）；leave 仅随「离校请假」状态出现
TYPE_OPTIONS: dict[str, tuple[str, str]] = {
    "unreachable": ("11", "未联系上"),
    "reported": ("12", "已报备"),
    "normal": ("21", "正常归寝"),
    "late": ("22", "晚归"),
    "leave": ("31", "离校请假"),
}


def _build_lookup(options: dict[str, tuple[str, str]]) -> dict[str, tuple[str, str]]:
    """英文枚举 / 官方数字代码 / 中文名称 都接受."""
    lookup: dict[str, tuple[str, str]] = {}
    for key, item in options.items():
        lookup[key] = item
        lookup[item[0]] = item
        lookup[item[1]] = item
    return lookup


STATUS_LOOKUP = _build_lookup(STATUS_OPTIONS)
TYPE_LOOKUP = _build_lookup(TYPE_OPTIONS)

STATUS_CODE_LABEL = {code: label for code, label in STATUS_OPTIONS.values()}
TYPE_CODE_LABEL = {code: label for code, label in TYPE_OPTIONS.values()}
STATUS_LABEL_KEY = {label: key for key, (_code, label) in STATUS_OPTIONS.items()}
TYPE_LABEL_KEY = {label: key for key, (_code, label) in TYPE_OPTIONS.items()}

#: 单页条数（官方上限 200；对外接口自动翻页拉全）
PAGE_SIZE = 200


class XsfwSystem(BaseSystem):
    key = "xsfw"
    name = "智能查寝（xsfw.sec.lit.edu.cn / zncqapp）"
    HOST = "https://xsfw.sec.lit.edu.cn"
    INDEX = HOST + "/xsfw/sys/zncqapp/*default/index.do"
    SWROOT = HOST + "/xsfw/sys/swpubapp"
    APPID = "5499550419006327"
    APPNAME = "zncqapp"
    service_url = INDEX

    #: 官方查寝表格接口（仅内部使用，不再对外暴露）
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
        client = self.session.client
        body = urllib.parse.urlencode(fields).encode()
        status, _final_url, raw = client.post(
            path if path.startswith("http") else self.HOST + path,
            body, "application/x-www-form-urlencoded",
            headers=self._headers(referer), referer=referer or self.INDEX)
        return status, raw.decode("utf-8", "replace")

    def _post_data(self, path: str, obj: dict, referer: str | None = None) -> tuple[int, str]:
        """swpubapp 的接口使用 WIS 规范：表单字段 data=<JSON>."""
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
        """确保 CAS 会话 + 智能查寝 SSO 就绪."""
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

            cas_url = L.BASE + "/authserver/login?service=" + urllib.parse.quote(self.INDEX, safe="")
            status, final_url, raw = client.get(
                cas_url, headers={"User-Agent": L.UA,
                                  "Accept": "text/html,application/xhtml+xml,*/*"})
            text = raw.decode("utf-8", "replace")
            if "/authserver/login" in final_url and "ticket=" not in final_url:
                raise SystemError("CAS SSO 到智能查寝失败（可能 CAS 会话已失效）",
                                  code="SSO_FAILED", http_status=502,
                                  hint=text[:160])
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

    # ------------------------------------------------------------------ 自有业务接口
    @staticmethod
    def _build_query_setting(filters: dict[str, str]) -> str:
        items = [{"name": name, "value": str(value),
                  "linkOpt": "AND", "builder": "equal"}
                 for name, value in filters.items() if value not in (None, "")]
        return json.dumps(items, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _resolve_choice(value: str, lookup: dict[str, tuple[str, str]],
                        field: str, options: dict[str, tuple[str, str]]) -> tuple[str, str]:
        text = (value or "").strip()
        if not text:
            return "", ""
        hit = lookup.get(text) or lookup.get(text.lower())
        if hit:
            return hit
        allowed = ", ".join("%s(%s)" % (key, label) for key, (_code, label) in options.items())
        raise SystemError("%s 无效: %s" % (field, text), code="BAD_PARAMS",
                          http_status=422, hint="可选值: " + allowed)

    @classmethod
    def _map_student(cls, row: dict) -> dict:
        """官方行 → 易读字段（枚举同时给出 code 和中文 label）."""
        status_code = str(row.get("CQZT") or "")
        status_label = str(row.get("CQZT_DISPLAY") or STATUS_CODE_LABEL.get(status_code) or status_code)
        type_code = str(row.get("CQZTLX") or "")
        type_label = str(row.get("CQZTLX_DISPLAY") or TYPE_CODE_LABEL.get(type_code) or type_code)
        return {
            "studentId": row.get("XH") or row.get("XSBH") or "",
            "name": row.get("XM") or "",
            "classCode": row.get("BJDM") or "",
            "className": row.get("BJDM_DISPLAY") or row.get("BJDM") or "",
            "collegeName": row.get("DWDM_DISPLAY") or row.get("DWDM") or "",
            "majorName": row.get("ZYDM_DISPLAY") or row.get("ZYDM") or "",
            "dormBuilding": row.get("SSLMC") or "",
            "roomNumber": row.get("FJH") or "",
            "bedNumber": row.get("CWH") or "",
            "phone": row.get("SJH") or "",
            "checkStatus": STATUS_LABEL_KEY.get(status_label, status_code),
            "checkStatusLabel": status_label,
            "checkStatusType": TYPE_LABEL_KEY.get(type_label, type_code),
            "checkStatusTypeLabel": type_label,
        }

    def _fetch_page(self, fields: dict) -> tuple[int, str]:
        try:
            status, text = self._post_form(self.CQZTBG_PATH, fields)
        except Exception as e:  # noqa: BLE001
            raise SystemError("调用智能查寝接口失败: %s" % e,
                              code="UPSTREAM_ERROR", http_status=502) from e
        if status == 401 or "Not login" in text:
            log.warning("智能查寝会话失效，重建 SSO 后重试一次")
            self._sso_ready = False
            self.ensure_session(force=True)
            self.set_role(None)
            status, text = self._post_form(self.CQZTBG_PATH, fields)
        return status, text

    def check_records(self, class_code: str = "", check_status: str = "",
                      check_status_type: str = "") -> dict:
        """智能查寝-查寝记录（自有参数/易读返回），自动分页拉全.

        :param class_code: 班级代码，如 B260714（空=当前账号可见范围）
        :param check_status: not_returned/returned/leave（也接受中文/官方数字）
        :param check_status_type: unreachable/reported/normal/late（也接受中文/官方数字）
        """
        status_code, _status_label = self._resolve_choice(
            check_status, STATUS_LOOKUP, "checkStatus", STATUS_OPTIONS)
        type_code, _type_label = self._resolve_choice(
            check_status_type, TYPE_LOOKUP, "checkStatusType", TYPE_OPTIONS)
        class_code = (class_code or "").strip()

        filters: dict[str, str] = {}
        if class_code:
            filters["BJDM"] = class_code
        if status_code:
            filters["CQZT"] = status_code
        if type_code:
            filters["CQZTLX"] = type_code
        query_setting = self._build_query_setting(filters)

        with self.session.lock:
            self.set_role(None)
            items: list[dict] = []
            total = 0
            page = 1
            while True:
                fields = {
                    "querySetting": query_setting,
                    "pageSize": str(PAGE_SIZE),
                    "pageNumber": str(page),
                }
                status, text = self._fetch_page(fields)
                data = self._parse_json(status, text, "cqztbg.do")
                if str(data.get("code")) not in ("0", "200"):
                    raise SystemError("查询查寝记录失败: %s" % data.get("msg"),
                                      code=data.get("code"), http_status=502)
                table = (data.get("datas") or {}).get("cqztbg") or {}
                try:
                    total = int(table.get("totalSize") or 0)
                except (TypeError, ValueError):
                    total = 0
                rows = table.get("rows") or []
                items.extend(self._map_student(row) for row in rows if isinstance(row, dict))
                if not rows or len(items) >= total or len(rows) < PAGE_SIZE:
                    break
                page += 1
                if page > 100:  # 安全上限（200*100=2 万条）
                    break

        return {"code": 0, "msg": "ok",
                "data": {"total": total, "items": items}}
