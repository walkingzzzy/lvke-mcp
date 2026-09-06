"""Deterministic validation use cases for immutable report revisions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from lvke_mcp.adapters.report_repository import PREPARATION_STORE
from lvke_mcp.adapters.research_repository import PACKAGE_STORE as RESEARCH_STORE
from lvke_mcp.domains.finance import gate as finance_gate
from lvke_mcp.domains.reports import doc_service as doc
from lvke_mcp.domains.reports import readiness as report_artifacts
from lvke_mcp.domains.reports.read_model import (
    resolve_revision_record,
    supplied_document_snapshot,
)
from lvke_mcp.runtime.formal_promotion import FormalLineageError, SIM_A_FORMAL
from lvke_mcp.adapters.quality_diagnostic_repository import diagnostics_for_target
from lvke_mcp.runtime.quality_severity import is_finance_data_quality_issue

# ── 九章实质内容契约 ──────────────────────────────────────────────────
# 各章有效中文字符下限（标题/目录/引用列表/表格单元格不计入）
_CHAPTER_MIN_CHARS: dict[str, tuple[int, ...]] = {
    "asset_acquisition": (1000, 1500, 1800, 2200, 1200, 2000, 1400, 1000, 800),
    # 发改委 2023 十章制。此前该 report_type 没有登记阈值，`min_chars` 取不到就
    # 整段跳过校验——正文只有几百字也照样报 technical_preview_ready，字数不足在
    # 交付前无人可见。阈值按十章各自的论证负荷分档：背景/必要性、投资估算、财务
    # 分析、结论四章承载主要论证，取 1800；站址、设备、运营三章以方案描述为主，
    # 取 1200。
    "gov10": (1800, 1800, 1800, 1200, 1200, 1200, 1800, 1800, 1400, 1800),
    # 细粒度三级目录（144 个叶子节）的下限，按**各章叶子数 × 单叶下限 700 字**
    # 派生。gov10 的下限是按「一章一段论证」定的，直接拿来用在细粒度结构上会
    # 让 20 章叶子的第 5 章与 5 章叶子的第 10 章拿同一个阈值，完全脉冲不上篇幅。
    # 叶子数：13/14/14/13/20/15/19/15/16/5。
    "gov10_full": (9100, 9800, 9800, 9100, 14000, 10500, 13300, 10500, 11200, 3500),
}

#: 单个叶子节（最深一级标题）的目标字数区间。
#:
#: 此前全仓只有「下限」一种信号，没有任何地方告诉调用方「应该写多长」：门禁
#: 只问「够不够 1800」，于是 Agent 写到 1800 就停，整篇稳定落在下限附近。区间
#: 上缘不阻断（写长不是错），但低于下缘会出 warning，使「差多少」可见。
_LEAF_TARGET_CHARS: dict[str, tuple[int, int]] = {
    "gov10_full": (800, 1500),
}

# 资产收购报告必须覆盖的主题（按章节索引）
_CHAPTER_REQUIRED_THEMES: dict[str, tuple[tuple[str, ...], ...]] = {
    "asset_acquisition": (
        ("标的范围", "交易结构", "报价"),
        ("项目背景", "建设必要性", "交易主体"),
        ("需求分析", "建设规模", "运营数据", "区域消纳"),
        ("总体建设方案", "并网条件", "技术尽调", "运营运维"),
        ("投资估算", "资金筹措", "购买价分摊", "融资"),
        ("财务分析", "收益指标", "最高收购价", "敏感性"),
        ("政策风险", "市场风险", "技术风险", "财务风险", "实施风险", "运营风险", "社会环境风险"),
        ("保障措施", "交割条件", "运营保障"),
        ("结论", "投决建议", "建议"),
    ),
}

# 风险章必须覆盖的七类风险关键词
_REQUIRED_RISK_CATEGORIES = (
    "政策风险", "市场风险", "技术风险", "财务风险",
    "实施风险", "运营风险", "社会环境风险",
)

#: 风险章的章号，按报告类型登记。此前该章号硬编码为 7——那是资产收购九章制的
#: 风险章位置，而十章制的第 7 章是投资估算、风险在第 9 章。硬编码会让 gov10
#: 报告在投资估算章上误报"七类风险缺失"，同时真正的风险章不被校验。
_RISK_CHAPTER_INDEX: dict[str, int] = {
    "asset_acquisition": 7,
    "gov10": 9,
}

# 需要含特定表格的章节
_CHAPTER_REQUIRED_TABLES: dict[str, tuple[int, ...]] = {
    "asset_acquisition": (1, 3, 5, 6),  # 第1/3/5/6章
}


def _cn_chars(text: str) -> int:
    """Count Chinese characters in a text string."""
    import re
    return len(re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]", text))


def _numbered_chapter_blocks(markdown: str) -> list[str]:
    """Return only the numbered body chapters, in order.

    ``_split_chapters`` 按 ``#``/``##`` 一律切块，因此结果里还夹着报告标题块
    （``# 项目名…``）与附录块（``## 附：受控假设边界``）。直接和阈值元组 zip 会
    整体错位一位：第 1 章的阈值被用来校验标题块（永远只有几十字，必然报缺），
    而最后一章根本没有阈值参与校验。

    这里只认"## <数字>、"这种编号正文章节标题，标题块与附录块都不参与字数契约。
    """

    import re

    return [
        block
        for block in _split_chapters(markdown)
        if re.match(r"^##\s*(?:\d+|[零〇一二三四五六七八九十百千两]+)\s*[、.]", block.lstrip())
    ]


def _leaf_target_warnings(
    content: str,
    report_type: str,
) -> list[str]:
    """比对每个叶子节的字数与目标区间，返回偏离提示。

    只出 warning不出 blocker：篇幅不足是「还没写完」而不是「写错了」，整篇的硬
    约束由 ``_CHAPTER_MIN_CHARS`` 按章扒。这里的作用是把「哪一节差多少」暴露出来，
    否则调用方只能看到章级总数不足，不知道该去加长哪节。
    """
    import re

    band = _LEAF_TARGET_CHARS.get(report_type)
    if not band:
        return []
    low, _high = band
    from lvke_mcp.domains.reports._doc_service.outline import report_leaf_titles
    from lvke_mcp.domains.reports.read_model import section_span

    warnings: list[str] = []
    for title in report_leaf_titles(report_type):
        span = section_span(content, title)
        if span is None:
            continue
        body = re.sub(r"^#{1,6}\s+.*$", "", str(span["content"]), flags=re.MULTILINE)
        body = re.sub(r"\|[^|]*\|", "", body)
        body = re.sub(r"\[\^[^\]]*\]", "", body)
        count = _cn_chars(body)
        if count < low:
            warnings.append(f"leaf_below_target:{title}:{count}<{low}")
    return warnings


def _split_chapters(markdown: str) -> list[str]:
    """Split markdown into chapters by heading level 1/2."""
    import re
    lines = markdown.split("\n")
    chapters: list[str] = []
    current: list[str] = []
    for line in lines:
        if re.match(r"^#{1,2}\s+", line) and current:
            chapters.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        chapters.append("\n".join(current))
    return chapters


def _validate_chapter_content(
    content: str,
    report_type: str,
    chapters: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate chapter-level content contract for word count, themes, tables.

    Returns ``{chapter_index: {...}, "blockers": [...], "warnings": [...]}``.
    """
    import re

    min_chars = _CHAPTER_MIN_CHARS.get(report_type)
    required_themes = _CHAPTER_REQUIRED_THEMES.get(report_type)
    required_tables = _CHAPTER_REQUIRED_TABLES.get(report_type)
    # 未登记的报告类型取 0：0 不会等于任何 1 起的章号，等于不做风险章校验，
    # 而不是沿用别的报告类型的章号去误判。
    risk_chapter_index = _RISK_CHAPTER_INDEX.get(report_type, 0)

    if not min_chars:
        return {"chapter_results": {}, "blockers": [], "warnings": []}

    # 只取编号正文章节；标题块与附录块不参与字数契约（见 _numbered_chapter_blocks）。
    # 无编号章节时回落到原切分，避免旧格式报告的校验整体失效。
    raw_chapters = _numbered_chapter_blocks(content) or _split_chapters(content)
    results: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    warnings: list[str] = []

    for idx, (raw_ch, min_cn) in enumerate(zip(raw_chapters, min_chars), 1):
        # Remove table cells (|...|), citation references [^...], heading markers
        clean = re.sub(r"\|[^|]*\|", "", raw_ch)
        clean = re.sub(r"\[\^[^\]]*\]", "", clean)
        clean = re.sub(r"^#{1,6}\s+", "", clean, flags=re.MULTILINE)
        cn_count = _cn_chars(clean)
        key = f"chapter_{idx}"

        result: dict[str, Any] = {
            "chinese_chars": cn_count,
            "min_required": min_cn,
            "chars_ok": cn_count >= min_cn,
        }

        # Theme coverage
        if required_themes and idx - 1 < len(required_themes):
            themes = required_themes[idx - 1]
            covered = [t for t in themes if t in raw_ch]
            missing = [t for t in themes if t not in raw_ch]
            result["required_themes"] = list(themes)
            result["covered_themes"] = covered
            result["missing_themes"] = missing
            result["themes_ok"] = len(missing) == 0
            if missing:
                blockers.append(f"REPORT_CONTENT_INSUFFICIENT:chapter_{idx}_themes_missing:{','.join(missing)}")

        # Risk categories (风险章章号按报告类型取，见 _RISK_CHAPTER_INDEX)
        if idx == risk_chapter_index:
            risk_covered = [r for r in _REQUIRED_RISK_CATEGORIES if r in raw_ch]
            risk_missing = [r for r in _REQUIRED_RISK_CATEGORIES if r not in raw_ch]
            result["risk_categories_covered"] = risk_covered
            result["risk_categories_missing"] = risk_missing
            result["risk_ok"] = len(risk_missing) == 0
            if risk_missing:
                blockers.append(
                    f"REPORT_CONTENT_INSUFFICIENT:chapter_{idx}_risk_missing"
                    f":{','.join(risk_missing)}"
                )

        # Required tables (chapters 1, 3, 5, 6)
        if required_tables and idx in required_tables:
            has_table = bool(re.search(r"\|.*\|.*\|", raw_ch))
            result["has_required_table"] = has_table
            if not has_table:
                blockers.append(f"REPORT_CONTENT_INSUFFICIENT:chapter_{idx}_table_missing")

        # Word count check
        if not result["chars_ok"]:
            blockers.append(f"REPORT_CONTENT_INSUFFICIENT:chapter_{idx}_chars:{cn_count}<{min_cn}")

        results[key] = result

    return {
        "chapter_results": results,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set([*warnings, *_leaf_target_warnings(content, report_type)])),
    }


