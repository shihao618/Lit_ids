"""前台接口文档页面（用户视角，无需登录后台）.

页面:
  GET  /            前台接口文档（HTML，自动根据 OpenAPI 生成）
    GET  /api/v1/meta 服务元信息（JSON）

说明:
  * 文档内容由 FastAPI 的 OpenAPI 自动生成（新增接口后页面自动出现），
    再叠加少量人工维护的「请求/响应示例」，排版更适合阅读/拷贝；
  * 页面不包含任何密钥；"在线调试" 需要用户自己填写 API Key（存 localStorage）；
  * Swagger UI 仍在 /docs，后台调试界面在 /admin。
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
    "/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect",
    "/healthz", "/readyz", "/api/v1/meta",
}

#: 人工维护的示例（key = (METHOD, path)）
EXAMPLES: dict[tuple[str, str], dict[str, Any]] = {
    ("GET", "/api/v1/auth/status"): {
        "response": {
            "logged_in": True, "username": "B2607xxxx",
            "login_time": "2026-10-30 09:00:00",
            "final_url": "http://ids.lit.edu.cn/authserver/personal/",
            "fingerprint_saved": True, "cookie_names": ["JSESSIONID", "TGC"],
            "tgc_masked": "eyJhbGciOiJIUzUxMi...（已脱敏）",
            "pending_mfa": False, "mfa_used": False, "last_error": None,
        },
    },
    ("POST", "/api/v1/auth/login"): {
        "request": {"force": True, "mfa_code": None},
        "response": {"code": 0, "msg": "ok", "data": {
            "ok": True, "reused": False, "logged_in": True,
            "username": "B2607xxxx", "login_time": "2026-10-30 09:00:00",
            "final_url": "http://ids.lit.edu.cn/authserver/personal/"}},
    },
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
    ("POST", "/api/v1/dorm/check-records"): {
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
}

TAG_ORDER = ["认证/会话", "智能查寝", "教务系统", "元信息"]

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


def _render_endpoint(ep: dict, idx: int, base: str) -> str:
    eid = "ep-%d" % idx
    method_cls = "m-" + ep["method"].lower()
    desc = _render_description(ep["description"])
    parts = [f'<article class="endpoint" id="{eid}">',
             f'<div class="ep-head"><span class="method {method_cls}">{ep["method"]}</span>'
             f'<code class="path">{_h(ep["path"])}</code></div>',
             f'<h3>{_h(ep["summary"])}</h3>']
    if desc:
        parts.append(f'<div class="desc">{desc}</div>')
    if ep["params"]:
        parts.append('<div class="table-wrap"><table class="params"><thead><tr><th>参数</th><th>位置</th>'
                     '<th>类型</th><th>必填</th><th>默认值</th><th>说明</th></tr></thead><tbody>')
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
        parts.append('<div class="blk-title">请求体参数（JSON）</div>')
        parts.append('<div class="table-wrap"><table class="params"><thead><tr><th>参数</th><th>类型</th>'
                     '<th>必填</th><th>默认值</th><th>说明</th></tr></thead><tbody>')
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
        parts.append('<div class="blk"><div class="blk-title">请求体示例 '
                     '<button class="copy" data-copy="req-%d">复制</button></div>'
                     '<pre id="req-%d">%s</pre></div>' % (idx, idx, _h(body)))
    curl = _curl(ep, base)
    parts.append('<div class="blk"><div class="blk-title">curl 示例 '
                 '<button class="copy" data-copy="curl-%d">复制</button></div>'
                 '<pre id="curl-%d">%s</pre></div>' % (idx, idx, _h(curl)))
    resp = json.dumps(ep["response"], ensure_ascii=False, indent=2)
    parts.append('<div class="blk"><div class="blk-title">响应示例 '
                 '<button class="copy" data-copy="resp-%d">复制</button></div>'
                 '<pre id="resp-%d">%s</pre></div>' % (idx, idx, _h(resp[:4000])))
    # 在线调试
    parts.append('<div class="try"><div class="blk-title">在线调试（需先在上方填写 API Key）</div>')
    if ep["params"]:
        parts.append('<div class="try-params">')
        for p in ep["params"]:
            if p["in"] != "query":
                continue
            if p["enum"]:
                opts = ['<option value="">（不传）</option>'] + [
                    '<option value="%s">%s</option>' % (_h(v), _h(v)) for v in p["enum"]]
                parts.append('<label>%s<select id="q-%d-%s">%s</select></label>'
                             % (_h(p["name"]), idx, _h(p["name"]), "".join(opts)))
                continue
            default = "" if p["default"] is None else p["default"]
            parts.append('<label>%s<input id="q-%d-%s" value="%s" placeholder="%s"></label>'
                         % (_h(p["name"]), idx, _h(p["name"]), _h(default), _h(p["description"])))
        parts.append("</div>")
    if ep["request"] is not None:
        body = json.dumps(ep["request"], ensure_ascii=False, indent=2)
        parts.append('<textarea id="body-%d" spellcheck="false">%s</textarea>' % (idx, _h(body)))
    parts.append('<button class="send" data-idx="%d">发送请求</button>' % idx)
    parts.append('<pre class="result" id="out-%d">（响应结果）</pre>' % idx)
    parts.append("</div></article>")
    return "\n".join(parts)


def _render_html(app: Any, request: Request) -> str:
    from .. import __version__

    runtime = getattr(request.app.state, "runtime_settings", None)
    app_name = runtime.get("app_name") if runtime is not None else None
    app_name = app_name or app.title
    base = str(request.base_url).rstrip("/")
    groups = _build_endpoints(app)
    endpoints_js: list[dict] = []
    cards: list[str] = []
    nav: list[str] = []
    idx = 0
    for group in groups:
        nav.append(f'<div class="nav-title">{_h(group["tag"])}</div>')
        cards.append(f'<h2 class="group" id="g-{idx}">{_h(group["tag"])}</h2>')
        for ep in group["endpoints"]:
            eid = "ep-%d" % idx
            nav.append(f'<a href="#{eid}"><span class="m-{ep["method"].lower()}">'
                       f'{ep["method"]}</span> {_h(ep["path"])}</a>')
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
            idx += 1

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
*{box-sizing:border-box}
body{margin:0;background:#f5f7fb;color:#1e2430;font:14px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{background:#16233d;color:#fff;padding:14px 26px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:20;box-shadow:0 2px 8px rgba(10,20,40,.15)}
header h1{font-size:17px;margin:0;font-weight:600}
header .links a{color:#a9c8ff;text-decoration:none;margin-left:18px;font-size:13px}
.layout{display:flex;max-width:1280px;margin:0 auto;padding:20px 18px;gap:22px;align-items:flex-start}
nav.side{width:265px;flex:none;position:sticky;top:72px;max-height:calc(100vh - 92px);overflow:auto;background:#fff;border-radius:10px;padding:14px 12px;box-shadow:0 1px 4px rgba(20,40,80,.08)}
nav.side .nav-title{font-size:12px;color:#8794ab;text-transform:uppercase;margin:12px 6px 5px;letter-spacing:.06em}
nav.side a{display:block;padding:5px 8px;border-radius:6px;color:#2b3852;text-decoration:none;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
nav.side a:hover{background:#eef3fd;color:#1d4ed8}
nav.side a span{display:inline-block;width:46px;font-weight:700;font-size:11px}
main{flex:1;min-width:0}
.hero{background:linear-gradient(135deg,#1e3a8a,#2f6fed);color:#fff;border-radius:12px;padding:24px 26px;margin-bottom:20px}
.hero h2{margin:0 0 8px;font-size:21px}
.hero p{margin:4px 0;color:#dbe6ff;font-size:13.5px}
.hero .kv{display:flex;gap:26px;flex-wrap:wrap;margin-top:14px;font-size:13px;color:#e6eeff}
.hero code{background:rgba(255,255,255,.16);padding:2px 7px;border-radius:5px}
.authbox{background:#fff;border-radius:10px;padding:14px 18px;margin-bottom:20px;box-shadow:0 1px 4px rgba(20,40,80,.08);display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.authbox b{color:#16233d}
.authbox input{flex:1;min-width:240px;padding:8px 10px;border:1px solid #d5dce8;border-radius:7px;font:inherit}
.authbox button{padding:8px 16px;border:0;border-radius:7px;background:#2f6fed;color:#fff;font:inherit;cursor:pointer}
.authbox .note{font-size:12px;color:#8794ab;width:100%}
h2.group{font-size:16px;margin:26px 0 12px;padding-left:10px;border-left:4px solid #2f6fed;color:#16233d}
.endpoint{background:#fff;border-radius:10px;padding:18px 20px;margin-bottom:16px;box-shadow:0 1px 4px rgba(20,40,80,.08)}
.ep-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.method{display:inline-block;padding:3px 10px;border-radius:6px;color:#fff;font-size:12px;font-weight:700;letter-spacing:.03em}
.m-get{background:#15905a}.m-post{background:#2f6fed}.m-put{background:#b45309}.m-delete{background:#c94040}
.endpoint .path{font-size:14px;font-weight:600;color:#1e293b;background:#f1f5fb;padding:3px 8px;border-radius:5px;word-break:break-all}
.endpoint h3{font-size:14.5px;margin:10px 0 4px;color:#1e293b}
.desc{color:#5b6880;font-size:13px;margin:4px 0 10px}
table.params{width:100%;border-collapse:collapse;font-size:12.5px;margin:10px 0}
table.params th,table.params td{border-bottom:1px solid #eef2f8;padding:6px 8px;text-align:left;vertical-align:top}
table.params th{color:#66738a;font-weight:600;background:#f8fafd}
.blk{margin-top:12px}
.blk-title{font-size:12.5px;color:#66738a;font-weight:600;margin-bottom:5px;display:flex;align-items:center;gap:10px}
button.copy{padding:1px 8px;font-size:11.5px;border:1px solid #cfd9e8;background:#f8fafd;border-radius:5px;color:#445;cursor:pointer}
pre{background:#0f172a;color:#d8e3f6;padding:12px;border-radius:8px;overflow:auto;font-size:12.5px;line-height:1.5;margin:0}
.try{margin-top:14px;border-top:1px dashed #dfe6f1;padding-top:12px}
.try-params{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.try-params label{font-size:12px;color:#66738a;display:flex;align-items:center;gap:6px}
.try-params input{width:150px;padding:5px 8px;border:1px solid #d5dce8;border-radius:6px;font:inherit}
.try textarea{width:100%;min-height:74px;padding:8px 10px;border:1px solid #d5dce8;border-radius:7px;font:12.5px Consolas,monospace;background:#fbfcfe}
button.send{margin-top:9px;padding:7px 18px;border:0;border-radius:7px;background:#16233d;color:#fff;font:inherit;cursor:pointer}
button.send:disabled{opacity:.6;cursor:not-allowed}
pre.result{margin-top:10px;max-height:260px}
.err-codes{background:#fff;border-radius:10px;padding:16px 20px;margin-top:22px;box-shadow:0 1px 4px rgba(20,40,80,.08)}
.err-codes h2{font-size:15px;margin:0 0 10px;color:#16233d}
.err-codes table{width:100%;border-collapse:collapse;font-size:12.5px}
.err-codes td,.err-codes th{border-bottom:1px solid #eef2f8;padding:6px 8px;text-align:left}
footer{max-width:1280px;margin:10px auto 30px;padding:0 18px;color:#8794ab;font-size:12px}
@media(max-width:980px){.layout{flex-direction:column;align-items:stretch}.layout>main{width:100%;min-width:0}.layout>nav.side{position:static;width:100%;max-height:none}}
@media(max-width:640px){
 body{-webkit-text-size-adjust:100%}
 header{padding:10px 12px;flex-wrap:wrap;gap:8px;position:sticky}
 header h1{font-size:15px;line-height:1.35}
 header .links{display:flex;flex-wrap:wrap;gap:6px 14px;width:100%}
 header .links a{margin-left:0;font-size:12px}
 .layout{padding:12px;gap:12px}
 nav.side{display:flex;flex-wrap:wrap;gap:4px;padding:10px;border-radius:8px}
 nav.side .nav-title{width:100%;margin:2px 6px}
 nav.side a{display:inline-block;max-width:100%;padding:5px 9px;background:#f3f6fc;border-radius:999px;font-size:12px;white-space:nowrap}
 nav.side a span{width:auto;margin-right:5px}
 .hero{padding:16px 14px;border-radius:10px;margin-bottom:12px}
 .hero h2{font-size:18px}
 .hero p{font-size:12.5px}
 .hero .kv{gap:6px 14px;font-size:12px}
 .hero .kv code{word-break:break-word;overflow-wrap:anywhere}
 .authbox{padding:12px;gap:8px;flex-direction:column;align-items:stretch;margin-bottom:12px}
 .authbox b{font-size:13px}
 .authbox input{width:100%;min-width:0}
 .authbox button{width:100%}
 h2.group{font-size:15px;margin:18px 0 10px}
 .endpoint{padding:14px 12px;border-radius:8px}
 .endpoint h3{font-size:14px;margin-top:8px}
 .endpoint .path{font-size:12.5px;word-break:break-all}
 .table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:8px 0}
 table.params{min-width:560px;margin:0;font-size:12px}
 .err-codes{padding:14px 12px}
 .err-codes table{min-width:480px}
 pre{font-size:11.5px;padding:10px;border-radius:6px}
 .try-params{gap:8px}
 .try-params label{width:100%}
 .try-params input{flex:1;width:auto;min-width:0}
 button.send{width:100%}
 footer{padding:0 12px;font-size:11.5px}
}
"""

