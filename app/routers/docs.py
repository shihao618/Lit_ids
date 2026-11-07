"""前台接口文档页面（用户视角，无需登录后台）.

页面:
  GET  /            前台接口文档（HTML，自动根据 OpenAPI 生成）
    GET  /api/v1/meta 服务元信息（JSON）

说明:
  * 文档内容由 FastAPI 的 OpenAPI 自动生成（新增接口后页面自动出现），
    再叠加少量人工维护的「请求/响应示例」，排版更适合阅读/拷贝；
  * 页面不包含任何密钥；"在线调试" 需要用户自己填写 API Key（存 localStorage）；
  * 后台调试界面在 /admin。
"""

from __future__ import annotations

import json
import re
import urllib.parse
from html import escape
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["前台接口文档"])

#: 不在前台文档中展示的路径
HIDDEN_PATHS = {
    "/healthz", "/readyz", "/api/v1/meta",
}

#: 人工维护的示例（key = (METHOD, path)）
EXAMPLES: dict[tuple[str, str], dict[str, Any]] = {
    ("POST", "/api/v1/lljw/schedule"): {
        "request": {"query": "day", "date": "2026-10-29"},
        "response": {"code": 0, "msg": "ok", "data": {
            "semester": "202601",
            "semesterName": "2026-2027-1",
            "query": "day",
            "week": 8,
            "weekStart": "2026-10-26",
            "weekEnd": "2026-11-01",
            "date": "2026-10-29",
            "total": 2,
            "items": [{
                "courseName": "示例课程",
                "courseCode": "10005536",
                "teacher": "示例教师",
                "location": "XC3",
                "className": "示例班级",
                "date": "2026-10-29",
                "weekday": "周四",
                "week": 8,
                "startPeriod": "01",
                "endPeriod": "02",
                "periodLabel": "第1-2节",
                "startTime": "08:00",
                "endTime": "09:40",
                "weeks": "8",
                "color": "#009688",
            }]}},
    },
    ("GET", "/api/v1/xsfw/dorm-checkin/locations"): {
        "response": {"code": 0, "msg": "ok", "data": {
            "total": 31, "mine": False, "building": "",
            "locations": [{
                "name": "洛阳理工学院(开元校区)璁苑2号学生公寓",
                "latitude": 34.615408,
                "longitude": 112.422575,
                "radius": 100.0,
            }],
        }},
    },
    ("POST", "/api/v1/xsfw/dorm-checkin/check-in"): {
        "request": {"latitude": 34.610278, "longitude": 112.409723,
                    "coordType": "auto"},
        "response": {"code": 0, "msg": "ok", "data": {
            "submitted": True,
            "input": {"latitude": 34.610278, "longitude": 112.409723,
                      "coordType": "auto"},
            "converted": {"latitude": 34.6154091, "longitude": 112.4225198,
                          "coordType": "bd09"},
            "detectedCoordType": "wgs84",
            "location": {
                "name": "洛阳理工学院(开元校区)璁苑2号学生公寓",
                "latitude": 34.615408, "longitude": 112.422575,
                "radius": 100.0, "distance": 5.1,
            },
            "candidates": [
                {"coordType": "wgs84", "distance": 5.1},
                {"coordType": "gcj02", "distance": 588.3},
                {"coordType": "bd09", "distance": 1307.2},
            ],
            "checks": {
                "inDate": True, "inTime": True, "inCheckinList": True,
                "onLeave": False, "alreadyNormal": False, "alreadyLate": False,
                "hasCheckedIn": False, "hasCheckedOutAfter": False,
                "inRange": True,
            },
            "after": {"status": "正常归宿", "statusCode": "normal",
                      "checkinTime": "11/01 23:20"},
        }},
    },
    ("POST", "/api/v1/xsfw/dorm-checkin/records"): {
        "request": {"classCode": "B260714",
                    "checkStatus": "not_returned",
                    "checkStatusType": "unreachable"},
        "response": {"code": 0, "msg": "ok", "data": {
            "total": 39,
            "items": [{
                "studentId": "B2607xxxx",
                "name": "示例同学",
                "classCode": "B260714",
                "className": "智科(专升本)B260714",
                "collegeName": "人工智能学院/未来技术学院",
                "majorName": "智能科学与技术",
                "dormBuilding": "xx学生公寓",
                "roomNumber": "218",
                "bedNumber": "2",
                "phone": "138****0000",
                "checkStatus": "returned",
                "checkStatusLabel": "已归寝",
                "checkStatusType": "normal",
                "checkStatusTypeLabel": "正常归寝",
            }]}},
    },
    ("GET", "/api/v1/xsfw/qdzs/locations"): {
        "response": {"code": 0, "msg": "ok", "data": {
            "task": {"rwbh": "5F5FB88605DBEAACE0638A6210AC2D19",
                     "name": "1106-1108全校定位签到",
                     "method": "手机定位签到", "period": "循环签到",
                     "dateStart": "2026-11-06", "dateEnd": "2026-11-08",
                     "timeStart": "19:40", "timeEnd": "23:00",
                     "serverTime": "2026-11-08 19:41:00"},
            "total": 1,
            "locations": [{"name": "学校", "code": "5F5FB88605E5EAACE0638A6210AC2D19",
                            "latitude": 34.612396, "longitude": 112.441906,
                            "radius": 100.0}],
            "DDLIST": [{"DDMC": "学校", "WD": "34.612396", "JD": "112.441906",
                        "YXFW": 100, "DDDM": "5F5FB88605E5EAACE0638A6210AC2D19"}],
            "QDXX": {},
        }},
    },
    ("POST", "/api/v1/xsfw/qdzs/today-signin"): {
        "request": {"latitude": 34.610278, "longitude": 112.409723,
                    "coordType": "auto"},
        "response": {"code": 0, "msg": "ok", "data": {
            "task": {"rwbh": "5F5FB88605DBEAACE0638A6210AC2D19",
                     "name": "1106-1108全校定位签到",
                     "method": "手机定位签到", "period": "循环签到",
                     "dateStart": "2026-11-06", "dateEnd": "2026-11-08",
                     "timeStart": "19:40", "timeEnd": "23:00",
                     "serverTime": "2026-11-08 19:41:00"},
            "checks": {"taskEnded": False, "needToday": True, "inTime": True,
                       "alreadySigned": False, "allowOutside": True,
                       "inRange": False, "needAttachment": False},
            "input": {"latitude": 34.610278, "longitude": 112.409723,
                      "coordType": "auto"},
            "converted": {"latitude": 34.6154091, "longitude": 112.4225198,
                          "coordType": "bd09"},
            "address": "河南省洛阳市洛龙区古城街道龙祥商业街c-6号",
            "location": {"name": "学校", "distance": 1805.5, "radius": 100.0,
                         "inRange": False},
            "DDLIST": [{"DDMC": "学校", "WD": "34.612396", "JD": "112.441906",
                        "YXFW": 100, "DDDM": "5F5FB88605E5EAACE0638A6210AC2D19"}],
            "QDXX": {},
            "submitted": True,
            "result": {"prompt": "签到成功"},
            "after": {"signed": True},
        }},
    },

}