def validate_report(workspace_id: str, revision_id: str) -> dict[str, Any]:
    record, native_alias = resolve_revision_record(workspace_id, revision_id)
    if record is None:
        return _failure("revision_not_found", "未找到研报修订")
    payload = record.get("payload") or {}
    upstream = payload.get("upstream") or {}
    if str(upstream.get("evidence_policy") or "") == SIM_A_FORMAL:
        from lvke_mcp.domains.reports.formal_lineage import (
            validate_report_revision_lineage,
        )

        try:
            validate_report_revision_lineage(workspace_id, record)
        except FormalLineageError as exc:
            # Formal lineage is retained as a report diagnostic.
            lineage_warning = exc.code
        else:
            lineage_warning = ""
    else:
        lineage_warning = ""
    native = str(payload.get("native_revision_id") or "")
    document = supplied_document_snapshot(workspace_id, payload.get("document_snapshot"))
    if document is None:
        document = doc.read_document(workspace_id, revision_id=native)
    if document is None:
        return _failure("document_snapshot_missing", "修订缺少不可变 document_snapshot")

    content = str(document.get("content") or "")
    report_type = str(document.get("report_type") or "generic_feasibility")
    expected_chapters = list(upstream.get("outline") or [])
    structure = doc.validate_report_structure(
        content,
        report_type,
        expected_chapters=expected_chapters,
    )
    run_id = str(upstream.get("run_id") or "")
    finance_binding = upstream.get("finance_binding") or {}
    acquisition_preview = False
    if str(finance_binding.get("kind") or "") == "asset_acquisition":
        from lvke_mcp.domains.asset_acquisition.backend import get_run

        acquisition_run = get_run(workspace_id, run_id)
        acquisition_preview = str(acquisition_run.get("delivery_mode") or "") in {
            "estimate_preview", "process_acceptance",
        }
    narrative = finance_gate.verify_narrative_numbers(
        workspace_id,
        content,
        run_id=run_id,
    )
    if acquisition_preview:
        binding = finance_gate.assert_acquisition_report_finance_binding(
            workspace_id,
            run_id=run_id,
            package_id=str(upstream.get("finance_tables_package_id") or ""),
        )
    else:
        binding = finance_gate.assert_publish_finance_binding(
            workspace_id,
            expected_run_id=run_id,
            strict=True,
        )
    scope_token = (
        report_artifacts._FINANCE_VALIDATION_SCOPE.set("technical")
        if acquisition_preview
        else None
    )
    try:
        readiness = report_artifacts.build_readiness(
            workspace_id,
            persist=False,
            revision_id=native,
            document_snapshot=document,
            expected_chapters=expected_chapters,
        )
    finally:
        if scope_token is not None:
            report_artifacts._FINANCE_VALIDATION_SCOPE.reset(scope_token)

    # Collect all report-level blockers/warnings before applying chapter/content gates.
    blockers: list[str] = [lineage_warning] if lineage_warning else []
    warnings: list[str] = []

    # ── 九章实质内容契约校验 ────────────────────────────────────────────
    chapter_content = _validate_chapter_content(
        content,
        report_type,
        expected_chapters,
    )
    blockers.extend(chapter_content.get("blockers") or [])
    warnings.extend(chapter_content.get("warnings") or [])

    bound_preparation_id = str(payload.get("report_preparation_id") or "")
    preparations = sorted(
        PREPARATION_STORE.list(workspace_id),
        key=lambda item: str(item.get("created_at") or ""),
        reverse=True,
    )
    latest_preparation_id = (
        str(preparations[0].get("object_id") or "") if preparations else ""
    )
    if latest_preparation_id and bound_preparation_id != latest_preparation_id:
        blockers.append("upstream_basis_superseded")
    if not structure.get("ok"):
        blockers.append("report_structure_invalid")
    if not narrative.get("ok"):
        blockers.append("finance_narrative_mismatch")
    blockers.extend(
        str(item.get("code") or "finance_binding_blocker")
        for item in (binding.get("blockers") or [])
    )
    blockers.extend(str(item) for item in (binding.get("quality_issues") or []))
    blockers.extend(
        str(item.get("code") or "readiness_blocker")
        for item in (readiness.get("blockers") or [])
    )
    blockers.extend(str(item) for item in (readiness.get("quality_issues") or []))
    readiness_warnings = [
        str(item.get("message") or item.get("code") or "")
        for item in (readiness.get("warnings") or [])
    ]
    warnings.extend(readiness_warnings)
    warnings.extend(
        str(item.get("message") or item.get("code") or "")
        for item in (binding.get("warnings") or [])
    )
    if native_alias:
        warnings.append("native_revision_id 输入已弃用；请改用 report_revision_id")
    for research_id in upstream.get("research_package_ids") or []:
        research = RESEARCH_STORE.get(workspace_id, research_id)
        research_status = str((research or {}).get("status") or "")
        if research_status == "partial":
            warnings.append(f"{research_id}: partial 研究限制必须保留")
        elif research_status not in {"done", "completed", "ok"}:
            blockers.append(
                f"research_package_not_usable:{research_id}:{research_status or 'unknown'}"
            )
    task_status = str(payload.get("task_status") or "")
    if task_status in {"failed", "cancelled"}:
        warnings.append("起草任务未完成；当前修订不能视为生成成功")

    quality_issues = sorted(set(blockers))
    financial_blockers = [
        item for item in quality_issues if is_finance_data_quality_issue(item)
    ]
    formal_ok = True
    quality_diagnostic_ids = [
        str(item.get("object_id") or "")
        for item in diagnostics_for_target(workspace_id, record["object_id"])
        if str(item.get("object_id") or "")
    ]
    warnings.extend(f"质量提示：{item}" for item in quality_issues)
    readiness = _synchronize_readiness(
        readiness,
        quality_issues,
        formal_release_eligible=formal_ok,
    )
    return {
        "success": True,
        "transport_success": True,
        "business_success": True,
        "completed": True,
        "outcome": "partial" if quality_issues else "ok",
        "status": "partial" if quality_issues else "ok",
        "valid": formal_ok,
        "quality_valid": not financial_blockers,
        "technical_ready": True,
        "formal_release_eligible": formal_ok,
        # 技术验收阶段：任何报告校验结果都是内部诊断草稿（§6）。
        "artifact_kind": "report_revision",
        "confirmation_status": "not_required",
        "uncertainty_summary": list(readiness.get("uncertainties") or []) if isinstance(readiness, dict) else [],
        "quality_diagnostic_ids": quality_diagnostic_ids,
        "report_revision_id": record["object_id"],
        "native_revision_id": native,
        "run_id": run_id,
        "finance_tables_package_id": str(upstream.get("finance_tables_package_id") or ""),
        "basis_hash": str(payload.get("basis_hash") or record.get("basis_hash") or ""),
        "structure": structure,
        "chapter_content": chapter_content,
        "finance_narrative": narrative,
        "finance_binding": binding,
        "readiness": readiness,
        "bound_preparation_id": bound_preparation_id,
        "latest_preparation_id": latest_preparation_id,
        "resource_uris": [record["resource_uri"]],
        "warnings": warnings,
        "blockers": [],
        "quality_issues": quality_issues,
        "next_actions": (
            ["财务模型数据质量存在不一致，修复后重新校验"]
            if financial_blockers
            else ["校验已完成；其他质量发现已作为诊断保留"]
        ),
    }


