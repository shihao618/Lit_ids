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
