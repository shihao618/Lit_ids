#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""洛阳理工学院 统一身份认证 (ids.lit.edu.cn) 账号登录脚本.

流程
----
1. GET  /authserver/login                      -> 取 execution / salt / JSESSIONID
2. POST /authserver/captcha/gen?type=SLIDER    -> 滑块验证码背景图 + 拼图模板图
3. 本地图像识别滑块缺口位置 randomX
4. POST /authserver/captcha/check              -> 得到验证码 token (= captcha id)
5. POST /authserver/login                      -> AES-CBC 加密密码 + token
6. 若返回"企业微信验证码验证"页面: 输入验证码完成二次验证
   (首次登录需要; 通过后保存的设备指纹被服务端信任, 之后可免二次验证)
7. 登录成功, 保存 JSESSIONID / TGC 会话到 cookies.json

依赖: Pillow, numpy  (HTTP 与 AES 全部用标准库实现, 不需要 requests / pycryptodome)
    pip install pillow numpy

用法
----
    python lit_login.py
    python lit_login.py -u B2607xxxx -p 'xxxx'
    python lit_login.py --force-login            # 忽略已保存的会话, 强制重新登录
    python lit_login.py --mfa-code 123456        # 非交互传入企业微信验证码
    python lit_login.py --fingerprint <32位hex>  # 使用浏览器中已受信任的设备指纹
    python lit_login.py -s https://xxx.lit.edu.cn/  # 登录后跳转已注册的 service

