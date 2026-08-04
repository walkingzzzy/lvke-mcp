"""Markdown report renderer for vendor-workbook reviews."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any


def _text(value: Any) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")


def _num(value: Any, digits: int = 4) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _finding_rows(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "| — | 未检出 | — | — | — |"
    rows = []
    for item in findings:
        vendor = item.get("vendor_value")
        if item.get("npv_residual_wan") is not None:
            vendor = f"{vendor}；NPV残差 {_num(item.get('npv_residual_wan'))} 万元"
        rows.append(
            "| {code} | {locator} | {vendor} | {detail} | {suggestion} |".format(
                code=_text(item.get("code")),
                locator=_text(item.get("locator")),
                vendor=_text(vendor),
                detail=_text(item.get("detail")),
                suggestion=_text(item.get("engine_suggestion")),
            )
        )
    return "\n".join(rows)


def _comparison_rows(comparison: dict[str, Any]) -> str:
    items = [*(comparison.get("matched") or []), *(comparison.get("mismatched") or [])]
    if not items:
        return "| — | — | — | — | — |"
    rows = []
    for item in items:
        verdict = {
            "converged": "收敛",
            "explain": "需说明",
            "red_flag": "红旗",
        }.get(str(item.get("verdict")), str(item.get("verdict") or "—"))
        deviation = item.get("deviation_pct")
        rows.append(
            f"| {_text(item.get('locator'))} | {_num(item.get('ref_value'))} | "
            f"{_num(item.get('engine_value'))} | "
            f"{_num(deviation, 2) + '%' if deviation is not None else '—'} | {_text(verdict)} |"
        )
    return "\n".join(rows)


def _verdict_rows(verdict: list[dict[str, Any]]) -> str:
    if not verdict:
        return "| — | — | 否 | 未发现裁决问题 |"
    rows = []
    for issue in verdict:
        rows.append(
            f"| {_text(issue.get('rule'))} | {_text(issue.get('severity'))} | "
            f"{'是' if issue.get('blocking') else '否'} | {_text(issue.get('detail'))} |"
        )
    return "\n".join(rows)


def render_review_md(
    reference_pack: dict[str, Any],
    cleanup_findings: list[dict[str, Any]],
    run: dict[str, Any],
    comparison: dict[str, Any],
    verdict: list[dict[str, Any]],
    *,
    reference_replay: dict[str, Any] | None = None,
    amount_bridge: dict[str, Any] | None = None,
) -> str:
    """Render a self-contained review report; this function has no IO side effects."""
    source = reference_pack.get("source") or {}
    mappings = reference_pack.get("sheet_map") or {}
    mapped_count = sum(1 for item in mappings.values() if item.get("mapped"))
    formula_count = sum(
        len(items or {}) for items in (reference_pack.get("formulas") or {}).values()
    )
    indicators = run.get("indicators") or {}
    annual = run.get("annual") or {}
    blocking = [item for item in verdict if item.get("blocking")]
    rating = "E（引擎结果存在阻断项）" if blocking else "C（甲方参考证据等级）"
    warnings = reference_pack.get("warnings") or []
    warning_text = "；".join(map(str, warnings)) if warnings else "无"
    run_status = (
        f"已完成，我方数字真源 run_id=`{run.get('run_id')}`"
        if run.get("available") else f"未完成：{run.get('reason') or '输入不足'}"
    )
    replay = reference_replay or {}
    replay_after_tax = (replay.get("tracks") or {}).get("project_after_tax") or {}
    bridge = amount_bridge or {}

    return f"""# 甲方计算表导入与复核报告

> 生成日期：{date.today().isoformat()}  
> 甲方工作簿：`{_text(source.get('workbook_name'))}`  
> 证据等级：**C**（只作公式与历史值参考）  
> 数字红线：**对外数字只取我方引擎 run；本报告不回写、不篡改甲方原表。**

## 一、导入摘要