TAG_ORDER = ["认证/会话", "智慧学工", "智能查寝", "签到助手", "教务系统", "元信息"]

# ============================================================ OpenAPI 辅助
def _resolve_ref(spec: dict, ref: str) -> dict:
    node: Any = spec
    for part in ref.lstrip("#/").split("/"):
        if not isinstance(node, dict):
            return {}
        node = node.get(part, {})
    return node if isinstance(node, dict) else {}


def _schema_example(schema: dict | None, spec: dict, depth: int = 0) -> Any:
    """根据 JSON Schema 生成一个示例值（用于请求体/响应体示例）."""
    if not isinstance(schema, dict) or depth > 5:
        return None
    if "example" in schema:
        return schema["example"]
    if "$ref" in schema:
        return _schema_example(_resolve_ref(spec, schema["$ref"]), spec, depth + 1)
    if "anyOf" in schema:
        for item in schema["anyOf"]:
            if item.get("type") != "null":
                return _schema_example(item, spec, depth + 1)
        return None
    typ = schema.get("type")
    if typ == "object" or "properties" in schema:
        return {k: _schema_example(v, spec, depth + 1)
                for k, v in (schema.get("properties") or {}).items()}
    if typ == "array":
        item = _schema_example(schema.get("items") or {}, spec, depth + 1)
        return [item] if item is not None else []
    if typ == "integer":
        return schema.get("default", 1)
    if typ == "number":
        return schema.get("default", 1.0)
    if typ == "boolean":
        return schema.get("default", False)
    if typ == "string":
        return schema.get("default", "")
    return None


def _param_rows(op: dict, spec: dict) -> list[dict]:
    rows = []
    for p in op.get("parameters") or []:
        name = str(p.get("name") or "")
        where = p.get("in")
        # API Key 统一在页面上方说明，不在每个接口的参数表里重复展示
        if where == "header" and name.lower() in ("x-api-key", "authorization"):
            continue
        schema = p.get("schema") or {}
        if schema.get("$ref"):
            schema = _resolve_ref(spec, schema["$ref"])
        if "anyOf" in schema:
            schema = next((s for s in schema["anyOf"] if s.get("type") != "null"), schema)
        rows.append({
            "name": name,
            "in": where,
            "type": schema.get("type") or "",
            "enum": schema.get("enum") or [],
            "required": bool(p.get("required")),
            "default": schema.get("default"),
            "description": p.get("description") or schema.get("description") or "",
        })
    return rows


def _body_schema(op: dict, spec: dict) -> dict | None:
    rb = op.get("requestBody") or {}
    content = rb.get("content") or {}
    for media_type in ("application/json", "application/x-www-form-urlencoded"):
        if media_type in content:
            schema = (content[media_type] or {}).get("schema") or {}
            if schema.get("$ref"):
                schema = _resolve_ref(spec, schema["$ref"])
            return schema
    return None


def _response_schema(op: dict, spec: dict) -> dict | None:
    responses = op.get("responses") or {}
    for code in ("200", "201", 200, 201):
        item = responses.get(code) or responses.get(str(code))
        if not item:
            continue
        content = item.get("content") or {}
        if "application/json" in content:
            schema = (content["application/json"] or {}).get("schema") or {}
            if schema.get("$ref"):
                schema = _resolve_ref(spec, schema["$ref"])
            return schema
    return None


def _body_param_rows(op: dict, spec: dict) -> list[dict]:
    """从 requestBody schema 提取顶层参数（用于 POST/PUT 的 JSON 请求体参数表）."""
    schema = _body_schema(op, spec)
    if not schema:
        return []
    if "properties" not in schema:
        merged = {"properties": {}, "required": []}
        for part in schema.get("allOf") or []:
            if part.get("$ref"):
                part = _resolve_ref(spec, part["$ref"])
            merged["properties"].update(part.get("properties") or {})
            merged["required"].extend(part.get("required") or [])
        schema = merged
    required = set(schema.get("required") or [])
    rows = []
    for name, prop in (schema.get("properties") or {}).items():
        item = prop
        outer_desc = item.get("description") or ""
        if item.get("$ref"):
            item = _resolve_ref(spec, item["$ref"])
        if "anyOf" in item:
            item = next((x for x in item["anyOf"] if x.get("type") != "null"), item)
        rows.append({
            "name": name,
            "type": item.get("type") or "",
            "enum": item.get("enum") or [],
            "required": name in required,
            "default": item.get("default"),
            "description": item.get("description") or outer_desc,
        })
    return rows


