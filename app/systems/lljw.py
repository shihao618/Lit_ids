"""教务系统（lljw.lit.edu.cn）适配器.

对外提供**自有契约** ``schedule``：
  * 只暴露「学期 / 周次 / 查询类型 / 日期」；
  * 学期不传自动取当前学期；周次不传自动取本周；``week="all"`` 获取全部；
  * 支持本周 / 今天 / 上午 / 下午 / 晚上 / 指定哪天 / 最近一节；
  * 返回课程、教师、地点、日期、节次、时间等易读字段。

内部仍调用官方接口：
  * GET  /new/student/xsgrkb/week.page              -> 解析当前学期
  * POST /new/xlxx/getDatesOfWeek                   -> 某周 7 天日期
  * POST /new/student/xsgrkb/getCalendarWeekDatas   -> 某周课程
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from datetime import date, datetime, timedelta, timezone

import lit_login as L

from ..session import SessionManager
from .base import BaseSystem, SystemError

log = logging.getLogger(__name__)

#: 北京时间（避免容器 UTC 导致“今天”算错）
TZ = timezone(timedelta(hours=8))

#: 节次时间表（教务页面 businessHours）
PERIOD_TIMES: dict[str, tuple[str, str]] = {
    "01": ("08:00", "08:45"),
    "02": ("08:55", "09:40"),
    "03": ("10:00", "10:45"),
    "04": ("10:55", "11:40"),
    "05": ("14:30", "15:15"),
    "06": ("15:25", "16:10"),
    "07": ("16:30", "17:15"),
    "08": ("17:25", "18:10"),
    "09": ("19:10", "19:55"),
    "10": ("20:05", "20:50"),
}

QUERY_TYPES = ("week", "today", "morning", "afternoon", "evening", "day", "next")
WEEKDAY_CN = "一二三四五六日"


class LljwSystem(BaseSystem):
    key = "lljw"
    name = "教务系统（lljw.lit.edu.cn）"
    HOST = "https://lljw.lit.edu.cn"
    SSO_PATH = "/new/ssoLogin"
    WEEK_PAGE = HOST + "/new/student/xsgrkb/week.page"
    CALENDAR_PATH = "/new/student/xsgrkb/getCalendarWeekDatas"
    DATES_PATH = "/new/xlxx/getDatesOfWeek"
    #: 该系统通过 CAS 登录时的入口（service 参数）
    service_url = HOST + SSO_PATH

    def __init__(self, session: SessionManager):
        super().__init__(session)
        self._sso_ready = False
        self._generation = -1
        self._semester_info: dict | None = None
        self._week_dates_cache: dict[tuple[str, int], list[str]] = {}
        self._max_week_cache: dict[str, int] = {}

    # ------------------------------------------------------------------ 基础
    @staticmethod
    def _now() -> datetime:
        return datetime.now(TZ)

    @classmethod
    def _today(cls) -> date:
        return cls._now().date()

    def _week_referer(self, semester: str, week: int | str = "") -> str:
        query = urllib.parse.urlencode({
            "szxqdm": "", "xnxqdm": semester, "yxdm": "",
            "zc": str(week), "d1": "", "d2": "",
        })
        return self.WEEK_PAGE + "?" + query

    def _api_headers(self, referer: str) -> dict:
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

    # ------------------------------------------------------------------ 会话
    def ensure_session(self, force: bool = False) -> None:
        """确保完成 CAS -> 教务系统 SSO（lljw 会话）."""
        with self.session.lock:
            self.session.ensure_login()
            if self._generation != self.session.generation:
                self._sso_ready = False
                self._semester_info = None
                self._week_dates_cache.clear()
                self._max_week_cache.clear()
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

    # ------------------------------------------------------------------ 页面 / 学期
    def _page_html(self) -> str:
        self.ensure_session()
        client = self.session.client
        headers = {"User-Agent": L.UA,
                   "Accept": "text/html,application/xhtml+xml,*/*",
                   "Referer": self.HOST + "/new/welcome.page",
                   "X-Requested-With": "XMLHttpRequest"}
        status, final_url, raw = client.get(self.WEEK_PAGE, headers=headers)
        text = raw.decode("utf-8", "replace")
        if status != 200 or "xnxqdm" not in text or "/welcome.page" in final_url:
            client.get(self.HOST + "/new/welcome.page",
                       headers={"User-Agent": L.UA, "Accept": "text/html,*/*"})
            status, final_url, raw = client.get(self.WEEK_PAGE, headers=headers)
            text = raw.decode("utf-8", "replace")
        if status != 200 or "xnxqdm" not in text:
            raise SystemError("无法获取教务课表页面（可能 lljw 会话未登录或页面改版）",
                              code="LLJW_PAGE_FAILED", http_status=502,
                              hint=text[:200])
        return text

    @staticmethod
    def _parse_semester_options(html: str) -> list[dict]:
        m = re.search(r"<select id='xnxqdm'.*?</select>", html, re.S)
        if not m:
            return []
        options = []
        for code, attrs, name in re.findall(
                r"<option value='([^']*)'([^>]*)>([^<]*)</option>", m.group(0)):
            options.append({"code": code, "name": name,
                            "selected": "selected" in attrs})
        return options

    def _fallback_semester(self) -> tuple[str, str]:
        today = self._today()
        if today.month >= 8:
            code = "%d01" % today.year
        elif today.month == 1:
            code = "%d01" % (today.year - 1)
        else:
            code = "%d02" % (today.year - 1)
        return code, code

    def semester_info(self, refresh: bool = False) -> dict:
        """当前学期信息（code/name/options），带缓存."""
        with self.session.lock:
            if self._semester_info is not None and not refresh:
                return self._semester_info
            options: list[dict] = []
            try:
                options = self._parse_semester_options(self._page_html())
            except SystemError:
                log.warning("解析教务当前学期失败，使用日期兜底")
            selected = next((o for o in options if o["selected"]), None)
            if selected:
                code, name = selected["code"], selected["name"]
            else:
                code, name = self._fallback_semester()
            self._semester_info = {"code": code, "name": name, "options": options}
            log.info("教务当前学期: %s (%s)", code, name)
            return self._semester_info

    def _resolve_semester(self, semester: str | None) -> tuple[str, str]:
        """返回 (semester_code, semester_name)；支持代码或名称."""
        text = (semester or "").strip()
        info = self.semester_info()
        options = info.get("options") or []
        if not text:
            return info["code"], info["name"]
        for opt in options:
            if text in (opt["code"], opt["name"]):
                return opt["code"], opt["name"]
        if options:
            codes = [o["code"] for o in options]
            raise SystemError("semester 无效: %s" % text, code="BAD_PARAMS",
                              http_status=422, hint="可选学期: %s" % ", ".join(codes[-8:]))
        return text, text

    # ------------------------------------------------------------------ 官方请求
    def _post_json(self, path: str, fields: dict, what: str, referer: str):
        body = urllib.parse.urlencode(fields).encode()
        headers = self._api_headers(referer)

        def _do():
            return self.session.client.post(
                self.HOST + path, body,
                "application/x-www-form-urlencoded; charset=UTF-8",
                headers=headers, referer=referer)

        status, _final_url, raw = _do()
        text = raw.decode("utf-8", "replace")
        if status == 401 or "尚未登录" in text or '"code":-401' in text:
            log.warning("教务系统会话失效，重新 SSO 后重试一次")
            self._sso_ready = False
            self.ensure_session(force=True)
            status, _final_url, raw = _do()
            text = raw.decode("utf-8", "replace")
        if status != 200 or text.lstrip().startswith("<"):
            raise SystemError("教务系统接口返回异常 HTTP %s: %s" % (status, what),
                              code="UPSTREAM_BAD_STATUS", http_status=502,
                              hint=text[:200])
        try:
            return json.loads(text)
        except ValueError as e:
            raise SystemError("教务系统返回非 JSON 数据: %s" % e,
                              code="UPSTREAM_BAD_JSON", http_status=502,
                              hint=text[:200]) from e

    def _week_dates(self, semester: str, week: int) -> list[str]:
        key = (semester, int(week))
        if key in self._week_dates_cache:
            return self._week_dates_cache[key]
        data = self._post_json(self.DATES_PATH,
                               {"xnxqdm": semester, "zc": str(week)},
                               "getDatesOfWeek", self._week_referer(semester, week))
        if not isinstance(data, list):
            raise SystemError("教务返回的周日期格式异常", code="UPSTREAM_BAD_JSON",
                              http_status=502, hint=str(data)[:200])
        dates = [str(item.get("rq")) for item in data if item.get("rq")]
        self._week_dates_cache[key] = dates
        return dates

    def _week_rows(self, semester: str, week: int) -> list[dict]:
        data = self._post_json(self.CALENDAR_PATH,
                               {"xnxqdm": semester, "zc": str(week)},
                               "getCalendarWeekDatas",
                               self._week_referer(semester, week))
        if isinstance(data, dict) and str(data.get("code")) not in ("0", "200"):
            raise SystemError("查询课表失败: %s" % data.get("message"),
                              code=data.get("code"), http_status=502)
        rows = (data.get("data") if isinstance(data, dict) else data) or []
        return [r for r in rows if isinstance(r, dict)]

    def _max_week(self, semester: str) -> int:
        if semester in self._max_week_cache:
            return self._max_week_cache[semester]
        last = 0
        for week in range(1, 40):
            if not self._week_dates(semester, week):
                break
            last = week
        if not last:
            raise SystemError("无法获取学期 %s 的周历" % semester,
                              code="SEMESTER_NOT_FOUND", http_status=422,
                              hint="请检查 semester 是否正确")
        self._max_week_cache[semester] = last
        return last

    def _week1_start(self, semester: str) -> date:
        dates = self._week_dates(semester, 1)
        if not dates:
            raise SystemError("无法获取学期 %s 第 1 周日期" % semester,
                              code="SEMESTER_NOT_FOUND", http_status=422)
        return date.fromisoformat(dates[0])

    def _week_for_date(self, semester: str, target: date) -> tuple[int, list[str]]:
        start = self._week1_start(semester)
        delta = (target - start).days
        if delta < 0:
            raise SystemError("日期 %s 不在学期 %s 内" % (target, semester),
                              code="NOT_IN_SEMESTER", http_status=422,
                              hint="日期早于学期第 1 周，请检查 semester/date")
        week = delta // 7 + 1
        dates = self._week_dates(semester, week)
        if not dates:
            raise SystemError("日期 %s 不在学期 %s 内" % (target, semester),
                              code="NOT_IN_SEMESTER", http_status=422,
                              hint="日期晚于学期最后一周，请检查 semester/date")
        return week, dates

    def _current_week(self, semester: str) -> tuple[int, list[str]]:
        return self._week_for_date(semester, self._today())

    # ------------------------------------------------------------------ 映射
    @staticmethod
    def _period_num(value) -> int:
        text = str(value or "").strip()
        return int(text) if text.isdigit() else 0

    @staticmethod
    def _hhmm(value) -> str:
        text = str(value or "")
        return text[:5] if len(text) >= 5 else ""

    def _course_date(self, row: dict, week_start: date) -> date:
        text = str(row.get("qsrq") or "")
        if len(text) >= 10:
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                pass
        qsxq = self._period_num(row.get("qsxq"))
        if qsxq:
            weekday = ((qsxq - 2) % 7) + 1
            return week_start + timedelta(days=weekday - 1)
        return week_start

    def _course_end_date(self, row: dict, start: date) -> date:
        text = str(row.get("jsrq") or "")
        if len(text) >= 10:
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                pass
        return start

    def _map_course(self, row: dict, week: int, week_start: date) -> dict:
        start_date = self._course_date(row, week_start)
        ps = str(row.get("ps") or "").strip().zfill(2)
        pe = str(row.get("pe") or "").strip().zfill(2)
        start_time = self._hhmm(row.get("qssj")) or PERIOD_TIMES.get(ps, ("", ""))[0]
        end_time = self._hhmm(row.get("jssj")) or PERIOD_TIMES.get(pe, ("", ""))[1]
        if ps and pe:
            label = "第%d-%d节" % (self._period_num(ps), self._period_num(pe))
        else:
            label = ""
        return {
            "courseName": row.get("kcmc") or "",
            "courseCode": row.get("kcdm") or "",
            "teacher": row.get("teaxms") or "",
            "location": row.get("jxcdmc") or "",
            "className": row.get("jxbmc") or "",
            "date": start_date.isoformat(),
            "weekday": "周" + WEEKDAY_CN[start_date.weekday()],
            "week": week,
            "startPeriod": ps,
            "endPeriod": pe,
            "periodLabel": label,
            "startTime": start_time,
            "endTime": end_time,
            "weeks": str(row.get("zc") or week),
            "color": row.get("bgcolor") or "",
        }

    @staticmethod
    def _sort_key(item: dict) -> tuple:
        return (item["date"], LljwSystem._period_num(item["startPeriod"]),
                item["startTime"])

    @staticmethod
    def _filter_period(items: list[dict], query: str) -> list[dict]:
        result = []
        for item in items:
            ps = LljwSystem._period_num(item["startPeriod"])
            pe = LljwSystem._period_num(item["endPeriod"])
            if query == "morning":
                if pe and pe <= 4:
                    result.append(item)
            elif query == "afternoon":
                if ps and 5 <= ps <= 8:
                    result.append(item)
            elif query == "evening":
                if ps and ps >= 9:
                    result.append(item)
        return result

    # ------------------------------------------------------------------ 自有接口
    def schedule(self, semester: str = "", week=None, query: str = "week",
                 date_text: str = "") -> dict:
        """教务课表查询（学期/周次可自动获取）.

        :param semester: 学期代码（空=自动当前学期）
        :param week: 周次 1~N；``"all"``/0=全部（仅 query=week）；空=自动本周
        :param query: week/today/morning/afternoon/evening/day/next
        :param date_text: query=day 时的日期，YYYY-MM-DD
        """
        query = (query or "week").strip().lower()
        if query not in QUERY_TYPES:
            raise SystemError("query 无效: %s" % query, code="BAD_PARAMS",
                              http_status=422,
                              hint="可选值: " + ", ".join(QUERY_TYPES))
        if query != "week" and week not in (None, ""):
            raise SystemError("week 仅用于 query=week（其它查询会自动定位周次）",
                              code="BAD_PARAMS", http_status=422)

        with self.session.lock:
            self.ensure_session()
            semester_code, semester_name = self._resolve_semester(semester)

            def base_data(resolved_week=None, week_start=None, week_end=None,
                          target_date=None) -> dict:
                return {
                    "semester": semester_code,
                    "semesterName": semester_name,
                    "query": query,
                    "week": resolved_week,
                    "weekStart": week_start,
                    "weekEnd": week_end,
                    "date": target_date,
                    "total": 0,
                    "items": [],
                }

            # ============================ 全部周
            if query == "week" and str(week).strip().lower() in ("all", "0"):
                items: list[dict] = []
                max_week = self._max_week(semester_code)
                for w in range(1, max_week + 1):
                    dates = self._week_dates(semester_code, w)
                    start = date.fromisoformat(dates[0])
                    items.extend(self._map_course(r, w, start)
                                 for r in self._week_rows(semester_code, w))
                items.sort(key=self._sort_key)
                data = base_data(resolved_week="all")
                data["total"] = len(items)
                data["items"] = items
                return {"code": 0, "msg": "ok", "data": data}

            # ============================ 指定/当前周
            if query == "week":
                if week in (None, ""):
                    resolved_week, dates = self._current_week(semester_code)
                else:
                    try:
                        resolved_week = int(week)
                    except (TypeError, ValueError):
                        raise SystemError("week 无效: %s" % week, code="BAD_PARAMS",
                                          http_status=422,
                                          hint='week 为 1~N，或 "all"/0 表示全部')
                    if resolved_week < 1:
                        raise SystemError("week 必须 >= 1", code="BAD_PARAMS",
                                          http_status=422)
                dates = self._week_dates(semester_code, resolved_week)
                if not dates:
                    raise SystemError("第 %s 周没有课表（可能超出学期）" % resolved_week,
                                      code="WEEK_NOT_FOUND", http_status=422)
                start = date.fromisoformat(dates[0])
                items = [self._map_course(r, resolved_week, start)
                         for r in self._week_rows(semester_code, resolved_week)]
                items.sort(key=self._sort_key)
                data = base_data(resolved_week, dates[0], dates[-1])
                data["total"] = len(items)
                data["items"] = items
                return {"code": 0, "msg": "ok", "data": data}

            # ============================ 某天 / 今天 / 时段
            if query == "day":
                if not date_text:
                    raise SystemError("query=day 需要 date 参数", code="BAD_PARAMS",
                                      http_status=422, hint="date 格式 YYYY-MM-DD")
                try:
                    target = date.fromisoformat(date_text.strip())
                except ValueError as e:
                    raise SystemError("date 格式无效: %s" % date_text,
                                      code="BAD_PARAMS", http_status=422,
                                      hint="date 格式 YYYY-MM-DD") from e
            else:
                target = self._today()

            if query == "next":
                return self._next_course(semester_code, semester_name)

            week_no, dates = self._week_for_date(semester_code, target)
            start = date.fromisoformat(dates[0])
            week_rows = []
            for row in self._week_rows(semester_code, week_no):
                row_start = self._course_date(row, start)
                row_end = self._course_end_date(row, row_start)
                if row_start <= target <= row_end:
                    week_rows.append(row)
            items = [self._map_course(r, week_no, start) for r in week_rows]
            if query in ("morning", "afternoon", "evening"):
                items = self._filter_period(items, query)
            items.sort(key=self._sort_key)
            data = base_data(week_no, dates[0], dates[-1], target.isoformat())
            data["total"] = len(items)
            data["items"] = items
            return {"code": 0, "msg": "ok", "data": data}

    def _next_course(self, semester_code: str, semester_name: str) -> dict:
        now = self._now()
        week_no, dates = self._current_week(semester_code)
        last_checked_week = week_no
        last_dates = dates
        max_week = self._max_week(semester_code)
        while week_no <= max_week:
            week_dates = self._week_dates(semester_code, week_no)
            if not week_dates:
                break
            start = date.fromisoformat(week_dates[0])
            candidates = []
            for row in self._week_rows(semester_code, week_no):
                item = self._map_course(row, week_no, start)
                start_time = item["startTime"] or "00:00"
                try:
                    dt = datetime.fromisoformat(
                        item["date"] + "T" + start_time).replace(tzinfo=TZ)
                except ValueError:
                    continue
                if dt > now:
                    candidates.append((dt, item))
            if candidates:
                candidates.sort(key=lambda pair: pair[0])
                item = candidates[0][1]
                data = {
                    "semester": semester_code,
                    "semesterName": semester_name,
                    "query": "next",
                    "week": week_no,
                    "weekStart": week_dates[0],
                    "weekEnd": week_dates[-1],
                    "date": item["date"],
                    "total": 1,
                    "items": [item],
                }
                return {"code": 0, "msg": "ok", "data": data}
            last_checked_week, last_dates = week_no, week_dates
            week_no += 1
        data = {
            "semester": semester_code,
            "semesterName": semester_name,
            "query": "next",
            "week": last_checked_week,
            "weekStart": last_dates[0] if last_dates else None,
            "weekEnd": last_dates[-1] if last_dates else None,
            "date": None,
            "total": 0,
            "items": [],
        }
        return {"code": 0, "msg": "ok", "data": data}