| 项目 | 结果 |
|---|---|
| 源路径 | `{_text(source.get('path'))}` |
| SHA-256 | `{_text(source.get('workbook_sha256'))}` |
| 工作表映射 | {mapped_count}/{len(mappings)} 张已映射 |
| 公式抽取 | {formula_count} 个；状态 `{_text(reference_pack.get('formula_status'))}` |
| 降级/人工核对 | {_text(warning_text)} |
| 我方重算 | {_text(run_status)} |
| 综合评级 | **{rating}** |

## 二、三个严重问题清洗检测

> 清洗采用“只标记、不改值”。甲方原值、问题说明与我方建议并列留痕。

| 编号 | 定位 | 甲方原值/残差 | 问题说明 | 我方重算/建议 |
|---|---|---:|---|---|
{_finding_rows(cleanup_findings)}

## 三、我方模型重算结果（对外为准）

| 指标 | 我方引擎值 |
|---|---:|
| 项目总投资（万元） | {_num((run.get('investment') or {}).get('total'), 2)} |
| 项目 IRR（%） | {_num(indicators.get('project_irr_pct'))} |
| 资本金 IRR（%） | {_num(annual.get('capital_irr_pct'))} |
| NPV（万元） | {_num(indicators.get('npv_wan'), 2)} |
| 计算状态 | {_text(run.get('calculation_status') or ('computed' if run.get('available') else 'unavailable'))} |

## 四、双轨对照

> “甲方值”仅为历史参考；“我方值”是报告与13张附表的唯一数字来源。  
> 分级：偏差≤15%收敛，15%～30%需说明，>30%为红旗。

| 定位 | 甲方参考值 | 我方引擎值 | 相对偏差 | 判定 |
|---|---:|---:|---:|---|
{_comparison_rows(comparison)}

对照汇总：匹配 {comparison.get('summary', {}).get('matched', 0)} 项，差异 {comparison.get('summary', {}).get('mismatched', 0)} 项，红旗 {comparison.get('summary', {}).get('red_flags', 0)} 项。

### 4.1 甲方参考轨只读复现

| 项目 | 甲方表值 | 独立复算值 | 差异（百分点） | 0.01pp容差 |
|---|---:|---:|---:|---|
| 税后项目IRR | {_num(replay_after_tax.get('stated_irr_pct'))}% | {_num(replay_after_tax.get('solved_irr_pct'))}% | {_num(replay_after_tax.get('irr_delta_percentage_points'), 6)} | {'通过' if replay_after_tax.get('irr_within_tolerance') else '未通过'} |

参考轨只读复现状态：**{'通过' if replay.get('passed') else '未通过'}**。原表错误与硬编码试算保留并披露，不复制到修正轨。

### 4.2 修正轨金额桥接

逐期桥接 {len(bridge.get('rows') or [])} 项，待业务裁决 {bridge.get('blocking_count', 0)} 项。待裁决项保持 blocking，不得批准或发布。

## 五、审查裁决与发布预警

| 规则 | 严重度 | 阻断发布 | 说明 |
|---|---|---|---|
{_verdict_rows(verdict)}

## 六、结论与落地建议

1. 甲方工作簿已作为只读公式参考档留存，证据等级固定为 C，不进入对外数字源。
2. F1/F2/F3 均按检测结果留痕；系统没有静默修改甲方单元格。
3. 对外报告、13张附表和正文数字必须绑定本报告列示的我方 run。
4. 负 IRR、ICR/DSCR<1 及其他 blocking 问题必须关闭后才允许批准和发布。
"""


def default_report_path(workspace_id: str, reference_pack: dict[str, Any]) -> Path:
    from lvke_mcp.runtime.workspace import workspace_root

    stem = Path(str((reference_pack.get("source") or {}).get("workbook_name") or "vendor")).stem
    safe_stem = re.sub(r"[^0-9A-Za-z一-鿿_.-]+", "_", stem)[:80] or "vendor"
    root = workspace_root(workspace_id) / "vendor_reviews"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{safe_stem}_复核报告.md"


def write_review_md(path: str | Path, content: str) -> str:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return str(target)
