"""lvke-archive MCP server 入口(stdio)。

数据目录优先级（v1.1 修订 R4 后）:
1. 环境变量 ``LVKE_ARCHIVE_DATA_DIR`` 指定的目录(生产)；判据是 ``metadata.sqlite``。
2. ``<repo>/data/archive_index/``（默认 build_archive_index 输出位置）。
3. ``lvke_mcp/servers/lvke_archive/data/``(若用户已覆盖)。
4. ``lvke_mcp/servers/lvke_archive/seed/``(仓库自带样例)。

启动方式::

    python -m lvke_mcp.servers.lvke_archive.server
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path


from lvke_mcp.runtime.logging import get_logger  # noqa: E402
from lvke_mcp.runtime.responses import err, ok  # noqa: E402
from lvke_mcp.runtime.stdio import StdioServer  # noqa: E402

from lvke_mcp.servers.lvke_archive.storage import ArchiveStorage

SERVER_NAME = "lvke-archive"
SERVER_VERSION = "1.0.0"
logger = get_logger(SERVER_NAME)


def resolve_data_dir() -> Path:
    """按优先级解析数据目录。

    修订 R4：判据从 ``index.json`` 升级到 ``metadata.sqlite``，
    但保留 ``index.json`` 作为兼容回退（seed 仍能工作）。
    """

    env_dir = os.environ.get("LVKE_ARCHIVE_DATA_DIR", "").strip()
    if env_dir:
        p = Path(env_dir).expanduser()
        if (p / "metadata.sqlite").exists() or (p / "index.json").exists():
            return p
        logger.warning(
            "LVKE_ARCHIVE_DATA_DIR 指向不存在或缺少索引文件:%s,回退默认目录", env_dir
        )

    # 仓库默认输出目录
    repo_default = (
        Path(__file__).resolve().parents[2] / "data" / "archive_index"
    )
    if (repo_default / "metadata.sqlite").exists():
        return repo_default

    base = Path(__file__).resolve().parent
    data_dir = base / "data"
    if (data_dir / "metadata.sqlite").exists() or (data_dir / "index.json").exists():
        return data_dir
    return base / "seed"


def build_storage() -> ArchiveStorage:
    data_dir = resolve_data_dir()
    storage = ArchiveStorage(data_dir=data_dir)
    logger.info("使用归档数据目录:%s (mode=%s)", data_dir, storage.mode())
    return storage


_storage: ArchiveStorage | None = None


def _get_storage() -> ArchiveStorage:
    global _storage
    if _storage is None:
        _storage = build_storage()
    return _storage


_SCENE_KEYWORDS = {
    "policy-driver": "政策依据 战略 规划",
    "necessity": "必要性 建设必要",
    "market-demand": "市场需求 需求分析",
    "risk-financial": "财务风险 偿债 流动性",
    "risk-policy": "政策风险",
    "conclusion": "研究结论 主要结论",
    "site-selection": "项目选址 建设条件",
}


# ── 工具实现 ─────────────────────────────────────────────────────────────


def _tool_search_archive(args: dict) -> dict:
    # 同时接受旧字段（keyword/year）与新字段（query/year_from/year_to/chapter/region/corpus）
    keyword = args.get("keyword") or args.get("query") or ""
    industry = args.get("industry") or None
    year = args.get("year")
    year_from = args.get("year_from")
    year_to = args.get("year_to")
    chapter = args.get("chapter")
    region = args.get("region") or None
    corpus = args.get("corpus") or None
    limit = int(args.get("limit") or args.get("top_k") or 20)

    for name, value in (
        ("year", year), ("year_from", year_from), ("year_to", year_to),
        ("chapter", chapter), ("limit", limit),
    ):
        if value is not None and not isinstance(value, int):
            return err(
                f"{SERVER_NAME}.invalid_argument",
                f"{name} 必须是整数,收到 {type(value).__name__}",
            )
    if chapter is not None and not (0 <= chapter <= 9):
        return err(f"{SERVER_NAME}.invalid_argument", "chapter 必须在 0-9")

    try:
        records = _get_storage().search(
            keyword=keyword,
            industry=industry,
            year=year if (year_from is None and year_to is None) else None,
            year_from=year_from,
            year_to=year_to,
            chapter=chapter,
            region=region,
            corpus=corpus,
            limit=limit,
        )
    except Exception:  # noqa: BLE001
        logger.exception("archive search failed")
        return err(
            f"{SERVER_NAME}.storage_error",
            "归档库访问失败",
        )

    items = []
    for r in records:
        item = r.as_dict()
        # 加 snippet（brief 截 200 字）便于前端展示
        if item.get("brief"):
            item["snippet"] = item["brief"][:200]
        items.append(item)

    return ok(
        {
            "count": len(items),
            "items": items,
            "filter": {
                "keyword": keyword,
                "industry": industry,
                "year": year,
                "year_from": year_from,
                "year_to": year_to,
                "chapter": chapter,
                "region": region,
                "corpus": corpus,
                "limit": limit,
            },
        },
        source=f"{SERVER_NAME}.search_archive",
    )


def _tool_get_chapter(args: dict) -> dict:
    report_id = args.get("report_id")
    chapter = args.get("chapter")
    if not isinstance(report_id, str) or not report_id:
        return err(f"{SERVER_NAME}.invalid_argument", "report_id 必须是非空字符串")
    if not isinstance(chapter, int) or chapter < 1 or chapter > 9:
        return err(f"{SERVER_NAME}.invalid_argument", "chapter 必须是 1-9 的整数")

    storage = _get_storage()
    meta = storage.get_meta(report_id)
    if meta is None:
        return err(f"{SERVER_NAME}.not_found", f"未找到 report_id={report_id}")
    text = storage.get_chapter(report_id, chapter)
    if text is None:
        return err(
            f"{SERVER_NAME}.chapter_not_found",
            f"报告 {report_id} 不存在第 {chapter} 章正文",
        )
    return ok(
        {
            "report_id": report_id,
            "chapter": chapter,
            "content_markdown": text,
            "content": text,  # 新名兼容
            "meta": meta.as_dict(),
        },
        source=f"{SERVER_NAME}.get_chapter",
    )


def _tool_find_similar_projects(args: dict) -> dict:
    brief = args.get("brief")
    top_n = int(args.get("top_n") or args.get("top_k") or 5)
    if isinstance(brief, str):
        if not brief.strip():
            return err(f"{SERVER_NAME}.invalid_argument", "brief 字符串不能为空")
    elif isinstance(brief, dict):
        if not brief:
            return err(f"{SERVER_NAME}.invalid_argument", "brief 对象不能为空")
    else:
        return err(
            f"{SERVER_NAME}.invalid_argument",
            "brief 必须是字符串或 {industry,scale,type,region,…} 对象",
        )

    pairs = _get_storage().find_similar(brief, top_n=top_n)
    query_industry = ""
    if isinstance(brief, dict):
        query_industry = str(brief.get("industry") or "")
    else:
        from lvke_mcp.servers.lvke_archive.storage import _effective_industry, _infer_industry, _industry_match
        query_industry = _infer_industry(brief)
    if isinstance(brief, dict):
        from lvke_mcp.servers.lvke_archive.storage import _effective_industry, _industry_match

    items = []
    for rec, score in pairs:
        industry_match = (
            _industry_match(query_industry, _effective_industry(rec))
            if query_industry and _effective_industry(rec)
            else None
        )
        item = {"similarity": round(score, 4), **rec.as_dict()}
        item["industry_match"] = industry_match
        if industry_match is False:
            item["fallback_reason"] = "cross_industry_fallback"
        items.append(item)
    return ok(
        {
            "query": brief,
            "count": len(pairs),
            "items": items,
        },
        source=f"{SERVER_NAME}.find_similar_projects",
    )


def _tool_extract_structure(args: dict) -> dict:
    report_id = args.get("report_id")
    if not isinstance(report_id, str) or not report_id:
        return err(f"{SERVER_NAME}.invalid_argument", "report_id 必须是非空字符串")
    storage = _get_storage()
    if storage.mode() != "sqlite":
        return err(
            f"{SERVER_NAME}.index_unavailable",
            "结构提取需要 metadata.sqlite，请先运行 scripts/build_archive_index.py",
        )
    # PT-6：with_appendix=True 时用 v2 识别附表/附图/附件（默认开启，向后兼容——
    # 旧调用方仍能拿到 chapters/total_chars，只是多出 appendix 字段）。
    with_appendix = args.get("with_appendix", True)
    if with_appendix:
        structure = storage.extract_structure_v2(report_id)
    else:
        structure = storage.extract_structure(report_id)
    if structure is None:
        return err(f"{SERVER_NAME}.not_found", f"未找到 report_id={report_id}")
    return ok(structure, source=f"{SERVER_NAME}.extract_structure")


def _tool_compare_cases(args: dict) -> dict:
    report_ids = args.get("report_ids")
    if not isinstance(report_ids, list) or not report_ids:
        return err(f"{SERVER_NAME}.invalid_argument", "report_ids 必须是非空列表")
    if len(report_ids) > 8:
        return err(f"{SERVER_NAME}.invalid_argument", "report_ids 最多 8 个")
    # PT-6：三模式 —— structure（章节骨架对比）/ appendix（附表附件对比）/
    # indicators（元数据，结构化指标抽取待 aux-LLM，诚实标注）。
    dim = args.get("dim") or "indicators"
    if dim not in ("structure", "appendix", "indicators", "key-indicators"):
        return err(f"{SERVER_NAME}.invalid_argument",
                   "dim 可选：structure / appendix / indicators")

    storage = _get_storage()
    rows: list[dict] = []
    for rid in report_ids:
        if not isinstance(rid, str):
            continue
        meta = storage.get_meta(rid)
        if meta is None:
            rows.append({"report_id": rid, "error": "not_found"})
            continue
        base = {
            "report_id": meta.report_id, "title": meta.project_name,
            "industry": meta.industry, "year": meta.year, "region": meta.region,
        }
        if dim == "structure":
            st = storage.extract_structure(rid) or {}
            base["chapter_count"] = len(st.get("chapters", []))
            base["total_chars"] = st.get("total_chars", 0)
            base["chapters"] = [{"no": c["no"], "title": c["title"], "char_len": c["char_len"]}
                                for c in st.get("chapters", [])]
        elif dim == "appendix":
            st = storage.extract_structure_v2(rid) or {}
            ap = st.get("appendix", {}) or {}
            base["appendix_counts"] = ap.get("counts", {})
            base["has_appendix"] = ap.get("has_appendix", False)
            base["tables"] = [t["label"] for t in ap.get("tables", [])]
            base["figures"] = [f["label"] for f in ap.get("figures", [])]
            base["attachments"] = [a["label"] for a in ap.get("attachments", [])]
        elif dim in ("indicators", "key-indicators"):
            ic = storage.extract_indicators(rid) or {}
            ind = ic.get("indicators", {}) or {}
            base["total_investment_wan"] = ind.get("total_investment_wan")
            base["irr_pct"] = ind.get("irr_pct")
            base["npv_wan"] = ind.get("npv_wan")
            base["payback_years"] = ind.get("payback_years")
            base["capital_ratio_pct"] = ind.get("capital_ratio_pct")
            base["land"] = ind.get("land")
            base["extracted_hits"] = ic.get("hits", 0)
        rows.append(base)

    headers_map = {
        "structure": ["report_id", "title", "chapter_count", "total_chars"],
        "appendix": ["report_id", "title", "appendix_counts", "has_appendix"],
        "indicators": ["report_id", "title", "total_investment_wan", "irr_pct",
                       "npv_wan", "payback_years", "capital_ratio_pct", "land"],
        "key-indicators": ["report_id", "title", "total_investment_wan", "irr_pct",
                           "npv_wan", "payback_years", "capital_ratio_pct", "land"],
    }
    note = {
        "structure": "章节骨架对比（章数/字数/各章标题）。",
        "appendix": "附表/附图/附件识别对比（正则识别，供附件完备性核查）。",
        "indicators": "技经指标正则抽取横向对比（总投资/IRR/NPV/回收期/资本金比例/占地）；抽取值可能缺失或误抽，须人工复核，不作精确权威数值。",
        "key-indicators": "技经指标正则抽取横向对比（总投资/IRR/NPV/回收期/资本金比例/占地）；抽取值可能缺失或误抽，须人工复核，不作精确权威数值。",
    }[dim]
    return ok(
        {"dim": dim, "headers": headers_map[dim], "matrix": rows, "note": note},
        source=f"{SERVER_NAME}.compare_cases",
    )


def _tool_get_template_paragraph(args: dict) -> dict:
    scene = (args.get("scene") or "").strip()
    industry = args.get("industry") or None
    top_k = int(args.get("top_k") or 3)
    if not scene:
        return err(
            f"{SERVER_NAME}.invalid_argument",
            "scene 必填，可选: policy-driver / necessity / market-demand / "
            "risk-financial / risk-policy / conclusion / site-selection",
        )
    if scene not in _SCENE_KEYWORDS:
        return err(
            f"{SERVER_NAME}.invalid_argument",
            "scene 非法，可选: " + " / ".join(_SCENE_KEYWORDS),
        )

    # 场景 → 关键词映射（简版；Phase A.1 可升级为聚类落表）
    query = _SCENE_KEYWORDS[scene]

    items: list[dict] = []
    storage = _get_storage()
    if storage.mode() != "sqlite":
        return err(
            f"{SERVER_NAME}.index_unavailable",
            "套话段落需要全量索引；请先运行 scripts/build_archive_index.py",
        )
    records = storage.search(query=query, industry=industry, corpus="lvke", limit=top_k * 2)
    if not records:
        records = storage.search(query=query, industry=industry, limit=top_k * 2)
    for rec in records[:top_k]:
        snippet = (rec.brief or "")[:300]
        content_hash = hashlib.sha256(snippet.encode("utf-8")).hexdigest()
        items.append({
            "kind": "archive_snippet",
            "source_report_id": rec.report_id,
            "source_title": rec.project_name,
            "source_industry": rec.industry,
            "snippet": snippet,
            "source_path": rec.source_path,
            "content_hash": content_hash,
            "validation_status": "source_indexed" if snippet else "content_missing",
            "evidence_level": "C",
            "note": "归档索引片段仅用于参考论证结构与表述风格。",
        })
    return ok(
        {"scene": scene, "industry": industry, "items": items},
        source=f"{SERVER_NAME}.get_template_paragraph",
    )


# ── 注册 ───────────────────────────────────────────────────────────────


def build_server() -> StdioServer:
    server = StdioServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        logger=logger,
    )
    server.register_tool(
        name="search_archive",
        description=(
            "按关键词/行业/年份/章节/区域检索绿科历史可研档案。"
            "兼容旧参数 keyword/year/limit,新参数 query/year_from/year_to/chapter/region/corpus/top_k。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "(旧)关键词"},
                "query": {"type": "string", "description": "(新)检索语句"},
                "industry": {"type": "string"},
                "year": {"type": "integer"},
                "year_from": {"type": "integer"},
                "year_to": {"type": "integer"},
                "chapter": {"type": "integer", "minimum": 0, "maximum": 9},
                "region": {"type": "string"},
                "corpus": {"type": "string", "description": "lvke / public"},
                "limit": {"type": "integer", "default": 20},
                "top_k": {"type": "integer", "default": 20},
            },
        },
        handler=_tool_search_archive,
    )
    server.register_tool(
        name="get_chapter",
        description="按 report_id 和章节号(1-9)取出该章节正文 markdown（>3000 字会截断）。",
        input_schema={
            "type": "object",
            "properties": {
                "report_id": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1, "maximum": 9},
            },
            "required": ["report_id", "chapter"],
        },
        handler=_tool_get_chapter,
    )
    server.register_tool(
        name="find_similar_projects",
        description=(
            "找最相似的 top_n 个历史项目。brief 接受字符串(项目摘要)或对象"
            "{industry, scale, type, region, summary, keywords}。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "brief": {
                    "description": "字符串或结构化对象",
                    "oneOf": [
                        {"type": "string"},
                        {"type": "object"},
                    ],
                },
                "top_n": {"type": "integer", "default": 5},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["brief"],
        },
        handler=_tool_find_similar_projects,
    )
    server.register_tool(
        name="extract_structure",
        description=(
            "提取一份档案的章节骨架(每章字数、标题)。with_appendix=true(默认)时额外"
            "识别附表/附图/附件清单，便于附件完备性核查。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "report_id": {"type": "string"},
                "with_appendix": {"type": "boolean", "default": True,
                                  "description": "是否识别附表/附图/附件（默认 true）"},
            },
            "required": ["report_id"],
        },
        handler=_tool_extract_structure,
    )
    server.register_tool(
        name="compare_cases",
        description=(
            "横向对比多份档案(≤8)。dim 三模式：structure(章节骨架) / appendix(附表附件) / "
            "indicators(元数据，默认)。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "report_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 8,
                },
                "dim": {
                    "type": "string",
                    "description": "structure / appendix / indicators（默认 indicators）",
                },
            },
            "required": ["report_ids"],
        },
        handler=_tool_compare_cases,
    )
    server.register_tool(
        name="get_template_paragraph",
        description=(
            "按场景获取归档索引中的可复用段落。scene 枚举: policy-driver / necessity / "
            "market-demand / risk-financial / risk-policy / conclusion / site-selection。"
            "每项包含来源定位、内容 hash 和确定性 validation_status。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "scene": {"type": "string", "enum": list(_SCENE_KEYWORDS)},
                "industry": {"type": "string"},
                "top_k": {"type": "integer", "default": 3},
            },
            "required": ["scene"],
        },
        handler=_tool_get_template_paragraph,
    )
    return server


def main() -> None:
    server = build_server()
    logger.info("%s server v%s 启动(stdio)", SERVER_NAME, SERVER_VERSION)
    server.serve_forever()


if __name__ == "__main__":
    main()