#: 前台 API 文档层级（顶层分类 -> 二级分类 -> 接口）
DOC_SECTIONS = [
    {
        "title": "智慧学工",
        "groups": [
            {"title": "智能查寝", "items": [
                ("查寝记录", "POST", "/api/v1/xsfw/dorm-checkin/records"),
                ("签到地点", "GET", "/api/v1/xsfw/dorm-checkin/locations"),
                ("定位签到", "POST", "/api/v1/xsfw/dorm-checkin/check-in"),
            ]},
            {"title": "签到助手", "items": [
                ("签到地点", "GET", "/api/v1/xsfw/qdzs/locations"),
                ("今日签到", "POST", "/api/v1/xsfw/qdzs/today-signin"),
            ]},
        ],
    },
    {
        "title": "教务系统",
        "items": [
            ("课表查询", "POST", "/api/v1/lljw/schedule"),
        ],
    },
]


def _build_endpoints(app: Any) -> list[dict]:
    spec = app.openapi()
    groups: dict[str, list[dict]] = {}
    for path, item in (spec.get("paths") or {}).items():
        if path in HIDDEN_PATHS or path.startswith("/admin"):
            continue
        for method, op in item.items():
            if method.lower() not in {"get", "post", "put", "delete", "patch"}:
                continue
            tag = (op.get("tags") or ["其他"])[0]
            key = (method.upper(), path)
            ex = EXAMPLES.get(key, {})
            params = _param_rows(op, spec)
            body_params = _body_param_rows(op, spec)
            body_schema = _body_schema(op, spec)
            req_example = ex.get("request")
            if req_example is None and body_schema is not None:
                req_example = _schema_example(body_schema, spec)
            resp_example = ex.get("response")
            if resp_example is None:
                schema = _response_schema(op, spec)
                resp_example = _schema_example(schema, spec) if schema else {"code": 0, "msg": "ok"}
            groups.setdefault(tag, []).append({
                "method": method.upper(),
                "path": path,
                "summary": op.get("summary") or "",
                "description": (op.get("description") or "").strip(),
                "params": params,
                "body_params": body_params,
                "request": req_example,
                "response": resp_example,
            })
    order = {name: i for i, name in enumerate(TAG_ORDER)}
    tags = sorted(groups, key=lambda t: (order.get(t, 99), t))
    return [{"tag": t, "endpoints": groups[t]} for t in tags]


# ============================================================ HTML 渲染
def _h(value: Any) -> str:
    return escape(str(value), quote=True)


def _format_desc_text(text: str) -> str:
    """普通描述：转义 + 行内 `code` + 换行."""
    if not text.strip():
        return ""
    escaped = _h(text)
    escaped = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", escaped)
    return escaped.replace("\n", "<br>")


def _render_description(text: str) -> str:
    """把路由 docstring 中的 ```lang 代码块渲染成 <pre>，其余按普通文本处理."""
    text = text or ""
    parts: list[str] = []
    pos = 0
    for m in re.finditer(r"```(?:[a-zA-Z0-9_-]*)\n(.*?)```", text, re.S):
        parts.append(_format_desc_text(text[pos:m.start()]))
        parts.append("<pre>%s</pre>" % _h(m.group(1).strip("\n")))
        pos = m.end()
    parts.append(_format_desc_text(text[pos:]))
    return "".join(parts)


def _curl(ep: dict, base: str) -> str:
    query = [(p["name"], p["default"]) for p in ep["params"]
             if p["in"] == "query" and p["default"] not in (None, "")]
    url = base + ep["path"]
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)
    lines = [f"curl -X {ep['method']} '{url}' \\",
             "  -H 'X-API-Key: <你的 API Key>'"]
    if ep["method"] != "GET" and ep["request"] is not None:
        body = json.dumps(ep["request"], ensure_ascii=False, separators=(",", ":"))
        lines[-1] += " \\"
        lines.append("  -H 'Content-Type: application/json' \\")
        lines.append("  -d '%s'" % body.replace("'", "'\\''"))
    return "\n".join(lines)


def _code_block(content: str, copy_id: str, label: str) -> str:
    return ('<div class="blk"><p class="blk-title">%s '
            '<button class="copy" data-copy="%s">复制</button></p>'
            '<div class="code-block"><pre id="%s"><code>%s</code></pre></div></div>'
            % (_h(label), copy_id, copy_id, _h(content)))


