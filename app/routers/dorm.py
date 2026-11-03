"""智能查寝（自有契约）接口路由.

对外只暴露一个接口：POST /api/v1/dorm/check-records
参数只有：班级、查寝状态、查寝状态类型；返回易读字段。
内部由 XsfwSystem 调用官方 cqztbg.do。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..deps import get_xsfw, require_api_key
from ..schemas import CheckRecordsIn, LocationCheckinIn
from ..systems import XsfwSystem

router = APIRouter(prefix="/api/v1/dorm", tags=["智能查寝"],
                   dependencies=[Depends(require_api_key)])


@router.get("/checkin-locations",
            summary="智能查寝-获取可打卡地点")
def checkin_locations(
    system: XsfwSystem = Depends(get_xsfw),
) -> dict[str, Any]:
    """返回当前账号允许的签到地点（名称/经纬度/允许半径）.

    只返回当前学生自己宿舍楼对应的地点，不再列出全部宿舍。
    """
    return system.checkin_locations()


@router.post("/location-checkin",
             summary="智能查寝-定位签到")
def location_checkin(body: LocationCheckinIn,
                     system: XsfwSystem = Depends(get_xsfw)) -> dict[str, Any]:
    """用真实经纬度提交当前学生定位签到。

    调用方只传 `latitude` / `longitude`（以及可选 `coordType`）；服务端自动完成
    角色切换、WGS84/GCJ02/BD09 坐标转换、状态查询、考勤资格判断、最近围栏
    距离校验和签到提交，并回读最新状态。

    例：
    ```json
    {"latitude": 34.610278, "longitude": 112.409723, "coordType": "wgs84"}
    ```
    """

    return system.location_checkin(body.latitude, body.longitude, body.coordType)


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
