# -*- coding: utf-8 -*-
"""坐标系统转换：WGS84 / GCJ02 / BD09.

学校智能查寝的围栏点（DD_LIST 的 JDZB/WDZB）使用百度 BD09；
手机 GPS 通常是 WGS84，所以签到前必须先转换到 BD09 再计算距离。
"""
from __future__ import annotations

import math

_A = 6378245.0
_EE = 0.00669342162296594323
_PI = math.pi


def _out_of_china(lat: float, lng: float) -> bool:
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * _PI) + 40.0 * math.sin(y / 3.0 * _PI)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * _PI) + 320.0 * math.sin(y * _PI / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * _PI) + 40.0 * math.sin(x / 3.0 * _PI)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * _PI) + 300.0 * math.sin(x / 30.0 * _PI)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lat: float, lng: float) -> tuple[float, float]:
    """WGS84 → GCJ02（高德/腾讯坐标）."""
    if _out_of_china(lat, lng):
        return lat, lng
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    rad_lat = math.radians(lat)
    magic = 1 - _EE * math.sin(rad_lat) ** 2
    sqrt_magic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrt_magic) * _PI)
    dlng = (dlng * 180.0) / (_A / sqrt_magic * math.cos(rad_lat) * _PI)
    return lat + dlat, lng + dlng


def gcj02_to_bd09(lat: float, lng: float) -> tuple[float, float]:
    """GCJ02 → BD09（百度坐标）."""
    z = math.sqrt(lng * lng + lat * lat) + 0.00002 * math.sin(lat * _PI * 3000.0 / 180.0)
    theta = math.atan2(lat, lng) + 0.000003 * math.cos(lng * _PI * 3000.0 / 180.0)
    return z * math.sin(theta) + 0.006, z * math.cos(theta) + 0.0065


def to_bd09(lat: float, lng: float, coord_type: str = "wgs84") -> tuple[float, float]:
    """把任意支持坐标系的经纬度转换成 BD09.

    :param coord_type: wgs84 / gcj02 / bd09
    """
    kind = (coord_type or "wgs84").strip().lower()
    if kind == "bd09":
        return lat, lng
    if kind == "gcj02":
        return gcj02_to_bd09(lat, lng)
    if kind == "wgs84":
        return gcj02_to_bd09(*wgs84_to_gcj02(lat, lng))
    raise ValueError("coordType 不支持: %s（可选 wgs84/gcj02/bd09）" % coord_type)


#: 百度墨卡托投影参数（BD09LL -> BD09MC）
_LLBAND = [75.0, 60.0, 45.0, 30.0, 15.0, 0.0]
_LL2MC = [
    [-0.0015702102444, 111320.7020616939, 1704480524535203.0, -10338987376042340.0,
     26112667856603880.0, -35149669176653700.0, 26595700718403920.0,
     -10725012454188240.0, 1800819912950474.0, 82.5],
    [0.0008277824516172526, 111320.7020463578, 647795574.6671607, -4082003173.641316,
     10774905663.51142, -15171875531.51559, 12053065338.62167,
     -5124939663.577472, 913311935.9512032, 67.5],
    [0.00337398766765, 111320.7020202162, 4481351.045890365, -23393751.19931662,
     79682215.47186455, -115964993.2797253, 97236711.15602145,
     -43661946.33752821, 8477230.501135234, 52.5],
    [0.00220636496208, 111320.7020209128, 51751.86112841131, 3796837.749470245,
     992013.7397791013, -1221952.21711287, 1340652.697009075,
     -620943.6990984312, 144416.9293806241, 37.5],
    [-0.0003441963504368392, 111320.7020576856, 278.2353980772752, 2485758.690035394,
     6070.750963243378, 54821.18345352118, 9540.606633304236,
     -2710.55326746645, 1405.483844121726, 22.5],
    [-0.0003218135878613132, 111320.7020701615, 0.00369383431289, 823725.6402795718,
     0.46104986909093, 2351.343141331292, 1.58060784298199,
     8.77738589078284, 0.37238884252424, 7.45],
]


def bd09_to_mc(lat: float, lng: float) -> tuple[float, float]:
    """BD09 经纬度 → 百度墨卡托 x/y（用于百度逆地理编码）."""
    factors = _LL2MC[0]
    for i, band in enumerate(_LLBAND):
        if abs(lat) > band:
            factors = _LL2MC[i]
            break
    cc = abs(lat) / factors[9]
    x = factors[0] + factors[1] * abs(lng)
    y = factors[2] + factors[3] * cc + factors[4] * cc ** 2 + factors[5] * cc ** 3 + \
        factors[6] * cc ** 4 + factors[7] * cc ** 5 + factors[8] * cc ** 6
    return (x if lng >= 0 else -x, y if lat >= 0 else -y)