def _render_endpoint(ep: dict, idx: int, base: str) -> str:
    eid = "ep-%d" % idx
    method = ep["method"].lower()
    desc = _render_description(ep["description"])
    parts = [f'<div class="endpoint-block" id="{eid}">',
             f'<h3 class="doc-subheading">{_h(ep["summary"])}</h3>',
             f'<div class="endpoint-line"><span class="method method-{method}">'
             f'{ep["method"]}</span>'
             f'<code class="endpoint-path">{_h(ep["path"])}</code></div>']
    if desc:
        parts.append(f'<div class="desc">{desc}</div>')
    if ep["params"]:
        parts.append('<div class="table-wrap"><table class="doc-table"><thead><tr>'
                     '<th>参数</th><th>位置</th><th>类型</th><th>必填</th>'
                     '<th>默认值</th><th>说明</th></tr></thead><tbody>')
        for p in ep["params"]:
            type_display = p["type"]
            if p["enum"]:
                type_display = "%s（%s）" % (type_display, " / ".join(str(x) for x in p["enum"]))
            parts.append("<tr><td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td>"
                         "<td>%s</td><td>%s</td></tr>" % (
                             _h(p["name"]), _h(p["in"]), _h(type_display),
                             "是" if p["required"] else "否",
                             _h(p["default"]) if p["default"] is not None else "-",
                             _h(p["description"])))
        parts.append("</tbody></table></div>")
    if ep.get("body_params"):
        parts.append('<p class="blk-title">请求体参数（JSON）</p>')
        parts.append('<div class="table-wrap"><table class="doc-table"><thead><tr>'
                     '<th>参数</th><th>类型</th><th>必填</th><th>默认值</th>'
                     '<th>说明</th></tr></thead><tbody>')
        for bp in ep["body_params"]:
            type_display = bp["type"]
            if bp["enum"]:
                type_display = "%s（%s）" % (type_display, " / ".join(str(x) for x in bp["enum"]))
            parts.append("<tr><td><code>%s</code></td><td>%s</td><td>%s</td>"
                         "<td>%s</td><td>%s</td></tr>" % (
                             _h(bp["name"]), _h(type_display),
                             "是" if bp["required"] else "否",
                             _h(bp["default"]) if bp["default"] is not None else "-",
                             _h(bp["description"])))
        parts.append("</tbody></table></div>")
    if ep["request"] is not None:
        body = json.dumps(ep["request"], ensure_ascii=False, indent=2)
        parts.append(_code_block(body, "req-%d" % idx, "请求体示例"))
    parts.append(_code_block(_curl(ep, base), "curl-%d" % idx, "curl 示例"))
    resp = json.dumps(ep["response"], ensure_ascii=False, indent=2)
    parts.append(_code_block(resp[:4000], "resp-%d" % idx, "响应示例"))

    # 在线调试
    parts.append('<div class="try"><p class="blk-title">在线调试（需先在上方填写 API Key）</p>')
    if ep["params"]:
        parts.append('<div class="try-params">')
        for p in ep["params"]:
            if p["in"] != "query":
                continue
            if p["enum"]:
                opts = ['<option value="">（不传）</option>'] + [
                    '<option value="%s">%s</option>' % (_h(v), _h(v)) for v in p["enum"]]
                parts.append('<label>%s<select class="input" id="q-%d-%s">%s</select></label>'
                             % (_h(p["name"]), idx, _h(p["name"]), "".join(opts)))
                continue
            default = "" if p["default"] is None else p["default"]
            parts.append('<label>%s<input class="input" id="q-%d-%s" value="%s" placeholder="%s"></label>'
                         % (_h(p["name"]), idx, _h(p["name"]), _h(default), _h(p["description"])))
        parts.append("</div>")
    if ep["request"] is not None:
        body = json.dumps(ep["request"], ensure_ascii=False, indent=2)
        parts.append('<textarea id="body-%d" spellcheck="false">%s</textarea>' % (idx, _h(body)))
    parts.append('<button class="btn btn-primary send" data-idx="%d">发送请求</button>' % idx)
    parts.append('<pre class="result" id="out-%d">（响应结果）</pre>' % idx)
    parts.append("</div></div>")
    return "\n".join(parts)


def _render_html(app: Any, request: Request) -> str:
    from .. import __version__

    runtime = getattr(request.app.state, "runtime_settings", None)
    app_name = runtime.get("app_name") if runtime is not None else None
    app_name = app_name or app.title
    base = str(request.base_url).rstrip("/")
    all_groups = _build_endpoints(app)
    by_key = {(ep["method"], ep["path"]): ep
              for group in all_groups for ep in group["endpoints"]}
    endpoints_js: list[dict] = []
    cards: list[str] = []
    nav: list[str] = []
    idx = 0

    def add_endpoint(ep: dict, label: str) -> None:
        nonlocal idx
        cards.append(_render_endpoint(ep, idx, base))
        endpoints_js.append({
            "id": idx,
            "method": ep["method"],
            "path": ep["path"],
            "body": ep["request"] is not None,
            "query": [{"name": p["name"],
                       # 描述里含"空字符串"的参数，允许在线调试时显式传空值（如 cqzt=）
                       "send_empty": "空字符串" in (p["description"] or "")}
                      for p in ep["params"] if p["in"] == "query"],
        })
        method = ep["method"].lower()
        nav.append(
            f'<a class="doc-nav-link" href="#ep-{idx}">'
            f'<span class="method method-{method}">{_h(ep["method"])}</span>'
            f'<span class="nav-label">{_h(label)}</span></a>')
        idx += 1

    for si, section in enumerate(DOC_SECTIONS):
        section_anchor = "gs-%d" % si
        nav.append(f'<p class="doc-nav-title">{_h(section["title"])}</p>')
        cards.append(f'<section class="doc-section" id="{section_anchor}">'
                     f'<h2 class="doc-heading">{_h(section["title"])}</h2>')
        for gi, group in enumerate(section.get("groups") or []):
            group_anchor = "gs-%d-%d" % (si, gi)
            nav.append(f'<a class="doc-nav-group" href="#{group_anchor}">'
                       f'{_h(group["title"])}</a>')
            cards.append(f'<div class="doc-group" id="{group_anchor}">'
                         f'<h3 class="doc-group-heading">{_h(group["title"])}</h3>')
            for label, method, path in group["items"]:
                ep = by_key.get((method, path))
                if ep:
                    add_endpoint(ep, label)
            cards.append("</div>")
        for label, method, path in section.get("items") or []:
            ep = by_key.get((method, path))
            if ep:
                add_endpoint(ep, label)
        cards.append("</section>")

    endpoints_json = json.dumps(endpoints_js, ensure_ascii=False)
    return DOCS_TMPL.replace("__CSS__", _CSS) \
        .replace("__APP_NAME__", _h(app_name)) \
        .replace("__VERSION__", _h(__version__)) \
        .replace("__BASE__", _h(base)) \
        .replace("__NAV__", "\n".join(nav)) \
        .replace("__CARDS__", "\n".join(cards)) \
        .replace("__ENDPOINTS_JSON__", endpoints_json)


