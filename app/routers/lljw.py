"""教务系统（lljw / 校历周数据）接口路由.

提供官方 `getCalendarWeekDatas` 一个业务接口，参数/返回与官方一致。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..deps import get_lljw, require_api_key
from ..schemas import CalendarWeekIn
from ..systems import LljwSystem

router = APIRouter(prefix="/api/v1/lljw", tags=["教务系统"],
                   dependencies=[Depends(require_api_key)])


@router.post("/xsgrkb/getCalendarWeekDatas",
             summary="教务系统-校历周数据（官方 getCalendarWeekDatas）")
def calendar_week_datas(body: CalendarWeekIn,
                        system: LljwSystem = Depends(get_lljw)) -> dict[str, Any]:
    """返回上游官方响应：

    {"code":0,"data":[{"kcmc":"课程名","teaxms":"教师","qssj":"08:00:00", ...}]}
    """
    return system.get_calendar_week_datas(xnxqdm=body.xnxqdm, zc=body.zc,
                                          d1=body.d1, d2=body.d2)
