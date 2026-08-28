"""Generate deterministic Sim-A template packs and confirm formal promotion."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from lvke_mcp.runtime.evidence_qualification import SIM_A_FORMAL
from lvke_mcp.runtime.storage import require_safe_id, sha256_json

from .assumptions import _field_values
from .explicit_inputs import SOURCE_SENTENCE
from .base import (
    ASSUMPTION_STORE,
    INTENT_STORE,
    PROMOTION_STORE,
    RUN_STORE,
    SERVICE_NAME,
    TEMPLATE_PACK_STORE,
    _blocked,
    _envelope,
    _idempotent_mutation,
    _view,
)
from .finance_align import _scenario_inputs

SIM_A_ORIGIN = "sim_a_template"
TEMPLATE_ROOT = Path(__file__).resolve().parents[3] / "config" / "sim_a_templates"
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

FORBIDDEN_FABRICATION_FIELDS: tuple[dict[str, str], ...] = (
    {
        "name": "official_seal",
        "target_pointer": "/input_revision/official_seal",
        "replacement_condition": "须以盖章原件或行政机关核发材料替换；禁止编造公章或签章",
    },
    {
        "name": "official_document_no",
        "target_pointer": "/input_revision/official_document_no",
        "replacement_condition": "须以批复、备案或登记原文号替换；禁止编造正式文号",
    },
    {
        "name": "approval_no",
        "target_pointer": "/input_revision/approval_no",
        "replacement_condition": "须以主管部门批复号替换；禁止编造批复号",
    },
    {
        "name": "bank_statement_ref",
        "target_pointer": "/input_revision/bank_statement_ref",
        "replacement_condition": "须以银行对账单或资金流水原件替换；禁止编造流水",
    },
    {
        "name": "inspection_conclusion",
        "target_pointer": "/input_revision/inspection_conclusion",
        "replacement_condition": "须以检测/监测机构结论替换；禁止编造检测结论",
    },
    {
        "name": "audit_conclusion",
        "target_pointer": "/input_revision/audit_conclusion",
        "replacement_condition": "须以审计报告结论替换；禁止编造审计结论",
    },
)

ASSUMPTION_POINTERS: dict[str, str] = {
    "total_investment_wan": "/spec/total_investment_wan",
    "annual_revenue_wan": "/spec/annual_revenue_wan",
    "build_period_months": "/spec/build_period_months",
    "loan_ratio": "/spec/loan_ratio",
    "loan_rate": "/spec/loan_rate",
    "operating_period_years": "/spec/operating_period_years",
    "capacity": "/spec/capacity",
    "unit_price": "/spec/unit_price",
    "annual_visitors": "/spec/annual_visitors",
    "spend_per_visitor": "/spec/spend_per_visitor",
}


def _render_template(text: str, values: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        return str(values.get(match.group(1), ""))

    return _PLACEHOLDER_RE.sub(replace, text)


def _template_path(industry_code: str, requirement_id: str) -> Path:
    industry = TEMPLATE_ROOT / industry_code / f"{requirement_id}.md.j2"
    if industry.is_file():
        return industry
    generic = TEMPLATE_ROOT / "generic" / f"{requirement_id}.md.j2"
    if generic.is_file():
        return generic
    return TEMPLATE_ROOT / "generic" / "_default.md.j2"


def _pending_assumption_fields(package: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in package.get("fields") or []
        if isinstance(item, dict)
        and not item.get("confirmed")
        and item.get("source_ref") != SOURCE_SENTENCE
    ]


def _assumption_confirmed(package: dict[str, Any]) -> bool:
    fields = [item for item in package.get("fields") or [] if isinstance(item, dict)]
    if not fields:
        return False
    if str(package.get("confirmation_status") or "") == "confirmed":
        return not _pending_assumption_fields(package)
    return not _pending_assumption_fields(package)


def _assumption_table(fields: list[dict[str, Any]]) -> str:
    rows = ["| 字段 | 取值 | 单位 | 来源 | 已确认 |", "| --- | --- | --- | --- | --- |"]
    for item in fields:
        if not isinstance(item, dict):
            continue
        rows.append(
            "| {name} | {value} | {unit} | {source} | {confirmed} |".format(
                name=str(item.get("name") or ""),
                value=str(item.get("value") if item.get("value") is not None else ""),
                unit=str(item.get("unit") or ""),
                source=str(item.get("source_ref") or ""),
                confirmed="是" if item.get("confirmed") else "否",
            )
        )
    return "\n".join(rows)


def _flatten_pointers(value: Any, prefix: str) -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}/{key}"
            if isinstance(item, (dict, list)):
                rows.extend(_flatten_pointers(item, path))
            elif isinstance(item, (str, int, float, bool)) or item is None:
                rows.append((path, item))
    elif isinstance(value, list):
        for index, item in enumerate(value[:20]):
            path = f"{prefix}/{index}"
            if isinstance(item, (dict, list)):
                rows.extend(_flatten_pointers(item, path))
            elif isinstance(item, (str, int, float, bool)) or item is None:
                rows.append((path, item))
    return rows


def _mapping_rows(
    package: dict[str, Any],
    spec: dict[str, Any],
    finance_inputs: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    fields = [item for item in package.get("fields") or [] if isinstance(item, dict)]
    for item in fields:
        name = str(item.get("name") or "")
        if not name:
            continue
        pointer = ASSUMPTION_POINTERS.get(name, f"/input_revision/{name}")
        value = item.get("value")
        filled = value not in (None, "")
        rows.append(
            {
                "name": name,
                "target_pointer": pointer,
                "value": value if filled else None,
                "unit": str(item.get("unit") or ""),
                "basis": "controlled_assumption",
                "replacement_condition": str(
                    item.get("validation_condition")
                    or "须以合同、测绘、报价或权属等原始材料替换"
                ),
                "status": "mapped" if filled else "interface_only",
            }
        )
        seen.add(pointer)
    for prefix, source in (("/spec", spec), ("/input_revision", finance_inputs)):
        for pointer, value in _flatten_pointers(source, prefix):
            if pointer in seen:
                continue
            if any(token in pointer for token in ("assumptions", "field_sources", "source_hint")):
                continue
            filled = value not in (None, "")
            rows.append(
                {
                    "name": pointer.rsplit("/", 1)[-1],
                    "target_pointer": pointer,
                    "value": value if filled else None,
                    "unit": "",
                    "basis": "controlled_assumption",
                    "replacement_condition": "须以原始材料或已认证证据替换后才能作为项目事实",
                    "status": "mapped" if filled else "interface_only",
                }
            )
            seen.add(pointer)
    for item in FORBIDDEN_FABRICATION_FIELDS:
        if item["target_pointer"] in seen:
            continue
        rows.append(
            {
                "name": item["name"],
                "target_pointer": item["target_pointer"],
                "value": None,
                "unit": "",
                "basis": "controlled_assumption",
                "replacement_condition": item["replacement_condition"],
                "status": "interface_only",
            }
        )
    return rows


def _resolve_applicable_requirements(
    workspace_id: str,
    *,
    project_type: str,
    industry_code: str,
    idempotency_key: str,
) -> tuple[list[dict[str, Any]], str]:
    from lvke_mcp.servers.lvke_deliverable_review._service.standards import (
        list_standard_requirements,
        resolve_standards,
    )

    resolved = resolve_standards(
        {
            "workspace_id": workspace_id,
            "project_context": {
                "project_type": project_type,
                "industry_code": industry_code,
                "target_type": "report_revision",
            },
            "facilities": [],
            "idempotency_key": f"zmd-std-{idempotency_key}",
        }
    )
    if not resolved.get("success"):
        return [], str(resolved.get("code") or "standards_resolve_failed")
    applicability_id = str(resolved.get("standard_applicability_id") or "")
    listed = list_standard_requirements(
        {
            "workspace_id": workspace_id,
            "standard_applicability_id": applicability_id,
        }
    )
    if not listed.get("success"):
        return [], str(listed.get("code") or "standards_list_failed")
    requirements = [
        dict(item)
        for item in listed.get("requirements") or []
        if isinstance(item, dict) and str(item.get("requirement_id") or "")
    ]
    return requirements, ""


def _scale_summary(values: dict[str, Any]) -> str:
    parts: list[str] = []
    if values.get("total_investment_wan") is not None:
        parts.append(f"总投资 {values['total_investment_wan']} 万元")
    if values.get("annual_revenue_wan") is not None:
        parts.append(f"年收入 {values['annual_revenue_wan']} 万元")
    if values.get("build_period_months") is not None:
        parts.append(f"建设期 {values['build_period_months']} 个月")
    return "；".join(parts) if parts else "规模参数见已确认假设表"


def generate_template_pack(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    run_id = require_safe_id(args.get("delivery_run_id"), "delivery_run_id")
    idempotency_key = str(args.get("idempotency_key") or "")
    project_type = str(args.get("project_type") or "generic_feasibility").strip() or "generic_feasibility"
    request_payload = {
        "delivery_run_id": run_id,
        "project_type": project_type,
    }

    def mutation() -> dict[str, Any]:
        run_record = RUN_STORE.get(workspace_id, run_id)
        if run_record is None:
            return _blocked("delivery_run_not_found", "未找到指定 DeliveryRun")
        run = _view(run_record, "delivery_run_id")
        package_id = str(run.get("assumption_package_id") or "")
        if not package_id:
            return _blocked("assumption_package_missing", "DeliveryRun 未绑定 AssumptionPackage")
        package_record = ASSUMPTION_STORE.get(workspace_id, package_id)
        if package_record is None:
            return _blocked("assumption_package_not_found", "未找到指定 AssumptionPackage")
        package = _view(package_record, "assumption_package_id")
        if not _assumption_confirmed(package):
            return _blocked(
                "assumptions_not_confirmed",
                "须先确认 AssumptionPackage 再生成拟定模板包",
                next_actions=["调用 delivery_list_assumptions 与 delivery_confirm_assumptions"],
            )
        intent_id = str(run.get("intent_id") or "")
        intent_record = INTENT_STORE.get(workspace_id, intent_id) if intent_id else None
        intent = _view(intent_record, "delivery_intent_id") if intent_record else {}
        industry = dict(intent.get("industry") or package.get("industry_profile") or {})
        industry_code = str(
            industry.get("industry_code") or package.get("industry_code") or ""
        )
        requirements, standards_error = _resolve_applicable_requirements(
            workspace_id,
            project_type=project_type,
            industry_code=industry_code,
            idempotency_key=idempotency_key,
        )
        if standards_error:
            return _blocked(standards_error, "未能解析本项目适用标准需求")
        if not requirements:
            return _blocked("applicable_requirements_empty", "适用标准需求为空，无法生成拟定模板包")
        spec, finance_inputs, _context = _scenario_inputs(package)
        from lvke_mcp.domains.finance import model_application as finance

        validated = finance.validate_spec({"spec": spec, "for_formal": False})
        if validated.get("structural_valid") is False or (
            validated.get("quality_valid") is False and validated.get("structural_valid") is not True
        ):
            return _blocked(
                "candidate_spec_invalid",
                "候选 FinanceSpec 未通过结构校验，拒绝生成拟定模板包",
                quality_issues=list(validated.get("quality_issues") or []),
                next_actions=["修正 AssumptionPackage 后重新确认并再生成模板包"],
            )
        mapping_rows = _mapping_rows(package, spec, finance_inputs)
        fields = [item for item in package.get("fields") or [] if isinstance(item, dict)]
        values = {
            "project_name": str(
                intent.get("project_name")
                or _project_name(str(intent.get("sentence") or ""), str(intent.get("project_name") or ""))
                or "未命名项目"
            ),
            "region": str(intent.get("region") or ""),
            "industry_code": industry_code,
            "industry_label": str(
                industry.get("industry_label") or package.get("industry_label") or industry_code
            ),
            "scale_summary": _scale_summary(_field_values(package)),
            "assumption_table": _assumption_table(fields),
        }
        files: list[dict[str, Any]] = []
        for requirement in requirements:
            requirement_id = str(requirement.get("requirement_id") or "")
            render_values = {
                **values,
                "requirement_id": requirement_id,
                "requirement_title": str(requirement.get("title") or requirement_id),
                "requirement_description": str(requirement.get("description") or ""),
            }
            template_path = _template_path(industry_code, requirement_id)
            body = _render_template(
                template_path.read_text(encoding="utf-8"),
                render_values,
            )
            digest = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
            files.append(
                {
                    "requirement_id": requirement_id,
                    "kind": "markdown",
                    "filename": f"{requirement_id}.md",
                    "sha256": digest,
                    "text": body,
                    "template_path": str(template_path.relative_to(TEMPLATE_ROOT)),
                }
            )
            mapping_document = {
                "schema_version": "sim-a-requirement-mapping.v1",
                "requirement_id": requirement_id,
                "requirement_title": str(requirement.get("title") or requirement_id),
                "evidence_policy": SIM_A_FORMAL,
                "evidence_origin": SIM_A_ORIGIN,
                "assumption_package_id": package_id,
                "delivery_run_id": run_id,
                "rows": mapping_rows,
            }
            mapping_text = json.dumps(mapping_document, ensure_ascii=False, indent=2)
            files.append(
                {
                    "requirement_id": requirement_id,
                    "kind": "json",
                    "filename": f"{requirement_id}.json",
                    "sha256": "sha256:" + hashlib.sha256(mapping_text.encode("utf-8")).hexdigest(),
                    "text": mapping_text,
                    "template_path": "generated",
                }
            )
        payload = {
            "object_type": "TemplatePack",
            "delivery_run_id": run_id,
            "assumption_package_id": package_id,
            "intent_id": intent_id,
            "project_type": project_type,
            "industry_code": industry_code,
            "evidence_policy": SIM_A_FORMAL,
            "evidence_origin": SIM_A_ORIGIN,
            "requirement_ids": [str(item.get("requirement_id") or "") for item in requirements],
            "files": [
                {
                    "requirement_id": item["requirement_id"],
                    "kind": item["kind"],
                    "filename": item["filename"],
                    "sha256": item["sha256"],
                    "text": item["text"],
                    "template_path": item["template_path"],
                }
                for item in files
            ],
            "mapping_row_count": len(mapping_rows),
            "interface_only_count": sum(
                1 for item in mapping_rows if item.get("status") == "interface_only"
            ),
            "fabrication_forbidden": [item["name"] for item in FORBIDDEN_FABRICATION_FIELDS],
        }
        record = TEMPLATE_PACK_STORE.put(
            workspace_id,
            payload,
            producer=f"{SERVICE_NAME}.delivery_generate_template_pack",
            status="ok",
            source_ids=[run_id, package_id],
            basis=request_payload,
        )
        view = _view(record, "template_pack_id")
        return _envelope(
            True,
            "ok",
            resource_uris=[record["resource_uri"]],
            template_pack_id=record["object_id"],
            template_pack=view,
            requirement_ids=payload["requirement_ids"],
            mapping_row_count=payload["mapping_row_count"],
            interface_only_count=payload["interface_only_count"],
            files=[
                {
                    "filename": item["filename"],
                    "sha256": item["sha256"],
                    "kind": item["kind"],
                    "requirement_id": item["requirement_id"],
                }
                for item in files
            ],
            next_actions=[
                "核对拟定模板包后调用 delivery_confirm_formal_promotion",
                "确认时必须填写 responsible_party 与 confirmation_note",
            ],
            validation_complete=False,
            input_evidence_complete=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="delivery_generate_template_pack",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutation,
    )


def _project_name(sentence: str, supplied: str = "") -> str:
    from .routing import _project_name as resolve_name

    return str(resolve_name(sentence, supplied) or supplied or "")


def confirm_formal_promotion(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    pack_id = require_safe_id(args.get("template_pack_id"), "template_pack_id")
    responsible_party = str(args.get("responsible_party") or "").strip()
    confirmation_note = str(args.get("confirmation_note") or "").strip()
    idempotency_key = str(args.get("idempotency_key") or "")
    if not responsible_party or not confirmation_note:
        return _blocked(
            "missing_inputs",
            "responsible_party 与 confirmation_note 为确认晋升必填项",
            next_actions=["补全责任方与确认说明后重试"],
        )
    request_payload = {
        "template_pack_id": pack_id,
        "responsible_party": responsible_party,
        "confirmation_note": confirmation_note,
    }

    def mutation() -> dict[str, Any]:
        pack_record = TEMPLATE_PACK_STORE.get(workspace_id, pack_id)
        if pack_record is None:
            return _blocked("template_pack_not_found", "未找到指定 TemplatePack")
        pack = _view(pack_record, "template_pack_id")
        from lvke_mcp.servers.lvke_source_files import service as source_files

        file_ids: list[str] = []
        imported: list[dict[str, Any]] = []
        for index, item in enumerate(pack.get("files") or []):
            if not isinstance(item, dict):
                continue
            filename = str(item.get("filename") or f"sim-a-{index}.txt")
            text = str(item.get("text") or "")
            mime = "application/json" if item.get("kind") == "json" else "text/markdown"
            imported_file = source_files.import_content(
                workspace_id,
                original_filename=filename,
                declared_mime=mime,
                content_base64=base64.b64encode(text.encode("utf-8")).decode("ascii"),
                idempotency_key=f"{idempotency_key}:{filename}",
                evidence_policy=SIM_A_FORMAL,
                evidence_origin=SIM_A_ORIGIN,
                project_fact_certified=True,
            )
            file_id = str(imported_file.get("file_id") or "")
            if not file_id:
                return _blocked(
                    str(imported_file.get("code") or "source_import_failed"),
                    "拟定模板导入失败",
                    import_result=imported_file,
                )
            file_ids.append(file_id)
            imported.append(
                {
                    "file_id": file_id,
                    "filename": filename,
                    "sha256": item.get("sha256"),
                    "evidence_policy": SIM_A_FORMAL,
                    "evidence_origin": SIM_A_ORIGIN,
                }
            )
        payload = {
            "object_type": "FormalPromotion",
            "template_pack_id": pack_id,
            "assumption_package_id": pack.get("assumption_package_id"),
            "delivery_run_id": pack.get("delivery_run_id"),
            "intent_id": pack.get("intent_id"),
            "responsible_party": responsible_party,
            "confirmation_note": confirmation_note,
            "evidence_policy": SIM_A_FORMAL,
            "evidence_origin": SIM_A_ORIGIN,
            "file_ids": file_ids,
            "imported_files": imported,
            "requirement_ids": list(pack.get("requirement_ids") or []),
            "release_not_invoked": True,
        }
        record = PROMOTION_STORE.put(
            workspace_id,
            payload,
            producer=f"{SERVICE_NAME}.delivery_confirm_formal_promotion",
            status="ok",
            source_ids=[pack_id, str(pack.get("assumption_package_id") or "")],
            basis=request_payload,
        )
        return _envelope(
            True,
            "ok",
            resource_uris=[record["resource_uri"]],
            promotion_id=record["object_id"],
            file_ids=file_ids,
            imported_files=imported,
            evidence_policy=SIM_A_FORMAL,
            evidence_origin=SIM_A_ORIGIN,
            next_actions=[
                "project_context_create",
                "feasibility_start",
            ],
            next_action_details=[
                {
                    "tool": "project_context_create",
                    "evidence_policy": SIM_A_FORMAL,
                    "promotion_id": record["object_id"],
                    "file_ids": file_ids,
                },
                {
                    "tool": "feasibility_start",
                    "evidence_policy": SIM_A_FORMAL,
                    "note": "必须新建可研链对象，禁止复用 zmr_* 作为 delivery_run_id",
                },
            ],
            validation_complete=False,
            input_evidence_complete=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="delivery_confirm_formal_promotion",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutation,
    )