# ============================================================ 路由
@router.get("/", response_class=HTMLResponse, include_in_schema=False, summary="前台接口文档")
def docs_page(request: Request) -> HTMLResponse:
    return HTMLResponse(_render_html(request.app, request))


# ============================================================ 模板与样式
_CSS = """
:root{
  --green:#07c160;--green-dark:#06ad56;--green-soft:#e8f8ef;--green-line:#c8eedb;
  --bg:#f5f7fa;--card:#fff;--text:#1f2937;--text2:#4b5563;--muted:#8b95a5;
  --border:#e5e7eb;--border2:#d1d5db;--danger:#e5484d;--danger-soft:#fdecec;
  --warning:#d97706;--warning-soft:#fff7e6;--info-soft:#eff6ff;
  --radius:10px;--radius-lg:14px;--shadow:0 1px 3px rgba(15,23,42,.06);
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.65 var(--sans);-webkit-text-size-adjust:100%}
a{color:inherit;text-decoration:none}
.skip-link{position:fixed;top:-60px;left:12px;z-index:200;padding:8px 14px;border-radius:8px;background:var(--green);color:#fff;font-size:13px}
.skip-link:focus{top:10px}
.doc-sidebar{position:fixed;top:0;left:0;bottom:0;z-index:40;width:232px;padding:12px 10px;border-right:1px solid var(--border);background:#fff;display:flex;flex-direction:column;overflow-y:auto}
.doc-sidebar-top{display:flex;align-items:center;gap:6px;margin:0 2px 10px}
.brand{display:inline-flex;align-items:center;gap:8px;min-width:0;color:var(--text)}
.brand:hover{color:var(--text)}
.brand-icon{width:28px;height:28px;border-radius:8px;flex:none}
.brand-name{font-size:16px;font-weight:700;letter-spacing:-.01em;white-space:nowrap}
.brand-tag{display:inline-block;padding:1px 8px;border-radius:999px;background:var(--green-soft);color:#047a3d;font-size:11px;font-weight:600;white-space:nowrap}
.doc-sidebar-brand{margin:0 6px 0 2px}
.doc-nav{display:flex;flex-direction:column;gap:2px;padding:12px;border:1px solid var(--border);border-radius:var(--radius-lg);background:var(--card)}
.doc-nav-title{padding:2px 10px 8px;color:var(--muted);font-size:12px;font-weight:600;margin:8px 0 0}
.doc-nav-title:first-child{margin-top:0}
.doc-nav-link{display:flex;align-items:center;gap:6px;padding:7px 10px;border-radius:7px;color:var(--text2);font-size:13px;line-height:1.45;transition:background .15s,color .15s;min-width:0}
.doc-nav-link:hover{background:#f9fafb;color:var(--text)}
.doc-nav-link .method{flex:none;min-width:42px;padding:1px 5px;font-size:10.5px}
.doc-nav-link .path{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--mono);font-size:11.5px;color:var(--muted)}
.doc-nav-link .nav-label{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.doc-nav-group{display:block;padding:5px 10px;color:var(--muted);font-size:12px;font-weight:700;line-height:1.4;border-radius:6px}
.doc-nav-group:hover{background:#f9fafb;color:var(--text2)}
.doc-group{margin-top:6px}
.doc-group-heading{font-size:16px;font-weight:700;margin:20px 0 2px;color:var(--text2)}
.doc-sidebar-footer{margin-top:auto;padding-top:14px}
.doc-sidebar-footer .doc-nav-link{font-size:13px;padding:8px 10px}
.doc-sidebar-footer .doc-nav-link span{margin-right:2px}
.docs-nav-close{display:none;flex:none;align-items:center;justify-content:center;width:32px;height:32px;margin-left:auto;border:1px solid var(--border);border-radius:8px;background:#fff;color:var(--text2);font-size:20px;line-height:1;cursor:pointer}
.docs-nav-overlay{position:fixed;inset:0;z-index:35;background:rgba(15,23,42,.45)}
.docs-nav-overlay[hidden]{display:none!important}
.docs-main{display:flex;flex-direction:column;min-height:100vh;margin-left:232px;min-width:0}
.docs-topbar{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:12px;padding:12px 24px;background:rgba(255,255,255,.94);border-bottom:1px solid var(--border);backdrop-filter:blur(6px)}
.docs-topbar-title{flex:1;min-width:0;display:flex;flex-direction:column}
.docs-topbar-title strong{font-size:19px;font-weight:700;line-height:1.3}
.docs-topbar-title span{font-size:12px;color:var(--muted);margin-top:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.docs-topbar-actions{display:flex;align-items:center;gap:8px;flex:none}
.docs-menu-toggle{display:none;flex:none;align-items:center;justify-content:center;width:36px;height:36px;border:1px solid var(--border);border-radius:10px;background:#fff;color:var(--text);font-size:17px;line-height:1;cursor:pointer}
.docs-menu-toggle:hover{background:#f9fafb}
.docs-content{flex:1;width:100%;max-width:1200px;margin:0 auto;padding:20px 24px 40px}
.doc-content{max-width:100%;padding:28px 32px;border:1px solid var(--border);border-radius:var(--radius-lg);background:var(--card);box-shadow:var(--shadow);min-width:0}
.doc-title{font-size:clamp(22px,3vw,28px);font-weight:700;letter-spacing:-.02em;margin:0}
.doc-lead{margin:8px 0 0;color:var(--text2);font-size:14px}
.doc-note{margin:12px 0;padding:10px 12px;border-left:4px solid var(--warning);border-radius:8px;background:var(--warning-soft);color:#7a4f01;font-size:13px}
.doc-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:16px 0}
.doc-card{padding:14px;border:1px solid var(--border);border-radius:10px;background:#f9fafb;min-width:0}
.doc-card-title{font-size:13.5px;font-weight:700;margin:0 0 6px}
.doc-card p{margin:4px 0;font-size:12.5px;color:var(--text2)}
.doc-toolbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:12px 0}
.doc-toolbar .input{flex:1 1 240px;min-width:0}
.input{width:100%;padding:8px 10px;border:1px solid var(--border2);border-radius:8px;background:#fff;font:inherit;color:var(--text)}
.input:focus{outline:2px solid var(--green-line);border-color:var(--green);outline-offset:0}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:8px 14px;border:1px solid transparent;border-radius:8px;background:var(--green);color:#fff;font:inherit;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap}
.btn:hover{background:var(--green-dark)}
.btn-ghost{border-color:var(--border);background:#fff;color:var(--text2);font-weight:500}
.btn-ghost:hover{background:#f9fafb;color:var(--text)}
.doc-section{margin-top:32px;padding-top:22px;border-top:1px solid var(--border);scroll-margin-top:calc(64px + 16px)}
.doc-heading{font-size:19px;font-weight:700;padding-left:10px;border-left:4px solid var(--green);margin:0 0 12px}
.doc-subheading{font-size:15.5px;font-weight:700;margin:24px 0 6px;color:var(--text)}
.doc-subheading:first-child{margin-top:0}
.endpoint-block{padding:16px 0;min-width:0}
.endpoint-block + .endpoint-block{border-top:1px dashed var(--border)}
.endpoint-line{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:10px 0}
.endpoint-path{font-family:var(--mono);font-size:13px;color:var(--text);background:#f9fafb;border:1px solid var(--border);border-radius:6px;padding:1px 7px;word-break:break-all}
.method{display:inline-block;min-width:58px;text-align:center;padding:1px 7px;border-radius:6px;font-family:var(--mono);font-size:11.5px;font-weight:700;letter-spacing:.03em}
.method-get{background:var(--info-soft);color:#1d4ed8}
.method-post{background:var(--green-soft);color:#047a3d}
.method-put{background:var(--warning-soft);color:#b45309}
.method-delete{background:var(--danger-soft);color:#b91c1c}
.desc{color:var(--text2);font-size:13.5px;margin:6px 0 10px;min-width:0}
.desc pre{margin:8px 0;padding:10px 12px;border-radius:8px;background:#0f172a;color:#e2e8f0;font:12px/1.6 var(--mono);overflow-x:auto;max-width:100%;white-space:pre}
.blk{margin-top:12px}
.blk-title{display:flex;align-items:center;gap:8px;margin:0 0 6px;color:var(--muted);font-size:12.5px;font-weight:600}
button.copy{padding:1px 8px;font-size:11.5px;border:1px solid var(--border);background:#fff;border-radius:6px;color:var(--text2);cursor:pointer;font-family:inherit}
button.copy:hover{background:#f9fafb;color:var(--text)}
.table-wrap{width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch;margin:10px 0;border:1px solid var(--border);border-radius:10px}
.doc-table{width:100%;border-collapse:collapse;font-size:13px;min-width:620px;margin:0}
.doc-table th,.doc-table td{padding:9px 11px;text-align:left;vertical-align:top;border-bottom:1px solid var(--border)}
.doc-table th{background:#f9fafb;color:var(--muted);font-size:12px;font-weight:600;white-space:nowrap}
.doc-table tr:last-child td{border-bottom:none}
.doc-table code,.code-inline{padding:1px 5px;border-radius:5px;background:#f1f5f9;color:#334155;font-family:var(--mono);font-size:12px;word-break:break-word;overflow-wrap:anywhere}
.code-block{margin:10px 0;border-radius:10px;background:#0f172a;overflow:hidden;min-width:0;max-width:100%}
.code-block pre{margin:0;padding:10px 14px 14px;overflow-x:auto;-webkit-overflow-scrolling:touch}
.code-block code{color:#e2e8f0;font:12.5px/1.7 var(--mono);white-space:pre}
.try{margin-top:16px;padding-top:14px;border-top:1px dashed var(--border)}
.try-params{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.try-params label{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text2);min-width:0}
.try-params label input,.try-params label select{width:150px}
.try textarea{width:100%;min-height:76px;padding:9px 10px;border:1px solid var(--border2);border-radius:8px;font:12.5px/1.6 var(--mono);background:#fbfcfe;color:var(--text)}
button.send{margin-top:10px}
pre.result{margin-top:10px;max-height:280px;padding:12px 14px;border-radius:10px;background:#0f172a;color:#e2e8f0;font:12.5px/1.65 var(--mono);overflow:auto;white-space:pre-wrap;word-break:break-word}
.site-footer{margin-top:32px;padding-top:16px;border-top:1px solid var(--border);color:var(--muted);font-size:12px;text-align:center}
.site-footer a{color:var(--text2)}
@media(max-width:899px){
 .docs-main{margin-left:0}
 .doc-sidebar{width:min(300px,85vw);padding:12px;transform:translateX(-100%);visibility:hidden;transition:transform .2s ease,visibility .2s ease;box-shadow:none}
 body.docs-nav-open .doc-sidebar{transform:none;visibility:visible;box-shadow:0 18px 40px rgba(15,23,42,.18)}
 .docs-nav-close{display:inline-flex}
 .docs-nav-overlay{display:block}
 .docs-menu-toggle{display:inline-flex}
 .docs-topbar{padding:10px 14px;gap:8px}
 .docs-topbar-title strong{font-size:17px}
 .docs-topbar-title span{font-size:11.5px}
 .docs-topbar-actions .btn{display:none}
 .docs-content{max-width:none;padding:14px 12px 28px}
 .doc-content{padding:18px 16px;border-radius:12px}
 .doc-title{font-size:22px}
}
@media(max-width:640px){
 .doc-sidebar{width:min(86vw,300px)}
 .docs-topbar-title span{display:none}
 .doc-content{padding:14px 12px;border-radius:10px}
 .doc-title{font-size:20px}
 .doc-lead{font-size:13px}
 .doc-note{font-size:12.5px}
 .doc-section{margin-top:24px;padding-top:16px}
 .doc-heading{font-size:17px}
 .doc-subheading{font-size:14.5px}
 .endpoint-line{gap:6px}
 .endpoint-path{font-size:12px}
 .doc-table{min-width:560px;font-size:12.5px}
 .doc-table th,.doc-table td{padding:8px 9px}
 .code-block code{font-size:11.5px}
 .try-params label{width:100%}
 .try-params label input,.try-params label select{flex:1;width:auto;min-width:0}
 .doc-cards{grid-template-columns:1fr}
 .doc-toolbar .input{flex-basis:100%}
 .doc-toolbar .btn{width:100%}
 pre.result{font-size:11.5px}
}
"""

