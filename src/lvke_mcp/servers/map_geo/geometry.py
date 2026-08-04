"""map-geo 的纯函数计算层(Haversine 距离 + POI 查找)。"""

from __future__ import annotations

import math
from typing import Sequence

_EARTH_R_KM = 6371.0088

# Haversine → 公路距离 经验放大系数。
# 来源:多个研究表明公路实际里程比 great-circle 平均高 1.2-1.3 倍。
HIGHWAY_COEFFICIENT = 1.25


def haversine_km(
    lat1: float, lng1: float, lat2: float, lng2: float
) -> float:
    """两个 WGS84 经纬度点的 great-circle 距离(km)。"""

    lat1r = math.radians(lat1)
    lat2r = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1r) * math.cos(lat2r) * math.sin(dlng / 2) ** 2
    )
    c = 2 * math.asin(min(1.0, math.sqrt(a)))
    return _EARTH_R_KM * c


def estimate_highway_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """估算公路里程(km),Haversine × 1.25。"""

    return haversine_km(lat1, lng1, lat2, lng2) * HIGHWAY_COEFFICIENT


def find_pois_within(
    pois: Sequence[dict],
    lat: float,
    lng: float,
    type_filter: str | None,
    radius_km: float,
) -> list[dict]:
    """在 POI 列表中找出距离 (lat,lng) ≤ radius_km 的项,按距离升序。"""

    matches: list[tuple[float, dict]] = []
    for p in pois:
        if type_filter and p.get("type") != type_filter:
            continue
            
        d = haversine_km(lat, lng, float(p["lat"]), float(p["lng"]))
        if d <= radius_km:
            matches.append((d, p))
    matches.sort(key=lambda x: x[0])
    return [{**p, "distance_km": round(d, 3)} for d, p in matches]
