# -*- coding: utf-8 -*-
"""签到助手（swmqdzsapp / 全校定位签到）适配器."""
from __future__ import annotations

import json
import logging
import urllib.parse

import lit_login as L
from ..geo import to_bd09
from ..session import SessionManager
from .base import BaseSystem, SystemError

log = logging.getLogger(__name__)


class QdzsSystem(BaseSystem):
    key = "qdzs"
    name = "签到助手（swmqdzsapp / 全校定位签到）"
    HOST = "https://xsfw.sec.lit.edu.cn"
    INDEX = HOST + "/xsfw/sys/swmqdzsapp/*default/index.do"
    APPID = "6390414391613368"
    APPNAME = "swmqdzsapp"
    INFO_PATH = "/xsfw/sys/swmqdzsapp/MobileJrqdController/getTodaySignInInfo.do"
    SIGN_PATH = "/xsfw/sys/swmqdzsapp/MobileJrqdController/doSignIn.do"
    ROLES_PATH = "/xsfw/sys/swpubapp/NewMobileAPIController/getUserRoles.do"
    ROLE_SET_PATH = "/xsfw/sys/swpubapp/NewMobileAPIController/setAppRole.do"
    service_url = INDEX

    def __init__(self, session: SessionManager):
        super().__init__(session)
        self._sso_ready = False
        self._generation = -1
        self._role = None

    def _post_data(self, path: str, payload: dict, referer: str | None = None):
        body = urllib.parse.urlencode({"data": json.dumps(payload, ensure_ascii=False,
                                                          separators=(",", ":"))})
        status, final, raw = self.session.client.post(
            self.HOST + path, body.encode("utf-8"),
            "application/x-www-form-urlencoded",
            headers={"Accept": "application/json, text/plain, */*"},
            referer=referer or self.INDEX,
        )
        text = raw.decode("utf-8", "replace")
        if status != 200:
            return status, {"code": str(status), "msg": text[:200]}
        try:
            return status, json.loads(text)
        except Exception as exc:  # noqa: BLE001
            raise SystemError("签到助手返回非 JSON 数据: %s (%s)" % (exc, text[:200]),
                              http_status=502)

    def _post_form(self, path: str, fields: dict):
        status, final, raw = self.session.client.post_form(
            self.HOST + path, fields, referer=self.INDEX)
        try:
            return status, json.loads(raw.decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            maybe = raw.decode("utf-8", "replace")[:200]
            raise SystemError("签到助手返回非 JSON 数据: %s (%s)" % (exc, maybe),
                              http_status=502)

    def ensure_session(self, force: bool = False) -> None:
        self.session.ensure_login()
        generation = self.session.generation
        with self.session.lock:
            if generation != self._generation:
                self._sso_ready = False
                self._role = None
                self._generation = generation
            if self._sso_ready and not force:
                return
            cas_url = L.BASE + "/authserver/login?service=" + urllib.parse.quote(
                self.INDEX, safe="")
            self.session.client.get(cas_url, headers={
                "User-Agent": L.UA, "Accept": "text/html,application/xhtml+xml,*/*"})
            self.session.client.get(self.INDEX, headers={
                "User-Agent": L.UA, "Accept": "text/html,application/xhtml+xml,*/*"})
            # 尝试设置应用角色；部分账号可能由移动端预置角色，失败也不立刻阻断，
            # 具体数据接口会给出 403/业务错误。
            try:
                status, data = self._post_form(
                    self.ROLES_PATH, {"APPID": self.APPID, "APPNAME": self.APPNAME})
                roles = data.get("data") or []
                role = next((r for r in roles if str(r.get("ROLENAME") or "") in
                             ("学生", "学生组", "签到学生")), None)
                if role is None and roles:
                    role = roles[0]
                if role:
                    _, set_data = self._post_form(self.ROLE_SET_PATH, {
                        "APPID": self.APPID, "APPNAME": self.APPNAME,
                        "ROLEID": role.get("ROLEID")})
                    if str(set_data.get("code")) in ("0", "200"):
                        self._role = role
            except Exception as exc:  # noqa: BLE001
                log.info("签到助手角色预置跳过: %s", exc)
            self._sso_ready = True

    def _info(self, rwbh: str) -> dict:
        status, data = self._post_data(self.INFO_PATH, {"RWBH": rwbh})
        if status == 403:
            raise SystemError(
                "当前账号没有「签到助手」应用权限（403）",
                code="QdzsForbidden", http_status=403,
                hint="请使用能在企业微信/浏览器正常打开「签到助手」的账号；"
                     "如页面正常但接口 403，请在后台重新登录该账号")
        if str(data.get("code")) not in ("0", "200"):
            raise SystemError("查询签到任务失败: %s" % data.get("msg"),
                              code=data.get("code"), http_status=502)
        return data.get("data") or {}

    def _sign(self, payload: dict) -> dict:
        status, data = self._post_data(self.SIGN_PATH, payload)
        if status == 403:
            raise SystemError("签到助手提交被拒绝（403）", code="QdzsForbidden",
                              http_status=403)
        return data

    @staticmethod
    def _location_list(data: dict) -> list[dict]:
        locations = []
        for item in data.get("DDLIST") or []:
            try:
                locations.append({
                    "name": str(item.get("DDMC") or ""),
                    "code": str(item.get("DDDM") or ""),
                    "latitude": float(item.get("WD")),
                    "longitude": float(item.get("JD")),
                    "radius": float(item.get("YXFW") or 0),
                })
            except (TypeError, ValueError):
                continue
        return locations

    @staticmethod
    def _distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        import math
        r = 6371000.0
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp = p2 - p1
        dl = math.radians(lng2 - lng1)
        h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(h))

    def today_signin(self, rwbh: str, latitude: float, longitude: float,
                     coord_type: str = "auto") -> dict:
        self.ensure_session()
        task = self._info(rwbh)
        rw = task.get("RWXX") or {}
        qd = task.get("QDXX") or {}
        locations = self._location_list(task)
        bd_lat, bd_lng = to_bd09(latitude, longitude, coord_type)

        nearest = None
        for item in locations:
            distance = self._distance(bd_lat, bd_lng, item["latitude"], item["longitude"])
            if nearest is None or distance < nearest["distance"]:
                nearest = {**item, "distance": round(distance, 1)}

        allow_outside = str(rw.get("SFFWWQD") or "0") == "1"
        in_range = bool(nearest and nearest["distance"] <= nearest["radius"])
        checks = {
            "taskEnded": str(rw.get("SFJS") or "0") == "1",
            "needToday": str(rw.get("JRSFXYQD") or "0") == "1",
            "inTime": str(rw.get("SFZSJDN") or "0") == "1",
            "alreadySigned": str(rw.get("SFQD") or "0") == "1",
            "allowOutside": allow_outside,
            "inRange": in_range,
            "needAttachment": str(rw.get("FJXX") or "0") == "1",
        }
        task_view = {
            "rwbh": rw.get("RWBH") or rwbh,
            "name": rw.get("RWMC") or "",
            "method": rw.get("QDFS_DISPLAY") or "",
            "period": rw.get("QDPL_DISPLAY") or "",
            "dateStart": rw.get("QDKSRQ") or "",
            "dateEnd": rw.get("QDJSRQ") or "",
            "timeStart": rw.get("QDKSSJ") or "",
            "timeEnd": rw.get("QDJSSJ") or "",
            "serverTime": rw.get("CURTIME") or "",
        }
        base = {"task": task_view, "checks": checks,
                "input": {"rwbh": rwbh, "latitude": latitude, "longitude": longitude,
                          "coordType": coord_type},
                "converted": {"latitude": round(bd_lat, 7),
                              "longitude": round(bd_lng, 7), "coordType": "bd09"},
                "location": nearest}
        reason = ""
        if checks["taskEnded"]:
            reason = "任务已结束"
        elif not checks["needToday"]:
            reason = "今天无需签到"
        elif checks["alreadySigned"]:
            reason = "今天已签到"
        elif not checks["inTime"]:
            reason = "当前时间不在签到时段内"
        elif checks["needAttachment"]:
            reason = "该任务需要填写附加信息，当前接口暂不支持"
        elif not in_range and not allow_outside:
            reason = "当前位置不在签到范围内"
        if reason:
            return {"code": 0, "msg": "ok", "data": {
                **base, "submitted": False, "reason": reason}}

        payload = {
            "RWBH": rwbh,
            "SFFWN": "1" if in_range else "0",
            "DDMC": (nearest["name"] if in_range else
                     (nearest["name"] if nearest else "当前位置")),
            "QDJD": str(bd_lng),
            "QDWD": str(bd_lat),
        }
        if in_range and nearest and nearest.get("code"):
            payload["DDDM"] = nearest["code"]
        result = self._sign(payload)
        if str(result.get("code")) not in ("0", "200"):
            return {"code": 1, "msg": "签到失败: %s" % result.get("msg"),
                    "data": {**base, "submitted": False, "upstream": result}}
        latest = self._info(rwbh)
        return {"code": 0, "msg": "ok", "data": {
            **base,
            "submitted": True,
            "result": result.get("data") or {},
            "after": {"signed": str((latest.get("RWXX") or {}).get("SFQD") or "0") == "1",
                      "task": latest.get("RWXX") or {},
                      "record": latest.get("QDXX") or {}},
        }}
