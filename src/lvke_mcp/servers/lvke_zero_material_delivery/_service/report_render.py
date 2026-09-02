"""Config-driven full-report rendering for zero-material delivery.

正文由 ``config/report_profiles/<profile>.v1.json`` 的章节树 + 数据槽位决定，
本模块只做两件事：把槽位名解析成上游不可变对象里的真实值，按章节树输出 Markdown。

三条硬约束：

1. **不重算。** 财务数字只从同一个 FinanceRun 快照读，正文不自己算 IRR/NPV/税费。
2. **不编造。** 槽位解析不到值就写"未形成"并登记到 unresolved_slots，绝不用
   相邻字段凑一个看起来合理的数——那会让读者无法区分"算出来是这个数"和
   "这里其实没有数"。
3. **披露先行。** 预览横幅、受控假设说明、跳过字段清单来自配置的 disclosure，
   任何一条缺失都在 profile 加载期就阻断（见 ``report_profiles``）。
"""

from __future__ import annotations

from typing import Any

#: 配置未声明时的兜底文案。**只是**默认值，不是唯一来源：profile 的 ``prose``
#: 优先。保留兜底是为了让 v1 老配置（没有 prose 段）仍能渲染，而不是允许新配置
#: 省略这些文案。
_PROSE_DEFAULTS: dict[str, str] = {
    "missing_value": "未形成",
    "validation_condition": (
        "甲方原始材料缺失，结果按受控假设范围校验；"
        "补充材料后必须重算并校验 input hash、lineage 与数值一致性"
    ),
    "no_public_sources": "- 未形成可固化的公开来源快照；相关字段已回退为受控假设",
    "finance_tables_manifest": (
        "已从同一 FinanceRun 确定性生成14张交付主表、15 个 CSV（含数据血缘）与 XLSX；"
        "表格完整性由 manifest、文件 hash 与跨表一致性校验共同确定。"
    ),
    "no_blockers": "- 无技术链阻断项",
    "no_limitations": "- 无非阻断质量限制",
    "no_skipped_fields": "- 无用户跳过字段",
    "empty_section": "（本节数据槽位在当前输入快照下均未形成）",
    "assumption_boundary_title": "附：受控假设边界",
    "boolean_true": "是",
    "boolean_false": "否",
}

#: 假设表列定义的兜底。同上：配置的 ``tables.assumption_table.columns`` 优先。
_ASSUMPTION_COLUMNS_DEFAULT: tuple[dict[str, Any], ...] = (
    {"header": "参数", "field": "name", "align": "left"},
    {"header": "当前值", "field": "value", "align": "right"},
    {"header": "单位", "field": "unit", "align": "left"},
    {"header": "来源类型", "field": "source_type", "align": "left"},
    {"header": "置信度", "field": "confidence", "align": "right"},
    {"header": "已确认", "field": "confirmed", "align": "left", "boolean_labels": ["是", "否"]},
    {"header": "正式使用条件", "field": "validation_condition", "align": "left"},
)

#: 跳过决策变更段的固定说明。刻意不做成 profile 可配项：它是审计追溯依据，
#: 不该因为某份配置忘记声明就整段消失。
_SKIP_HISTORY_NOTICE = (
    "以下字段曾被用户显式跳过、其后已补充回答；保留本记录以便审计追溯确认过程的变更。"
)

#: 财务血缘片段的兜底。配置的 ``fragments.finance_lineage`` 优先。
_FINANCE_LINEAGE_DEFAULT: tuple[dict[str, str], ...] = (
    {"label": "FinanceRun", "field": "run_id"},
    {"label": "模型版本", "field": "model_version"},
    {"label": "模板版本", "field": "template_version"},
    {"label": "spec hash", "field": "spec_hash"},
    {"label": "input hash", "field": "input_hash"},
    {"label": "财务勾稽状态", "field": "consistency_ok"},
)


def _prose(profile: dict[str, Any], key: str) -> str:
    """Resolve one prose string: profile declaration first, built-in default next."""

    declared = dict(profile.get("prose") or {}).get(key)
    if isinstance(declared, str) and declared.strip():
        return declared
    return _PROSE_DEFAULTS.get(key, "")


