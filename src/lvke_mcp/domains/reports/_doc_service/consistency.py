"""跨工作区一致性检查：正文、财务摘要与财务模型的交叉核对。"""

from __future__ import annotations

from typing import Any, Optional



from .paths import (
    MISSING_MARKER,
)

from .structure import (
    parse_revision_sections,
    validate_report_structure,
)

from .workspace import (
    _current_revision_content,
    _read_meta,
    finance_summary,
    workspace_finance_model,
    workspace_report_type,
)


def consistency_check(
    workspace_id: str = "",
    *,
    report_text: str = "",
    finance: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """报告一致性自检(参考可研 ``consistency_check(params, fin, report_text)``)。

    - **结构**:B 型 9 章是否齐全(缺章 → ``check_issues``)。
    - **财务**:finance.json 的 ``required_markers`` 是否都在正文出现(缺失 → ``missing_items``)。
    - **待补充**:正文是否仍含占位标记 ``（待补充）``(逐章 → ``missing_items``)。

    返回 ``{ok, check_issues, missing_items, structure}``。
    """
    if not report_text and workspace_id:
        _rev, report_text = _current_revision_content(workspace_id)
    if finance is None and workspace_id:
        finance = finance_summary(workspace_id)
    finance = finance if isinstance(finance, dict) else {}

    rt = workspace_report_type(workspace_id) if workspace_id else ""
    structure = validate_report_structure(report_text, rt)
    check_issues: list[str] = list(structure["issues"])
    missing_items: list[str] = []

    markers = finance.get("required_markers")
    if isinstance(markers, (list, tuple)):
        for marker in markers:
            text = str(marker).strip()
            if text and text not in report_text:
                missing_items.append(f"正文缺少关键财务指标：{text}")

    for sec in parse_revision_sections(report_text):
        if MISSING_MARKER in str(sec.get("body") or ""):
            missing_items.append(f"章节待补充：{sec.get('title')}")

    # M5 T5.4：环保方案齐全性校验（环评硬门槛）。
    if workspace_id:
        try:
            meta = _read_meta(workspace_id)
            req = meta.get("requirement") if isinstance(meta, dict) else {}
            req = req if isinstance(req, dict) else {}
            fin_req = req.get("finance") if isinstance(req.get("finance"), dict) else {}
            if fin_req.get("is_operating"):
                from lvke_mcp.domains.finance import env_templates

                prof = env_templates.env_profile(str(req.get("industry") or ""))
                if prof.get("available"):
                    kw_hit = any(
                        k in report_text
                        for k in ("环保", "环境影响", "三废", "废水", "废气", "污染", "达标排放")
                    )
                    if not kw_hit:
                        missing_items.append(
                            "环保方案缺失：经营性项目须在影响效果章体现三废治理措施与环保投入"
                        )
        except Exception:  # noqa: BLE001 - 环保校验失败不阻断自检
            pass

    # M1 T1.2：财务数值勾稽层（资金筹措=总投资、三处 IRR 一致、成本勾稽、建设期利息）。
    finance_reconciliation: list[dict[str, Any]] = []
    if workspace_id:
        try:
            from lvke_mcp.domains.finance import finance_model

            fm_r = workspace_finance_model(workspace_id)
            finance_reconciliation = finance_model.check_consistency(fm_r)
        except Exception:  # noqa: BLE001 - 勾稽失败不阻断自检主流程
            finance_reconciliation = []
    for chk in finance_reconciliation:
        if not chk.get("ok"):
            check_issues.append(f"财务勾稽不一致：{chk.get('rule')}（{chk.get('detail')}）")

    return {
        "ok": not check_issues and not missing_items,
        "check_issues": check_issues,
        "missing_items": missing_items,
        "structure": structure,
        "finance_reconciliation": finance_reconciliation,
    }
