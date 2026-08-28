"""Deterministic preview report and immutable delivery manifests."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from lvke_mcp.runtime.storage import require_safe_id
from lvke_mcp.runtime.workspace import deliverable_dir


def _artifact_root(workspace_id: str) -> Path:
    """零材料交付研报（MD/DOCX）落盘根，统一到仓库 ``lvke产出/``。"""
    return deliverable_dir(
        require_safe_id(workspace_id, "workspace_id"),
        "zero-material-delivery",
        "artifacts",
    )


def _format_number(value: Any, suffix: str = "") -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "未形成"
    return f"{value:,.2f}{suffix}"


def _first_value(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def _finance_summary(
    workspace_id: str,
    finance_run_id: str,
) -> dict[str, Any]:
    from lvke_mcp.domains.finance.run_service import get_workspace_finance_run

    run = get_workspace_finance_run(
        workspace_id,
        run_id=finance_run_id,
        view="full",
    )
    indicators = dict(run.get("indicators") or {})
    investment = dict(run.get("investment") or {})
    total_investment_wan = _first_value(indicators, "total_investment_wan")
    if total_investment_wan is None:
        total_investment_wan = _first_value(investment, "total")
    return {
        "run_id": finance_run_id,
        "available": bool(run.get("available")),
        "assurance_level": str(run.get("assurance_level") or "estimate_preview"),
        "model_version": str(run.get("model_version") or ""),
        "template_version": str(run.get("template_version") or ""),
        "spec_hash": str(run.get("spec_hash") or ""),
        "input_hash": str(run.get("input_hash") or ""),
        "consistency_ok": bool(run.get("consistency_ok")),
        "total_investment_wan": total_investment_wan,
        "annual_revenue_wan": _first_value(
            indicators, "annual_revenue_wan", "revenue"
        ),
        "project_irr": _first_value(indicators, "project_irr", "project_irr_pct"),
        "project_npv": _first_value(indicators, "project_npv", "npv_wan"),
        "capital_irr": _first_value(indicators, "capital_irr", "capital_irr_pct"),
        "payback_years": _first_value(
            indicators,
            "payback_years",
            "dynamic_payback_years",
            "static_payback_years",
        ),
    }


def _report_markdown(
    intent: dict[str, Any],
    assumption_package: dict[str, Any],
    finance: dict[str, Any],
    blockers: list[str],
) -> str:
    industry = dict(intent.get("industry") or {})
    assumptions = [dict(item) for item in assumption_package.get("fields") or []]
    rows = "\n".join(
        f"| {item.get('name')} | {item.get('value')} {item.get('unit')} | "
        f"{item.get('source_type')} | {item.get('confidence')} | {item.get('validation_condition')} |"
        for item in assumptions
    )
    gaps = "\n".join(f"- `{item}`" for item in blockers) or "- 无技术链 blocker"
    return f"""# {intent.get('project_name')}技术预估报告

> **技术预估版。** 本报告在甲方零材料条件下生成，所有结论均受当前输入快照和受控假设约束。

## 一、项目识别

- 地区：{intent.get('region') or '待确认'}
- 行业：{industry.get('industry_label') or '待确认'}
- 项目性质：{intent.get('project_nature') or '待确认'}
- 报告类型：{intent.get('report_type') or '可行性研究报告'}
- 交付等级：`estimate_preview`

## 二、依据与边界

本次没有甲方合同、测绘、报价、权属、设计或批复材料。公开研究会话只支持行业、地区、政策与可比项目；项目面积、设备、客流或产量、造价、价格、融资和工期仍属于 `controlled_assumption`。

## 三、受控假设登记

| 参数 | 当前值 | 来源类型 | 置信度 | 正式使用条件 |
|---|---:|---|---:|---|
{rows}

## 四、财务技术预估

- FinanceRun：`{finance.get('run_id')}`
- 模型版本：`{finance.get('model_version')}`
- 模板版本：`{finance.get('template_version')}`
- 总投资：{_format_number(finance.get('total_investment_wan'), ' 万元')}
- 达产年营业收入：{_format_number(finance.get('annual_revenue_wan'), ' 万元')}
- 项目投资财务内部收益率：{_format_number(finance.get('project_irr'), '')}
- 项目财务净现值：{_format_number(finance.get('project_npv'), ' 万元')}
- 资本金内部收益率：{_format_number(finance.get('capital_irr'), '')}
- 静态/动态回收期指标：{_format_number(finance.get('payback_years'), ' 年')}
- 财务勾稽状态：`{finance.get('consistency_ok')}`

