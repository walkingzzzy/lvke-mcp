"""statistics-cn MCP server 入口(stdio)。"""

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

SERVER_NAME = "statistics-cn"
SERVER_VERSION = "0.1.0"
logger = get_logger(SERVER_NAME)


@dataclass
class StatStorage:
    data_dir: Path
    _payload: dict[str, Any] = field(default_factory=dict, init=False)
    _loaded: bool = field(default=False, init=False)

    def _load(self) -> None:
        if self._loaded:
            return
        path = self.data_dir / "indicators.json"
        if path.exists():
            self._payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            self._payload = {"indicators": [], "metadata": {}}
        self._loaded = True

    def metadata(self) -> dict[str, Any]:
        self._load()
        return self._payload.get("metadata") or {}

    def indicators(self) -> list[dict[str, Any]]:
        self._load()
        return self._payload.get("indicators") or []

    def query(
        self,
        name: str,
        region: str | None = None,
        year: int | None = None,
    ) -> dict[str, Any] | None:
        self._load()
        for ind in self.indicators():
            if ind.get("name") != name and ind.get("label") != name:
                continue
            series = ind.get("series") or []
            filtered: list[dict[str, Any]] = []
            for pt in series:
                if region and pt.get("region") != region:
                    continue
                if year and pt.get("year") != year:
                    continue
                filtered.append(pt)
            return {
                "name": ind.get("name"),
                "label": ind.get("label"),
                "unit": ind.get("unit"),
                "count": len(filtered),
                "series": filtered,
            }
        return None

    def indicator(self, name: str) -> dict[str, Any] | None:
        self._load()
        return next(
            (
                ind for ind in self.indicators()
                if ind.get("name") == name or ind.get("label") == name
            ),
            None,
        )

    @staticmethod
    def coverage(indicator: dict[str, Any]) -> list[dict[str, Any]]:
        return sorted(
            [
                {"region": str(point.get("region") or ""), "year": int(point["year"])}
                for point in (indicator.get("series") or [])
                if point.get("region") and point.get("year") is not None
            ],
            key=lambda item: (item["region"], item["year"]),
        )

    def dictionaries(self) -> dict[str, Any]:
        self._load()
        names: list[dict[str, str]] = []
        regions: set[str] = set()
        years: set[int] = set()
        for ind in self.indicators():
            combinations = self.coverage(ind)
            names.append({
                "name": ind.get("name", ""),
                "label": ind.get("label", ""),
                "unit": ind.get("unit", ""),
                "coverage": combinations,
                "available_regions": sorted({row["region"] for row in combinations}),
                "available_years": sorted({row["year"] for row in combinations}),
            })
            for pt in ind.get("series") or []:
                if pt.get("region"):
                    regions.add(pt["region"])
                if pt.get("year"):
                    years.add(int(pt["year"]))
        return {
            "indicators": names,
            "coverage_semantics": "coverage 中每个 indicator-region-year 为真实可查记录；不得将全局地区与年份做笛卡尔组合。",
            "regions": sorted(regions),
            "years": sorted(years),
            "metadata": self.metadata(),
        }


def resolve_data_dir() -> Path:
    env_dir = os.environ.get("LVKE_STATISTICS_DATA_DIR", "").strip()
    if env_dir:
        p = Path(env_dir).expanduser()
        if p.exists():
            return p
    base = Path(__file__).resolve().parent
    data_dir = base / "data"
    if (data_dir / "indicators.json").exists():
        return data_dir
    return base / "seed"


_storage: StatStorage | None = None


def _get_storage() -> StatStorage:
    global _storage
    if _storage is None:
        d = resolve_data_dir()
        logger.info("使用统计数据目录:%s", d)
        _storage = StatStorage(data_dir=d)
    return _storage


def _tool_query_indicator(args: dict) -> dict:
    name = args.get("name")
    if not isinstance(name, str) or not name:
        return err(f"{SERVER_NAME}.invalid_argument", "name 必须是非空字符串")
    year = args.get("year")
    if year is not None and not isinstance(year, int):
        return err(f"{SERVER_NAME}.invalid_argument", "year 必须是整数")
    region = args.get("region")
    if region is not None and (not isinstance(region, str) or not region.strip()):
        return err(f"{SERVER_NAME}.invalid_argument", "region 必须是非空字符串")
    storage = _get_storage()
    resolved_region = None
    if region:
        resolved_region, suggestions = resolve_administrative_name(
            region, storage.dictionaries()["regions"],
        )
        if resolved_region is None:
            return err(
                f"{SERVER_NAME}.not_found",
                f"地区 {region} 不在本地字典中",
                detail={"suggestions": suggestions},
            )
    out = storage.query(name=name, region=resolved_region, year=year)
    if out is None:
        return err(
            f"{SERVER_NAME}.not_found",
            f"指标 {name} 不在本地字典中",
            detail={
                "available": [
                    i.get("name") for i in storage.indicators()
                ],
            },
        )
    if not out.get("series"):
        indicator = storage.indicator(name) or {}
        coverage = storage.coverage(indicator)
        region_rows = [row for row in coverage if not resolved_region or row["region"] == resolved_region]
        available_years = sorted({row["year"] for row in region_rows})
        closest_years = (
            sorted(available_years, key=lambda item: (abs(item - year), item))[:3]
            if year is not None else available_years[:3]
        )
        return err(
            f"{SERVER_NAME}.not_found",
            f"指标 {name} 在指定地区或年份无数据",
            detail={
                "available_regions": sorted({row["region"] for row in coverage}),
                "available_years": available_years,
                "closest_years": closest_years,
                "coverage": coverage,
                "suggestions": [resolved_region] if resolved_region else [],
            },
        )
    out["region"] = resolved_region
    out["requested_region"] = region
    return ok(out, source=f"{SERVER_NAME}.query_indicator")


def _tool_list_dictionaries(args: dict) -> dict:
    return ok(_get_storage().dictionaries(), source=f"{SERVER_NAME}.list_dictionaries")


def build_server() -> StdioServer:
    server = StdioServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        logger=logger,
    )
    server.register_tool(
        name="query_indicator",
        description=(
            "按指标名称(如 GDP / POPULATION / CPI)+ 地区 + 年份查询统计指标序列。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "region": {"type": "string"},
                "year": {"type": "integer"},
            },
            "required": ["name"],
        },
        handler=_tool_query_indicator,
    )
    server.register_tool(
        name="list_dictionaries",
        description="列出本地数据库可用的指标 / 地区 / 年份字典。",
        input_schema={"type": "object", "properties": {}},
        handler=_tool_list_dictionaries,
    )
    return server


def main() -> None:
    server = build_server()
    logger.info("%s server v%s 启动(stdio)", SERVER_NAME, SERVER_VERSION)
    server.serve_forever()


if __name__ == "__main__":
    main()