DOCS_TMPL = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>__APP_NAME__ · 接口文档</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>__CSS__</style></head>
<body>
<header>
  <h1>__APP_NAME__ · 接口文档 <span style="font-weight:400;font-size:12px;color:#93a7c8">v__VERSION__</span></h1>
  <div class="links">
    <a href="/docs" target="_blank">Swagger 调试</a>
    <a href="/admin" target="_blank">后台管理</a>
    <a href="/openapi.json" target="_blank">OpenAPI</a>
  </div>
</header>
<div class="layout">
  <nav class="side">
    <div class="nav-title">目录</div>
    __NAV__
  </nav>
  <main>
    <div class="hero">
      <h2>洛阳理工学院统一认证 · 接口服务</h2>
      <p>多用户版：每个网关用户拥有自己的统一认证账号、独立 CAS 会话和独立 API Key；当前业务接口：智能查寝、教务课表。</p>
      <div class="kv">
        <span>Base URL：<code>__BASE__</code></span>
        <span>鉴权：<code>X-API-Key</code> 请求头</span>
        <span>响应格式：<code>{"code":0,"msg":"ok","data":...}</code></span>
      </div>
    </div>

    <div class="authbox">
      <b>API Key：</b>
      <input id="api-key" type="password" placeholder="输入 API_KEY（仅保存在本机浏览器）" autocomplete="off">
      <button onclick="saveKey()">保存</button>
      <span id="key-tip" class="note">每个用户有自己的 API Key（后台 <a href="/admin" target="_blank">/admin</a> 登录后查看）；在线调试时会把该 Key 放进 X-API-Key 请求头，页面本身不包含任何密钥。</span>
    </div>

    __CARDS__

    <section class="err-codes">
      <h2>通用错误码</h2>
      <div class="table-wrap"><table>
        <tr><th>HTTP / code</th><th>含义</th><th>处理建议</th></tr>
        <tr><td>401 + <code>{"detail":"无效的 API Key…"}</code></td><td>API Key 缺失或错误</td><td>在页面顶部填写正确的 API_KEY</td></tr>
        <tr><td>503 + <code>SESSION_ERROR</code></td><td>CAS 会话失效且自动登录失败 / 需要企业微信验证码</td><td>到 <a href="/admin">后台</a> 登录调试页处理（输入验证码或重新登录）</td></tr>
        <tr><td>403 + <code>FORBIDDEN</code></td><td>智能查寝角色无权限（如「学生组」）</td><td>使用有查寝权限的角色（如「班级管理员」）</td></tr>
            <tr><td>200 + <code>{"code":0}</code></td><td>成功</td><td>-</td></tr>
      </table></div>
    </section>
    <footer>接口文档由 OpenAPI 自动生成 · 多用户版 · 配置自己的统一认证账号/查看 API Key 请访问 <a href="/admin">后台管理</a></footer>
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
function saveKey(){const v=document.getElementById('api-key').value.trim();localStorage.setItem('lit_api_key',v);document.getElementById('key-tip').textContent=v?'API Key 已保存到本机浏览器（localStorage）':'已清除 API Key';}
(function(){const k=localStorage.getItem('lit_api_key');if(k)document.getElementById('api-key').value=k;})();
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
