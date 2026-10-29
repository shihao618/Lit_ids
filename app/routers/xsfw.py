"""学工系统（xsfw）接口路由.

接口保持"官方参数、官方返回"原则：参数名、取值、响应结构都与上游 WIS 接口一致，
网关只负责 CAS 登录/SSO/角色上下文，不做额外抽象。

已接入接口（数据看板 sjkb）
--------------------------
* ``GET /api/v1/xsfw/sjkb/ssgq/students``  学生归寝查询（官方 getSsgqStudent.do）
  官方参数：`SEARCHKEY`、`pageNumber`、`pageSize`、`CQZT`、`CQZTLX`
  - CQZT  ：1=未归寝、2=已归寝、3=离校请假，空=全部
  - CQZTLX：11=未联系上、12=已报备、21=正常归寝、22=晚归
* ``GET /api/v1/xsfw/sjkb/getCqztDic``     查寝状态字典（官方 getCqztDic.do）
* ``GET /api/v1/xsfw/sjkb/getCqztlxDic``   查寝状态类型字典（官方 getCqztlxDic.do，官方参数 CQZT）
* ``GET /api/v1/xsfw/roles``                当前账号在 xsfw 的角色列表（网关辅助接口）
* ``GET /api/v1/xsfw/menu``                 当前角色的菜单/功能列表（网关辅助接口）
* ``POST /api/v1/xsfw/call``                通用调用（path + data，data 为官方 JSON 参数）
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Query

from ..deps import get_xsfw, require_api_key
from ..schemas import Envelope, GenericCallIn
from ..systems import XsfwSystem

router = APIRouter(prefix="/api/v1/xsfw", tags=["学工系统 xsfw"],
                   dependencies=[Depends(require_api_key)])


@router.get("/roles", summary="xsfw 角色列表（辅助）")
def roles(system: XsfwSystem = Depends(get_xsfw)) -> dict[str, Any]:
    items = system.roles(refresh=True)
    return {"code": 0, "msg": "ok", "data": [
        {"role_id": r.get("ROLEID"), "role_name": r.get("ROLENAME"),
         "data_role": r.get("DATAROLE")} for r in items]}


@router.get("/menu", summary="当前角色的菜单/功能列表（辅助）")
def menu(system: XsfwSystem = Depends(get_xsfw)) -> dict[str, Any]:
    system.ensure_session()
    if system.current_role() is None:
        system.set_role()
    return {"code": 0, "msg": "ok",
            "data": {"role": system.current_role(), "menu": system.menu()}}


# ---------------------------------------------------------------- 数据看板（官方参数/返回）
@router.get("/sjkb/ssgq/students", summary="学生归寝查询（官方 getSsgqStudent.do）")
def ssgq_students(
    searchkey: str = Query("", alias="SEARCHKEY", description="官方参数：搜索关键字"),
    page_number: int = Query(1, alias="pageNumber", ge=1, description="官方参数：页码"),
    page_size: int = Query(10, alias="pageSize", ge=1, le=200, description="官方参数：每页条数"),
    cqzt: str = Query("", alias="CQZT",
                      description="官方参数：查寝状态 1=未归寝 2=已归寝 3=离校请假，空=全部"),
    cqztlx: str = Query("", alias="CQZTLX",
                        description="官方参数：查寝状态类型 11=未联系上 12=已报备 21=正常归寝 22=晚归"),
    system: XsfwSystem = Depends(get_xsfw),
) -> dict[str, Any]:
    """返回上游官方响应：{"code":"0","msg":"成功","data":[...]}"""
    return system.get_ssgq_students(searchkey=searchkey, page_number=page_number,
                                    page_size=page_size, cqzt=cqzt, cqztlx=cqztlx)


@router.get("/sjkb/getCqztDic", summary="查寝状态字典（官方 getCqztDic.do）")
def cqzt_dic(system: XsfwSystem = Depends(get_xsfw)) -> dict[str, Any]:
    return system.get_cqzt_dic()


@router.get("/sjkb/getCqztlxDic", summary="查寝状态类型字典（官方 getCqztlxDic.do）")
def cqztlx_dic(
    cqzt: str = Query("", alias="CQZT", description="官方参数：只返回某个查寝状态下的类型"),
    system: XsfwSystem = Depends(get_xsfw),
) -> dict[str, Any]:
    return system.get_cqztlx_dic(cqzt=cqzt)


@router.post("/call", response_model=Envelope, summary="通用调用 xsfw 接口（data 为官方参数 JSON）")
def generic_call(body: GenericCallIn, system: XsfwSystem = Depends(get_xsfw)) -> dict[str, Any]:
    # 通用调用固定使用默认（有权限的）角色，避免受之前请求切换角色的影响
    system.set_role(None)
    upstream = system.call(body.path, body.data)
    return {"code": 0, "msg": "ok", "data": upstream,
            "meta": {"system": "xsfw", "path": body.path,
                     "role": (system.current_role() or {}).get("ROLENAME"),
                     "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S")}}