def _format_number(value: Any, missing: str, suffix: str = "") -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return missing
    return f"{value:,.2f}{suffix}"


def _text(value: Any, missing: str = "未形成") -> str:
    if value is None:
        return missing
    if isinstance(value, bool):
        return (
            _PROSE_DEFAULTS["boolean_true"] if value else _PROSE_DEFAULTS["boolean_false"]
        )
    text = str(value).strip()
    return text or missing


def _bullet_list(values: Any) -> str:
    rows = [
        _text(item)
        for item in (values or [])
        if isinstance(item, (str, int, float)) and str(item).strip()
    ]
    if not rows:
        return ""
    return "\n".join(f"- {row}" for row in rows)


_ALIGN_MARKERS = {"left": "---", "right": "---:", "center": ":---:"}


def _assumption_table(
    fields: list[dict[str, Any]],
    columns: list[dict[str, Any]],
    missing: str,
) -> str:
    """Render the assumption register from the configured column definitions."""

    if not columns:
        return ""
    header = "| " + " | ".join(str(col.get("header") or "") for col in columns) + " |"
    divider = (
        "|"
        + "|".join(
            _ALIGN_MARKERS.get(str(col.get("align") or "left"), "---") for col in columns
        )
        + "|"
    )
    rows: list[str] = []
    for item in fields:
        if not isinstance(item, dict):
            continue
        cells: list[str] = []
        for col in columns:
            value = item.get(str(col.get("field") or ""))
            labels = col.get("boolean_labels")
            if isinstance(labels, list) and len(labels) == 2:
                cells.append(str(labels[0] if value else labels[1]))
            else:
                cells.append(_text(value, missing))
        rows.append("| " + " | ".join(cells) + " |")
    if not rows:
        return ""
    return "\n".join([header, divider, *rows])


