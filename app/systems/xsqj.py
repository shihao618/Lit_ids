# -*- coding: utf-8 -*-
"""学生请销假（swmxsqjapp）适配器."""
from __future__ import annotations

import json
import logging
import math
import time
import urllib.parse
from datetime import datetime

import lit_login as L
from ..geo import to_bd09
from ..session import SessionManager
from .base import BaseSystem, SystemError

log = logging.getLogger(__name__)


class XsqjSystem(BaseSystem):
    key = "xsqj"
    name = "学生请销假（swmxsqjapp）"
    HOST = "https://xsfw.sec.lit.edu.cn"
    INDEX = HOST + "/xsfw/sys/swmxsqjapp/*default/index.do"
    APPID = "5027685304497491"
    APPNAME = "swmxsqjapp"
    LIST_PATH = "/xsfw/sys/swmxsqjapp/MobileServiceStudentApply/getStudentLeaveRecords.do"
    DW_INFO_PATH = "/xsfw/sys/swmxsqjapp/MobileServiceStudentApply/queryDWXJInfo.do"
    CANCEL_PATH = "/xsfw/sys/swmxsqjapp/MobileServiceStudentApply/setDWXJ.do"
    SEL_ROLE_PATH = "/xsfw/sys/swpubapp/MobileCommon/getSelRoleConfig.do"
    SET_ROLE_PATH = "/xsfw/sys/swpubapp/MobileCommon/setAppRole.do"
    service_url = INDEX

    def __init__(self, session: SessionManager):
        super().__init__(session)
        self._sso_ready = False
        self._generation = -1
        self._role: dict | None = None

    def _post_data(self, path: str, payload: dict) -> tuple[int, dict]:
        body = urllib.parse.urlencode({
            "data": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        }).encode("utf-8")
        status, final, raw = self.session.client.post(
            self.HOST + path, body, "application/x-www-form-urlencoded",
            headers={"Accept": "application/json, text/plain, */*"},
            referer=self.INDEX)
        text = raw.decode("utf-8", "replace")
        if status != 200:
            return status, {"code": str(status), "msg": text[:200]}
        try:
            return status, json.loads(text)
        except Exception as exc:  # noqa: BLE001
            raise SystemError("学生请销假返回非 JSON 数据: %s" % exc,
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
            self.session.client.get(
                L.BASE + "/authserver/login?service=" +
                urllib.parse.quote(self.INDEX, safe=""),
                headers={"User-Agent": L.UA,
                         "Accept": "text/html,application/xhtml+xml,*/*"})
            self.session.client.get(self.INDEX, headers={
                "User-Agent": L.UA,
                "Accept": "text/html,application/xhtml+xml,*/*"})
            try:
                _, sel = self._post_data(self.SEL_ROLE_PATH, {
                    "APPID": self.APPID, "APPNAME": self.APPNAME})
                data = sel.get("data") or {}
                role_id = data.get("DEFAULT_ROLEID") or ""
                if str(data.get("IS_NEED_SELECTROLE") or "") == "1":
                    role_id = next((x.get("id") for x in (data.get("ROLELIST") or [])
                                    if x.get("id")), role_id)
                if role_id:
                    _, set_data = self._post_data(self.SET_ROLE_PATH, {
                        "APPID": self.APPID, "APPNAME": self.APPNAME,
                        "ROLEID": role_id})
                    if str(set_data.get("code")) in ("0", "200"):
                        self._role = {"ROLEID": role_id}
            except Exception as exc:  # noqa: BLE001
                log.info("学生请销假角色预置跳过: %s", exc)
            self._sso_ready = True

    def leave_records(self) -> dict:
        self.ensure_session()
        _, data = self._post_data(self.LIST_PATH, {})
        if str(data.get("code")) not in ("0", "200"):
            raise SystemError("查询请假列表失败: %s" % data.get("msg"),
                              code=data.get("code"), http_status=502)
        rows = data.get("data") or []
        items = [self._map_leave(row) for row in rows if isinstance(row, dict)]
        return {"code": 0, "msg": "ok",
                "data": {"total": len(items), "items": items}}

    @staticmethod
    def _map_leave(row: dict) -> dict:
        return {
            "leaveId": row.get("SQBH") or "",
            "applyNo": row.get("QJXLH") or "",
            "leaveType": row.get("QJLX_DISPLAY") or row.get("QJLX") or "",
            "leaveTypeCode": row.get("QJLX") or "",
            "reason": row.get("QJSY") or "",
            "startTime": row.get("QJKSRQ") or "",
            "endTime": row.get("QJJSRQ") or "",
            "days": row.get("QJTS"),
            "applyTime": row.get("SQRQ") or "",
            "status": row.get("SHZT") or "",
            "statusLabel": row.get("SHZT_DISPLAY") or "",
            "cancelStatus": row.get("XJZT") or "",
            "cancelStatusLabel": row.get("XJZT_DISPLAY") or "",
            "cancelTime": row.get("XJRQ") or "",
            "leaveScope": row.get("QJXZ_DISPLAY") or "",
        }

    @staticmethod
    def _distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        r = 6371000.0
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp = p2 - p1
        dl = math.radians(lng2 - lng1)
        h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(h))

    def _detect_coords(self, latitude: float, longitude: float,
                       coord_type: str, locations: list[dict]) -> tuple[float, float, str]:
        kind = (coord_type or "auto").strip().lower()
        if kind != "auto":
            bd_lat, bd_lng = to_bd09(latitude, longitude, kind)
            return bd_lat, bd_lng, kind
        candidates = []
        for candidate_kind in ("wgs84", "gcj02", "bd09"):
            cand_lat, cand_lng = to_bd09(latitude, longitude, candidate_kind)
            nearest = None
            for item in locations:
                distance = self._distance(cand_lat, cand_lng,
                                          item["latitude"], item["longitude"])
                if nearest is None or distance < nearest:
                    nearest = distance
            candidates.append((nearest if nearest is not None else 1e18,
                               candidate_kind, cand_lat, cand_lng))
        best = min(candidates, key=lambda item: item[0])
        return best[2], best[3], best[1]

    def location_cancel(self, latitude: float, longitude: float,
                        coord_type: str = "auto") -> dict:
        """自动定位销假：自动挑选已通过、未销假、已到结束时间的请假记录."""
        self.ensure_session()
        records = self.leave_records()["data"]["items"]
        now = datetime.now()
        candidates = []
        for record in records:
            if str(record.get("status") or "") != "99":
                continue
            if str(record.get("cancelStatus") or "0") != "0":
                continue
            try:
                end_time = datetime.strptime(
                    str(record.get("endTime") or ""), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if end_time <= now:
                candidates.append(record)
        if not candidates:
            return {"code": 0, "msg": "ok", "data": {
                "submitted": False,
                "reason": "没有已通过、未销假且已结束的请假记录",
                "now": now.strftime("%Y-%m-%d %H:%M:%S"),
            }}
        candidates.sort(key=lambda item: item.get("endTime") or "")

        _, info = self._post_data(self.DW_INFO_PATH, {})
        if str(info.get("code")) not in ("0", "200"):
            raise SystemError("查询定位销假配置失败: %s" % info.get("msg"),
                              code=info.get("code"), http_status=502)
        data = info.get("data") or {}
        if str(data.get("KQDWXJ") or "0") != "1":
            return {"code": 0, "msg": "ok", "data": {
                "submitted": False, "reason": "当前未开启定位销假",
                "candidates": candidates}}
        locations = []
        for item in data.get("location") or []:
            try:
                locations.append({
                    "name": str(item.get("DWMC") or ""),
                    "latitude": float(item.get("WDZB")),
                    "longitude": float(item.get("JDZB")),
                    "radius": float(item.get("YXPCJL") or 0),
                })
            except (TypeError, ValueError):
                continue
        bd_lat, bd_lng, detected_type = self._detect_coords(
            latitude, longitude, coord_type, locations)
        nearest = None
        for item in locations:
            distance = self._distance(bd_lat, bd_lng,
                                      item["latitude"], item["longitude"])
            if nearest is None or distance < nearest["distance"]:
                nearest = {**item, "distance": round(distance, 1)}
        base = {
            "input": {"latitude": latitude, "longitude": longitude,
                      "coordType": coord_type},
            "converted": {"latitude": round(bd_lat, 7),
                          "longitude": round(bd_lng, 7), "coordType": "bd09"},
            "detectedCoordType": detected_type,
        }
        if not nearest or nearest["distance"] > nearest["radius"]:
            return {"code": 0, "msg": "ok", "data": {
                **base, "submitted": False, "reason": "不在定位销假范围",
                "candidates": candidates, "location": nearest}}

        cancelled, failed = [], []
        for record in candidates:
            leave_id = record.get("leaveId") or ""
            payload = {
                "SQBH": leave_id,
                "XJR": self.session.credentials.username,
                "XJRQ": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            if str(data.get("KQDWPZXJ") or "0") == "1":
                hzw = data.get("HZW") or []
                if not hzw:
                    failed.append({"leaveId": leave_id,
                                   "reason": "未配置合照"})
                    continue
                payload["HZSLTP"] = hzw[0].get("HZWSL") or ""
                payload["HZWMC"] = hzw[0].get("HZWMC") or ""
            status, result = self._post_data(self.CANCEL_PATH, payload)
            if status == 200 and str(result.get("code")) in ("0", "200"):
                cancelled.append(leave_id)
            else:
                failed.append({"leaveId": leave_id,
                               "reason": result.get("msg") or "销假失败"})

        latest_items = self.leave_records()["data"]["items"]
        latest_map = {x.get("leaveId"): x for x in latest_items}
        return {"code": 0, "msg": "ok", "data": {
            **base,
            "submitted": bool(cancelled),
            "count": len(candidates),
            "cancelled": [latest_map.get(x, {"leaveId": x}) for x in cancelled],
            "failed": failed,
            "location": {**nearest, "inRange": True},
        }}
