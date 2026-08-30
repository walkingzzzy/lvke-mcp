"""Deterministic report-profile selection and chapter-tree loading.

报告内容此前由 ``artifact_delivery._report_markdown`` 里一段固定 f-string 决定：
换行业、换项目类型、换报告类型都得改 Python。这里把"报告长什么样"移到版本化配置，
Python 只负责**解析、选择、校验**，正文渲染在 ``report_render`` 里按配置走。

选择规则刻意与 ``planning_resolve_industry_skill``（``domains/project_planning/
_service/context.py:82``）同构：按适用条件筛出候选 → 取最高 priority → **必须唯一**。
同优先级多命中或零命中都阻断，不静默套用通用模板——那正是"配置化"最容易退化回
"其实还是一份固定模板"的地方。

每次运行都把 ``template_set_id`` / ``version`` / ``content_hash`` / 匹配理由固化进
DeliveryRun，历史运行因此可重放：配置升级只影响新运行，旧记录保留原 hash。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from lvke_mcp.runtime.storage import sha256_json
from lvke_mcp.runtime.package_config import (
    PackageConfigError,
    load_versioned_config,
)

MANIFEST_SCHEMA_VERSION = "lvke-report-profiles.v1"
PROFILE_SCHEMA_VERSION = "lvke-report-profile.v1"
_CONFIG_DIR = "report_profiles"
_MANIFEST_NAME = "manifest.v1.json"


class ReportProfileError(RuntimeError):
    """No unique active report profile could be resolved.

    Attributes:
        code: 机器可读阻断码，直接进 blockers。
        message: 面向人的说明。
        detail: 候选清单等诊断信息；粗粒度码会让排查方向系统性跑偏，
            因此这里始终带上"看到了哪些候选、按什么条件筛的"。
    """

    def __init__(self, code: str, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail if detail is not None else {}


def verified_snapshot(selection: dict[str, Any]) -> dict[str, Any] | None:
    """Return the frozen profile snapshot only when it recomputes to its hash.

    唯一的快照采信入口。三处消费方（正文渲染、大纲、缺口计算）都必须走它，
    否则任一处漏做复算就成了篡改入口。

    判据是**从内容重算**：只比对 ``snapshot["content_hash"]`` 与冻结字符串这两个
    字面量是不够的——把章节改成 TAMPERED 同时保留原 hash，两个字符串仍然相等。

    Returns:
        通过校验的快照副本；快照缺失、结构不完整或 hash 不符时返回 ``None``，
        由调用方决定是阻断还是回落到磁盘配置。
    """

    snapshot = selection.get("profile_snapshot")
    if not isinstance(snapshot, dict) or not snapshot.get("chapters"):
        return None
    expected = str(selection.get("profile_content_hash") or "")
    if not expected:
        return None
    body = {key: value for key, value in snapshot.items() if key != "content_hash"}
    if sha256_json(body) != expected:
        return None
    declared = str(snapshot.get("content_hash") or "")
    if declared and declared != expected:
        return None
    return dict(snapshot)


def load_manifest() -> dict[str, Any]:
    """Load the profile routing manifest with its canonical content hash."""

    try:
        return load_versioned_config(
            _CONFIG_DIR,
            _MANIFEST_NAME,
            expected_schema_version=MANIFEST_SCHEMA_VERSION,
        )
    except PackageConfigError as exc:
        raise ReportProfileError(
            f"report_profile_{exc.code}",
            f"报告配置清单不可用：{exc.message}",
        ) from None


def load_profile_document(document_name: str) -> dict[str, Any]:
    """Load one profile document and validate the structure Python relies on."""

    try:
        document = load_versioned_config(
            _CONFIG_DIR,
            document_name,
            expected_schema_version=PROFILE_SCHEMA_VERSION,
        )
    except PackageConfigError as exc:
        raise ReportProfileError(
            f"report_profile_{exc.code}",
            f"报告配置文档不可用：{exc.message}",
            {"document": document_name},
        ) from None
    chapters = document.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise ReportProfileError(
            "report_profile_chapters_missing",
            f"报告配置缺少章节树：{document_name}",
            {"document": document_name},
        )
    for index, chapter in enumerate(chapters):
        if not isinstance(chapter, dict) or not str(chapter.get("title") or "").strip():
            raise ReportProfileError(
                "report_profile_chapter_invalid",
                f"报告配置第 {index + 1} 章标题缺失：{document_name}",
                {"document": document_name, "chapter_index": index},
            )
        subs = chapter.get("subs")
        if subs is not None and not isinstance(subs, list):
            raise ReportProfileError(
                "report_profile_chapter_invalid",
                f"报告配置第 {index + 1} 章 subs 非法：{document_name}",
                {"document": document_name, "chapter_index": index},
            )
        for sub_index, sub in enumerate(subs or []):
            if not isinstance(sub, dict) or not str(sub.get("title") or "").strip():
                raise ReportProfileError(
                    "report_profile_chapter_invalid",
                    f"报告配置第 {index + 1} 章第 {sub_index + 1} 节标题缺失：{document_name}",
                    {"document": document_name, "chapter_index": index, "sub_index": sub_index},
                )
    if not isinstance(document.get("disclosure"), dict):
        raise ReportProfileError(
            "report_profile_disclosure_missing",
            f"报告配置缺少披露规则：{document_name}",
            {"document": document_name},
        )
    # required_fields 必须真的被某个章节槽位消费。否则该字段会被追问、被计入
    # 限制项，却在正文里无处出现——用户回答了也看不到影响，追问因此变成噪声。
    consumed = {
        str(slot)
        for chapter in document.get("chapters") or []
        if isinstance(chapter, dict)
        for sub in chapter.get("subs") or []
        if isinstance(sub, dict)
        for slot in sub.get("slots") or []
    }
    orphans = sorted(
        {
            str(field)
            for field in document.get("required_fields") or []
            if str(field) not in consumed
        }
    )
    if orphans:
        raise ReportProfileError(
            "report_profile_required_field_unused",
            f"报告配置的必填字段未被任何章节槽位引用：{document_name}",
            {"document": document_name, "unused_required_fields": orphans},
        )
    # 每个必填字段都必须能取到元数据（关键性/单位/范围）：内置表或配置的
    # ``field_specs`` 提供其一。缺失时静默降级成"非关键、无单位、无范围"，
    # 未回答也不触发正式候选门禁——方案要求的关键字段门禁会被整体绕过。
    from .questions import fields_without_metadata

    undeclared = fields_without_metadata(document)
    if undeclared:
        raise ReportProfileError(
            "report_profile_field_spec_missing",
            f"报告配置的必填字段缺少元数据声明：{document_name}",
            {
                "document": document_name,
                "fields_without_metadata": undeclared,
                "next_actions": [
                    "在配置的 field_specs 中声明 critical/unit/minimum/maximum",
                ],
            },
        )
    missing_groups = _missing_argument_groups(document)
    if missing_groups:
        raise ReportProfileError(
            "report_profile_argument_chain_incomplete",
            f"报告配置章节标题未覆盖可研论证链必需词组：{document_name}",
            {"document": document_name, "missing_groups": missing_groups},
        )
    return document


#: 审查侧 ``FEASIBILITY.STRUCTURE.COVERAGE`` 按**词组**扫描正文，命不中就判 P1。
#: 在配置加载期就核对同一批词组，让"章节语义对但用词不匹配"在写配置时暴露，
#: 而不是等跑到审查阶段才收到一条 P1——那时根因已经隔了三层。
#:
#: 这份表是 ``suite_review`` 里 ``required_groups`` 的镜像。它必须与那边保持一致；
#: 两处都改动过一次而不同步，就会出现"配置能加载但审查恒判不足"的静默偏差。
ARGUMENT_CHAIN_GROUPS: dict[str, tuple[str, ...]] = {
    "市场需求": ("市场", "需求"),
    "建设方案": ("建设方案", "技术方案", "工程方案"),
    "投资融资": ("投资", "融资", "资金筹措"),
    "财务评价": ("财务", "内部收益率", "现金流"),
    "风险结论": ("风险", "结论", "建议"),
}


def _missing_argument_groups(document: dict[str, Any]) -> list[str]:
    """Return argument-chain groups no chapter or section title covers."""

    titles: list[str] = []
    for chapter in document.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        titles.append(str(chapter.get("title") or ""))
        for sub in chapter.get("subs") or []:
            if isinstance(sub, dict):
                titles.append(str(sub.get("title") or ""))
    text = "\n".join(titles)
    return [
        name
        for name, terms in ARGUMENT_CHAIN_GROUPS.items()
        if not any(term in text for term in terms)
    ]


def _matches(applicability: dict[str, Any], selector: dict[str, str]) -> tuple[bool, list[str]]:
    """Return whether one profile applies, plus the conditions that matched.

    空列表表示"该维度不限制"，与 ``industry_skill_routes`` 的语义一致：不限制的
    维度不参与筛选，也不计入匹配理由（否则理由里全是"未限制"的噪声）。
    """

    reasons: list[str] = []
    for key, value in (
        ("industry_codes", selector.get("industry_code", "")),
        ("project_types", selector.get("project_type", "")),
        ("transaction_structures", selector.get("transaction_structure", "")),
        ("asset_types", selector.get("asset_type", "")),
        ("report_types", selector.get("report_type", "")),
    ):
        allowed = [str(item) for item in applicability.get(key) or []]
        if not allowed:
            continue
        if value not in allowed:
            return False, []
        reasons.append(f"{key}={value}")
    return True, reasons


def resolve_profile(
    *,
    industry_code: str,
    project_type: str,
    transaction_structure: str = "",
    asset_type: str = "",
    report_type: str = "",
    requested_profile_id: str = "",
    requested_template_set_id: str = "",
) -> dict[str, Any]:
    """Select exactly one active report profile, or fail closed.

    Args:
        industry_code: 零材料路由解析出的行业码。
        project_type: ``generic_feasibility`` 或 ``asset_acquisition``。
        transaction_structure: 交易结构；空值按"不参与筛选"处理。
        asset_type: 资产类型；空值按"不参与筛选"处理。
        report_type: 报告类型；空值按"不参与筛选"处理。
        requested_profile_id: 用户显式覆盖的 profile。命中即直接选中，
            但仍要求 ``status=active``——显式指定不等于可以用停用配置。
        requested_template_set_id: 同上，按 template_set_id 覆盖。

    Returns:
        含 ``profile``（完整配置文档）与 ``selection``（可固化的选择记录）。

    Raises:
        ReportProfileError: 显式指定不存在/已停用，或自动匹配零命中/同优先级冲突。
    """

    manifest = load_manifest()
    rows = [item for item in manifest.get("profiles") or [] if isinstance(item, dict)]
    if not rows:
        raise ReportProfileError(
            "report_profile_catalog_empty", "报告配置清单为空"
        )
    seen: set[str] = set()
    for row in rows:
        profile_id = str(row.get("profile_id") or "")
        if not profile_id or profile_id in seen:
            raise ReportProfileError(
                "report_profile_catalog_invalid",
                "报告配置清单存在空或重复 profile_id",
                {"profile_id": profile_id},
            )
        seen.add(profile_id)

    manifest_lineage = {
        "profile_manifest_version": str(manifest.get("schema_version") or ""),
        "profile_catalog_version": str(manifest.get("catalog_version") or ""),
        "profile_manifest_hash": str(manifest.get("content_hash") or ""),
    }
    selector = {
        "industry_code": str(industry_code or ""),
        "project_type": str(project_type or ""),
        "transaction_structure": str(transaction_structure or ""),
        "asset_type": str(asset_type or ""),
        "report_type": str(report_type or ""),
    }

    requested = str(requested_profile_id or "").strip()
    requested_set = str(requested_template_set_id or "").strip()
    if requested or requested_set:
        chosen = [
            row
            for row in rows
            if (requested and str(row.get("profile_id") or "") == requested)
            or (requested_set and str(row.get("template_set_id") or "") == requested_set)
        ]
        if not chosen:
            raise ReportProfileError(
                "report_profile_not_found",
                "显式指定的报告配置不存在",
                {
                    "requested_profile_id": requested,
                    "requested_template_set_id": requested_set,
                    "available_profile_ids": sorted(seen),
                },
            )
        if len(chosen) > 1:
            raise ReportProfileError(
                "report_profile_request_ambiguous",
                "profile_id 与 template_set_id 指向不同配置",
                {
                    "requested_profile_id": requested,
                    "requested_template_set_id": requested_set,
                    "matched_profile_ids": sorted(
                        str(row.get("profile_id") or "") for row in chosen
                    ),
                },
            )
        row = chosen[0]
        if str(row.get("status") or "") != "active":
            raise ReportProfileError(
                "report_profile_not_active",
                "显式指定的报告配置已停用，不能用于新运行",
                {
                    "profile_id": str(row.get("profile_id") or ""),
                    "status": str(row.get("status") or ""),
                },
            )
        document = load_profile_document(str(row.get("document") or ""))
        return _selection(row, document, manifest_lineage, selector, "explicit_request", [])

    candidates: list[tuple[int, dict[str, Any], list[str]]] = []
    for row in rows:
        if str(row.get("status") or "") != "active":
            continue
        applicable, reasons = _matches(
            dict(row.get("applicability") or {}),
            selector,
        )
        if not applicable:
            continue
        try:
            priority = int(row.get("priority") or 0)
        except (TypeError, ValueError):
            raise ReportProfileError(
                "report_profile_catalog_invalid",
                "报告配置 priority 非整数",
                {"profile_id": str(row.get("profile_id") or "")},
            ) from None
        candidates.append((priority, row, reasons))
    if not candidates:
        raise ReportProfileError(
            "report_profile_not_matched",
            "没有报告配置匹配当前项目条件；不套用通用模板",
            {
                "selector": selector,
                "available_profile_ids": sorted(seen),
                "next_actions": [
                    "显式传入 report_profile_id 或 template_set_id",
                    "或在 config/report_profiles/manifest.v1.json 增加匹配路由",
                ],
            },
        )
    top = max(item[0] for item in candidates)
    tied = [item for item in candidates if item[0] == top]
    if len(tied) != 1:
        raise ReportProfileError(
            "report_profile_ambiguous",
            "同优先级有多个报告配置命中，必须显式指定",
            {
                "selector": selector,
                "priority": top,
                "matched_profile_ids": sorted(
                    str(row.get("profile_id") or "") for _priority, row, _reasons in tied
                ),
            },
        )
    _priority, row, reasons = tied[0]
    document = load_profile_document(str(row.get("document") or ""))
    return _selection(row, document, manifest_lineage, selector, "applicability_match", reasons)


def _selection(
    row: dict[str, Any],
    document: dict[str, Any],
    manifest_lineage: dict[str, str],
    selector: dict[str, str],
    method: str,
    reasons: list[str],
) -> dict[str, Any]:
    profile_id = str(row.get("profile_id") or "")
    declared_set = str(row.get("template_set_id") or "")
    document_set = str(document.get("template_set_id") or "")
    if document_set != declared_set or str(document.get("profile_id") or "") != profile_id:
        raise ReportProfileError(
            "report_profile_identity_mismatch",
            "报告配置文档与清单声明的身份不一致",
            {
                "profile_id": profile_id,
                "manifest_template_set_id": declared_set,
                "document_template_set_id": document_set,
            },
        )
    return {
        "profile": deepcopy(document),
        "selection": {
            **manifest_lineage,
            "profile_id": profile_id,
            "template_set_id": declared_set,
            "profile_version": str(document.get("version") or ""),
            "profile_content_hash": str(document.get("content_hash") or ""),
            "profile_status": str(row.get("status") or ""),
            "report_type": str(document.get("report_type") or ""),
            "selection_method": method,
            "selection_reasons": list(reasons),
            "selector": dict(selector),
            # 完整配置快照随运行冻结。只存 ID+版本+hash 不够："配置文件被升级、
            # 移除，或部署根目录（LVKE_MCP_PACKAGE_CONFIG_DIR）变化"之后，重新
            # 加载会 not_found 或 hash 漂移，旧运行就再也按不了原配置重放——
            # 而"历史运行冻结、可重放"是这套配置化的硬要求。
            "profile_snapshot": deepcopy(document),
        },
    }


def chapter_titles(profile: dict[str, Any]) -> list[str]:
    """Flatten the configured chapter tree into report_prepare outline titles."""

    titles: list[str] = []
    for chapter in profile.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        titles.append(str(chapter.get("title") or ""))
    return [item for item in titles if item]


def outline_descriptors(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a two-level ``report_prepare`` outline from the chapter tree.

    ``parent_section_id`` 必须指向已出现过的 section（见
    ``domains/reports/read_model.normalize_outline``），所以父章节先于子节输出。
    这里不自造 section_id：留空让报告域按 ``(parent, order, title)`` 确定性派生，
    同一配置因此每次得到同一组 ID。
    """

    descriptors: list[dict[str, Any]] = []
    for chapter in profile.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        title = str(chapter.get("title") or "")
        if not title:
            continue
        descriptors.append({"title": title, "depth": 1})
    return descriptors


__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "PROFILE_SCHEMA_VERSION",
    "ReportProfileError",
    "chapter_titles",
    "load_manifest",
    "load_profile_document",
    "outline_descriptors",
    "resolve_profile",
]
