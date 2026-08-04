"""environmental-data MCP server 入口(stdio)。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


from lvke_mcp.runtime.logging import get_logger  # noqa: E402
from lvke_mcp.domains.geo.administrative_names import resolve_administrative_name  # noqa: E402
from lvke_mcp.runtime.responses import err, ok  # noqa: E402
from lvke_mcp.runtime.stdio import StdioServer  # noqa: E402

SERVER_NAME = "environmental-data"
SERVER_VERSION = "0.1.0"
logger = get_logger(SERVER_NAME)


@dataclass
class EnvStorage:
    data_dir: Path
    _payload: dict[str, Any] = field(default_factory=dict, init=False)
    _loaded: bool = field(default=False, init=False)

    def _load(self) -> None:
        if self._loaded:
            return
        path = self.data_dir / "env.json"
        if path.exists():
            self._payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            self._payload = {"air_quality": [], "water_quality": [], "metadata": {}}
        self._loaded = True

    def query_air(
        self, city: str, year: int | None = None
    ) -> list[dict[str, Any]]:
        self._load()
        out: list[dict[str, Any]] = []
        for rec in self._payload.get("air_quality") or []:
            if rec.get("city") != city:
                continue
            if year and rec.get("year") != year:
                continue
            out.append(rec)
        return out

    def query_water(
        self,
        section_or_basin: str,
        year: int | None = None,
    ) -> list[dict[str, Any]]:
        self._load()
        out: list[dict[str, Any]] = []
        kw = section_or_basin
        for rec in self._payload.get("water_quality") or []:
            if (
                rec.get("section") != kw
                and rec.get("basin") != kw
                and kw not in rec.get("section", "")
                and kw not in rec.get("basin", "")
            ):
                continue
            if year and rec.get("year") != year:
                continue
            out.append(rec)
        return out

    def dictionaries(self) -> dict[str, Any]:
        self._load()
        cities: set[str] = set()
        sections: set[str] = set()
        basins: set[str] = set()
        for rec in self._payload.get("air_quality") or []:
            cities.add(rec.get("city", ""))
        for rec in self._payload.get("water_quality") or []:
            sections.add(rec.get("section", ""))
            basins.add(rec.get("basin", ""))
        air_coverage = sorted(
            [
                {"city": str(rec.get("city") or ""), "year": int(rec["year"])}
                for rec in (self._payload.get("air_quality") or [])
                if rec.get("city") and rec.get("year") is not None
            ],
            key=lambda item: (item["city"], item["year"]),
        )
        water_coverage = sorted(
            [
                {
                    "section": str(rec.get("section") or ""),
                    "basin": str(rec.get("basin") or ""),
                    "year": int(rec["year"]),
                }
                for rec in (self._payload.get("water_quality") or [])
                if rec.get("year") is not None
            ],
            key=lambda item: (item["basin"], item["section"], item["year"]),
        )
        return {
            "cities": sorted(c for c in cities if c),
            "water_sections": sorted(s for s in sections if s),
            "water_basins": sorted(b for b in basins if b),
            "air_coverage": air_coverage,
            "water_coverage": water_coverage,
            "coverage_semantics": "仅 coverage 中明示的地点-年份组合可查。",
            "metadata": self._payload.get("metadata") or {},
        }


def resolve_data_dir() -> Path:
    env_dir = os.environ.get("LVKE_ENV_DATA_DIR", "").strip()
    if env_dir:
        p = Path(env_dir).expanduser()
        if p.exists():
            return p
    base = Path(__file__).resolve().parent
    data_dir = base / "data"
    if (data_dir / "env.json").exists():
        return data_dir
    return base / "seed"


_storage: EnvStorage | None = None


def _get_storage() -> EnvStorage:
    global _storage
    if _storage is None:
        d = resolve_data_dir()
        logger.info("使用环境数据目录:%s", d)
        _storage = EnvStorage(data_dir=d)
    return _storage


def _tool_query_air_quality(args: dict) -> dict:
    city = args.get("city")
    if not isinstance(city, str) or not city:
        return err(f"{SERVER_NAME}.invalid_argument", "city 必须是非空字符串")
    year = args.get("year")
    if year is not None and not isinstance(year, int):
        return err(f"{SERVER_NAME}.invalid_argument", "year 必须是整数")
    storage = _get_storage()
    resolved_city, suggestions = resolve_administrative_name(
        city, storage.dictionaries()["cities"],
    )
    if resolved_city is None:
        return err(
            f"{SERVER_NAME}.not_found",
            f"{city} {year or ''} 大气数据不在本地数据库",
            detail={"suggestions": suggestions},
        )
    out = storage.query_air(city=resolved_city, year=year)
    if not out:
        coverage = storage.dictionaries()["air_coverage"]
        available_years = sorted(
            {row["year"] for row in coverage if row["city"] == resolved_city}
        )
        return err(
            f"{SERVER_NAME}.not_found",
            f"{city} {year or ''} 大气数据不在本地数据库",
            detail={
                "suggestions": [resolved_city],
                "available_cities": sorted({row["city"] for row in coverage}),
                "available_years": available_years,
                "closest_years": (
                    sorted(available_years, key=lambda item: (abs(item - year), item))[:3]
                    if year is not None else available_years[:3]
                ),
            },
        )
    return ok(
        {
            "city": resolved_city,
            "requested_city": city,
            "count": len(out),
            "items": out,
        },
        source=f"{SERVER_NAME}.query_air_quality",
    )


def _tool_query_water_quality(args: dict) -> dict:
    sec = args.get("section_or_basin")
    if not isinstance(sec, str) or not sec:
        return err(f"{SERVER_NAME}.invalid_argument", "section_or_basin 必须是非空字符串")
    year = args.get("year")
    if year is not None and not isinstance(year, int):
        return err(f"{SERVER_NAME}.invalid_argument", "year 必须是整数")
    storage = _get_storage()
    dictionaries = storage.dictionaries()
    resolved_location, suggestions = resolve_administrative_name(
        sec,
        [*dictionaries["water_sections"], *dictionaries["water_basins"]],
    )
    if resolved_location is None:
        return err(
            f"{SERVER_NAME}.not_found",
            f"{sec} {year or ''} 水环境数据不在本地数据库",
            detail={
                "suggestions": suggestions,
                "available_sections": dictionaries["water_sections"],
                "available_basins": dictionaries["water_basins"],
            },
        )
    out = storage.query_water(section_or_basin=resolved_location, year=year)
    if not out:
        coverage = dictionaries["water_coverage"]
        available_years = sorted({
            row["year"] for row in coverage
            if row["section"] == resolved_location or row["basin"] == resolved_location
        })
        return err(
            f"{SERVER_NAME}.not_found",
            f"{sec} {year or ''} 水环境数据不在本地数据库",
            detail={
                "suggestions": [resolved_location],
                "available_years": available_years,
                "closest_years": (
                    sorted(available_years, key=lambda item: (abs(item - year), item))[:3]
                    if year is not None else available_years[:3]
                ),
            },
        )
    return ok(
        {
            "query": resolved_location,
            "requested_query": sec,
            "count": len(out),
            "items": out,
        },
        source=f"{SERVER_NAME}.query_water_quality",
    )


def _tool_list_monitored_locations(args: dict) -> dict:
    return ok(
        _get_storage().dictionaries(),
        source=f"{SERVER_NAME}.list_monitored_locations",
    )


def build_server() -> StdioServer:
    server = StdioServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        logger=logger,
    )
    server.register_tool(
        name="query_air_quality",
        description="按城市 + 年份查大气质量(AQI / PM2.5 等)。",
        input_schema={
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "year": {"type": "integer"},
            },
            "required": ["city"],
        },
        handler=_tool_query_air_quality,
    )
    server.register_tool(
        name="query_water_quality",
        description="按断面名或流域名查水环境质量(类别 + 主要污染物)。",
        input_schema={
            "type": "object",
            "properties": {
                "section_or_basin": {"type": "string"},
                "year": {"type": "integer"},
            },
            "required": ["section_or_basin"],
        },
        handler=_tool_query_water_quality,
    )
    server.register_tool(
        name="list_monitored_locations",
        description="列出可查询的城市 / 断面 / 流域。",
        input_schema={"type": "object", "properties": {}},
        handler=_tool_list_monitored_locations,
    )
    return server


def main() -> None:
    server = build_server()
    logger.info("%s server v%s 启动(stdio)", SERVER_NAME, SERVER_VERSION)
    server.serve_forever()


if __name__ == "__main__":
    main()