以上数字只从同一不可变 FinanceRun 读取；正文不单独重算 IRR、NPV、税费或十三表。

## 五、十三表交付

已从同一 FinanceRun 确定性生成十三张主表、13 个 CSV 与 XLSX。表格的完整性状态由 manifest、文件 hash 和跨表一致性校验共同确定。

## 六、缺口与下一步

{gaps}

用户确认参数后，系统创建新的 AssumptionPackage、FinanceSpec、FinanceRun、十三表和报告版本，不覆盖本版本。

## 七、验证边界

- 输入范围：甲方原始材料缺失，当前结果使用受控假设。
- 后续替换材料时必须重新计算并校验 input hash、lineage 与数值一致性。
"""


def _docx_bytes(markdown: str, title: str) -> bytes:
    from lvke_mcp.domains.reports._doc_service.docx import append_markdown_pipe_table

    document = Document()
    heading = document.add_heading(title, level=0)
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    document.add_paragraph("技术预估版，非正式发布").alignment = WD_ALIGN_PARAGRAPH.CENTER
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        text = lines[index].strip()
        if not text or text.startswith("# "):
            index += 1
            continue
        if text.startswith("## "):
            document.add_heading(text[3:], level=1)
        elif text.startswith("> "):
            paragraph = document.add_paragraph(text[2:])
            paragraph.style = document.styles["Intense Quote"]
        elif text.startswith("- "):
            document.add_paragraph(text[2:], style="List Bullet")
        elif text.startswith("|"):
            table_rows: list[list[str]] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                row = lines[index].strip()
                if not row.replace("|", "").replace("-", "").replace(":", "").strip():
                    index += 1
                    continue
                table_rows.append([cell.strip() for cell in row.strip("|").split("|")])
                index += 1
            append_markdown_pipe_table(document, table_rows)
            continue
        else:
            document.add_paragraph(text)
        index += 1
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def build_delivery_artifacts(
    workspace_id: str,
    intent: dict[str, Any],
    assumption_package: dict[str, Any],
    source_run: dict[str, Any],
    domain: dict[str, Any],
    *,
    stores: dict[str, Any],
    service_version: str,
    delivery_run_id: str = "",
) -> dict[str, Any]:
    refs = {
        str(key): str(value)
        for key, value in dict(domain.get("object_refs") or {}).items()
        if value
    }
    finance_run_id = refs.get("finance_run_id", "")
    if not finance_run_id:
        return {
            "resource_uris": [],
            "object_refs": {},
            "blockers": ["technical_report_finance_run_required"],
        }
    finance = _finance_summary(
        workspace_id,
        finance_run_id,
    )
    blockers = list(domain.get("blockers") or [])
    markdown = _report_markdown(intent, assumption_package, finance, blockers)
    report_payload = {
        "object_type": "TechnicalReport",
        "title": f"{intent.get('project_name')}技术预估报告",
        "format_version": "zero-material-technical-report.v1",
        "assurance_level": "estimate_preview",
        "content_markdown": markdown,
        "finance_summary": finance,
        "intent_id": intent.get("delivery_intent_id"),
        "assumption_package_id": assumption_package.get("assumption_package_id"),
        "finance_run_id": finance_run_id,
        "finance_tables_package_id": refs.get("finance_tables_package_id", ""),
        "research_task_id": refs.get("research_task_id", ""),
        "validation_complete": False,
        "input_evidence_complete": False,
    }
    report_record = stores["report"].put(
        workspace_id,
        report_payload,
        producer="lvke-zero-material-delivery.technical_report",
        status="partial",
        source_ids=[value for value in refs.values() if value],
        basis={
            "intent_id": intent.get("delivery_intent_id"),
            "assumption_package_id": assumption_package.get("assumption_package_id"),
            "finance_run_id": finance_run_id,
        },
    )
    report_base_uri = report_record["resource_uri"]
    root = _artifact_root(workspace_id) / report_record["object_id"]
    root.mkdir(parents=True, exist_ok=True)
    markdown_bytes = markdown.encode("utf-8")
    docx_bytes = _docx_bytes(markdown, str(report_payload["title"]))
    files = {
        "report.md": markdown_bytes,
        "report.docx": docx_bytes,
    }
    file_manifest: list[dict[str, Any]] = []
    for name, content in files.items():
        path = root / name
        path.write_bytes(content)
        file_manifest.append(
            {
                "name": name,
                "content_hash": "sha256:" + hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "resource_uri": f"{report_base_uri}/files/{name}",
            }
        )

    assumption_record = stores["assumption_register"].put(
        workspace_id,
        {
            "object_type": "AssumptionRegister",
            "assumption_package_id": assumption_package.get("assumption_package_id"),
            "revision": assumption_package.get("revision"),
            "fields": assumption_package.get("fields") or [],
            "validation_complete": False,
        },
        producer="lvke-zero-material-delivery.assumption_register",
        status="ok",
        source_ids=[str(assumption_package.get("assumption_package_id") or "")],
    )
    gap_record = stores["gap_register"].put(
        workspace_id,
        {
            "object_type": "GapRegister",
            "blockers": blockers,
            "missing_client_materials": ["合同", "测绘", "报价", "权属", "设计", "批复"],
            "research_status": str((domain.get("research") or {}).get("status") or ""),
            "validation_complete": False,
            "input_evidence_complete": False,
        },
        producer="lvke-zero-material-delivery.gap_register",
        status="partial",
        source_ids=[str(source_run.get("delivery_run_id") or "")],
    )
    evidence_record = stores["evidence_manifest"].put(
        workspace_id,
        {
            "object_type": "EvidenceManifest",
            "research_task_id": refs.get("research_task_id", ""),
            "evidence_pack_ids": [],
            "research_package_ids": [],
            "source_policy": "public_sources_only",
            "evidence_status": "pending",
            "controlled_assumptions_are_evidence": False,
            "formal_evidence_ready": False,
        },
        producer="lvke-zero-material-delivery.evidence_manifest",
        status="partial",
        source_ids=[refs.get("research_task_id", "")],
    )
    manifest_payload = {
        "object_type": "RunManifest",
        "schema_version": "zero-material-run-manifest.v1",
        "workspace_id": workspace_id,
        "delivery_run_id": delivery_run_id or source_run.get("delivery_run_id"),
        "intent_id": intent.get("delivery_intent_id"),
        "assumption_package_id": assumption_package.get("assumption_package_id"),
        "object_refs": {
            **refs,
            "technical_report_id": report_record["object_id"],
            "assumption_register_id": assumption_record["object_id"],
            "gap_register_id": gap_record["object_id"],
            "evidence_manifest_id": evidence_record["object_id"],
        },
        "finance_lineage": finance,
        "files": file_manifest,
        "artifact_uris": list(domain.get("resource_uris") or []),
        "service_version": service_version,
        "status": "estimate_preview",
        "blockers": blockers,
        "validation_complete": False,
        "input_evidence_complete": False,
    }
    manifest_record = stores["manifest"].put(
        workspace_id,
        manifest_payload,
        producer="lvke-zero-material-delivery.run_manifest",
        status="partial",
        source_ids=[value for value in manifest_payload["object_refs"].values() if value],
        basis=manifest_payload,
    )
    resource_uris = [
        report_record["resource_uri"],
        *[item["resource_uri"] for item in file_manifest],
        assumption_record["resource_uri"],
        gap_record["resource_uri"],
        evidence_record["resource_uri"],
        manifest_record["resource_uri"],
    ]
    return {
        "resource_uris": resource_uris,
        "object_refs": {
            "technical_report_id": report_record["object_id"],
            "assumption_register_id": assumption_record["object_id"],
            "gap_register_id": gap_record["object_id"],
            "evidence_manifest_id": evidence_record["object_id"],
            "run_manifest_id": manifest_record["object_id"],
        },
        "manifest_uri": manifest_record["resource_uri"],
        "file_manifest": file_manifest,
        "blockers": [],
    }


def resolve_report_file(
    uri: str,
    *,
    report_store: Any,
) -> tuple[bytes, str] | None:
    marker = "/files/"
    if marker not in uri:
        return None
    base, name = uri.rsplit(marker, 1)
    if name not in {"report.md", "report.docx"}:
        return None
    record = report_store.resolve_uri(base)
    if record is None:
        return None
    path = (
        _artifact_root(str(record["workspace_id"]))
        / str(record["object_id"])
        / name
    )
    if not path.is_file():
        return None
    mime = (
        "text/markdown; charset=utf-8"
        if name.endswith(".md")
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return path.read_bytes(), mime


__all__ = ["build_delivery_artifacts", "resolve_report_file"]
