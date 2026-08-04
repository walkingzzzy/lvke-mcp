"""industry-research MCP server 入口(stdio)。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


from lvke_mcp.runtime.logging import get_logger  # noqa: E402
from lvke_mcp.runtime.responses import err, ok  # noqa: E402
from lvke_mcp.runtime.stdio import StdioServer  # noqa: E402

SERVER_NAME = "industry-research"
SERVER_VERSION = "0.1.0"
logger = get_logger(SERVER_NAME)


@dataclass
class ResearchStorage:
    data_dir: Path
    _records: list[dict[str, Any]] = field(default_factory=list, init=False)
    _loaded: bool = field(default=False, init=False)

    def _load(self) -> None:
        if self._loaded:
            return
        path = self.data_dir / "reports.json"
        if path.exists():
            self._records = json.loads(path.read_text(encoding="utf-8"))
        else:
            self._records = []
        self._loaded = True

    def search(
        self,
        keyword: str | None = None,
        industry: str | None = None,
        year: int | None = None,
        publisher: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self._load()
        kw = (keyword or "").strip().lower()
        out: list[dict[str, Any]] = []
        for rec in self._records:
            if industry and rec.get("industry") != industry:
                continue
            if year and rec.get("year") != year:
                continue
            if publisher and publisher not in rec.get("publisher", ""):
                continue
            if kw:
                hay_parts = [
                    rec.get("title", ""),
                    rec.get("summary", ""),
                    rec.get("publisher", ""),
                    rec.get("industry", ""),
                    " ".join(rec.get("key_observations") or []),
                ]
                hay = " ".join(hay_parts).lower()
                if kw not in hay:
                    continue
            out.append(rec)
            if len(out) >= max(1, limit):
                break
        return out

    def get(self, report_id: str) -> dict[str, Any] | None:
        self._load()
        for rec in self._records:
            if rec.get("report_id") == report_id:
                return rec
        return None


def resolve_data_dir() -> Path:
    env_dir = os.environ.get("LVKE_RESEARCH_DATA_DIR", "").strip()
    if env_dir:
        p = Path(env_dir).expanduser()
        if p.exists():
            return p
    base = Path(__file__).resolve().parent
    data_dir = base / "data"
    if (data_dir / "reports.json").exists():
        return data_dir
    return base / "seed"


_storage: ResearchStorage | None = None


def _get_storage() -> ResearchStorage:
    global _storage
    if _storage is None:
        d = resolve_data_dir()
        logger.info("使用行业研报目录:%s", d)
        _storage = ResearchStorage(data_dir=d)
    return _storage


def _tool_search_report(args: dict) -> dict:
    year = args.get("year")
    if year is not None and not isinstance(year, int):
        return err(f"{SERVER_NAME}.invalid_argument", "year 必须是整数")
    out = _get_storage().search(
        keyword=args.get("keyword"),
        industry=args.get("industry"),
        year=year,
        publisher=args.get("publisher"),
        limit=int(args.get("limit") or 20),
    )
    return ok(
        {"count": len(out), "items": out},
        source=f"{SERVER_NAME}.search_report",
    )


def _tool_get_report_summary(args: dict) -> dict:
    rid = args.get("report_id")
    if not isinstance(rid, str) or not rid:
        return err(f"{SERVER_NAME}.invalid_argument", "report_id 必须是非空字符串")
    rec = _get_storage().get(rid)
    if rec is None:
        return err(f"{SERVER_NAME}.not_found", f"未找到 report_id={rid}")
    return ok(rec, source=f"{SERVER_NAME}.get_report_summary")


def build_server() -> StdioServer:
    server = StdioServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        logger=logger,
    )
    server.register_tool(
        name="search_report",
        description="按关键词 / 行业 / 年份 / 发布机构检索行业研究报告。",
        input_schema={
            "type": "object",
            "properties": {
                "keyword": {"type": "string"},
                "industry": {"type": "string"},
                "year": {"type": "integer"},
                "publisher": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
        },
        handler=_tool_search_report,
    )
    server.register_tool(
        name="get_report_summary",
        description="按 report_id 拿研报摘要、关键观点、关键数据点。",
        input_schema={
            "type": "object",
            "properties": {"report_id": {"type": "string"}},
            "required": ["report_id"],
        },
        handler=_tool_get_report_summary,
    )
    return server


def main() -> None:
    server = build_server()
    logger.info("%s server v%s 启动(stdio)", SERVER_NAME, SERVER_VERSION)
    server.serve_forever()


if __name__ == "__main__":
    main()