def build_slot_values(
    *,
    intent: dict[str, Any],
    assumption_package: dict[str, Any],
    finance: dict[str, Any],
    blockers: list[str],
    quality_issues: list[str],
    public_research: dict[str, Any],
    skipped_fields: list[dict[str, Any]],
    report_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve every configurable slot from immutable upstream objects only.

    Args:
        report_profile: 所选报告配置。正文说明句、表头、血缘片段结构都从它的
            ``prose`` / ``tables`` / ``fragments`` 读取；缺省时用内置兜底，
            使 v1 老配置仍可渲染。

    跳过**历史**槽位刻意由独立的 :func:`build_skip_history_slots` 产出，不作为
    本函数的新参数：本函数签名被 ``tests/fixtures/baseline/refactor`` 的 API 护栏
    冻结，加参数会被判成公共 API 回退。调用方把两者的结果合并即可。
    """

    report = dict(report_profile or {})
    missing = _prose(report, "missing_value")
    industry = dict(intent.get("industry") or {})
    profile = dict(assumption_package.get("industry_profile") or {})
    fields = [item for item in assumption_package.get("fields") or [] if isinstance(item, dict)]
    values = {
        str(item.get("name")): item.get("value")
        for item in fields
        if str(item.get("name") or "")
    }
    sources = [
        item for item in public_research.get("source_summaries") or [] if isinstance(item, dict)
    ]
    source_lines = "\n".join(
        f"- {_text(item.get('title') or item.get('source_id'), missing)}: "
        f"{_text(item.get('url'), missing)}"
        for item in sources
    )
    lineage_fragments = [
        item
        for item in dict(report.get("fragments") or {}).get("finance_lineage")
        or _FINANCE_LINEAGE_DEFAULT
        if isinstance(item, dict)
    ]
    assumption_columns = [
        item
        for item in dict(report.get("tables") or {}).get("assumption_table", {}).get(
            "columns"
        )
        or _ASSUMPTION_COLUMNS_DEFAULT
        if isinstance(item, dict)
    ]

    slots: dict[str, Any] = {
        # ── 项目识别 ──
        "project_name": intent.get("project_name"),
        "region": intent.get("region"),
        "industry_label": industry.get("industry_label"),
        "project_nature": intent.get("project_nature"),
        "report_type": intent.get("report_type"),
        "material_state": intent.get("material_state"),
        "assurance_level": intent.get("assurance_level"),
        "validation_condition": _prose(report, "validation_condition"),
        # ── 行业语义 ──
        "industry_applicability": _bullet_list(profile.get("applicability")),
        "revenue_model": profile.get("revenue_model"),
        "revenue_drivers": _bullet_list(profile.get("revenue_drivers")),
        "investment_structure": _bullet_list(profile.get("investment_structure")),
        "cost_structure": _bullet_list(profile.get("cost_structure")),
        "labor_rule": profile.get("labor_rule"),
        "regional_adjustment": _bullet_list(profile.get("regional_adjustment")),
        "sensitivity_variables": _bullet_list(profile.get("sensitivity_variables")),
        # ── 公开检索 ──
        "public_research_status": public_research.get("status") or "not_run",
        "public_research_sources": source_lines or _prose(report, "no_public_sources"),
        # ── 受控假设 ──
        "assumption_table": _assumption_table(fields, assumption_columns, missing),
        "assumption_replacement_conditions": _bullet_list(
            sorted(
                {
                    str(item.get("validation_condition") or "")
                    for item in fields
                    if str(item.get("validation_condition") or "")
                }
            )
        ),
        "explicit_inputs": _bullet_list(
            sorted(str(item) for item in assumption_package.get("explicit_inputs_applied") or [])
        ),
        # ── 财务（只读同一 FinanceRun）──
        "finance_lineage": "\n".join(
            f"- {str(item.get('label') or '')}："
            f"`{_text(finance.get(str(item.get('field') or '')), missing)}`"
            for item in lineage_fragments
        ),
        "consistency_ok": finance.get("consistency_ok"),
        "total_investment_wan": _format_number(
            finance.get("total_investment_wan"), missing, " 万元"
        ),
        "annual_revenue_wan": _format_number(
            finance.get("annual_revenue_wan"), missing, " 万元"
        ),
        "project_irr": _format_number(finance.get("project_irr"), missing),
        "project_npv": _format_number(finance.get("project_npv"), missing, " 万元"),
        "capital_irr": _format_number(finance.get("capital_irr"), missing),
        "payback_years": _format_number(finance.get("payback_years"), missing, " 年"),
        "finance_tables_manifest": _prose(report, "finance_tables_manifest"),
        # ── 缺口与限制 ──
        "blockers": _bullet_list(blockers) or _prose(report, "no_blockers"),
        "release_limitations": _bullet_list(quality_issues)
        or _prose(report, "no_limitations"),
        "skipped_fields": _bullet_list(
            [
                f"{_text(item.get('field'), missing)}（{_text(item.get('reason'), missing)}）"
                for item in skipped_fields
                if isinstance(item, dict)
            ]
        )
        or _prose(report, "no_skipped_fields"),
        # 渲染器据此决定是否输出跳过披露段。用布尔量而不是匹配
        # "无用户跳过字段" 这类文案：文案一改，披露段就静默失效。
        "_has_skipped_fields": bool(skipped_fields),
        "evidence_policy": "controlled_assumption",
    }
    # 假设字段本身也可以直接被槽位引用（route_length_km、purchase_price_wan 等
    # 行业特有字段就是这样进配置的）。已显式定义的槽位优先，避免同名覆盖。
    for name, value in values.items():
        slots.setdefault(name, value)
    return slots


def build_skip_history_slots(
    skipped_fields: list[dict[str, Any]],
    skip_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Render the "was skipped, later answered" slots for the report body.

    与 ``skipped_fields`` 分开：后者是"现在还缺谁的确认"，本函数回答"这个字段的
    确认过程有没有变过"。回答一个此前跳过的字段会把它从 ``skipped_fields`` 移除，
    于是决策变更在正文里完全消失——而那正是审计要查的东西。

    只列**已被回答**的历史项：仍在跳过中的由 ``skipped_fields`` 槽位逐项披露，
    两处都列会让读者以为同一字段既跳过又已答。
    """

    still_skipped = {
        str(item.get("field") or "")
        for item in skipped_fields or []
        if isinstance(item, dict)
    }
    rows = _bullet_list(
        [
            f"{_text(item.get('field'))}"
            f"（原因：{_text(item.get('reason'))}；"
            f"当前：{_text(item.get('resolution'), '已回答')}）"
            for item in skip_history or []
            if isinstance(item, dict)
            and str(item.get("field") or "")
            and str(item.get("field") or "") not in still_skipped
        ]
    )
    return {"skip_history": rows, "_has_skip_history": bool(rows)}


def render_report_markdown(
    *,
    profile: dict[str, Any],
    selection: dict[str, Any],
    slots: dict[str, Any],
    promoted: bool = False,
) -> tuple[str, list[str]]:
    """Render the configured chapter tree, returning ``(markdown, unresolved)``.

    ``unresolved`` 列出配置声明但上游确实没有值的槽位。它不是异常：零材料链本来
    就允许缺项，但必须让读者和验收看到缺的是哪些，而不是让"未形成"混在正文里
    看不出来。
    """

    disclosure = dict(profile.get("disclosure") or {})
    missing = _prose(profile, "missing_value")
    banner = str(
        disclosure.get("promoted_banner" if promoted else "preview_banner") or ""
    )
    title = f"{_text(slots.get('project_name'), missing)}{str(profile.get('label') or '')}"
    lines: list[str] = [f"# {title}", ""]
    if banner:
        lines.extend([f"> **{banner}**", ""])
    lines.extend(
        [
            f"> 报告配置：`{_text(selection.get('template_set_id'), missing)}`"
            f" 版本 `{_text(selection.get('profile_version'), missing)}`"
            f" hash `{_text(selection.get('profile_content_hash'), missing)}`",
            "",
        ]
    )

    unresolved: list[str] = []
    for chapter_index, chapter in enumerate(profile.get("chapters") or [], start=1):
        if not isinstance(chapter, dict):
            continue
        lines.extend([f"## {chapter_index}、{_text(chapter.get('title'), missing)}", ""])
        subs = [item for item in chapter.get("subs") or [] if isinstance(item, dict)]
        if not subs:
            lines.append("")
            continue
        for sub_index, sub in enumerate(subs, start=1):
            lines.extend(
                [f"### {chapter_index}.{sub_index} {_text(sub.get('title'), missing)}", ""]
            )
            rendered_any = False
            for slot in sub.get("slots") or []:
                name = str(slot)
                # 下划线开头的键是渲染器内部控制量（如 _has_skipped_fields），
                # 不是可被配置引用的数据槽位。
                if name.startswith("_") or name not in slots:
                    unresolved.append(name)
                    continue
                value = slots[name]
                text = value if isinstance(value, str) else _text(value, missing)
                if not str(text).strip() or text == missing:
                    unresolved.append(name)
                    lines.extend([f"- {name}：{missing}", ""])
                    rendered_any = True
                    continue
                if "\n" in str(text):
                    lines.extend([str(text), ""])
                else:
                    lines.extend([f"- {name}：{text}", ""])
                rendered_any = True
            if not rendered_any:
                lines.extend([_prose(profile, "empty_section"), ""])

    notice = str(disclosure.get("assumption_notice") or "")
    if notice:
        lines.extend([f"## {_prose(profile, 'assumption_boundary_title')}", "", notice, ""])
    skipped_notice = str(disclosure.get("skipped_notice") or "")
    if skipped_notice and slots.get("_has_skipped_fields"):
        lines.extend([skipped_notice, "", str(slots.get("skipped_fields") or ""), ""])
    # 跳过决策变更记录独立成段，且**不**依赖 profile 声明 disclosure 文案：
    # 已补答的跳过项在配置里没有对应的 notice 键，若与 skipped_notice 共用条件，
    # 老配置下这段会静默消失，而它正是审计追溯决策变更的唯一正文落点。
    if slots.get("_has_skip_history"):
        lines.extend(
            [
                _SKIP_HISTORY_NOTICE,
                "",
                str(slots.get("skip_history") or ""),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n", sorted(set(unresolved))


__all__ = [
    "build_skip_history_slots",
    "build_slot_values",
    "render_report_markdown",
]
