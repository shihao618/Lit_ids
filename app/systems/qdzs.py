# -*- coding: utf-8 -*-
"""签到助手（swmqdzsapp / 全校定位签到）适配器."""
from __future__ import annotations

import json
import logging
from datetime import date
import os
import urllib.parse
import urllib.request

import lit_login as L
from ..geo import bd09_to_mc, to_bd09
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
    TASKS_PATH = "/xsfw/sys/swmqdzsapp/MobileJrqdController/getJrqdList.do"
    RWLB_PATH = "/xsfw/sys/swmqdzsapp/MobileRwlbController/getRwlbList.do"
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
        self._last_rwbh = ""

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
            # getSelRoleConfig / setAppRole 使用 data=JSON 表单字段。
            try:
                _, sel = self._post_data(
                    "/xsfw/sys/swpubapp/MobileCommon/getSelRoleConfig.do",
                    {"APPID": self.APPID, "APPNAME": self.APPNAME})
                sel_data = sel.get("data") or {}
                role_id = sel_data.get("DEFAULT_ROLEID") or ""
                if str(sel_data.get("IS_NEED_SELECTROLE") or "") == "1":
                    roles = sel_data.get("ROLELIST") or []
                    role_id = next((r.get("id") for r in roles if r.get("id")), role_id)
                if role_id:
                    _, set_data = self._post_data(
                        "/xsfw/sys/swpubapp/MobileCommon/setAppRole.do",
                        {"APPID": self.APPID, "APPNAME": self.APPNAME,
                         "ROLEID": role_id})
                    if str(set_data.get("code")) in ("0", "200"):
                        self._role = {"ROLEID": role_id}
            except Exception as exc:  # noqa: BLE001
                log.info("签到助手角色预置跳过: %s", exc)
            self._sso_ready = True

    def _tasks(self) -> list[dict]:
        _, data = self._post_data(self.TASKS_PATH, {
            "querySetting": "[]", "pageNumber": 1, "pageSize": 10})
        if str(data.get("code")) not in ("0", "200"):
            raise SystemError("查询今日签到任务列表失败: %s" % data.get("msg"),
                              code=data.get("code"), http_status=502)
        return data.get("data") or []

    def _all_tasks(self) -> list[dict]:
        """今日任务为空时的兜底：任务列表（含已签到任务）."""
        _, data = self._post_data(self.RWLB_PATH, {
            "querySetting": "[]", "pageNumber": 1, "pageSize": 10})
        if str(data.get("code")) not in ("0", "200"):
            return []
        return data.get("data") or []

    def _select_today_task(self) -> str:
        """不传 RWBH 时，从任务列表中选择今天可用的任务."""
        from datetime import date
        today = date.today().isoformat()
        tasks = self._tasks()
        if not tasks:
            tasks = self._all_tasks()
        if not tasks and self._last_rwbh:
            return self._last_rwbh
        candidates = []
        for item in tasks:
            start = str(item.get("QDKSRQ") or "")
            end = str(item.get("QDJSRQ") or "")
            if start and start > today:
                continue
            if end and end < today:
                continue
            candidates.append(item)
        for item in candidates:
            rwbh = str(item.get("RWBH") or "")
            if not rwbh:
                continue
            try:
                info = self._info(rwbh)
            except SystemError:
                continue
            rw = info.get("RWXX") or {}
            if str(rw.get("JRSFXYQD") or "0") == "1" and str(rw.get("SFJS") or "0") != "1":
                self._last_rwbh = rwbh
                return rwbh
        if candidates:
            self._last_rwbh = str(candidates[0].get("RWBH") or "")
            return self._last_rwbh
        if tasks:
            self._last_rwbh = str(tasks[0].get("RWBH") or "")
            return self._last_rwbh
        return ""

    @staticmethod
    def _reverse_address(lat: float, lng: float) -> str:
        """用转换后的百度坐标反查 DDMC 地址；失败时返回空字符串."""
        try:
            x, y = bd09_to_mc(lat, lng)
            ak = os.environ.get("BAIDU_MAP_AK") or "pKJYS6YwnX5OIGnLdGmL1uEB8GzGuhQo"
            params = {
                "qt": "rgc", "x": "%.2f" % x, "y": "%.2f" % y,
                "dis_poi": "100", "poi_num": "10", "latest_admin": "1",
                "ie": "utf-8", "oue": "1", "fromproduct": "jsapi",
                "v": "2.1", "res": "api", "ak": ak,
            }
            url = "https://api.map.baidu.com/?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json, text/plain, */*",
            })
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            content = data.get("content") or {}
            address = str(content.get("address") or "").strip()
            poi = str(content.get("poi_desc") or "")
            if address:
                return address
            return poi.split(",")[0].strip()
        except Exception as exc:  # noqa: BLE001
            log.info("百度逆地理编码失败，DDMC 回退签到点名称: %s", exc)
            return ""

    @staticmethod
    def _distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        import math
        r = 6371000.0
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp = p2 - p1
        dl = math.radians(lng2 - lng1)
        h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(h))

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

    def checkin_locations(self) -> dict:
        """返回当前签到任务允许的签到地点（DDLIST）."""
        self.ensure_session()
        rwbh = self._select_today_task()
        if not rwbh:
            return {"code": 1, "msg": "没有可用的今日签到任务", "data": None}
        self._last_rwbh = rwbh
        task = self._info(rwbh)
        qdxx = task.get("QDXX") or {}
        items = []
        for item in task.get("DDLIST") or []:
            try:
                items.append({
                    # 信息使用官方字段，命名使用网关自有命名
                    "name": str(item.get("DDMC") or ""),
                    "longitude": float(item.get("JD")),
                    "latitude": float(item.get("WD")),
                    "checkin": qdxx,
                })
            except (TypeError, ValueError):
                continue
        return {"code": 0, "msg": "ok", "data": items}

    @staticmethod
    def _today_str() -> str:
        return date.today().isoformat()

    @staticmethod
    def _server_date(rw: dict) -> str:
        curtime = str(rw.get("CURTIME") or "")
        return curtime[:10] if len(curtime) >= 10 else ""

    @staticmethod
    def _is_today_record(qdxx: dict, today: str) -> bool:
        if not qdxx:
            return False
        qdrq = str(qdxx.get("QDRQ") or "").strip()
        qdsj = str(qdxx.get("QDSJ") or "").strip()
        return qdrq == today or qdsj[:10] == today

    @staticmethod
    def _checkin_fields(qdxx: dict) -> dict:
        out = {}
        if qdxx.get("DDMC"):
            out["location"] = str(qdxx.get("DDMC"))
        if qdxx.get("QDSJ"):
            out["time"] = str(qdxx.get("QDSJ"))
        for key, value in (("longitude", qdxx.get("QDJD")),
                           ("latitude", qdxx.get("QDWD"))):
            try:
                out[key] = float(value)
            except (TypeError, ValueError):
                pass
        return out

    def _today_tasks(self) -> list[dict]:
        """合并今日待签到任务与任务列表，按 RWBH 去重并限制在今天."""
        today = self._today_str()
        merged: dict[str, dict] = {}
        for loader in (self._tasks, self._all_tasks):
            try:
                items = loader()
            except SystemError:
                continue
            for item in items:
                rwbh = str(item.get("RWBH") or "").strip()
                if not rwbh:
                    continue
                start = str(item.get("QDKSRQ") or "")
                end = str(item.get("QDJSRQ") or "")
                if start and start > today:
                    continue
                if end and end < today:
                    continue
                merged.setdefault(rwbh, item)
        return list(merged.values())

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

    def today_signin(self, latitude: float, longitude: float,
                     coord_type: str = "auto") -> dict:
        """逐个任务查询、提交并回查今天的签到结果."""
        self.ensure_session()
        tasks = self._today_tasks()
        if not tasks:
            return {"code": 0, "msg": "ok", "data": {
                "status": "今天无需打卡", "items": []}}

        entries: list[dict] = []
        all_locations: list[dict] = []
        for task_item in tasks:
            rwbh = str(task_item.get("RWBH") or "").strip()
            task_name = str(task_item.get("RWMC") or "")
            try:
                info = self._info(rwbh)
            except SystemError as exc:
                entries.append({"rwbh": rwbh, "taskName": task_name,
                                "task": None, "error": str(exc)})
                continue
            rw = info.get("RWXX") or {}
            locs = self._location_list(info)
            all_locations.extend(locs)
            entries.append({"rwbh": rwbh,
                            "taskName": rw.get("RWMC") or task_name,
                            "task": info, "locations": locs,
                            "error": ""})

        bd_lat, bd_lng, _detected = self._detect_coords(
            latitude, longitude, coord_type, all_locations)
        reverse_cache = {"value": None}

        def reverse_address() -> str:
            if reverse_cache["value"] is None:
                reverse_cache["value"] = self._reverse_address(bd_lat, bd_lng)
            return reverse_cache["value"]

        items: list[dict] = []
        for entry in entries:
            task_name = entry["taskName"] or "未命名任务"
            if entry["error"] or entry["task"] is None:
                items.append({"taskName": task_name, "status": "提交失败"})
                continue
            task = entry["task"]
            rw = task.get("RWXX") or {}
            qdxx = task.get("QDXX") or {}
            today = self._server_date(rw) or self._today_str()
            base_item = {"taskName": task_name}
            if str(rw.get("SFJS") or "0") == "1":
                items.append({**base_item, "status": "任务已结束"})
                continue
            if str(rw.get("JRSFXYQD") or "0") != "1":
                items.append({**base_item, "status": "今天无需打卡"})
                continue
            if str(rw.get("SFQD") or "0") == "1" and self._is_today_record(qdxx, today):
                items.append({**base_item, "status": "已经打过卡了",
                              **self._checkin_fields(qdxx)})
                continue
            if str(rw.get("FJXX") or "0") == "1":
                items.append({**base_item, "status": "提交失败"})
                continue
            if str(rw.get("SFZSJDN") or "0") != "1":
                items.append({**base_item, "status": "不在签到时间"})
                continue

            locations = entry.get("locations") or []
            nearest = None
            for item in locations:
                distance = self._distance(bd_lat, bd_lng,
                                          item["latitude"], item["longitude"])
                if nearest is None or distance < nearest["distance"]:
                    nearest = {**item, "distance": round(distance, 1)}
            allow_outside = str(rw.get("SFFWWQD") or "0") == "1"
            in_range = bool(nearest and nearest["distance"] <= nearest["radius"])
            if not in_range and not allow_outside:
                items.append({**base_item, "status": "不在签到范围"})
                continue

            ddmc = (nearest["name"] if in_range else reverse_address())
            if not ddmc:
                ddmc = nearest["name"] if nearest else "当前位置"
            payload = {
                "RWBH": entry["rwbh"],
                "SFFWN": "1" if in_range else "0",
                "DDMC": ddmc,
                "QDJD": str(bd_lng),
                "QDWD": str(bd_lat),
            }
            if in_range and nearest and nearest.get("code"):
                payload["DDDM"] = nearest["code"]
            try:
                result = self._sign(payload)
            except SystemError:
                items.append({**base_item, "status": "提交失败"})
                continue
            if str(result.get("code")) not in ("0", "200"):
                items.append({**base_item, "status": "提交失败"})
                continue
            try:
                latest = self._info(entry["rwbh"])
            except SystemError:
                items.append({**base_item, "status": "提交失败"})
                continue
            latest_rw = latest.get("RWXX") or {}
            latest_qdxx = latest.get("QDXX") or {}
            latest_today = self._server_date(latest_rw) or self._today_str()
            if (str(latest_rw.get("SFQD") or "0") == "1"
                    and self._is_today_record(latest_qdxx, latest_today)):
                items.append({**base_item, "status": "打卡成功",
                              **self._checkin_fields(latest_qdxx)})
            else:
                items.append({**base_item, "status": "提交失败"})

        if not items or all(item["status"] == "今天无需打卡" for item in items):
            return {"code": 0, "msg": "ok", "data": {
                "status": "今天无需打卡", "items": []}}
        statuses = [item["status"] for item in items]
        if any(s == "打卡成功" for s in statuses):
            overall = "打卡成功" if all(s == "打卡成功" for s in statuses) else "部分成功"
        elif any(s == "已经打过卡了" for s in statuses):
            overall = ("已经打过卡了" if all(s == "已经打过卡了" for s in statuses)
                       else "部分成功")
        else:
            overall = statuses[0]
        return {"code": 0, "msg": "ok", "data": {"status": overall, "items": items}}
