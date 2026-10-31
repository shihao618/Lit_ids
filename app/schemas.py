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



class CheckRecordsIn(BaseModel):
    """智能查寝-查寝记录（自有参数；只保留班级/查寝状态/查寝状态类型）."""

    classCode: str = Field("", description="班级代码，如 B260714；不传=当前账号可见范围")
    checkStatus: str = Field(
        "", description="查寝状态：not_returned 未归寝 / returned 已归寝 / leave 离校请假（也接受中文）")
    checkStatusType: str = Field(
        "", description="查寝状态类型：unreachable 未联系上 / reported 已报备 / "
                        "normal 正常归寝 / late 晚归（也接受中文）")


class ScheduleIn(BaseModel):
    """教务系统-课表查询（自有参数）."""

    semester: str = Field("", description="学期代码，如 202601；不传=自动当前学期")
    week: str | int | None = Field(
        None, description='周次 1~N；"all" 或 0 = 全部周；不传=自动本周（仅 query=week 用）')
    query: str = Field(
        "week", description="week 本周/指定周；today 今天；morning 上午；afternoon 下午；"
                            "evening 晚上；day 指定日期；next 最近一节")
    date: str = Field("", description="query=day 时的日期，格式 YYYY-MM-DD")
