"""lvke-clients 数据访问层。

数据布局 (``<data_dir>/clients.json``):

::

    [
        {
            "client_id": "C-001",
            "name": "...",
            "uniscid": "9142...",           # 统一社会信用代码
            "industry": "能源",
            "region": "武汉",
            "client_type": "国企",
            "contact": "...",
            "cooperation_since": 2019,
            "projects": [
                {"report_id": "...", "project_name": "...", "year": 2023, "amount_yuan": 230000000},
                ...
            ],
            "notes": "..."
        },
        ...
    ]
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_ENERGY_TERMS = {
    "能源": {"能源", "新能源", "光伏", "太阳能", "储能", "电力"},
    "新能源": {"新能源", "光伏", "太阳能", "储能", "电力"},
    "光伏": {"光伏", "太阳能", "新能源"},
    "太阳能": {"太阳能", "光伏", "新能源"},
    "文旅": {"文旅", "文化旅游", "旅游", "儿童游乐", "主题乐园"},
    "儿童游乐": {"儿童游乐", "主题乐园", "文旅", "文化旅游"},
    "主题乐园": {"主题乐园", "儿童游乐", "文旅", "文化旅游"},
}

_KEYWORD_TERMS = {
    # 关键词检索保留具体业务语义。若把“新能源”父类视为“光伏”
    # 的同义词，会把新能源汽车等无关客户错误召回。
    "光伏": {"光伏", "太阳能"},
    "太阳能": {"太阳能", "光伏"},
    "文旅": {"文旅", "文化旅游", "旅游"},
    "儿童游乐": {"儿童游乐", "主题乐园", "游乐设施"},
    "主题乐园": {"主题乐园", "儿童游乐", "游乐设施"},
}

_HUBEI_REGIONS = {
    "湖北", "湖北省", "武汉", "黄石", "十堰", "宜昌", "襄阳", "鄂州",
    "荆门", "孝感", "荆州", "黄冈", "咸宁", "随州", "恩施", "仙桃",
    "潜江", "天门", "神农架",
}


def _terms(value: str) -> set[str]:
    normalized = str(value or "").strip()
    return _ENERGY_TERMS.get(normalized, {normalized} if normalized else set())


def _keyword_terms(value: str) -> set[str]:
    normalized = str(value or "").strip()
    return _KEYWORD_TERMS.get(normalized, {normalized} if normalized else set())


def _region_matches(query: str, value: str) -> bool:
    q = str(query or "").strip().removesuffix("省").removesuffix("市")
    v = str(value or "").strip().removesuffix("省").removesuffix("市")
    if not q:
        return True
    if q == "湖北":
        return value in _HUBEI_REGIONS or v in _HUBEI_REGIONS
    return q == v or q in v or v in q


@dataclass
class ClientStorage:
    data_dir: Path
    _records: list[dict[str, Any]] = field(default_factory=list, init=False)
    _loaded: bool = field(default=False, init=False)

    def _load(self) -> None:
        if self._loaded:
            return
        path = self.data_dir / "clients.json"
        if path.exists():
            self._records = json.loads(path.read_text(encoding="utf-8"))
        else:
            self._records = []
        self._loaded = True

    def reload(self) -> None:
        self._loaded = False
        self._load()

    def list_all(self) -> list[dict[str, Any]]:
        self._load()
        return list(self._records)

    def search(
        self,
        keyword: str | None = None,
        industry: str | None = None,
        region: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self._load()
        kw = (keyword or "").strip().lower()
        ind = (industry or "").strip()
        reg = (region or "").strip()
        out: list[dict[str, Any]] = []
        for rec in self._records:
            projects = rec.get("projects") or []
            hay = " ".join(
                [
                    rec.get("name", ""), rec.get("industry", ""),
                    rec.get("region", ""), rec.get("notes", ""), rec.get("contact", ""),
                    *[str(item.get("project_name") or "") for item in projects if isinstance(item, dict)],
                ]
            ).lower()
            if ind and not any(term.lower() in hay for term in _terms(ind)):
                continue
            if reg and not _region_matches(reg, str(rec.get("region") or "")):
                continue
            if kw:
                if not any(term.lower() in hay for term in _keyword_terms(kw)):
                    continue
            out.append(rec)
            if len(out) >= max(1, limit):
                break
        return out

    def get(
        self,
        client_id: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any] | None:
        self._load()
        for rec in self._records:
            if client_id and rec.get("client_id") == client_id:
                return rec
            if name and rec.get("name") == name:
                return rec
        return None

    def projects_of(
        self,
        client_id: str | None = None,
        name: str | None = None,
    ) -> list[dict[str, Any]] | None:
        rec = self.get(client_id=client_id, name=name)
        if rec is None:
            return None
        return list(rec.get("projects") or [])
