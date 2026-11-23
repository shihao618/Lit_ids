# -*- coding: utf-8 -*-
"""学生请销假路由."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..deps import get_xsqj, require_api_key
from ..schemas import LocationCancelIn
from ..systems import XsqjSystem

router = APIRouter(prefix="/api/v1/xsfw/xsqj", tags=["学生请销假"],
                   dependencies=[Depends(require_api_key)])


@router.get("/leaves", summary="请假列表")
def leaves(system: XsqjSystem = Depends(get_xsqj)) -> dict[str, Any]:
    """返回当前学生的请假记录列表."""
    return system.leave_records()


@router.post("/location-cancel", summary="定位销假")
def location_cancel(body: LocationCancelIn,
                    system: XsqjSystem = Depends(get_xsqj)) -> dict[str, Any]:
    """按请假记录 ID 和当前位置提交定位销假."""
    return system.location_cancel(body.leaveId, body.latitude, body.longitude,
                                  body.coordType)
