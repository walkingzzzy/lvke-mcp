"""开发期可读财务表包：结构化 JSON + 可直接阅读的报告（不依赖 Excel）。

设计决策（2026-07-12）:
- 开发/验收阶段 **不以 xlsx 为门禁**；
- 真源优先 ``annual`` / investment / funding 结构化数据；
- structured 统一由 ``table_render``（catalog 风格投影）生成；
- 同时产出:
  1. ``tables_structured.json`` — 机器可读行列
  2. ``READABLE.md`` — 人眼可扫的表 + 证据头
  3. ``evidence.json`` — run_id / hash / 勾稽摘要 / 来源链
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from lvke_mcp.domains.finance.run_service import (
    DELIVERY_TABLE_KEYS,
    DELIVERY_TABLE_META,
    ENGINE_DELIVERY_COUNT,
    MODEL_VERSION,
    TEMPLATE_VERSION,
    compute_table_bundle_hash,
    delivery_count_semantics,
    delivery_table_contract_hash,
)
from lvke_mcp.domains.finance import table_render

_DELIVERY_META = list(DELIVERY_TABLE_META)


def _fmt(v: Any) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, (int, float)):
        try:
            x = float(v)
            if abs(x - round(x)) < 1e-9:
                return f"{int(round(x)):,}"
            return f"{x:,.2f}"
        except (TypeError, ValueError):
            return str(v)
    return str(v)


def build_tables_structured(fin: dict[str, Any]) -> dict[str, Any]:
    """从 finance result 构建结构化 13 表（开发可读真源）。

    统一走 ``table_render.build_all_structured``（catalog 风格投影）。
    对外 API / e2e 只暴露交付表（张数见 DELIVERY_TABLE_KEYS）；``_meta`` 写入
    evidence / artifacts 侧。
    """
    pack = table_render.build_all_structured(fin)
    # 剥离内部 meta，避免被计成第 14 张「空表」
    pack.pop("_meta", None)
    return pack


def build_tables_structured_with_meta(fin: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """返回 (tables_only, meta)。"""
    pack = table_render.build_all_structured(fin)
    meta = dict(pack.pop("_meta", None) or {})
    return pack, meta


def build_evidence(
    fin: dict[str, Any],
    *,
    workspace_id: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """构造可直接阅读/落盘的证据块。"""
    checks: list[dict[str, Any]] = []
    try:
        from lvke_mcp.domains.finance import finance_model as fm

        checks = fm.check_consistency(fin) or []
    except Exception:  # noqa: BLE001
        checks = list(fin.get("checks") or [])

    blocking_fail = [
        c for c in checks
        if isinstance(c, dict) and not c.get("ok") and c.get("blocking", True)
    ]
    soft_fail = [
        c for c in checks
        if isinstance(c, dict) and not c.get("ok") and not c.get("blocking", True)
    ]

    ind = fin.get("indicators") or {}
    inv = fin.get("investment") or {}
    fund = fin.get("funding") or {}
    raw = fin.get("raw") or {}
    fin_inputs = fin.get("finance_inputs") or {}
    input_rev = fin.get("input_revision") or {}
    auto_injected = (
        fin.get("_auto_injected_cost_items")
        or input_rev.get("_auto_injected_cost_items")
        or fin_inputs.get("_auto_injected_cost_items")
        or raw.get("_auto_injected_cost_items")
        or []
    )
    surtax_on_vat = bool(raw.get("surtax_on_vat"))
    surtax_mode = "vat_base" if surtax_on_vat else "revenue_base"
    if fin.get("spec"):
        spec_source = "spec"
    elif fin.get("force_flat"):
        spec_source = "flat"
    elif fin.get("spec_hash") and "null" not in str(fin.get("spec_hash")):
        spec_source = "spec_hash_only"
    else:
        spec_source = "flat"

    quality = (table_render.build_all_structured(fin).get("_meta") or {})
    notes = [
        "正式交付必须包含 13-sheet xlsx、跨表公式、有效内容校验与发布门禁；JSON/READABLE 仅作审计副本。",
        "grade=summary 表示估算摘要级，禁止对外宣称已生成专业财务附表。",
        "13 表数字仅由 finance_model 确定性计算生成；LLM 永不填格子。",
        f"本 run 附加税路径 surtax_mode={surtax_mode}"
        f"（surtax_on_vat={surtax_on_vat}）。",
        f"spec_source={spec_source}；force_flat={bool(fin.get('force_flat'))}。",
    ]
    if auto_injected:
        inj_txt = "；".join(
            f"{x.get('key')}={x.get('amount_wan')}（{x.get('source')}）"
            for x in auto_injected if isinstance(x, dict)
        )
        notes.append(f"自动注入成本项：{inj_txt}")

    # 从 extra 合并 prepare 侧 AI 边界（若 package 传入）
    extra = dict(extra or {})
    prepare_used_llm = extra.get("prepare_used_llm")
    if prepare_used_llm is None:
        prepare_used_llm = False
    if bool(fin.get("force_flat")):
        notes.append(
            "本 package 为 force_flat：prepare 未用 LLM 定规范；"
            "13 表与 LLM 无调用关系。"
        )
    elif prepare_used_llm:
        notes.append(
            "LLM 仅参与 prepare_spec（FinanceSpec 口径），未参与 13 表算术与填格；"
            f"prepare_spec_source_hint={extra.get('prepare_spec_source_hint')}。"
        )
    else:
        notes.append(
            "prepare 未采用 LLM 有效 spec（或回退默认）；"
            "13 表仍由确定性引擎生成。"
        )

    ev = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace_id": workspace_id,
        "run_id": fin.get("run_id"),
        "grade": quality.get("grade") or "summary",
        "delivery_format": "xlsx+json+readable_md",
        **delivery_count_semantics(),
        "table_contract_hash": delivery_table_contract_hash(),
        "validation_complete": bool(quality.get("validation_complete")),
        "effective_table_count": quality.get("effective_table_count"),
        "required_table_count": quality.get("required_table_count"),
        "ineffective_tables": quality.get("ineffective_tables") or [],
        "model_version": fin.get("model_version") or MODEL_VERSION,
        "template_version": fin.get("template_version") or TEMPLATE_VERSION,
        "model_manifest": fin.get("model_manifest") or {},
        "manifest_hash": fin.get("manifest_hash"),
        "manifest_errors": fin.get("manifest_errors") or [],
        "valuation_date": fin.get("valuation_date"),
        "policy_version": (fin.get("model_manifest") or {}).get("policy_version"),
        "industry_profile_version": (fin.get("model_manifest") or {}).get("industry_profile_version"),
        "gate_version": (fin.get("model_manifest") or {}).get("gate_version"),
        "input_hash": fin.get("input_hash"),
        "spec_hash": fin.get("spec_hash"),
        "table_bundle_hash": fin.get("table_bundle_hash")
        or compute_table_bundle_hash(fin.get("tables") or {}),
        "source_chain": [
            "lvke_mcp.domains.finance.run_service.run_workspace_finance_model",
            "lvke_mcp.domains.finance.finance_model.compute_financials",
            "lvke_mcp.domains.finance.table_render.build_all_structured",
            "lvke_mcp.domains.finance.table_pack.build_tables_structured",
        ],
        "llm_role": extra.get("prepare_llm_role") or (
            "none" if fin.get("force_flat") else "prepare_spec_only_if_used"
        ),
        "llm_fills_cells": False,
        "llm_participated_in_tables": False,
        "tables_generated_by": "lvke_mcp.domains.finance.finance_model.compute_financials",
        "prepare_used_llm": bool(prepare_used_llm),
        "spec_source": spec_source,
        "surtax_mode": surtax_mode,
        "surtax_on_vat": surtax_on_vat,
        "auto_injected_cost_items": auto_injected,
        "cost_path": raw.get("cost_path"),
        "cost_path_detail": raw.get("cost_path_detail"),
        "cost_policy": raw.get("cost_policy") or fin_inputs.get("cost_policy") or "user_items",
        "missing_fields_by_table": quality.get("missing_fields_by_table") or (
            (extra or {}).get("missing_fields_by_table") if isinstance(extra, dict) else None
        ),
        "reference_schema": "docs/reference_table_schema.json",
        "indicators": {
            "project_irr_pct": ind.get("project_irr_pct"),
            "npv_wan": ind.get("npv_wan"),
            "static_payback_years": ind.get("static_payback_years"),
            "revenue": ind.get("revenue"),
            "net_profit": ind.get("net_profit"),
            "capital_irr_pct": (fin.get("annual") or {}).get("capital_irr_pct"),
            "tax_surcharge": ind.get("tax_surcharge"),
        },
        "investment": {
            "total": inv.get("total"),
            "construction": inv.get("construction"),
            "interest": inv.get("interest"),
            "working_capital": inv.get("working_capital"),
        },
        "funding": {
            "capital": fund.get("capital"),
            "loan": fund.get("loan"),
            "subsidy": fund.get("subsidy"),
        },
        "checks_total": len(checks),
        "checks_blocking_failed": len(blocking_fail),
        "checks_soft_failed": len(soft_fail),
        "consistency_ok": len(blocking_fail) == 0,
        "blocking_failures": blocking_fail[:20],
        "soft_failures": soft_fail[:20],
        "delivery_keys": list(DELIVERY_TABLE_KEYS),
        "notes": notes,
    }
    if extra:
        # 避免 notes 已消费字段重复噪音；仍保留 extra 审计
        ev["extra"] = extra
    return ev


def structured_to_readable_md(
    pack: dict[str, Any],
    evidence: dict[str, Any],
) -> str:
    """把结构化表包渲染为可直接阅读的 Markdown。"""
    lines: list[str] = []
    lines.append("# 财务 13 表交付审计副本")
    lines.append("")
    lines.append("> **正式交付判定以 xlsx + 跨表公式 + 有效内容门禁为准**；本 MD 仅用于复核与追溯。")
    lines.append(">")
    lines.append(
        f"> run_id=`{evidence.get('run_id')}` · grade=`{evidence.get('grade')}` · "
        f"model=`{evidence.get('model_version')}` · "
        f"consistency_ok=`{evidence.get('consistency_ok')}`"
    )
    lines.append(">")
    lines.append(
        f"> input_hash=`{evidence.get('input_hash')}` · spec_hash=`{evidence.get('spec_hash')}`"
    )
    lines.append("")
    lines.append("## 证据摘要")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("| --- | --- |")
    for k in (
        "run_id", "workspace_id", "grade", "delivery_format",
        "model_version", "checks_total", "checks_blocking_failed", "validation_complete",
        "effective_table_count", "required_table_count",
        "llm_fills_cells", "llm_participated_in_tables",
        "prepare_used_llm", "tables_generated_by", "llm_role",
    ):
        lines.append(f"| {k} | {evidence.get(k)} |")
    ind = evidence.get("indicators") or {}
    lines.append(f"| project_irr_pct | {ind.get('project_irr_pct')} |")
    lines.append(f"| capital_irr_pct | {ind.get('capital_irr_pct')} |")
    lines.append(f"| npv_wan | {ind.get('npv_wan')} |")
    lines.append(f"| static_payback_years | {ind.get('static_payback_years')} |")
    lines.append(f"| tax_surcharge | {ind.get('tax_surcharge')} |")
    lines.append(f"| cost_path | {evidence.get('cost_path')} |")
    lines.append(f"| cost_policy | {evidence.get('cost_policy')} |")
    lines.append("")
    lines.append("**source_chain:**")
    for s in evidence.get("source_chain") or []:
        lines.append(f"- `{s}`")
    lines.append("")
    if evidence.get("blocking_failures"):
        lines.append("### 阻断勾稽失败")
        for c in evidence["blocking_failures"]:
            lines.append(f"- ❌ {c.get('rule')}: {c.get('detail')}")
        lines.append("")
    if evidence.get("soft_failures"):
        lines.append("### 非阻断警告")
        for c in evidence["soft_failures"]:
            lines.append(f"- ⚠️ {c.get('rule')}: {c.get('detail')}")
        lines.append("")

    # 张数从交付清单派生，不写死：附表增减时标题会自动跟上（曾停在「13 张」）。
    lines.append(f"## {len(_DELIVERY_META)} 张交付表（catalog 投影 / 结构化渲染）")
    lines.append("")

    for key, delivery_no, title in _DELIVERY_META:
        t = pack.get(key) or {}
        if not isinstance(t, dict):
            continue
        lines.append(f"### {delivery_no} {title}")
        lines.append("")
        lines.append(
            f"- table_id: `{key}` · rows: {t.get('row_count', 0)} · "
            f"source: `{t.get('source', '')}` · grade: `{t.get('grade', '')}`"
        )
        notes = t.get("notes") or []
        for n in notes:
            lines.append(f"- note: {n}")
        cols = t.get("column_labels") or [
            (c.get("label") if isinstance(c, dict) else str(c))
            for c in (t.get("columns") or [])
        ]
        rows = t.get("rows") or []
        if not cols or not rows:
            lines.append("")
            lines.append("_（无行数据）_")
            lines.append("")
            continue
        lines.append("")
        lines.append("| " + " | ".join(str(c) for c in cols) + " |")
        lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
        col_keys = [
            (c.get("key") if isinstance(c, dict) else "")
            for c in (t.get("columns") or [])
        ]
        for row in rows:
            cells = []
            for i, v in enumerate(row):
                col_key = col_keys[i] if i < len(col_keys) else ""
                if col_key in ("rate", "rate_pct", "loan_rate", "salvage_rate"):
                    try:
                        from lvke_mcp.domains.finance.table_render import _fmt_rate_pct
                        cells.append(_fmt_rate_pct(v))
                    except Exception:  # noqa: BLE001
                        cells.append(_fmt(v))
                else:
                    cells.append(_fmt(v))
            while len(cells) < len(cols):
                cells.append("")
            lines.append("| " + " | ".join(cells[: len(cols)]) + " |")
        footer = t.get("footer") or ""
        if footer:
            lines.append("")
            lines.append(footer)
        lines.append("")

    if evidence.get("missing_fields_by_table"):
        lines.append("## 参考表缺口（missing_fields）")
        lines.append("")
        lines.append(f"- reference_schema: `{evidence.get('reference_schema')}`")
        lines.append(f"- template_ready: `{evidence.get('template_ready')}`")
        for tk, mf in (evidence.get("missing_fields_by_table") or {}).items():
            lines.append(f"- **{tk}**: {';'.join(mf)}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        f"*generated_at={evidence.get('generated_at')} · "
        f"delivery_format={evidence.get('delivery_format')}*"
    )
    return "\n".join(lines)


def write_readable_artifacts(
    fin: dict[str, Any],
    out_dir: Path | str,
    *,
    workspace_id: str = "",
    extra_evidence: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """写入开发可读产物目录，返回路径摘要。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    pack, meta = build_tables_structured_with_meta(fin)
    extra_ev = dict(extra_evidence or {})
    if meta.get("missing_fields_by_table") is not None:
        extra_ev.setdefault("missing_fields_by_table", meta.get("missing_fields_by_table"))
        extra_ev.setdefault("template_ready", meta.get("template_ready"))
    evidence = build_evidence(fin, workspace_id=workspace_id, extra=extra_ev)
    if meta.get("missing_fields_by_table") is not None:
        evidence["missing_fields_by_table"] = meta.get("missing_fields_by_table")
        evidence["template_ready"] = meta.get("template_ready")
        evidence["reference_schema"] = meta.get("reference_schema") or "docs/reference_table_schema.json"
        evidence["validation_complete"] = bool(meta.get("validation_complete"))
        evidence["effective_table_count"] = meta.get("effective_table_count")
        evidence["required_table_count"] = meta.get("required_table_count")
        evidence["ineffective_tables"] = meta.get("ineffective_tables") or []
    evidence["table_row_counts"] = {
        k: (pack.get(k) or {}).get("row_count", 0) for k in DELIVERY_TABLE_KEYS
    }
    evidence["delivery_present"] = [
        k for k in DELIVERY_TABLE_KEYS if (pack.get(k) or {}).get("row_count", 0) > 0
    ]
    evidence["delivery_effective"] = [
        k for k in DELIVERY_TABLE_KEYS if (pack.get(k) or {}).get("effective") is True
    ]
    evidence["delivery_count"] = len(evidence["delivery_present"])
    evidence["effective_delivery_count"] = len(evidence["delivery_effective"])

    # 同源 MD 表（catalog 投影）也写入，便于对照旧 tables
    md_tables = table_render.render_all_markdown_from_structured(pack)
    evidence["render_backend"] = "table_render.catalog_projection"

    readable = structured_to_readable_md(pack, evidence)

    paths = {
        "evidence": out / "evidence.json",
        "tables_structured": out / "tables_structured.json",
        "readable": out / "READABLE.md",
        "result_snapshot": out / "result_snapshot.json",
        "tables_md_json": out / "tables_md.json",
    }
    paths["evidence"].write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    paths["tables_structured"].write_text(
        json.dumps(pack, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    paths["readable"].write_text(readable, encoding="utf-8")
    paths["tables_md_json"].write_text(
        json.dumps(md_tables, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths["result_snapshot"].write_text(
        json.dumps(dict(fin), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    return {
        "ok": True,
        "out_dir": str(out),
        "run_id": evidence.get("run_id"),
        "delivery_count": evidence.get("delivery_count"),
        "effective_delivery_count": evidence.get("effective_delivery_count"),
        "validation_complete": evidence.get("validation_complete"),
        "consistency_ok": evidence.get("consistency_ok"),
        "grade": evidence.get("grade"),
        "delivery_format": evidence.get("delivery_format"),
        "files": {k: str(v) for k, v in paths.items()},
    }


def default_artifact_dir(
    workspace_id: str,
    run_id: str,
) -> Path:
    """默认落盘目录：``lvke产出/{id}/finance-tables/finance_artifacts/{run_id}/``

    与十三表 CSV/XLSX 导出、研报交付工件同归 ``lvke产出/``：这些是需要随仓库
    留存与复核的产出，``data_root`` 只放运行时状态。注意
    ``reports/artifacts.py`` 会从本目录抓取 XLSX 附到研报包，其
    ``_safe_support_source`` 以交付物根做包含性校验，两侧必须同步迁移。
    """
    from lvke_mcp.runtime.workspace import deliverable_dir

    root = deliverable_dir(str(workspace_id), "finance-tables", "finance_artifacts")
    return root / str(run_id or "unknown")

# 门面模块的公开面。显式声明而不是靠"碰巧 import 了"——API 快照门禁
# (tests/integration/test_refactor_guardrails.py) 要求这些 re-export 保持
# 可达,而 ruff F401 会把它们判成未使用。写成 __all__ 让两个门禁同时成立,
# 也让"哪些名字是刻意对外的"可读。
__all__ = [
    "Any",
    "DELIVERY_TABLE_KEYS",
    "DELIVERY_TABLE_META",
    "ENGINE_DELIVERY_COUNT",
    "MODEL_VERSION",
    "Optional",
    "Path",
    "TEMPLATE_VERSION",
    "_DELIVERY_META",
    "_fmt",
    "build_evidence",
    "build_tables_structured",
    "build_tables_structured_with_meta",
    "compute_table_bundle_hash",
    "datetime",
    "default_artifact_dir",
    "delivery_count_semantics",
    "delivery_table_contract_hash",
    "hashlib",
    "json",
    "structured_to_readable_md",
    "table_render",
    "timezone",
    "write_readable_artifacts",
]
