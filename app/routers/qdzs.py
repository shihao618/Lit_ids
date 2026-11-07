# -*- coding: utf-8 -*-
"""签到助手路由: 单一接口 今日签到."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..deps import get_qdzs, require_api_key
from ..schemas import TodaySigninIn
from ..systems import QdzsSystem

router = APIRouter(prefix="/api/v1/xsfw/qdzs", tags=["签到助手"],
                   dependencies=[Depends(require_api_key)])


@router.get("/locations", summary="签到地点")
def checkin_locations(system: QdzsSystem = Depends(get_qdzs)) -> dict[str, Any]:
    """返回签到助手当前任务允许的签到地点（名称/经纬度/半径）."""
    return system.checkin_locations()


@router.post("/today-signin", summary="今日签到")
def today_signin(body: TodaySigninIn,
                 system: QdzsSystem = Depends(get_qdzs)) -> dict[str, Any]:
    """查询并提交「签到助手」的今日定位签到。

    * 不需要传任务编号，接口自动从任务列表选择今天可用的任务；
    * `latitude` / `longitude` 为当前位置；
    * `coordType` 默认 `auto`，自动识别 WGS84/GCJ02/BD09；
    * 接口内部会先查询任务状态，再按任务规则提交签到；
    * 不在签到时间、已签到、范围外且不允许范围外时不会提交。

    例：
    ```json
    {"latitude": 34.610278, "longitude": 112.409723,
     "coordType": "auto"}
    ```
    """
    return system.today_signin(body.latitude, body.longitude,
                               body.coordType)
