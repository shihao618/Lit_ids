"""请求 / 响应模型（用于 OpenAPI 文档与参数校验）."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- 接口（API Key 用户）
class LoginIn(BaseModel):
    force: bool = Field(False, description="true = 忽略已保存会话，强制重新登录")
    mfa_code: str | None = Field(
        None,
        description="企业微信二次验证码（首次在新设备登录时需要；已信任设备可省略）",
        examples=["123456"],
    )


class Envelope(BaseModel):
    """统一响应信封."""

    code: int = 0
    msg: str = "ok"
    data: Any = None
    meta: dict[str, Any] | None = None


class HealthOut(BaseModel):
    status: str
    version: str


class AuthStatusOut(BaseModel):
    logged_in: bool
    username: str | None = None
    cas_username: str | None = None
    cas_configured: bool = False
    login_time: str | None = None
    final_url: str | None = None
    fingerprint_saved: bool = False
    fingerprint: str | None = None
    mfa_used: bool | None = None
    cookie_names: list[str] = Field(default_factory=list)
    tgc_masked: str | None = None
    pending_mfa: bool = False
    pending_since: str | None = None
    session_generation: int = 0
    last_error: str | None = None
    debug: dict[str, Any] | None = None


# ---------------------------------------------------------------- 后台
class AdminLoginIn(BaseModel):
    force: bool = Field(False, description="true = 强制重新登录统一认证")


class AdminMfaIn(BaseModel):
    code: str = Field(..., description="企业微信二次验证码")


class CasSettingsIn(BaseModel):
    """当前用户自己的统一认证账号设置（None = 不修改）."""

    cas_username: str | None = Field(None, description="统一认证账号（学号/工号）")
    cas_password: str | None = Field(None, description="统一认证密码；留空 None 表示不修改")
    cas_fingerprint: str | None = Field(None, description="设备指纹；空字符串表示清空")


class SelfPasswordIn(BaseModel):
    old_password: str = Field(..., description="当前后台密码")
    new_password: str = Field(..., min_length=6, description="新后台密码（至少 6 位）")


class UserCreateIn(BaseModel):
    """管理员创建用户."""

    username: str = Field(..., min_length=2, max_length=32, description="后台登录名")
    password: str = Field(..., min_length=6, description="后台登录密码")
    role: str = Field("user", description="角色：admin / user")
    cas_username: str = Field("", description="统一认证账号（可留空，用户自己再配）")
    cas_password: str = Field("", description="统一认证密码（可留空）")
    cas_fingerprint: str = Field("", description="设备指纹（可留空）")


class AdminPasswordIn(BaseModel):
    password: str = Field(..., min_length=6, description="新后台密码")


class AdminCasIn(BaseModel):
    """管理员为某个用户设置统一认证账号."""

    cas_username: str | None = None
    cas_password: str | None = None
    cas_fingerprint: str | None = None


class AdminRoleIn(BaseModel):
    role: str = Field(..., description="admin / user")


class SystemSettingsIn(BaseModel):
    """后台「系统设置」（只传要修改的字段）."""

    app_name: str | None = Field(None, description="系统名称（界面/文档标题）")
    log_level: str | None = Field(None, description="日志级别：DEBUG/INFO/WARNING/ERROR")
    session_refresh_interval: int | None = Field(None, ge=0, description="会话保活间隔（秒），0=关闭")
    auto_login_on_start: bool | None = Field(None, description="启动时自动登录（需重启容器）")



class CqztbgIn(BaseModel):
    """智能查寝 cqztbg.do 参数（官方字段名；筛选实际由 querySetting 生效）."""

    BJDM: str = Field("", description="班级代码，如 B260714（官方参数 BJDM）")
    DWDM: str = Field("", description="学院/单位代码（官方参数 DWDM）")
    CQZT: str = Field("", description="查寝状态：1=未归寝、2=已归寝、3=离校请假（官方参数 CQZT）")
    CQZTLX: str = Field("", description="查寝状态类型：11/12/21/22（官方参数 CQZTLX）")
    GZXS: str = Field("", description="关注学生：1=只看关注学生，0=全部（官方参数 GZXS）")
    pageSize: int = Field(10, ge=1, le=200, description="每页条数（官方 pageSize）")
    pageNumber: int = Field(1, ge=1, description="页码（官方 pageNumber）")
    querySetting: str | None = Field(
        None, description=("筛选条件 JSON 字符串（官方 querySetting，优先使用）；"
                           "不传则用上面的 BJDM/DWDM/CQZT/CQZTLX/GZXS 自动生成"))


class CalendarWeekIn(BaseModel):
    """教务系统 getCalendarWeekDatas 参数（官方字段名）."""

    xnxqdm: str = Field(..., min_length=4, description="学年学期代码，如 202601")
    zc: int = Field(..., ge=1, le=60, description="周次，如 7")
    d1: str = Field(..., description="开始日期时间，如 2026-10-26 00:00:00")
    d2: str = Field(..., description="结束日期时间，如 2026-11-02 00:00:00")