说明: 本项目仅用于自动化登录自己的账号(课程/门户对接等), 请勿用于其他用途.
"""
import argparse
import base64
import http.cookiejar
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

try:
    import numpy as np
    from PIL import Image
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖, 请先执行: pip install pillow numpy")

BASE = "https://ids.lit.edu.cn"
LOGIN_URL = BASE + "/authserver/login"
CAPTCHA_GEN_URL = BASE + "/authserver/captcha/gen?type=SLIDER"
CAPTCHA_CHECK_URL = BASE + "/authserver/captcha/check"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0")

#: 命令行默认值从环境变量读取（不要把真实账号密码写进代码）
DEFAULT_USERNAME = os.getenv("LIT_USERNAME", "")
DEFAULT_PASSWORD = os.getenv("LIT_PASSWORD", "")


# =============================================================================
# 纯 Python AES-128-CBC 加密 (与页面 CryptoJS 行为一致)
# =============================================================================
_SBOX = [
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
]
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36]


def _xtime(a):
    a <<= 1
    if a & 0x100:
        a ^= 0x11b
    return a & 0xff


def _expand_key(key):
    assert len(key) == 16
    w = [list(key[i * 4:(i + 1) * 4]) for i in range(4)]
    for i in range(4, 44):
        temp = list(w[i - 1])
        if i % 4 == 0:
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[b] for b in temp]
            temp[0] ^= _RCON[i // 4 - 1]
        w.append([w[i - 4][j] ^ temp[j] for j in range(4)])
    return w


def _aes_encrypt_block(block, w):
    s = [[block[r + 4 * c] for c in range(4)] for r in range(4)]

    def add_round_key(rnd):
        for c in range(4):
            for r in range(4):
                s[r][c] ^= w[rnd * 4 + c][r]

    add_round_key(0)
    for rnd in range(1, 11):
        for r in range(4):
            for c in range(4):
                s[r][c] = _SBOX[s[r][c]]
        for r in range(1, 4):
            s[r] = s[r][r:] + s[r][:r]
        if rnd != 10:
            for c in range(4):
                a = [s[r][c] for r in range(4)]
                t = a[0] ^ a[1] ^ a[2] ^ a[3]
                s[0][c] = a[0] ^ t ^ _xtime(a[0] ^ a[1])
                s[1][c] = a[1] ^ t ^ _xtime(a[1] ^ a[2])
                s[2][c] = a[2] ^ t ^ _xtime(a[2] ^ a[3])
                s[3][c] = a[3] ^ t ^ _xtime(a[3] ^ a[0])
        add_round_key(rnd)
    out = bytearray(16)
    for c in range(4):
        for r in range(4):
            out[r + 4 * c] = s[r][c]
    return bytes(out)


def aes_cbc_encrypt_b64(plaintext, key_str, iv_str="1234567890abcdef"):
    """AES-128-CBC + PKCS7, 输出 base64 (等价于页面 CryptoJS.AES.encrypt)."""
    key = key_str.encode("utf-8")
    if len(key) != 16:
        raise ValueError("salt 必须为 16 字节, 实际: %r" % key_str)
    iv = iv_str.encode("utf-8")
    data = plaintext.encode("utf-8")
    pad = 16 - len(data) % 16
    data += bytes([pad]) * pad
    w = _expand_key(key)
    out = bytearray()
    prev = iv
    for i in range(0, len(data), 16):
        block = bytes(a ^ b for a, b in zip(data[i:i + 16], prev))
        enc = _aes_encrypt_block(block, w)
        out += enc
        prev = enc
    return base64.b64encode(bytes(out)).decode("ascii")


# =============================================================================
# 极简 HTTP 客户端 (标准库, 自动管理 cookie)
# =============================================================================
class HttpClient:
    def __init__(self):
        self.cookiejar = http.cookiejar.CookieJar()
        handlers = [
            urllib.request.HTTPCookieProcessor(self.cookiejar),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        ]
        self.opener = urllib.request.build_opener(*handlers)

    def _do(self, req):
        try:
            resp = self.opener.open(req, timeout=30)
            status, final_url, body = resp.status, resp.geturl(), resp.read()
        except urllib.error.HTTPError as e:
            status, final_url, body = e.code, e.geturl(), e.read()
        return status, final_url, body

    def get(self, url, headers=None):
        req = urllib.request.Request(url, headers=dict(headers or {}), method="GET")
        req.add_header("User-Agent", UA)
        return self._do(req)

    def post(self, url, data, content_type, headers=None, referer=None):
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers=dict(headers or {}))
        req.add_header("User-Agent", UA)
        req.add_header("Content-Type", content_type)
        if referer:
            req.add_header("Referer", referer)
            req.add_header("Origin", BASE)
        return self._do(req)

    def post_json(self, url, obj, referer=None):
        data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        return self.post(url, data, "application/json;charset=UTF-8",
                         headers={"Accept": "application/json, text/javascript, */*; q=0.01",
                                  "X-Requested-With": "XMLHttpRequest"},
                         referer=referer or LOGIN_URL)

    def post_form(self, url, fields, referer=None):
        data = urllib.parse.urlencode(fields).encode("utf-8")
        return self.post(url, data, "application/x-www-form-urlencoded",
                         referer=referer or LOGIN_URL)

    def cookies_dict(self):
        return {c.name: c.value for c in self.cookiejar}


# =============================================================================
# 滑块验证码识别
# =============================================================================
# 服务端内置的第一套滑块模板 (tianai-captcha slider_1/fixed.png, 110x110):
# 缺口(凹槽)轮廓, alpha 通道决定叠加效果. 内嵌以便纯离线识别.
_FIXED_PNG_B64 = (
    "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"
)
_FIXED_RGBA = None


def _fixed_rgba():
    global _FIXED_RGBA
    if _FIXED_RGBA is None:
        img = Image.open(io.BytesIO(base64.b64decode(_FIXED_PNG_B64))).convert("RGBA")
        _FIXED_RGBA = np.asarray(img, dtype=np.float64)
    return _FIXED_RGBA


def _data_url_to_image(s):
    m = re.match(r"^data:image/[a-zA-Z0-9.+-]+;base64,(.*)$", s or "", re.S)
    if not m:
        raise ValueError("不是 data:image URL")
    return Image.open(io.BytesIO(base64.b64decode(m.group(1))))


def recognize_slider_x(bg_img, tpl_img):
    """识别滑块缺口的 strip 原点坐标 randomX.

    原理
    ----
    backgroundImage 在缺口处 = 半透明灰色模板 fixed.png 叠加在原图上;
    templateImage 是一条 110x360 的透明长条, 里面放着"从原图切下来"的拼图块
    (位置 y=randomY, x=0). 因此对候选 X:
        pred = fixed_rgb*a + strip_content*(1-a)
    与 bg[randomY:randomY+110, X:X+110] 的残差在真实 randomX 处最小.

    返回 (randomX, 质量信息 dict); 无法识别时 randomX 为 None.
    """
    bg = np.asarray(bg_img.convert("RGB"), dtype=np.float64)
    tpl = np.asarray(tpl_img.convert("RGBA"), dtype=np.float64)
    bg_h, bg_w = bg.shape[:2]
    tpl_h, tpl_w = tpl.shape[:2]
    if tpl_w != 110 or bg_h != tpl_h:
        return None, {"reason": "未知的验证码图片尺寸 %s / %s" % (tpl.shape[:2], bg.shape[:2])}

    alpha = tpl[:, :, 3]
    ys, xs = np.where(alpha > 200)          # 缺口(凹槽)本体 = 被切掉的区域
    if len(xs) == 0:
        return None, {"reason": "模板图为空"}
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    w, h = x1 - x0, y1 - y0
    style = "A" if (w, h, x0) == (88, 88, 11) else ("B" if (w, h, x0) == (82, 83, 15) else "?%dx%d@%d" % (w, h, x0))
    info = {"style": style, "piece_bbox_in_strip": [x0, y0, w, h]}

    if style != "A":
        # 目前内置模板只覆盖第一套滑块(约占 70% 的验证码). 识别不了就让调用方重新获取.
        info["reason"] = "未内置该滑块模板(%s), 重新获取验证码" % style
        return None, info

    fixed = _fixed_rgba()
    fh, fw = fixed.shape[:2]                      # 110 x 110
    fa = fixed[:, :, 3] / 255.0
    frgb = fixed[:, :, :3]
    inner = fixed[:, :, 3] > 100

    random_y = y0 - 11                            # fixed.png 内容在画布中的 y 偏移 = 11
    if random_y < 0 or random_y + fh > tpl_h:
        return None, {"reason": "randomY 越界"}

    strip_content = tpl[random_y:random_y + fh, 0:fw, :3]
    strip_a = tpl[random_y:random_y + fh, 0:fw, 3] / 255.0
    weight = inner & (strip_a > 0.9)
    if weight.sum() < 1000:
        return None, {"reason": "模板内容不足"}

    pred = frgb * fa[:, :, None] + strip_content * (1 - fa[:, :, None])
    scores = []
    for X in range(0, bg_w - fw + 1):
        region = bg[random_y:random_y + fh, X:X + fw, :]
        diff = np.abs(region - pred).mean(axis=2)
        scores.append(float((diff * weight).sum() / weight.sum()))
    scores = np.asarray(scores)
    best_x = int(np.argmin(scores))
    best = float(scores[best_x])
    # 与"次优"比较: 排除 ±20 邻域, 判断峰是否显著
    masked = scores.copy()
    masked[max(0, best_x - 20):best_x + 21] = np.inf
    second = float(masked.min()) if masked.size else 0.0
    info.update({"random_y": random_y, "residual": round(best, 2),
                 "second_best": round(second, 2),
                 "margin": round(second / max(best, 1e-6), 1)})
    return best_x, info


def recognize_ok(info, random_x):
    """判断识别结果是否足够可信 (不佳则重新获取验证码)."""
    if random_x is None or info.get("style") != "A":
        return False
    return info.get("residual", 1e9) < 15.0 and info.get("margin", 0) > 1.6


# =============================================================================
# CAS 登录流程
# =============================================================================
def parse_login_page(html):
    execution = re.search(r'name="execution"\s+value="([^"]+)"', html)
    salt = re.search(r'id="salt"\s+value="([^"]+)"', html)
    if not execution or not salt:
        raise RuntimeError("登录页缺少 execution/salt 字段, 页面结构可能已变更")
    return execution.group(1), salt.group(1)


def fetch_login_page(client, service=None):
    url = LOGIN_URL
    if service:
        url += "?service=" + urllib.parse.quote(service, safe="")
    status, final_url, body = client.get(url)
    html = body.decode("utf-8", "replace")
    if "未认证授权" in html or "未认证" in html and "execution" not in html:
        raise RuntimeError("该 service 未在统一身份认证平台注册/授权: %s" % service)
    return html, parse_login_page(html)


def get_captcha_token(client, max_tries=15, verbose=True, debug=None):
    """获取一个已通过服务端校验的验证码 token (即 captcha id).

    :param debug: 可选 list, 每次识别尝试会 append 一个 dict（供后台界面调试展示）
    """
    for attempt in range(1, max_tries + 1):
        status, _, body = client.post_json(CAPTCHA_GEN_URL, {})
        if status != 200:
            raise RuntimeError("获取验证码失败: HTTP %s" % status)
        data = json.loads(body.decode("utf-8"))
        cid = data["id"]
        captcha = data["captcha"]
        bg_img = _data_url_to_image(captcha["backgroundImage"])
        tpl_img = _data_url_to_image(captcha["templateImage"])

        random_x, info = recognize_slider_x(bg_img, tpl_img)
        if verbose:
            print("  [验证码 #%d] %s -> randomX=%s %s" % (
                attempt, cid[:8], random_x,
                "" if random_x is None else "(残差 %.1f, 峰值比 %.1f)" % (info["residual"], info["margin"])))
        accepted = recognize_ok(info, random_x)
        if debug is not None:
            debug.append({
                "attempt": attempt,
                "captcha_id": cid[:12],
                "style": info.get("style"),
                "random_x": random_x,
                "residual": info.get("residual"),
                "margin": info.get("margin"),
                "accepted": accepted,
                "reason": info.get("reason"),
            })
        if not accepted:
            if verbose and info.get("reason"):
                print("    跳过: " + info["reason"])
            continue

        payload = {
            "id": cid,
            "data": {
                "bgImageWidth": 600,
                "bgImageHeight": 360,
                "sliderImageWidth": 110,
                "sliderImageHeight": 360,
                "trackList": [
                    {"x": 0, "y": 0, "type": "down", "t": 0},
                    {"x": random_x, "y": 0, "type": "move", "t": 200},
                    {"x": random_x, "y": 0, "type": "up", "t": 400},
                ],
            },
        }
        status, _, body = client.post_json(CAPTCHA_CHECK_URL, payload)
        try:
            res = json.loads(body.decode("utf-8"))
        except Exception:
            raise RuntimeError("验证码校验接口返回异常: %s" % body[:200])
        if res.get("code") == 200:
            token = (res.get("data") or {}).get("id") or cid
            if verbose:
                print("    验证码校验通过, token=%s" % token)
            return token
        if verbose:
            print("    验证码校验失败: code=%s %s" % (res.get("code"), res.get("msg")))
        # code 4000/4001: 重新获取验证码即可
        time.sleep(0.2)
    raise RuntimeError("连续 %d 次未能通过滑块验证码识别" % max_tries)


def client_with_cookies(cookies):
    """创建一个带指定 cookie 的 HTTP 客户端."""
    client = HttpClient()
    if cookies:
        for name, value in cookies.items():
            c = http.cookiejar.Cookie(0, name, value, None, False, "ids.lit.edu.cn", False, False,
                                      "/", True, False, None, True, None, None, {})
            client.cookiejar.set_cookie(c)
    return client


def check_saved_session(cookies, timeout=20):
    """用已保存的 cookie (TGC) 检查 CAS 会话是否仍然有效.

    注意: /authserver/personal/ 是 SPA 外壳, 未登录也会返回 200, 不能用它判断.
    正确的判断方式: GET /authserver/login (不带 service):
      * TGC 有效 -> CAS 302 到 /authserver/personal/ (已登录首页)
      * TGC 失效 -> 返回登录页, 最终 URL 仍是 /authserver/login
    """
    if not cookies or "TGC" not in cookies:
        return False
    try:
        client = client_with_cookies(cookies)
        status, final_url, body = client.get(LOGIN_URL, headers={"Accept": "text/html"})
        return status == 200 and "/authserver/personal" in final_url
    except Exception:
        return False


def parse_page_error(text):
    """从登录相关页面上提取错误提示文本."""
    patterns = [
        r'id="credential\.errors"[^>]*>(.*?)</span>',
        r'class="error-tip"[^>]*>(.*?)</div>',
        r'class="error-tip"[^>]*>(.*?)</p>',
        r'id="error"[^>]*>(.*?)</div>',
    ]
    for p in patterns:
        m = re.search(p, text, re.S)
        if m:
            val = re.sub(r"<[^>]+>", " ", m.group(1))
            val = re.sub(r"\s+", " ", val).strip()
            if val:
                return val
    return ""


def is_mfa_page(text):
    """是否是"企业微信验证码"二次验证页面."""
    return ("企业微信验证码" in text) or ('id="realEventId"' in text) or ("sendAuth" in text)


def extract_mfa_execution(text):
    m = re.search(r'name="execution"\s+value="([^"]+)"', text)
    if not m:
        raise RuntimeError("二次验证页面缺少 execution 字段")
    return m.group(1)


def is_logged_in(final_url, text, cookies):
    """判断登录是否成功."""
    if any(k in cookies for k in ("CASTGC", "TGC")):
        # TGC 在二次验证前也可能下发, 因此还要求页面不是登录/二次验证页
        if not is_mfa_page(text) and "credential.errors" not in text:
            return True
    if "/authserver/login" not in final_url and "credential.errors" not in text:
        return True
    return False


def handle_mfa(client, page_html, mfa_code=None, verbose=True):
    """处理企业微信验证码二次验证. 返回 (ok, final_url, text, status)."""
    execution = extract_mfa_execution(page_html)
    attempts = 0
    while attempts < 6:
        if mfa_code:
            code = str(mfa_code).strip()
            mfa_code = None            # 只自动用一次, 之后交互输入
            print("[二次验证] 使用传入的验证码:", code)
        else:
            if verbose:
                print("-" * 60)
                print("学校要求企业微信二次验证: 请打开 企业微信 查看 '统一身份认证' 的验证码.")
                print("(未收到可输入 r 重新发送; 输入 q 放弃)")
            try:
                code = input("请输入企业微信验证码: ").strip()
            except EOFError:
                return False, "", "需要企业微信验证码, 但当前环境无法交互输入 (可用 --mfa-code 传入)", 0
            if code.lower() in ("q", "quit", "exit"):
                return False, "", "用户取消二次验证", 0
            if code.lower() in ("r", "resend"):
                fields = {"execution": execution, "_eventId": "sendAuth"}
                status, final_url, body = client.post_form(LOGIN_URL, fields)
                text = body.decode("utf-8", "replace")
                if is_mfa_page(text):
                    execution = extract_mfa_execution(text)
                    print("[二次验证] 验证码已重新发送.")
                else:
                    print("[二次验证] 重发请求返回异常页面.")
                continue
        if len(code) < 4:
            print("[二次验证] 验证码格式不正确, 请重试.")
            continue

        fields = {"execution": execution, "_eventId": "submit", "code": code}
        status, final_url, body = client.post_form(LOGIN_URL, fields)
        text = body.decode("utf-8", "replace")
        attempts += 1
        if not is_mfa_page(text):
            return True, final_url, text, status
        execution = extract_mfa_execution(text)
        err = parse_page_error(text) or "验证码错误"
        print("[二次验证] 失败: %s" % err)
        if "锁定" in err:
            return False, final_url, err, status
    return False, LOGIN_URL, "二次验证连续失败次数过多", 200


def login(username, password, service=None, max_rounds=3, verbose=True,
          max_captcha_tries=15, fingerprint=None, mfa_code=None):
    """账号 + 密码 (+ 企业微信验证码) 登录; 返回结果 dict."""
    client = HttpClient()
    fingerprint = fingerprint or uuid.uuid4().hex
    for rnd in range(1, max_rounds + 1):
        if verbose:
            print("[%d] 获取登录页 ..." % rnd)
        html, (execution, salt) = fetch_login_page(client, service)

        if verbose:
            print("[%d] 识别滑块验证码 ..." % rnd)
        token = get_captcha_token(client, max_tries=max_captcha_tries, verbose=verbose)

        encrypted = aes_cbc_encrypt_b64(password, salt)
        fields = {
            "username": username,
            "password": encrypted,
            "execution": execution,
            "_eventId": "submit",
            "captcha": token,
            "browserFingerprint": fingerprint,
        }
        if verbose:
            print("[%d] 提交账号密码 ..." % rnd)
        status, final_url, body = client.post_form(LOGIN_URL, fields)
        text = body.decode("utf-8", "replace")
        cookies = client.cookies_dict()

        # 情况一: 需要企业微信二次验证
        if is_mfa_page(text):
            print("[%d] 账号密码验证通过, 进入企业微信二次验证" % rnd)
            try:
                open("mfa_page.html", "w", encoding="utf-8").write(text)
            except OSError:
                pass
            ok, mfa_final_url, mfa_text, mfa_status = handle_mfa(
                client, text, mfa_code=mfa_code, verbose=verbose)
            cookies = client.cookies_dict()
            if ok:
                if verbose:
                    print("[%d] 二次验证通过: %s" % (rnd, mfa_final_url))
                return {"ok": True, "final_url": mfa_final_url, "cookies": cookies,
                        "status": mfa_status, "html": mfa_text, "fingerprint": fingerprint,
                        "mfa": True}
            return {"ok": False, "error": mfa_text, "final_url": mfa_final_url,
                    "cookies": cookies, "status": mfa_status, "fingerprint": fingerprint,
                    "mfa": True}

        # 情况二: 直接登录成功 (设备已被信任等)
        if is_logged_in(final_url, text, cookies):
            if verbose:
                print("[%d] 登录成功: %s" % (rnd, final_url))
                print("    cookies: " + ", ".join(sorted(cookies)))
            return {"ok": True, "final_url": final_url, "cookies": cookies,
                    "status": status, "html": text, "fingerprint": fingerprint, "mfa": False}

        # 情况三: 密码错误 / 验证码 token 被拒
        err = parse_page_error(text) or "unknown"
        if "captcha" in err.lower() or "验证码" in err:
            if verbose:
                print("[%d] 验证码 token 未被接受(%s), 重试" % (rnd, err))
            continue
        if verbose:
            print("[%d] 登录失败: %s  (final=%s)" % (rnd, err, final_url))
        try:
            open("last_login_response.html", "w", encoding="utf-8").write(text)
        except OSError:
            pass
        return {"ok": False, "error": err, "final_url": final_url,
                "cookies": cookies, "status": status, "fingerprint": fingerprint}
    return {"ok": False, "error": "重试 %d 次仍失败" % max_rounds,
            "cookies": client.cookies_dict(), "fingerprint": fingerprint}


# =============================================================================
# main
# =============================================================================
def load_saved_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def main(argv=None):
    ap = argparse.ArgumentParser(description="洛阳理工学院统一身份认证 账号登录脚本")
    ap.add_argument("-u", "--username", default=DEFAULT_USERNAME, help="学号/工号")
    ap.add_argument("-p", "--password", default=DEFAULT_PASSWORD, help="密码")
    ap.add_argument("-s", "--service", default=None,
                    help="可选: 登录后跳转的业务系统地址 (CAS service 参数)")
    ap.add_argument("--mfa-code", default=None,
                    help="企业微信二次验证码 (不给则登录时代交互输入)")
    ap.add_argument("--fingerprint", default=None,
                    help="浏览器设备指纹 (32位hex); 默认复用已保存的值, 用于'信任设备'")
    ap.add_argument("--save-cookie", default="cookies.json",
                    help="把登录后的 cookie / 会话信息保存到该文件 (默认 cookies.json)")
    ap.add_argument("--force-login", action="store_true",
                    help="即使已保存的会话仍然有效也重新登录")
    ap.add_argument("--max-captcha-tries", type=int, default=15, help="滑块验证码识别重试上限")
    ap.add_argument("-q", "--quiet", action="store_true", help="少打印")
    args = ap.parse_args(argv)
    if not args.username or not args.password:
        ap.error("缺少账号或密码：请用 -u/-p 参数，或设置环境变量 LIT_USERNAME / LIT_PASSWORD")

    print("=" * 70)
    print(" 洛阳理工学院 统一身份认证 (ids.lit.edu.cn) 账号登录")
    print(" 账号:", args.username, "| service:", args.service or "(无)")
    print("=" * 70)

    saved = load_saved_state(args.save_cookie)
    fingerprint = args.fingerprint or saved.get("fingerprint") or uuid.uuid4().hex
    mfa_code = args.mfa_code or os.environ.get("LIT_MFA_CODE") or None
    tries = int(os.environ.get("LIT_CAPTCHA_TRIES", args.max_captcha_tries))

    # 如果上次保存的会话(TGC)还有效, 直接复用, 不再走登录流程
    if not args.force_login and saved.get("cookies"):
        print("[0] 检查已保存的会话 ...")
        if check_saved_session(saved["cookies"]):
            print("-" * 70)
            print("已登录 (复用保存的会话, TGC 仍然有效), 无需重新登录.")
            print("会话文件:", os.path.abspath(args.save_cookie))
            if mfa_code is None:
                return 0
        else:
            print("    已保存的会话已失效, 重新登录.")

    try:
        result = login(args.username, args.password, service=args.service,
                       verbose=not args.quiet, max_captcha_tries=tries,
                       fingerprint=fingerprint, mfa_code=mfa_code)
    except KeyboardInterrupt:
        print("\n已取消.")
        return 130
    except Exception as e:  # noqa: BLE001  (给用户友好提示, 避免打一屏 traceback)
        print("-" * 70)
        print("登录过程出错:", e)
        if os.environ.get("LIT_DEBUG"):
            raise
        return 2
    if result.get("ok"):
        payload = {
            "login_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "username": args.username,
            "final_url": result.get("final_url"),
            "fingerprint": result.get("fingerprint"),
            "mfa_used": result.get("mfa"),
            "service": args.service,
            "cookies": result.get("cookies", {}),
        }
        with open(args.save_cookie, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print("-" * 70)
        print("登录成功!")
        print("最终地址:", result.get("final_url"))
        print("会话 cookies 已保存到:", os.path.abspath(args.save_cookie))
        print("TGC:", result.get("cookies", {}).get("TGC", "(无)"))
        print("提示: 设备指纹已保存, 下次运行会复用; 若设备被信任可能无需二次验证.")
        return 0
    print("-" * 70)
    print("登录失败:", result.get("error"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
