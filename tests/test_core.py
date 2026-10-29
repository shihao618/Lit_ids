"""离线单元测试：统一认证核心（AES 加密 / 滑块模板 / MFA 页面识别）.

这些测试不访问网络，可在 CI / 容器构建阶段运行。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import lit_login as L  # noqa: E402


def test_aes_cbc_matches_cryptojs():
    """与页面 CryptoJS.AES.encrypt 的行为一致（测试向量已脱敏）."""
    # 刚好 16 字节 → 需要补一整块 padding
    assert L.aes_cbc_encrypt_b64("password-that-is-exactly-16", "1234567890123456") == \
        "JQGyVPq7wJWKaUCV1bfxngDuTKvKivYDaC2hoqMPZVo="
    # 中文 UTF-8
    assert L.aes_cbc_encrypt_b64("中文密码测试123", "Zz9yY8xX7wW6vV5u") == \
        "A47LbX0d/hoBAd08S7N0PFYfRRKvdA9g5r/pedH/e5w="


def test_fixed_template_embedded():
    """内嵌的滑块模板图（fixed.png）能正常解码为 110x110 RGBA."""
    fixed = L._fixed_rgba()
    assert fixed.shape == (110, 110, 4)
    assert fixed[..., 3].max() > 200


def test_is_mfa_page_detection():
    assert L.is_mfa_page('<div class="title">企业微信验证码验证</div>')
    assert L.is_mfa_page('<input type="hidden" name="_eventId" id="realEventId" value="submit">')
    assert not L.is_mfa_page('<form id="form" action="/authserver/login"></form>')
