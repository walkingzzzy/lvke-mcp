"""章节结构、引用与正文内部一致性检查组。"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from lvke_mcp.servers.lvke_deliverable_review import rules

from .evidence import (
    _parse_datetime,
    _source_timestamp,
)

from .normalize import (
    _within_tolerance,
)

from .patterns import (
    _DEFAULT_SECTION_GROUPS,
    _FINANCIAL_METRICS,
)


def _headings(content: str) -> list[str]:
    output: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        match = re.match(r"^#{1,6}\s+(.+)$", line)
        if match:
            output.append(match.group(1).strip())
            continue
        if len(line) <= 80 and re.match(r"^(?:第[一二三四五六七八九十百0-9]+章|[一二三四五六七八九十]+[、.])", line):
            output.append(line)
    return output


def _normalize_heading(value: str) -> str:
    return re.sub(r"[\s#：:、，,。.\-—_（）()]", "", str(value or "")).lower()


def _required_section_findings(
    content: str,
    target_id: str,
    expected_sections: list[str],
    standard_basis: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    headings = [_normalize_heading(item) for item in _headings(content)]
    requirements: list[tuple[str, tuple[str, ...]]]
    if expected_sections:
        requirements = [(str(item), (str(item),)) for item in expected_sections if str(item).strip()]
    else:
        requirements = list(_DEFAULT_SECTION_GROUPS)
    findings: list[dict[str, Any]] = []
    for label, aliases in requirements:
        normalized_aliases = [_normalize_heading(alias) for alias in aliases]
        if any(alias and any(alias in heading or heading in alias for heading in headings) for alias in normalized_aliases):
            continue
        findings.append(rules.finding(
            "REPORT.SECTIONS.COMPLETE",
            "P1",
            f"研报缺少必需章节：{label}",
            category="report_structure",
            expected={"section": label, "accepted_aliases": list(aliases)},
            actual={"headings": headings},
            target_location={"target_id": target_id, "expected_section": label},
            standard_basis=standard_basis,
            review_area="report",
            remediation="补充必需章节及其证据、财务披露和结论后生成新修订",
        ))
    return findings


def _reference_findings(
    sources: list[dict[str, Any]],
    target_id: str,
    standard_basis: list[dict[str, Any]],
    review_as_of: str = "",
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    now = _parse_datetime(review_as_of) or datetime.now(timezone.utc)
    for source in sources:
        source_id = str(source.get("source_id") or "")
        content_hash = str(source.get("content_hash") or "")
        locators = source.get("locators") or []
        source_type = str(source.get("source_type") or "")
        url = str(source.get("url") or "")
        problems: list[str] = []
        if source_type in {"search_result", "search_summary", "web_search"}:
            problems.append("search_summary_not_original_source")
        if not re.fullmatch(r"(?:sha256:)?[0-9a-fA-F]{64}", content_hash):
            problems.append("content_hash_missing_or_invalid")
        if not locators:
            problems.append("readback_locator_missing")
        fetched_at = _source_timestamp(source)
        parsed = _parse_datetime(fetched_at)
        if url and not fetched_at:
            problems.append("fetched_at_missing")
        elif fetched_at and parsed is None:
            problems.append("fetched_at_invalid")
        elif parsed is not None and (now - parsed).days > 730:
            problems.append("source_snapshot_older_than_730_days")
        if not problems:
            continue
        findings.append(rules.finding(
            "REPORT.REFERENCES.FRESH",
            "P1",
            "引用来源无法满足原文回读、内容哈希或新鲜度要求",
            category="citation_quality",
            expected="官方或项目原始来源快照，含精确定位、SHA-256 和真实抓取时间",
            actual={
                "source_id": source_id,
                "source_type": source_type,
                "url": url,
                "content_hash": content_hash,
                "fetched_at": fetched_at,
                "problems": problems,
            },
            target_location={"target_id": target_id, "source_id": source_id},
            evidence=[{"evidence_pack_id": source.get("evidence_pack_id"), "source_id": source_id}],
            standard_basis=standard_basis,
            review_area="report",
            remediation="重新取得可回读原文快照，保存真实抓取时间、精确定位和内容哈希",
        ))
    return findings


def _internal_consistency_findings(
    claims: list[dict[str, Any]],
    target_id: str,
    standard_basis: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    # 分组键必须含语境维度：只按 (metric, period, unit) 分桶时，敏感性三情景的
    # NPV（−2996.48/−808.64/−300.64）与差异披露句里的「A 与 B 差 C」三个数会被
    # 判成同口径冲突，产出与数字对错无关的假 P0。period 只认时间期间
    # （_PERIOD_PATTERN），表达不了情景，故单列 variance_context。
    # 这不是豁免：情景/披露各自成桶，桶内仍有多值冲突照旧报出。
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for claim in claims:
        metric = str(claim.get("metric") or "")
        if not metric:
            continue
        period = str(claim.get("period") or "")
        if metric in {"room_count", "area"}:
            period = ""
        grouped.setdefault(
            (metric, period, str(claim.get("unit") or ""), str(claim.get("variance_context") or "")),
            [],
        ).append(claim)
    findings: list[dict[str, Any]] = []
    for (metric, period, unit, variance_context), rows in grouped.items():
        # 同一敏感性/披露行内部的多值是分析结构本身，不是口径冲突。跨行的同
        # 语境冲突仍然报出（rows 来自不同 line 时照常判定）。
        if variance_context and len({row["location"].get("line") for row in rows}) <= 1:
            continue
        values = sorted({round(float(row["value"]), 8) for row in rows})
        if len(values) <= 1 or all(
            _within_tolerance(value, values[0], metric=metric)
            for value in values[1:]
        ):
            continue
        severity = "P0" if metric in _FINANCIAL_METRICS else "P1"
        findings.append(rules.finding(
            "REPORT.INTERNAL.CONSISTENCY",
            severity,
            "正文、表格、摘要或结论中的同口径数字不一致",
            category="report_internal_consistency",
            expected="同一指标、期间和单位使用唯一口径，差异须明确解释范围与来源",
            actual={
                "metric": metric,
                "period": period,
                "unit": unit,
                "values": values,
                "claims": [{"claim_id": row["claim_id"], "value": row["value"], "location": row["location"]} for row in rows],
            },
            target_location={"target_id": target_id, "metric": metric, "period": period, "unit": unit},
            standard_basis=standard_basis,
            review_area="finance" if metric in _FINANCIAL_METRICS else "report",
            remediation="核对范围、时点、单位和主体；统一正文、表格、摘要、结论及附件口径",
        ))
    return findings
