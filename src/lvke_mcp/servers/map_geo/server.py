"""map-geo MCP server 入口(stdio)。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


from lvke_mcp.runtime.logging import get_logger  # noqa: E402
from lvke_mcp.runtime.responses import err, ok  # noqa: E402
from lvke_mcp.runtime.stdio import StdioServer  # noqa: E402
from lvke_mcp.servers.map_geo.geometry import (  # noqa: E402
    estimate_highway_km,
    find_pois_within,
    haversine_km,
)

SERVER_NAME = "map-geo"
SERVER_VERSION = "0.1.0"
logger = get_logger(SERVER_NAME)


@dataclass
class POIStorage:
    data_dir: Path
    _records: list[dict[str, Any]] = field(default_factory=list, init=False)
    _loaded: bool = field(default=False, init=False)

    def _load(self) -> None:
        if self._loaded:
            return
        path = self.data_dir / "pois.json"
        if path.exists():
            self._records = json.loads(path.read_text(encoding="utf-8"))
        else:
            self._records = []
        self._loaded = True

    def all(self) -> list[dict[str, Any]]:
        self._load()
        return list(self._records)

    def geocode(self, address: str) -> dict[str, Any] | None:
        self._load()
        addr = address.strip()
        compact = re.sub(r"[\s,，、]+", "", addr)
        normalized = re.sub(r"^(?:中国)?湖北省", "", addr)
        normalized = normalized.removesuffix("市")
        # Administrative queries should resolve to their dedicated centre,
        # not to the first unrelated POI whose address happens to contain the
        # city name.
        for rec in self._records:
            city = str(rec.get("city") or "").removesuffix("市")
            if (
                normalized == city
                and rec.get("type") == "行政中心"
            ):
                return rec
        # 精确匹配 name / id
        for rec in self._records:
            names = {str(rec.get("name") or ""), str(rec.get("id") or "")}
            if str(rec.get("name") or "") == "武昌火车站":
                names.add("武昌站")
            if compact in {re.sub(r"[\s,，、]+", "", name) for name in names}:
                return rec
        # 模糊匹配:address 字段包含或 name 包含
        for rec in self._records:
            haystack = re.sub(r"[\s,，、]+", "", str(rec.get("address", "")) + str(rec.get("name", "")))
            if compact and compact in haystack:
                return rec
        return None


def resolve_data_dir() -> Path:
    env_dir = os.environ.get("LVKE_MAPGEO_DATA_DIR", "").strip()
    if env_dir:
        p = Path(env_dir).expanduser()
        if p.exists():
            return p
    base = Path(__file__).resolve().parent
    data_dir = base / "data"
    if (data_dir / "pois.json").exists():
        return data_dir
    return base / "seed"


_storage: POIStorage | None = None


def _get_storage() -> POIStorage:
    global _storage
    if _storage is None:
        d = resolve_data_dir()
        logger.info("使用 POI 数据目录:%s", d)
        _storage = POIStorage(data_dir=d)
    return _storage


def _resolve_endpoint(point: dict | str) -> tuple[float, float, str | None] | None:
    """把 endpoint 规约成 (lat, lng, label)。

    支持三种输入:
    - 字符串(地名 / POI 名称)
    - {"address": "..."}
    - {"lat": x, "lng": y, "label": "..."}
    """

    if isinstance(point, str):
        rec = _get_storage().geocode(point)
        if rec is None:
            return None
        return (float(rec["lat"]), float(rec["lng"]), rec.get("name") or point)
    if isinstance(point, dict):
        if isinstance(point.get("lat"), (int, float)) and isinstance(
            point.get("lng"), (int, float)
        ) and not isinstance(point.get("lat"), bool) and not isinstance(
            point.get("lng"), bool
        ):
            lat_v = float(point["lat"])
            lng_v = float(point["lng"])
            if not -90.0 <= lat_v <= 90.0 or not -180.0 <= lng_v <= 180.0:
                return None
            return (
                lat_v,
                lng_v,
                str(point.get("label") or ""),
            )
        addr = point.get("address") or point.get("name")
        if isinstance(addr, str) and addr:
            rec = _get_storage().geocode(addr)
            if rec is None:
                return None
            return (float(rec["lat"]), float(rec["lng"]), rec.get("name") or addr)
    return None


def _tool_geocode(args: dict) -> dict:
    addr = args.get("address")
    if not isinstance(addr, str) or not addr.strip():
        return err(f"{SERVER_NAME}.invalid_argument", "address 必须是非空字符串")
    rec = _get_storage().geocode(addr)
    if rec is None:
        return err(
            f"{SERVER_NAME}.not_found",
            f"未在本地 POI 库找到匹配:{addr}",
            detail="启用高德 / 百度 API 后可覆盖更广区域;当前仅支持湖北主要 POI。",
        )
    return ok(
        {
            "address": addr,
            "lat": rec["lat"],
            "lng": rec["lng"],
            "matched_name": rec.get("name"),
            "city": rec.get("city"),
            "type": rec.get("type"),
        },
        source=f"{SERVER_NAME}.geocode",
    )


def _tool_distance_matrix(args: dict) -> dict:
    origins = args.get("origins")
    destinations = args.get("destinations")
    if not isinstance(origins, list) or not origins:
        return err(f"{SERVER_NAME}.invalid_argument", "origins 必须是非空数组")
    if not isinstance(destinations, list) or not destinations:
        return err(f"{SERVER_NAME}.invalid_argument", "destinations 必须是非空数组")
    o_res = [_resolve_endpoint(p) for p in origins]
    d_res = [_resolve_endpoint(p) for p in destinations]
    if any(r is None for r in o_res) or any(r is None for r in d_res):
        return err(
            f"{SERVER_NAME}.not_found",
            "部分端点无法解析(请检查地名拼写或传 lat/lng)",
            detail={
                "origins_unresolved": [
                    origins[i] for i, r in enumerate(o_res) if r is None
                ],
                "destinations_unresolved": [
                    destinations[i] for i, r in enumerate(d_res) if r is None
                ],
            },
        )
    rows = []
    for o in o_res:
        row = []
        for d in d_res:
            assert o is not None and d is not None
            great_circle = haversine_km(o[0], o[1], d[0], d[1])
            highway = estimate_highway_km(o[0], o[1], d[0], d[1])
            row.append(
                {
                    "origin_label": o[2],
                    "destination_label": d[2],
                    "great_circle_km": round(great_circle, 3),
                    "highway_estimate_km": round(highway, 3),
                }
            )
        rows.append(row)
    return ok(
        {
            "origins": [{"lat": r[0], "lng": r[1], "label": r[2]} for r in o_res],
            "destinations": [
                {"lat": r[0], "lng": r[1], "label": r[2]} for r in d_res
            ],
            "matrix": rows,
            "highway_coefficient": 1.25,
        },
        source=f"{SERVER_NAME}.distance_matrix",
    )


def _tool_nearby_pois(args: dict) -> dict:
    lat = args.get("lat")
    lng = args.get("lng")
    if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)) or isinstance(lat, bool) or isinstance(lng, bool):
        return err(f"{SERVER_NAME}.invalid_argument", "lat / lng 必须是数字")
    lat_f = float(lat)
    lng_f = float(lng)
    if not -90.0 <= lat_f <= 90.0:
        return err(f"{SERVER_NAME}.invalid_argument", "lat 超出 [-90, 90] 范围")
    if not -180.0 <= lng_f <= 180.0:
        return err(f"{SERVER_NAME}.invalid_argument", "lng 超出 [-180, 180] 范围")
    radius_km = float(args.get("radius_km") or 5.0)
    if not (0.0 < radius_km <= 100.0):
        return err(f"{SERVER_NAME}.invalid_argument", "radius_km 必须在 (0, 100] 范围内")
    type_filter = args.get("type")
    out = find_pois_within(
        _get_storage().all(),
        lat_f,
        lng_f,
        type_filter if isinstance(type_filter, str) else None,
        radius_km,
    )
    return ok(
        {
            "lat": lat,
            "lng": lng,
            "radius_km": radius_km,
            "type": type_filter,
            "count": len(out),
            "items": out,
        },
        source=f"{SERVER_NAME}.nearby_pois",
    )


def build_server() -> StdioServer:
    server = StdioServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        logger=logger,
    )
    server.register_tool(
        name="geocode",
        description="把地名 / POI 名 / 地址映射到经纬度。仅匹配本地 POI 库。",
        input_schema={
            "type": "object",
            "properties": {"address": {"type": "string"}},
            "required": ["address"],
        },
        handler=_tool_geocode,
    )
    server.register_tool(
        name="distance_matrix",
        description=(
            "批量计算两点间距离矩阵。endpoint 可以是字符串(地名)或 {lat, lng, label}。"
            "返回 great-circle 距离 + 公路里程估计(系数 1.25)。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "origins": {
                    "type": "array",
                    "description": "起点数组,每项可以是字符串或 {lat,lng,label}",
                },
                "destinations": {
                    "type": "array",
                    "description": "终点数组",
                },
            },
            "required": ["origins", "destinations"],
        },
        handler=_tool_distance_matrix,
    )
    server.register_tool(
        name="nearby_pois",
        description="在给定坐标半径范围内查找 POI(可按 type 过滤)。",
        input_schema={
            "type": "object",
            "properties": {
                "lat": {"type": "number", "minimum": -90, "maximum": 90},
                "lng": {"type": "number", "minimum": -180, "maximum": 180},
                "type": {
                    "type": "string",
                    "description": "可选 POI 类型(如 学校 / 医院 / 工业园 / 机场 / 火车站 / 湿地 / 干流)",
                },
                "radius_km": {"type": "number", "default": 5.0, "minimum": 0, "exclusiveMinimum": 0, "maximum": 100},
            },
            "required": ["lat", "lng"],
        },
        handler=_tool_nearby_pois,
    )
    return server


def main() -> None:
    server = build_server()
    logger.info("%s server v%s 启动(stdio)", SERVER_NAME, SERVER_VERSION)
    server.serve_forever()


if __name__ == "__main__":
    main()