DOCS_TMPL = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>__APP_NAME__ · API 文档</title>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#07C160">
<style>__CSS__</style></head>
<body class="page-docs">
<a class="skip-link" href="#main">跳到主要内容</a>
<aside class="doc-sidebar" id="docs-nav">
  <div class="doc-sidebar-top">
    <a class="brand doc-sidebar-brand" href="/" aria-label="__APP_NAME__ 首页">
      <svg class="brand-icon" viewBox="0 0 64 64" aria-hidden="true" focusable="false">
        <rect width="64" height="64" rx="14" fill="#07C160"></rect>
        <path d="M22 16h20a10 10 0 0 1 10 10v8a10 10 0 0 1-10 10H34l-9 8v-8h-3a10 10 0 0 1-10-10v-8a10 10 0 0 1 10-10z" fill="#ffffff"></path>
        <circle cx="27" cy="30" r="3" fill="#07C160"></circle>
        <circle cx="38" cy="30" r="3" fill="#07C160"></circle>
      </svg>
      <span class="brand-name">LIT Gateway</span>
      <span class="brand-tag">API 文档</span>
    </a>
    <button id="docs-nav-close" class="docs-nav-close" type="button" aria-label="关闭文档目录">×</button>
  </div>
  <nav class="doc-nav" aria-label="文档目录">
    <p class="doc-nav-title">目录</p>
    __NAV__
  </nav>
  <div class="doc-sidebar-footer">
    <a class="doc-nav-link" href="/admin"><span aria-hidden="true">⚙️</span> 管理后台</a>
  </div>
