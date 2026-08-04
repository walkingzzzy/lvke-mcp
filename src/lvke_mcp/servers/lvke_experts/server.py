"""lvke-experts MCP server 入口(stdio)。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


from lvke_mcp.runtime.logging import get_logger  # noqa: E402
from lvke_mcp.runtime.responses import err, ok  # noqa: E402
from lvke_mcp.runtime.stdio import StdioServer  # noqa: E402

SERVER_NAME = "lvke-experts"
SERVER_VERSION = "0.1.0"
logger = get_logger(SERVER_NAME)

_INDUSTRY_ALIASES = {
    "新能源": {"光伏", "储能", "电力系统", "新能源"},
    "太阳能": {"光伏", "太阳能"},
    "光伏发电": {"光伏", "光伏发电"},
    "文旅": {"文旅运营", "文化旅游", "主题乐园", "儿童游乐"},
    "儿童游乐": {"儿童游乐", "大型游乐设施", "主题乐园", "文旅运营"},
    "主题乐园": {"主题乐园", "大型游乐设施", "儿童游乐", "文旅运营"},
}

_HUBEI_REGIONS = {
    "湖北", "湖北省", "武汉", "黄石", "十堰", "宜昌", "襄阳", "鄂州",
    "荆门", "孝感", "荆州", "黄冈", "咸宁", "随州", "恩施", "仙桃",
    "潜江", "天门", "神农架",
}


def _matches_terms(query: str, values: list[str]) -> bool:
    terms = _INDUSTRY_ALIASES.get(query, {query})
    return any(
        term in value or value in term
        for term in terms
        for value in values
        if term and value
    )


def _region_matches(query: str, value: str) -> bool:
    q = str(query or "").strip().removesuffix("省").removesuffix("市")
    v = str(value or "").strip().removesuffix("省").removesuffix("市")
    if not q:
        return True
    if q == "湖北":
        return value in _HUBEI_REGIONS or v in _HUBEI_REGIONS
    return q == v or q in v or v in q


@dataclass
class ExpertStorage:
    """专家档案存储。"""

    data_dir: Path
    _records: list[dict[str, Any]] = field(default_factory=list, init=False)
    _loaded: bool = field(default=False, init=False)

    def _load(self) -> None:
        if self._loaded:
            return
        path = self.data_dir / "experts.json"
        if path.exists():
            self._records = json.loads(path.read_text(encoding="utf-8"))
        else:
            self._records = []
        self._loaded = True

    def list_all(self) -> list[dict[str, Any]]:
        self._load()
        return list(self._records)

    def find(
        self,
        industry: str | None = None,
        specialty: str | None = None,
        role: str | None = None,
        region: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self._load()
        out: list[dict[str, Any]] = []
        ind = (industry or "").strip()
        sp = (specialty or "").strip()
        rl = (role or "").strip()
        reg = (region or "").strip()
        for rec in self._records:
            if ind and not _matches_terms(ind, list(rec.get("industries") or [])):
                continue
            if sp:
                sp_list = rec.get("specialties") or []
                if not _matches_terms(sp, sp_list):
                    continue
            if rl and not _matches_terms(rl, list(rec.get("roles") or [])):
                continue
            if reg and not _region_matches(reg, str(rec.get("region") or "")):
                continue
            out.append(rec)
            if len(out) >= max(1, limit):
                break
        return out

    def get(self, expert_id: str) -> dict[str, Any] | None:
        self._load()
        for rec in self._records:
            if rec.get("expert_id") == expert_id:
                return rec
        return None

    def specialties(self) -> dict[str, list[str]]:
        self._load()
        industries: set[str] = set()
        specs: set[str] = set()
        roles: set[str] = set()
        regions: set[str] = set()
        for rec in self._records:
            industries.update(rec.get("industries") or [])
            specs.update(rec.get("specialties") or [])
            roles.update(rec.get("roles") or [])
            if rec.get("region"):
                regions.add(rec["region"])
        return {
            "industries": sorted(industries),
            "specialties": sorted(specs),
            "roles": sorted(roles),
            "regions": sorted(regions),
        }


def resolve_data_dir() -> Path:
    env_dir = os.environ.get("LVKE_EXPERTS_DATA_DIR", "").strip()
    if env_dir:
        p = Path(env_dir).expanduser()
        if p.exists():
            return p
    base = Path(__file__).resolve().parent
    data_dir = base / "data"
    if (data_dir / "experts.json").exists():
        return data_dir
    return base / "seed"


_storage: ExpertStorage | None = None


def _get_storage() -> ExpertStorage:
    global _storage
    if _storage is None:
        d = resolve_data_dir()
        logger.info("使用专家数据目录:%s", d)
        _storage = ExpertStorage(data_dir=d)
    return _storage


def _tool_find_experts(args: dict) -> dict:
    out = _get_storage().find(
        industry=args.get("industry"),
        specialty=args.get("specialty"),
        role=args.get("role"),
        region=args.get("region"),
        limit=int(args.get("limit") or 20),
    )
    return ok(
        {"count": len(out), "items": out},
        source=f"{SERVER_NAME}.find_experts",
    )


def _tool_get_expert(args: dict) -> dict:
    expert_id = args.get("expert_id")
    if not isinstance(expert_id, str) or not expert_id:
        return err(f"{SERVER_NAME}.invalid_argument", "expert_id 必须是非空字符串")
    rec = _get_storage().get(expert_id)
    if rec is None:
        return err(f"{SERVER_NAME}.not_found", f"未找到专家 expert_id={expert_id}")
    return ok(rec, source=f"{SERVER_NAME}.get_expert")


def _tool_list_specialties(args: dict) -> dict:
    return ok(_get_storage().specialties(), source=f"{SERVER_NAME}.list_specialties")


def build_server() -> StdioServer:
    server = StdioServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        logger=logger,
    )
    server.register_tool(
        name="find_experts",
        description="按行业 / 专长 / 角色 / 地区找匹配专家。",
        input_schema={
            "type": "object",
            "properties": {
                "industry": {"type": "string"},
                "specialty": {"type": "string"},
                "role": {"type": "string"},
                "region": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
        },
        handler=_tool_find_experts,
    )
    server.register_tool(
        name="get_expert",
        description="按 expert_id 取单专家档案。",
        input_schema={
            "type": "object",
            "properties": {"expert_id": {"type": "string"}},
            "required": ["expert_id"],
        },
        handler=_tool_get_expert,
    )
    server.register_tool(
        name="list_specialties",
        description="返回专家库中可用的行业 / 专长 / 角色 / 地区字典。",
        input_schema={"type": "object", "properties": {}},
        handler=_tool_list_specialties,
    )
    return server


def main() -> None:
    server = build_server()
    logger.info("%s server v%s 启动(stdio)", SERVER_NAME, SERVER_VERSION)
    server.serve_forever()


if __name__ == "__main__":
    main()
