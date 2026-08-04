"""lvke-archive 数据访问层（v1.1 重写：SQLite + BM25 混合检索）。

数据布局（修订 R4 之后）::

    <data_dir>/
        metadata.sqlite                # reports / chunks / indicators
        bm25/                          # BM25 索引(jieba 分词)
            bm25.pkl
            docs.pkl
        reports/                       # （兼容旧 seed）单章 markdown
            <report_id>/chapter-N.md
        index.json                     # （兼容旧 seed）

迁移期双读：
- 首选 ``metadata.sqlite``；
- 找不到时回退到旧 ``index.json``，保证 seed 仍能工作。

对外签名说明：
- ``search`` 兼容旧 ``keyword/year:int`` 参数，**同时**接受新 ``query/year_from/year_to``
- ``find_similar`` 同时接受 ``brief: str`` 与 ``brief: dict``（v1.1 修订 R2）
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

# 让 storage 能在被 server 子进程拉起后直接 import bm25 工具


@dataclass
class ArchiveRecord:
    """单份报告的元数据（对外兼容字段保持稳定）。"""

    report_id: str
    project_name: str
    industry: str
    year: int
    brief: str
    investment_amount_yuan: int | float | None = None
    build_period_months: int | None = None
    tags: list[str] = field(default_factory=list)
    # v1.1 新增字段（可选，不破坏兼容）
    corpus_origin: str | None = None
    report_type: str | None = None
    project_type: str | None = None
    region: str | None = None
    scale_bucket: str | None = None
    source_path: str | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> "ArchiveRecord":
        return cls(
            report_id=str(raw["report_id"]),
            project_name=str(raw.get("project_name", raw.get("title", ""))),
            industry=str(raw.get("industry", "")),
            year=int(raw.get("year") or 0),
            brief=str(raw.get("brief", "")),
            investment_amount_yuan=raw.get("investment_amount_yuan"),
            build_period_months=raw.get("build_period_months"),
            tags=list(raw.get("tags", [])),
            corpus_origin=raw.get("corpus_origin"),
            report_type=raw.get("report_type"),
            project_type=raw.get("project_type"),
            region=raw.get("region"),
            scale_bucket=raw.get("scale_bucket"),
            source_path=raw.get("source_path"),
        )

    def as_dict(self) -> dict:
        out = {
            "report_id": self.report_id,
            "project_name": self.project_name,
            "industry": self.industry,
            "year": self.year,
            "brief": self.brief,
            "investment_amount_yuan": self.investment_amount_yuan,
            "build_period_months": self.build_period_months,
            "tags": list(self.tags),
        }
        for opt in (
            "corpus_origin", "report_type", "project_type", "region",
            "scale_bucket", "source_path",
        ):
            v = getattr(self, opt)
            if v is not None:
                out[opt] = v
        return out


# ── 行业匹配（P0-2）─────────────────────────────────────────────────
# 用户传入的行业词（如"光伏发电""光伏""清洁能源"）与档案库既定类目
# （如"新能源-光伏"）常常词形不一致。这里做归一化 + 同义词 + 双向子串 +
# token 交集匹配，避免 find_similar 因词形不符而清零召回。
_INDUSTRY_SYNONYMS: dict[str, str] = {
    "光伏": "新能源-光伏",
    "光伏发电": "新能源-光伏",
    "太阳能": "新能源-光伏",
    "风电": "新能源-风电",
    "风力发电": "新能源-风电",
    "储能": "新能源-储能",
    "清洁能源": "新能源",
    "新能源发电": "新能源",
    "锂电": "化工新材料-锂电",
    "锂电池": "化工新材料-锂电",
    "半导体": "电子-半导体",
    "环保": "市政-环保",
    "供水": "市政-供水",
    "管网": "市政-管网",
    "文旅": "文化旅游-主题乐园",
    "文化旅游": "文化旅游-主题乐园",
    "儿童游乐": "文化旅游-主题乐园",
    "主题乐园": "文化旅游-主题乐园",
}


def _industry_tokens(text: str) -> set[str]:
    """把行业串按分隔符拆成 token 集合，并做同义词展开。"""
    if not text:
        return set()
    raw = str(text)
    mapped = _INDUSTRY_SYNONYMS.get(raw.strip(), raw)
    parts: set[str] = set()
    for chunk in re.split(r"[-/、,，\s]+", mapped):
        chunk = chunk.strip()
        if chunk:
            parts.add(chunk)
    parts.add(raw.strip())
    return {p for p in parts if p}


# PT-6：附表/附图/附件标签识别正则（可研常见写法）。
_RE_TABLE = re.compile(r"(?:附表|表)\s*([0-9０-９一二三四五六七八九十]+(?:[-.－][0-9０-９]+)?)")
_RE_FIGURE = re.compile(r"(?:附图|图)\s*([0-9０-９一二三四五六七八九十]+(?:[-.－][0-9０-９]+)?)")
_RE_ATTACH = re.compile(r"附件\s*([0-9０-９一二三四五六七八九十]+)")

# PT-6 indicators：可研核心技经指标的正则抽取（摆脱 aux-LLM 依赖，给出可核对候选值）。
_RE_TOTAL_INVEST = re.compile(r"(?:项目总投资|总投资额|总投资)[^0-9]{0,8}([0-9][0-9,，]*(?:\.[0-9]+)?)\s*万元")
_RE_IRR = re.compile(r"(?:全部投资|税后|税前|项目)?(?:财务)?内部收益率(?:\s*[（(]?\s*IRR\s*[)）]?)?[^0-9]{0,8}([0-9]+(?:\.[0-9]+)?)\s*%")
_RE_NPV = re.compile(r"(?:财务净现值|净现值)(?:\s*[（(]?\s*NPV\s*[)）]?)?[^0-9-]{0,8}(-?[0-9][0-9,，]*(?:\.[0-9]+)?)\s*万元")
_RE_PAYBACK = re.compile(r"(?:投资回收期|回收期)(?:\s*[（(]?\s*(?:含建设期|静态|动态)\s*[)）]?)?[^0-9]{0,8}([0-9]+(?:\.[0-9]+)?)\s*年")
_RE_CAPITAL_RATIO = re.compile(r"资本金[^0-9]{0,6}(?:比例|占比)?[^0-9]{0,4}([0-9]+(?:\.[0-9]+)?)\s*%")
_RE_LAND = re.compile(r"(?:占地|用地|征地)(?:面积)?[^0-9]{0,6}([0-9][0-9,，]*(?:\.[0-9]+)?)\s*(亩|公顷|平方米|万平方米|万m²)")


def _num(s: str) -> float | None:
    try:
        return float(str(s).replace(",", "").replace("，", ""))
    except (TypeError, ValueError):
        return None


def _first(pattern: "re.Pattern[str]", text: str) -> str | None:
    m = pattern.search(text)
    return m.group(1) if m else None


def _scan_appendix_labels(
    text: str,
    chapter_no: int,
    tables: dict[str, int],
    figures: dict[str, int],
    attachments: dict[str, int],
) -> None:
    """扫描一段正文，把识别到的附表/附图/附件标签记入去重字典（保留首现章号）。"""
    for m in _RE_TABLE.finditer(text):
        label = f"表{m.group(1)}"
        tables.setdefault(label, chapter_no)
    for m in _RE_FIGURE.finditer(text):
        label = f"图{m.group(1)}"
        figures.setdefault(label, chapter_no)
    for m in _RE_ATTACH.finditer(text):
        label = f"附件{m.group(1)}"
        attachments.setdefault(label, chapter_no)


def _industry_match(query_industry: str, record_industry: str) -> bool:
    """判断两个行业标签是否相关（双向子串 + token 交集 + 同义词）。"""
    a = (query_industry or "").strip()
    b = (record_industry or "").strip()
    if not a or not b:
        return False
    # 同一父类下的稳定叶子行业不能因共享“新能源”而被视为同业。
    # 光伏、风电、储能在资产、收入模型和技术风险上均不可互换。
    a_parts = [part for part in re.split(r"[-/、,，\s]+", a) if part]
    b_parts = [part for part in re.split(r"[-/、,，\s]+", b) if part]
    if (
        len(a_parts) > 1
        and len(b_parts) > 1
        and a_parts[0] == b_parts[0]
        and a_parts[-1] != b_parts[-1]
    ):
        return False
    na = a.replace("-", "").replace(" ", "")
    nb = b.replace("-", "").replace(" ", "")
    if a in b or b in a or na in nb or nb in na:
        return True
    return bool(_industry_tokens(a) & _industry_tokens(b))


_INDUSTRY_MARKERS: dict[str, tuple[str, ...]] = {
    "新能源-光伏": ("光伏", "太阳能", "农光互补", "新能源电站"),
    "新能源-风电": ("风电", "风力发电", "风电场"),
    "新能源-储能": ("储能", "电化学储能", "抽水蓄能"),
    "冶金矿产": ("锰矿", "银矿", "矿产", "冶金", "选矿"),
    "医疗卫生": ("医院", "医疗", "卫生院", "诊疗"),
    "房产建筑": ("房地产", "住宅", "小区", "建筑"),
    "通信": ("通信", "通讯", "电信"),
    "文化旅游-主题乐园": ("儿童游乐", "主题乐园", "游乐设施", "文旅运营", "亲子客群"),
}


def _infer_industry(text: str) -> str:
    normalized = str(text or "")
    for industry, markers in _INDUSTRY_MARKERS.items():
        if any(marker in normalized for marker in markers):
            return industry
    return ""


def _effective_industry(record: ArchiveRecord) -> str:
    """Prefer strong title/brief markers over demonstrably bad index labels."""
    inferred = _infer_industry(f"{record.project_name} {record.brief}")
    return inferred or record.industry


def _text_overlap(query: str, record: ArchiveRecord) -> float:
    query_tokens = set(_sql_fallback_tokens(query))
    record_text = " ".join(
        [record.project_name, record.brief, record.industry, " ".join(record.tags)]
    )
    record_tokens = set(_sql_fallback_tokens(record_text))
    if not query_tokens or not record_tokens:
        return SequenceMatcher(None, query, record_text).ratio()
    return len(query_tokens & record_tokens) / len(query_tokens)


def _absolute_similarity(
    brief: str | dict[str, Any],
    record: ArchiveRecord,
    *,
    retrieval_score: float = 0.0,
) -> float:
    """Return a candidate-independent semantic score in ``[0, 1]``.

    Retrieval rank is deliberately only one component.  In particular, the
    best item in a weak or cross-industry candidate set can no longer become
    ``1.0`` merely because it ranked first.
    """

    data = brief if isinstance(brief, dict) else {}
    query_text = brief if isinstance(brief, str) else ArchiveStorage._brief_dict_to_text(data)
    query_industry = str(data.get("industry") or _infer_industry(query_text))
    record_industry = _effective_industry(record)
    industry_known = bool(query_industry and record_industry)
    industry_match = _industry_match(query_industry, record_industry) if industry_known else False
    query_type = str(data.get("project_type") or data.get("type") or "").strip()
    query_region = str(data.get("region") or "").strip()
    query_scale = str(data.get("scale") or "").strip()

    score = 0.0
    if industry_match:
        score += 0.45
    if query_type and record.project_type:
        if query_type in record.project_type or record.project_type in query_type:
            score += 0.15
    if query_region and record.region:
        if query_region in record.region or record.region in query_region:
            score += 0.10
    if query_scale and record.scale_bucket and query_scale == record.scale_bucket:
        score += 0.10
    score += 0.10 * min(max(_text_overlap(query_text, record), 0.0), 1.0)
    score += 0.10 * min(max(retrieval_score, 0.0), 1.0)

    if industry_known and not industry_match:
        score = min(score, 0.30)
    return round(min(max(score, 0.0), 1.0), 4)




def _sql_fallback_tokens(query: str) -> list[str]:
    """Tokenize multi-word queries for SQL LIKE fallback.

    Prefer archive BM25 tokenizer (jieba or CJK n-gram). If unavailable,
    split on whitespace and keep meaningful CJK/ascii pieces.
    """
    q = str(query or "").strip()
    if not q:
        return []
    tokens: list[str] = []
    try:
        from lvke_mcp.servers.lvke_archive.archive_index.bm25_build import tokenize  # type: ignore
        tokens = [t for t in tokenize(q) if t and not str(t).isspace()]
    except BaseException:  # noqa: BLE001
        tokens = []
    if not tokens:
        import re as _re
        parts = [p for p in _re.split(r"\s+", q) if p]
        tokens = parts if parts else [q]
    # Preserve explicit whitespace-delimited search terms before tokenizer
    # expansions. CJK n-gram fallbacks can otherwise exhaust the result cap
    # before a meaningful three-or-more-character term is retained.
    ws_parts = [p for p in q.split() if p.strip()]
    tokens = [*ws_parts, *tokens]
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        tt = str(t).strip()
        if not tt:
            continue
        if all(not ch.isalnum() and not ("一" <= ch <= "鿿") for ch in tt):
            continue
        if len(q) > 1 and len(tt) == 1 and "一" <= tt <= "鿿":
            continue
        key = tt.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tt)
    if len(ws_parts) >= 2 and len(out) > max(8, len(ws_parts) * 4):
        return ws_parts
    return out[:12]


def _row_to_record(row: sqlite3.Row) -> ArchiveRecord:
    return ArchiveRecord(
        report_id=row["report_id"],
        project_name=row["title"] or "",
        industry=row["industry"] or "",
        year=int(row["year"] or 0),
        brief=row["brief"] or "",
        corpus_origin=row["corpus_origin"],
        report_type=row["report_type"],
        project_type=row["project_type"],
        region=row["region"],
        scale_bucket=row["scale_bucket"],
        source_path=row["source_path"],
    )


@dataclass
class ArchiveStorage:
    """SQLite + BM25 优先的归档存储，缺索引时回退到旧 index.json。"""

    data_dir: Path
    _mode: str = field(default="legacy", init=False)
    _conn: sqlite3.Connection | None = field(default=None, init=False)
    _legacy_index: list[ArchiveRecord] = field(default_factory=list, init=False)
    _bm25: Any = field(default=None, init=False)
    _last_search_backend: dict[str, Any] = field(default_factory=dict, init=False)
    _sql_fallback_score: Any = field(default=None, init=False)
    _vectors: Any = field(default=None, init=False)
    _archive_root: Path | None = field(default=None, init=False)
    _db_lock: Any = field(default=None, init=False)

    def __post_init__(self) -> None:
        sqlite_path = self.data_dir / "metadata.sqlite"
        if sqlite_path.exists():
            self._open_sqlite(sqlite_path)
            self._mode = "sqlite"
            # 优先用环境配置的 archive 根路径来回读原文
            import os as _os
            archive_root = _os.environ.get("LVKE_ARCHIVE_ROOT", "")
            if archive_root:
                self._archive_root = Path(archive_root)
            self._maybe_load_bm25()
        else:
            self._load_legacy_index()
            self._mode = "legacy"

    # ── SQLite 路径 ─────────────────────────────────────────────────────

    def _open_sqlite(self, path: Path) -> None:
        # ArchiveAdapter may run search() in a worker thread for timeout isolation.
        # Allow cross-thread use and serialize SQL with an instance lock.
        conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        self._conn = conn
        if not hasattr(self, "_db_lock") or self._db_lock is None:
            import threading as _threading
            self._db_lock = _threading.RLock()

    def _db_execute(self, sql: str, params: list | tuple | None = None):
        """Thread-safe execute for shared sqlite connection."""
        if self._conn is None:
            raise RuntimeError("sqlite connection not open")
        lock = getattr(self, "_db_lock", None)
        if lock is None:
            import threading as _threading
            lock = _threading.RLock()
            self._db_lock = lock
        with lock:
            if params is None:
                return self._conn.execute(sql)
            return self._conn.execute(sql, params)

    def _maybe_load_bm25(self) -> None:
        # bm25_build 在 jieba/rank_bm25 缺失时会 raise SystemExit（BaseException）,
        # 必须用 BaseException 兜底,否则会把 MCP server 子进程整个杀掉,
        # 触发上游 "MCP call failed: Connection closed"。
        import os as _os
        if str(_os.environ.get("RAG_FORCE_ARCHIVE_SQL", "")).strip().lower() in {
            "1", "true", "yes", "on"
        }:
            self._bm25 = None
            self._vectors = None
            return
        try:
            from lvke_mcp.servers.lvke_archive.archive_index.bm25_build import Bm25Index  # type: ignore
        except BaseException as exc:  # noqa: BLE001
            try:
                import logging
                logging.getLogger("lvke.mcp.lvke-archive").warning(
                    "BM25 不可用,降级为 SQL LIKE 检索:%s", exc
                )
            except Exception:
                pass
            return
        try:
            self._bm25 = Bm25Index.load(self.data_dir / "bm25")
        except BaseException:  # noqa: BLE001
            self._bm25 = None
        # Phase A.2：同时尝试加载向量索引（lazy，失败静默）
        import os as _os
        if _os.environ.get("RAG_SKIP_ARCHIVE_VECTORS", "").strip() in {"1", "true", "TRUE", "yes"}:
            self._vectors = None
        else:
            try:
                from lvke_mcp.servers.lvke_archive.archive_index.vector_index import load as _load_vec  # type: ignore
                self._vectors = _load_vec(self.data_dir)
            except BaseException:  # noqa: BLE001
                self._vectors = None

    # ── Legacy 路径（旧 seed 兼容） ─────────────────────────────────────

    def _load_legacy_index(self) -> None:
        index_path = self.data_dir / "index.json"
        if not index_path.exists():
            return
        try:
            raw = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            raw = []
        self._legacy_index = [ArchiveRecord.from_dict(item) for item in raw]

    # ── 工具 ────────────────────────────────────────────────────────────

    def mode(self) -> str:
        return self._mode

    def reload(self) -> None:
        self._legacy_index.clear()
        self._bm25 = None
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self.__post_init__()

    def list_all(self) -> list[ArchiveRecord]:
        if self._mode == "sqlite" and self._conn is not None:
            rows = self._db_execute(
                """SELECT report_id,title,corpus_origin,industry,project_type,
                          report_type,year,region,scale_bucket,source_path,
                          char_len,quality_flag,brief
                   FROM reports"""
            ).fetchall()
            return [_row_to_record(r) for r in rows]
        return list(self._legacy_index)

    def search(
        self,
        keyword: str | None = None,
        industry: str | None = None,
        year: int | None = None,
        limit: int = 20,
        *,
        query: str | None = None,
        chapter: int | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        region: str | None = None,
        corpus: str | None = None,
    ) -> list[ArchiveRecord]:
        # 接受旧 keyword / 新 query 两种入参
        q = (query or keyword or "").strip()
        if year and not year_from and not year_to:
            year_from = year_to = year

        if self._mode == "sqlite" and self._conn is not None:
            return self._search_sqlite(
                query=q,
                industry=industry,
                chapter=chapter,
                year_from=year_from,
                year_to=year_to,
                region=region,
                corpus=corpus,
                limit=limit,
            )
        return self._search_legacy(q, industry, year, limit)

    def _search_legacy(
        self, kw: str, industry: str | None, year: int | None, limit: int
    ) -> list[ArchiveRecord]:
        kw_l = kw.lower()
        ind = (industry or "").strip()
        out: list[ArchiveRecord] = []
        for rec in self._legacy_index:
            if ind and rec.industry != ind:
                continue
            if year and rec.year != year:
                continue
            if kw_l:
                hay = " ".join(
                    [rec.project_name, rec.brief, rec.industry, " ".join(rec.tags)]
                ).lower()
                if kw_l not in hay:
                    continue
            out.append(rec)
            if len(out) >= max(1, limit):
                break
        return out

    def _search_sqlite(
        self,
        *,
        query: str,
        industry: str | None,
        chapter: int | None,
        year_from: int | None,
        year_to: int | None,
        region: str | None,
        corpus: str | None,
        limit: int,
        allow_hybrid: bool = True,
    ) -> list[ArchiveRecord]:
        assert self._conn is not None
        import os as _os
        force_sql = str(_os.environ.get("RAG_FORCE_ARCHIVE_SQL", "")).strip().lower() in {
            "1", "true", "yes", "on"
        }
        if force_sql:
            self._bm25 = None
        if allow_hybrid and query and self._bm25 is not None:
            hits = self._hybrid_search(
                query=query,
                industry=industry,
                chapter=chapter,
                year_from=year_from,
                year_to=year_to,
                region=region,
                corpus=corpus,
                limit=limit,
            )
            self._last_search_backend = {
                "backend": "bm25_hybrid",
                "bm25": True,
                "vectors": self._vectors is not None,
                "query": query,
            }
            return hits

        # 无 query 或 BM25 缺失：纯 SQL 过滤
        clauses: list[str] = []
        params: list[Any] = []
        if industry:
            clauses.append("industry = ?")
            params.append(industry)
        if year_from:
            clauses.append("year >= ?")
            params.append(int(year_from))
        if year_to:
            clauses.append("year <= ?")
            params.append(int(year_to))
        if region:
            clauses.append("region LIKE ?")
            params.append(f"%{region}%")
        if corpus:
            clauses.append("corpus_origin = ?")
            params.append(corpus)
        if chapter is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM chunks "
                "WHERE chunks.report_id = reports.report_id AND chunks.chapter_no = ?)"
            )
            params.append(int(chapter))
        if query:
            # Multi-token SQL fallback: when BM25 is unavailable, do not require
            # the whole phrase to match. Token-OR over title/brief keeps multi-
            # word queries like "酒店 可行性" usable offline.
            tokens = _sql_fallback_tokens(query)
            if not tokens:
                clauses.append("(title LIKE ? OR brief LIKE ?)")
                params.extend([f"%{query}%", f"%{query}%"])
            elif len(tokens) == 1:
                tok = tokens[0]
                clauses.append("(title LIKE ? OR brief LIKE ?)")
                params.extend([f"%{tok}%", f"%{tok}%"])
            else:
                # Prefer rows that match more tokens; still allow partial hits.
                token_groups: list[str] = []
                for tok in tokens:
                    token_groups.append("(title LIKE ? OR brief LIKE ?)")
                    params.extend([f"%{tok}%", f"%{tok}%"])
                clauses.append("(" + " OR ".join(token_groups) + ")")
                # A token counts once even when repeated in both title and brief.
                # Require two distinct token hits so a common fragment cannot
                # turn an otherwise empty query into an unrelated top result.
                score_bits = []
                score_params: list[Any] = []
                for tok in tokens:
                    score_bits.append(
                        "(CASE WHEN title LIKE ? OR brief LIKE ? THEN 1 ELSE 0 END)"
                    )
                    score_params.extend([f"%{tok}%", f"%{tok}%"])
                score_expr = " + ".join(score_bits)
                clauses.append(f"({score_expr}) >= ?")
                params.extend([*score_params, 2])
                # Rebuild SELECT with the same deterministic score for ordering.
                self._sql_fallback_score = (score_expr, score_params, tokens)
            self._last_search_backend = {
                "backend": "sql_like",
                "bm25": False,
                "tokens": tokens,
                "query": query,
            }
        else:
            self._last_search_backend = {
                "backend": "sql_filter_only",
                "bm25": False,
                "tokens": [],
                "query": "",
            }

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        score_expr = None
        score_params: list[Any] = []
        score_meta = getattr(self, "_sql_fallback_score", None)
        if score_meta:
            score_expr, score_params, _tokens = score_meta
            self._sql_fallback_score = None
        select_cols = (
            "report_id,title,corpus_origin,industry,project_type,"
            "report_type,year,region,scale_bucket,source_path,char_len,"
            "quality_flag,brief"
        )
        if score_expr:
            sql = (
                f"SELECT {select_cols}, ({score_expr}) AS _tok_score FROM reports"
                + where
                + " ORDER BY _tok_score DESC, year DESC"
                + " LIMIT ?"
            )
            exec_params = list(score_params) + list(params) + [max(1, int(limit))]
        else:
            sql = (
                f"SELECT {select_cols} FROM reports"
                + where
                + " LIMIT ?"
            )
            exec_params = list(params) + [max(1, int(limit))]
        rows = self._db_execute(sql, exec_params).fetchall()
        return [_row_to_record(r) for r in rows]

    def _hybrid_search(
        self,
        *,
        query: str,
        industry: str | None,
        chapter: int | None,
        year_from: int | None,
        year_to: int | None,
        region: str | None,
        corpus: str | None,
        limit: int,
    ) -> list[ArchiveRecord]:
        assert self._conn is not None
        from lvke_mcp.servers.lvke_archive.archive_index.bm25_build import tokenize  # type: ignore

        tokens = tokenize(query)
        bm25_hits = self._bm25.search(tokens, top_k=200) if self._bm25 else []
        vec_hits = []
        if self._vectors is not None:
            try:
                vec_hits = self._vectors.search(query, top_k=200)
            except Exception:
                vec_hits = []

        # RRF 融合（k=60）→ chunk-level scores
        rrf_k = 60
        chunk_scores: dict[str, float] = {}
        chunk_to_report: dict[str, tuple[str, int]] = {}
        for rank, (doc, _s) in enumerate(bm25_hits):
            chunk_scores[doc.chunk_id] = chunk_scores.get(doc.chunk_id, 0.0) + 1.0 / (
                rrf_k + rank + 1
            )
            chunk_to_report[doc.chunk_id] = (doc.report_id, doc.chapter_no)
        for rank, h in enumerate(vec_hits):
            chunk_scores[h.chunk_id] = chunk_scores.get(h.chunk_id, 0.0) + 1.0 / (
                rrf_k + rank + 1
            )
            chunk_to_report[h.chunk_id] = (h.report_id, h.chapter_no)

        if not chunk_scores:
            return self._search_sqlite(
                query=query, industry=industry, chapter=chapter,
                year_from=year_from, year_to=year_to, region=region,
                corpus=corpus, limit=limit, allow_hybrid=False,
            )

        # 聚合到 report 维度（同份报告取最高分 chunk）
        report_scores: dict[str, float] = {}
        for cid, score in chunk_scores.items():
            rid, ch_no = chunk_to_report[cid]
            if chapter is not None and ch_no != chapter:
                continue
            if score > report_scores.get(rid, 0.0):
                report_scores[rid] = score
        if not report_scores:
            return []

        candidate_ids = sorted(report_scores, key=lambda r: -report_scores[r])[:500]
        placeholders = ",".join(["?"] * len(candidate_ids))
        clauses = [f"report_id IN ({placeholders})"]
        params: list[Any] = list(candidate_ids)
        if industry:
            clauses.append("industry = ?")
            params.append(industry)
        if year_from:
            clauses.append("year >= ?")
            params.append(int(year_from))
        if year_to:
            clauses.append("year <= ?")
            params.append(int(year_to))
        if region:
            clauses.append("region LIKE ?")
            params.append(f"%{region}%")
        if corpus:
            clauses.append("corpus_origin = ?")
            params.append(corpus)

        sql = (
            "SELECT report_id,title,corpus_origin,industry,project_type,"
            "report_type,year,region,scale_bucket,source_path,char_len,"
            "quality_flag,brief FROM reports WHERE "
            + " AND ".join(clauses)
        )
        rows = self._db_execute(sql, params).fetchall()
        records = [_row_to_record(r) for r in rows]
        records.sort(key=lambda r: report_scores.get(r.report_id, 0.0), reverse=True)
        return records[: max(1, int(limit))]

    def get_meta(self, report_id: str) -> ArchiveRecord | None:
        if self._mode == "sqlite" and self._conn is not None:
            row = self._db_execute(
                """SELECT report_id,title,corpus_origin,industry,project_type,
                          report_type,year,region,scale_bucket,source_path,
                          char_len,quality_flag,brief FROM reports
                   WHERE report_id = ?""",
                (report_id,),
            ).fetchone()
            return _row_to_record(row) if row else None
        for rec in self._legacy_index:
            if rec.report_id == report_id:
                return rec
        return None

    def get_chapter(self, report_id: str, chapter: int) -> str | None:
        if chapter < 1 or chapter > 9:
            return None
        if self._mode == "sqlite" and self._conn is not None:
            rows = self._db_execute(
                """SELECT content FROM chunks
                   WHERE report_id = ? AND chapter_no = ?
                   ORDER BY chunk_id""",
                (report_id, chapter),
            ).fetchall()
            if not rows:
                return None
            text = "\n\n".join(r["content"] for r in rows if r["content"])
            # token 预算
            if len(text) > 3000:
                text = text[:3000].rstrip() + "\n\n…（已截断，原章节超过 3000 字）"
            return text or None
        # legacy：从磁盘读单章
        path = self.data_dir / "reports" / report_id / f"chapter-{chapter}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def get_chapter_by_theme(
        self, report_id: str, theme_keywords: Iterable[str]
    ) -> tuple[int | None, str | None]:
        """PT-6：按主题关键词（匹配 chapter_title）取标杆报告对应章正文。

        不再死依赖 1-9 章号；先按章标题命中主题关键词，再回退按内容命中。
        返回 ``(chapter_no, text)``；未命中返回 ``(None, None)``。
        """
        kws = [str(k) for k in theme_keywords if str(k).strip()]
        if not kws or self._mode != "sqlite" or self._conn is None:
            return None, None
        rows = self._db_execute(
            """SELECT DISTINCT chapter_no, chapter_title FROM chunks
               WHERE report_id = ? ORDER BY chapter_no""",
            (report_id,),
        ).fetchall()
        # 1) 按章标题命中
        for r in rows:
            title = str(r["chapter_title"] or "")
            if any(k in title for k in kws):
                ch = int(r["chapter_no"])
                return ch, self.get_chapter(report_id, ch)
        # 2) 回退：按章内容命中关键词最多的章
        best_ch: int | None = None
        best_hits = 0
        for r in rows:
            ch = int(r["chapter_no"])
            text = self.get_chapter(report_id, ch) or ""
            hits = sum(text.count(k) for k in kws)
            if hits > best_hits:
                best_hits, best_ch = hits, ch
        if best_ch is not None and best_hits > 0:
            return best_ch, self.get_chapter(report_id, best_ch)
        return None, None

    def find_similar(
        self, brief: Any, top_n: int = 5
    ) -> list[tuple[ArchiveRecord, float]]:
        """支持 ``str`` 和 ``dict`` 两种 brief（v1.1 修订 R2）。"""
        text = brief if isinstance(brief, str) else self._brief_dict_to_text(brief or {})
        if not text.strip():
            return []
        if self._mode == "sqlite" and self._conn is not None and self._bm25 is not None:
            return self._find_similar_bm25(text, dict_brief=brief, top_n=top_n)
        if self._mode == "sqlite" and self._conn is not None:
            ranked = [
                (record, _absolute_similarity(brief, record))
                for record in self.list_all()
            ]
            ranked.sort(key=lambda item: item[1], reverse=True)
            return ranked[: max(1, top_n)]
        # legacy/SQL fallback
        return self._find_similar_legacy(text, dict_brief=brief, top_n=top_n)

    @staticmethod
    def _brief_dict_to_text(d: dict) -> str:
        parts: list[str] = []
        for key in ("industry", "type", "project_type", "scale", "region", "scene", "keywords"):
            v = d.get(key)
            if v:
                parts.append(str(v))
        if "summary" in d:
            parts.append(str(d["summary"]))
        return " ".join(parts)

    def _find_similar_legacy(
        self, brief: str, *, dict_brief: Any, top_n: int
    ) -> list[tuple[ArchiveRecord, float]]:
        ranked = [
            (rec, _absolute_similarity(dict_brief, rec))
            for rec in self._legacy_index
        ]
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked[: max(1, top_n)]

    def _find_similar_bm25(
        self, text: str, *, dict_brief: Any, top_n: int
    ) -> list[tuple[ArchiveRecord, float]]:
        assert self._conn is not None
        from lvke_mcp.servers.lvke_archive.archive_index.bm25_build import tokenize  # type: ignore

        tokens = tokenize(text)
        bm25_hits = self._bm25.search(tokens, top_k=200) if self._bm25 else []
        vec_hits = []
        if self._vectors is not None:
            try:
                vec_hits = self._vectors.search(text, top_k=200)
            except Exception:
                vec_hits = []

        rrf_k = 60
        chunk_scores: dict[str, float] = {}
        chunk_to_report: dict[str, str] = {}
        for rank, (doc, _s) in enumerate(bm25_hits):
            chunk_scores[doc.chunk_id] = chunk_scores.get(doc.chunk_id, 0.0) + 1.0 / (
                rrf_k + rank + 1
            )
            chunk_to_report[doc.chunk_id] = doc.report_id
        for rank, h in enumerate(vec_hits):
            chunk_scores[h.chunk_id] = chunk_scores.get(h.chunk_id, 0.0) + 1.0 / (
                rrf_k + rank + 1
            )
            chunk_to_report[h.chunk_id] = h.report_id

        report_scores: dict[str, float] = {}
        for cid, score in chunk_scores.items():
            rid = chunk_to_report[cid]
            if score > report_scores.get(rid, 0.0):
                report_scores[rid] = score
        if not report_scores:
            return []
        industry_filter = None
        if isinstance(dict_brief, dict):
            industry_filter = dict_brief.get("industry")
        ids = list(report_scores.keys())
        placeholders = ",".join(["?"] * len(ids))
        rows = self._db_execute(
            f"""SELECT report_id,title,corpus_origin,industry,project_type,
                       report_type,year,region,scale_bucket,source_path,
                       char_len,quality_flag,brief
                FROM reports WHERE report_id IN ({placeholders})""",
            ids,
        ).fetchall()
        records = [_row_to_record(r) for r in rows]
        if industry_filter:
            # v1.4 修订(P0-2)：industry 由"硬过滤清零"改为"软排序+回退"。
            # 旧逻辑 ``industry_filter in r.industry`` 在词形不一致时(如
            # 用户传"光伏发电" vs 库类目"新能源-光伏")会把候选全部滤掉→0 召回。
            # 现改为：同/近行业排前，其余作为兜底补齐，绝不清零。
            matched = [r for r in records if _industry_match(industry_filter, r.industry)]
            rest = [r for r in records if r not in matched]
            # 命中的按分数排，未命中的也按分数排，命中优先
            matched.sort(key=lambda r: report_scores.get(r.report_id, 0.0), reverse=True)
            rest.sort(key=lambda r: report_scores.get(r.report_id, 0.0), reverse=True)
            records = matched + rest
        else:
            records.sort(key=lambda r: report_scores.get(r.report_id, 0.0), reverse=True)
        # RRF has a fixed theoretical top score of 2/(k+1) for the two
        # retrievers.  Normalize against that constant, never against the
        # current candidate set's maximum.
        max_rrf_score = 2.0 / (rrf_k + 1)
        out = [
            (
                rec,
                _absolute_similarity(
                    dict_brief,
                    rec,
                    retrieval_score=report_scores.get(rec.report_id, 0.0) / max_rrf_score,
                ),
            )
            for rec in records[: max(1, top_n)]
        ]
        out.sort(key=lambda item: item[1], reverse=True)
        return out

    def extract_structure(self, report_id: str) -> dict | None:
        if self._mode != "sqlite" or self._conn is None:
            return None
        meta = self.get_meta(report_id)
        if meta is None:
            return None
        rows = self._db_execute(
            """SELECT chapter_no, chapter_title, char_len
               FROM chunks WHERE report_id = ?
               ORDER BY chapter_no, chunk_id""",
            (report_id,),
        ).fetchall()
        chapters: dict[int, dict] = {}
        for r in rows:
            ch = int(r["chapter_no"])
            entry = chapters.setdefault(
                ch, {"no": ch, "title": r["chapter_title"], "char_len": 0}
            )
            entry["char_len"] += int(r["char_len"] or 0)
        total = sum(c["char_len"] for c in chapters.values())
        return {
            "report_id": report_id,
            "title": meta.project_name,
            "industry": meta.industry,
            "chapters": [chapters[k] for k in sorted(chapters)],
            "total_chars": total,
        }

    def extract_structure_v2(self, report_id: str) -> dict | None:
        """PT-6：在 extract_structure 骨架上识别附表/附图/附件。

        扫描全文 chunk，用正则抽取“附表X/表X-X”“附图X/图X-X”“附件X”标签，
        去重并记录出现章号。返回 extract_structure 结果 + ``appendix`` 字段：
        ``{tables:[{label,chapter_no}], figures:[...], attachments:[...], has_appendix}``。
        """
        base = self.extract_structure(report_id)
        if base is None:
            return None
        if self._mode != "sqlite" or self._conn is None:
            base["appendix"] = {"tables": [], "figures": [], "attachments": [], "has_appendix": False}
            return base
        rows = self._db_execute(
            """SELECT chapter_no, content FROM chunks
               WHERE report_id = ? ORDER BY chapter_no, chunk_id""",
            (report_id,),
        ).fetchall()
        tables: dict[str, int] = {}
        figures: dict[str, int] = {}
        attachments: dict[str, int] = {}
        for r in rows:
            ch = int(r["chapter_no"])
            _scan_appendix_labels(str(r["content"] or ""), ch, tables, figures, attachments)
        base["appendix"] = {
            "tables": [{"label": k, "chapter_no": v} for k, v in tables.items()],
            "figures": [{"label": k, "chapter_no": v} for k, v in figures.items()],
            "attachments": [{"label": k, "chapter_no": v} for k, v in attachments.items()],
            "has_appendix": bool(tables or figures or attachments),
            "counts": {"tables": len(tables), "figures": len(figures), "attachments": len(attachments)},
        }
        return base

    def extract_indicators(self, report_id: str) -> dict | None:
        """PT-6 indicators：用正则从档案正文抽取核心技经指标（可核对候选值，非精确）。

        返回 ``{report_id, title, industry, indicators:{...}, hits:int}``；抽取值可能
        缺失(None)或误抽，供横向对比与人工复核，不作为精确权威数值。
        """
        meta = self.get_meta(report_id)
        if meta is None:
            return None
        if self._mode != "sqlite" or self._conn is None:
            return {"report_id": report_id, "title": meta.project_name,
                    "industry": meta.industry, "indicators": {}, "hits": 0}
        rows = self._db_execute(
            "SELECT content FROM chunks WHERE report_id = ? ORDER BY chapter_no, chunk_id",
            (report_id,),
        ).fetchall()
        text = "\n".join(str(r["content"] or "") for r in rows)
        land_m = _RE_LAND.search(text)
        ind = {
            "total_investment_wan": _num(_first(_RE_TOTAL_INVEST, text)),
            "irr_pct": _num(_first(_RE_IRR, text)),
            "npv_wan": _num(_first(_RE_NPV, text)),
            "payback_years": _num(_first(_RE_PAYBACK, text)),
            "capital_ratio_pct": _num(_first(_RE_CAPITAL_RATIO, text)),
            "land": (f"{land_m.group(1)}{land_m.group(2)}" if land_m else None),
        }
        hits = sum(1 for v in ind.values() if v is not None)
        return {
            "report_id": report_id, "title": meta.project_name,
            "industry": meta.industry, "indicators": ind, "hits": hits,
        }