def _synchronize_readiness(
    readiness: dict[str, Any],
    validation_blockers: list[str],
    *,
    formal_release_eligible: bool = True,
) -> dict[str, Any]:
    """Merge report-level blockers into the returned readiness snapshot.

    This only changes the in-memory result. ``build_readiness`` is called with
    ``persist=False``, so validation does not rewrite the cached artifact.
    """

    snapshot = deepcopy(readiness) if isinstance(readiness, dict) else {}
    existing = list(snapshot.get("blockers") or [])
    known_codes: set[str] = set()
    normalized: list[Any] = []
    for item in existing:
        if isinstance(item, dict):
            code = str(item.get("code") or "readiness_blocker")
            normalized.append(item)
        else:
            code = str(item or "readiness_blocker")
            normalized.append({"code": code, "message": code})
        known_codes.add(code)

    for raw_code in validation_blockers:
        code = str(raw_code or "readiness_blocker")
        if code not in known_codes:
            normalized.append({
                "code": code,
                "message": f"报告校验阻断：{code}",
            })
            known_codes.add(code)

    codes = sorted(known_codes)
    snapshot["quality_issues"] = normalized
    snapshot["blocking_issues"] = []
    snapshot["blockers"] = []
    snapshot["technical_ready"] = True
    snapshot["formal_release_eligible"] = True
    snapshot["publishable"] = True
    snapshot["quality_valid"] = not any(is_finance_data_quality_issue(code) for code in codes)
    return snapshot


def _failure(code: str, message: str) -> dict[str, Any]:
    return {
        "success": False,
        "transport_success": True,
        "business_success": False,
        "completed": False,
        "outcome": "blocked",
        "status": "blocked",
        "code": code,
        "message": message,
        "resource_uris": [],
        "warnings": [],
        "blockers": [code],
        "next_actions": [],
    }
