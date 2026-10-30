"""智能查寝（xsfw / zncqapp）接口路由.

只提供官方 `cqztbg.do` 一个业务接口，参数/返回与官方一致。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..deps import get_xsfw, require_api_key
from ..schemas import CqztbgIn
from ..systems import XsfwSystem

router = APIRouter(prefix="/api/v1/xsfw", tags=["智能查寝"],
                   dependencies=[Depends(require_api_key)])


@router.post("/sjkb/cqztbg", summary="智能查寝-查寝状态表格（官方 cqztbg.do）")
def cqztbg(body: CqztbgIn, system: XsfwSystem = Depends(get_xsfw)) -> dict[str, Any]:
    """返回上游官方响应：

    {"code":"0","datas":{"cqztbg":{"totalSize":39,"pageSize":10,
      "pageNumber":1,"rows":[...]}}}
    """
    return system.get_cqztbg(bjdm=body.BJDM, page_number=body.pageNumber,
                             page_size=body.pageSize,
                             query_setting=body.querySetting,
                             cqzt=body.CQZT, cqztlx=body.CQZTLX,
                             dwdm=body.DWDM, gzxs=body.GZXS)
