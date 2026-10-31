"""LIT API Gateway —— 洛阳理工学院统一认证接口服务.

以 ``lit_login.py``（CAS 统一认证核心）为基础，对外提供 REST 接口：
  * 会话管理：CAS 登录 / 会话复用 / 自动续期
  * 系统适配层（systems/）：每个业务系统一个适配器，负责通过 CAS SSO 进入并调用其接口
  * 路由层（routers/）：对外暴露的 HTTP API

业务系统：智能查寝（xsfw WIS 平台 zncqapp）、教务系统（lljw）。
业务接口：智能查寝-查寝记录（POST /api/v1/dorm/check-records，自有参数/易读返回）、
         教务系统-课表查询（POST /api/v1/lljw/schedule，学期/周次自动、支持多时段查询）。
"""

__version__ = "0.1.0"