</aside>
<div id="docs-nav-overlay" class="docs-nav-overlay" hidden></div>
<div class="docs-main">
  <header class="docs-topbar">
    <button id="docs-menu-toggle" class="docs-menu-toggle" type="button" aria-label="打开文档目录" aria-controls="docs-nav" aria-expanded="false">☰</button>
    <div class="docs-topbar-title"><strong>API 文档</strong><span>__APP_NAME__ · v__VERSION__</span></div>
  </header>
  <main id="main" class="docs-content">
    <article class="doc-content">
      <h1 class="doc-title">__APP_NAME__ · 接口文档</h1>
      <p class="doc-lead">多用户统一认证接口网关：每个用户拥有自己的统一认证账号、独立 CAS 会话和独立 API Key。当前业务分类：智慧学工（智能查寝、签到助手）、教务系统（课表查询）。</p>
      <p class="doc-note">调用 <code class="code-inline">/api/v1/**</code> 接口需在请求头带 <code class="code-inline">X-API-Key</code>；页面本身不包含任何密钥，在线调试时只从本机浏览器读取。</p>
      <div class="doc-cards">
        <div class="doc-card"><h3 class="doc-card-title">统一响应格式</h3><p>成功：<code class="code-inline">{"code":0,"msg":"ok","data":...}</code></p><p>错误：<code class="code-inline">code</code> 为非 0，并带 <code class="code-inline">msg</code> / <code class="code-inline">hint</code>。</p></div>
        <div class="doc-card"><h3 class="doc-card-title">鉴权方式</h3><p>每个网关用户有独立 API Key，请求头：<code class="code-inline">X-API-Key</code>。</p><p>可在后台「我的账号」查看/复制/重置。</p></div>
        <div class="doc-card"><h3 class="doc-card-title">会话机制</h3><p>每个 API Key 对应用户自己的统一认证会话；失效时自动重新登录，需要 MFA 时到后台「登录调试」处理。</p></div>
      </div>
      <div class="doc-toolbar">
        <input id="api-key" class="input" type="password" placeholder="输入 API Key（仅保存在本机浏览器）" autocomplete="off">
        <button class="btn btn-primary" onclick="saveKey()">保存 API Key</button>
      </div>
      <p id="key-tip" class="doc-lead">API Key 可在后台 <a href="/admin">/admin</a> 登录后查看；在线调试时放到 <code class="code-inline">X-API-Key</code> 请求头。</p>
      __CARDS__
      <section class="doc-section" id="error-codes">
        <h2 class="doc-heading">通用错误码</h2>
        <div class="table-wrap"><table class="doc-table">
          <thead><tr><th>HTTP / code</th><th>含义</th><th>处理建议</th></tr></thead>
          <tbody>
            <tr><td>401 + <code>无效的 API Key</code></td><td>API Key 缺失或错误</td><td>在页面顶部填写正确的 API Key</td></tr>
            <tr><td>503 + <code>SESSION_ERROR</code></td><td>CAS 会话失效且自动登录失败 / 需要企业微信验证码</td><td>到 <a href="/admin">后台</a>「登录调试」处理（输入验证码或重新登录）</td></tr>
            <tr><td>403 + <code>FORBIDDEN</code></td><td>智能查寝角色无权限（如「学生组」）</td><td>使用有查寝权限的角色（如「班级管理员」）</td></tr>
            <tr><td>200 + <code>{"code":0}</code></td><td>成功</td><td>-</td></tr>
          </tbody>
        </table></div>
      </section>
      <footer class="site-footer">接口文档 · 多用户版 · <a href="/admin">后台管理</a></footer>
    </article>
  </main>
</div>
<script>
const ENDPOINTS = __ENDPOINTS_JSON__;
function copyTextFallback(text){
  const ta=document.createElement('textarea');
  ta.value=text; ta.setAttribute('readonly','');
  ta.style.position='fixed'; ta.style.top='-1000px'; ta.style.left='-1000px'; ta.style.opacity='0';
  document.body.appendChild(ta); ta.focus(); ta.select();
  let ok=false;
  try{ok=document.execCommand('copy');}catch(e){ok=false;}
  document.body.removeChild(ta);
  return ok;
}
async function copyText(text){
  let ok=false;
  if(window.isSecureContext && navigator.clipboard && navigator.clipboard.writeText){
    try{await navigator.clipboard.writeText(text);ok=true;}catch(e){ok=false;}
  }
  if(!ok) ok=copyTextFallback(text);
  return ok;
}
function saveKey(){
  const v=document.getElementById('api-key').value.trim();
  localStorage.setItem('lit_api_key',v);
  document.getElementById('key-tip').textContent=v?'API Key 已保存到本机浏览器（localStorage）':'已清除 API Key';
}
(function(){const k=localStorage.getItem('lit_api_key');if(k)document.getElementById('api-key').value=k;})();
(function(){
  const toggle=document.getElementById('docs-menu-toggle');
  const closeBtn=document.getElementById('docs-nav-close');
  const overlay=document.getElementById('docs-nav-overlay');
  function openNav(){document.body.classList.add('docs-nav-open');if(overlay)overlay.hidden=false;if(toggle)toggle.setAttribute('aria-expanded','true');}
  function closeNav(){document.body.classList.remove('docs-nav-open');if(overlay)overlay.hidden=true;if(toggle)toggle.setAttribute('aria-expanded','false');}
  if(toggle)toggle.addEventListener('click',openNav);
  if(closeBtn)closeBtn.addEventListener('click',closeNav);
  if(overlay)overlay.addEventListener('click',closeNav);
  document.querySelectorAll('#docs-nav .doc-nav-link').forEach(a=>a.addEventListener('click',closeNav));
  document.addEventListener('keydown',e=>{if(e.key==='Escape')closeNav();});
  window.addEventListener('resize',()=>{if(window.innerWidth>899)closeNav();});
})();
document.addEventListener('click',async function(ev){
  const copyBtn=ev.target.closest('button.copy');
  if(copyBtn){const pre=document.getElementById(copyBtn.dataset.copy);if(pre){const ok=await copyText(pre.textContent);copyBtn.textContent=ok?'已复制':'复制失败，请手动选择';setTimeout(()=>copyBtn.textContent='复制',1600);}return;}
  const sendBtn=ev.target.closest('button.send');
  if(sendBtn){send(sendBtn.dataset.idx);}
});
async function send(idx){
  const ep=ENDPOINTS[idx]; const key=localStorage.getItem('lit_api_key')||'';
  if(!key){alert('请先在页面顶部填写并保存 API Key');return;}
  let url=ep.path; const qs=new URLSearchParams();
  ep.query.forEach(function(q){const el=document.getElementById('q-'+idx+'-'+q.name);if(el&&(el.value!==''||q.send_empty))qs.set(q.name,el.value);});
  if(qs.toString())url+='?'+qs.toString();
  const out=document.getElementById('out-'+idx); out.textContent='请求中…';
  const opts={method:ep.method,headers:{'X-API-Key':key},credentials:'same-origin'};
  if(ep.body){const ta=document.getElementById('body-'+idx); if(ta&&ta.value.trim()){try{JSON.parse(ta.value);}catch(e){out.textContent='请求体 JSON 格式错误: '+e.message;return;} opts.headers['Content-Type']='application/json';opts.body=ta.value.trim();}}
  try{const r=await fetch(url,opts);const text=await r.text();let pretty=text;try{pretty=JSON.stringify(JSON.parse(text),null,2);}catch(e){}out.textContent='HTTP '+r.status+'\\n'+pretty;}
  catch(e){out.textContent='请求失败: '+e;}
}
</script>
</body></html>"""
