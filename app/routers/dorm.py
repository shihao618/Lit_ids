"""智能查寝（自有契约）接口路由.

对外只暴露一个接口：POST /api/v1/dorm/check-records
参数只有：班级、查寝状态、查寝状态类型；返回易读字段。
内部由 XsfwSystem 调用官方 cqztbg.do。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..deps import get_xsfw, require_api_key
from ..schemas import CheckRecordsIn
from ..systems import XsfwSystem

router = APIRouter(prefix="/api/v1/dorm", tags=["智能查寝"],
                   dependencies=[Depends(require_api_key)])


@router.post("/check-records",
             summary="智能查寝-查寝记录（自有参数/易读返回）")
def check_records(body: CheckRecordsIn,
                  system: XsfwSystem = Depends(get_xsfw)) -> dict[str, Any]:
    """查询查寝记录。

    * 请求：`classCode`（班级）、`checkStatus`（查寝状态）、`checkStatusType`（状态类型）；
    * 返回：`data.total` + `data.items`（学号/姓名/班级/宿舍/房间/床位/电话/状态中文等）。

    例：
    ```json
    {"classCode": "B260714", "checkStatus": "not_returned",
     "checkStatusType": "unreachable"}
    ```
    """
    return system.check_records(class_code=body.classCode,
                                check_status=body.checkStatus,
                                check_status_type=body.checkStatusType)
