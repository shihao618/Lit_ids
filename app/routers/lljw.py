"""教务系统（lljw / 课表查询）接口路由.

对外只暴露一个接口：POST /api/v1/lljw/schedule
参数只有：学期、周次、查询类型、日期；返回易读课程字段。
内部由 LljwSystem 调用官方 getCalendarWeekDatas / getDatesOfWeek。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..deps import get_lljw, require_api_key
from ..schemas import ScheduleIn
from ..systems import LljwSystem

router = APIRouter(prefix="/api/v1/lljw", tags=["教务系统"],
                   dependencies=[Depends(require_api_key)])


@router.post("/schedule", summary="教务系统-课表查询（自有参数/易读返回）")
def schedule(body: ScheduleIn,
             system: LljwSystem = Depends(get_lljw)) -> dict[str, Any]:
    """查询课表。

    * `semester` 省略自动取当前学期；`week` 省略自动取本周；
    * `week="all"`/0 获取全部周；
    * `query` 支持：week / today / morning / afternoon / evening / day / next；
    * `query=day` 需要 `date`（YYYY-MM-DD）。

    例：
    ```json
    {"query": "today"}
    {"week": 8}
    {"week": "all"}
    {"query": "day", "date": "2026-10-29"}
    {"query": "next"}
    ```
    """
    return system.schedule(semester=body.semester, week=body.week,
                           query=body.query, date_text=body.date)
