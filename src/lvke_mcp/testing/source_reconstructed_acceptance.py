"""Deterministic inputs for source-reconstructed finance acceptance.

The helpers in this module replay values and formulas from an imported client
workbook.  They do not upgrade those values to original project facts.  Any
caller using the result must retain ``source_reconstructed`` and the returned
limitations through release.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from lvke_mcp.domains.finance.fact_pack import (
    DOMAIN_KEYS,
    _domain_fact_leaves,
    build_fact_pack_snapshot,
)
from lvke_mcp.domains.finance.vendor_import import (
    _find_mapped_sheet,
    build_finance_input_from_vendor,
    build_reference_pack,
    build_vendor_finance_spec,
)


FORMULA_REPLAY_LOCATOR = "workbook:sheets-and-formulas"


def load_nine_chapter_bodies(path: str | Path) -> list[str]:
    """Read the nine chapter bodies from a source-reconstructed Markdown revision."""

    text = Path(path).read_text(encoding="utf-8")
    matches = list(re.finditer(r"^#{1,6}\s+第([1-9])章\s+[^\n]+$", text, re.MULTILINE))
    if len(matches) != 9 or [int(item.group(1)) for item in matches] != list(range(1, 10)):
        raise ValueError("report revision must contain exactly chapters 1 through 9")
    return [
        text[item.end():(matches[index + 1].start() if index + 1 < len(matches) else len(text))].strip()
        for index, item in enumerate(matches)
    ]


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _sheet_rows(sheet: dict[str, Any]) -> dict[int, dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    for cell, value in (sheet.get("values") or {}).items():
        row_text = "".join(char for char in str(cell) if char.isdigit())
        column = "".join(char for char in str(cell) if char.isalpha()).upper()
        if row_text and column:
            rows.setdefault(int(row_text), {})[column] = value
    return rows


def _other_construction_items(reference_pack: dict[str, Any]) -> list[dict[str, Any]]:
    sheet = _find_mapped_sheet(reference_pack, "固定资产投资估算表")
    rows = _sheet_rows(sheet or {})
    start = next(
        (index for index, row in rows.items() if str(row.get("B") or "").strip() == "工程建设其它费用"),
        0,
    )
    end = next(
        (
            index
            for index, row in rows.items()
            if index > start and "基本预备费" in str(row.get("B") or "")
        ),
        0,
    )
    items: list[dict[str, Any]] = []
    for index in range(start + 1, end):
        row = rows.get(index) or {}
        name = str(row.get("B") or "").strip()
        amount = _number(row.get("I"))
        if not name or amount <= 0:
            continue
        quantity = _number(row.get("D")) or 1.0
        indicator = _number(row.get("E")) or amount * 10_000 / quantity
        items.append(
            {
                "name": name,
                "unit": str(row.get("C") or "项").strip() or "项",
                "quantity": quantity,
                "indicator_yuan": indicator,
                "amount_wan": amount,
                "category": "other",
                "source_row": index,
            }
        )
    return items


def _net_working_capital(reference_pack: dict[str, Any]) -> float:
    sheet = _find_mapped_sheet(reference_pack, "流动资金估算表")
    rows = _sheet_rows(sheet or {})
    row = next(
        (
            values
            for values in rows.values()
            if str(values.get("B") or "").replace(" ", "") == "流动资金(1-2)"
        ),
        {},
    )
    values = [_number(row.get(column)) for column in "EFGHIJKLMNOPQRS"]
    result = max(values or [0.0])
    if result <= 0:
        raise ValueError("vendor workbook does not contain a positive net working-capital row")
    return result


def _amortization_bases(reference_pack: dict[str, Any]) -> list[dict[str, Any]]:
    sheet = _find_mapped_sheet(reference_pack, "无形资产和其他资产摊销估算表")
    rows = _sheet_rows(sheet or {})
    result: list[dict[str, Any]] = []
    for index, row in sorted(rows.items()):
        name = str(row.get("B") or "").strip()
        next_row = rows.get(index + 1) or {}
        if not name or str(next_row.get("B") or "").strip() != "原值":
            continue
        amount = _number(next_row.get("C"))
        years = _number(next_row.get("D"))
        if amount > 0 and years > 0:
            result.append(
                {
                    "name": name,
                    "original_wan": amount,
                    "amort_years": int(years),
                }
            )
    return result


def _debt_repay_sources(reference_pack: dict[str, Any]) -> list[dict[str, Any]]:
    sheet = _find_mapped_sheet(reference_pack, "还款付息测算表")
    rows = _sheet_rows(sheet or {})
    result: list[dict[str, Any]] = []
    for row in rows.values():
        name = str(row.get("B") or "").strip()
        if not name or not any(token in name for token in ("利润", "折旧", "摊销")):
            continue
        schedule = [_number(row.get(column)) for column in "EFGHIJKL"]
        if any(value > 0 for value in schedule):
            result.append({"name": name, "annual_schedule_wan": schedule})
    return result[:3]


def _distribution_policy(reference_pack: dict[str, Any]) -> dict[str, Any]:
    sheet = _find_mapped_sheet(reference_pack, "利润与利润分配表")
    rows = _sheet_rows(sheet or {})
    by_name = {str(row.get("B") or "").strip(): row for row in rows.values()}

    def first_value(row: dict[str, Any]) -> float:
        return next(
            (_number(row.get(column)) for column in "EFGHIJKLMNOPQ" if _number(row.get(column)) > 0),
            0.0,
        )

    available = first_value(by_name.get("可供分配的利润(9-10)") or {})
    statutory = first_value(by_name.get("提取法定盈余公积金") or {})
    investor_base = first_value(by_name.get("可供投资者分配的利润(11-12)") or {})
    arbitrary = first_value(by_name.get("提取任意盈余公积金") or {})
    distribution = first_value(by_name.get("投资各方利润分配") or {})
    if min(available, investor_base) <= 0:
        raise ValueError("vendor workbook does not contain a usable profit-distribution policy")
    return {
        "statutory_reserve_rate": round(statutory / available, 8),
        "arbitrary_reserve_rate": round(arbitrary / investor_base, 8),
        "investor_distribution_rate": round(distribution / investor_base, 8),
        "retained_profit_policy": "vendor_formula_replay",
    }


def _working_capital_domain(finance_input: dict[str, Any], amount_wan: float) -> dict[str, Any]:
    source = dict(finance_input.get("wc_turnover") or {})
    inventory = dict(source.get("inventory_detail") or {})

    def component(name: str) -> dict[str, Any]:
        raw = dict(source.get(name) or {})
        return {
            "days": _number(raw.get("days")),
            "base_wan": _number(raw.get("annual_base_wan") or raw.get("base_wan")),
            "base_source": str(raw.get("base_source") or ""),
        }

    return {
        "receivable": component("receivable"),
        "cash": component("cash"),
        "payable": component("payable"),
        "inventory": _number(source.get("inventory")),
        "inventory_detail": {
            key: {
                "days": _number(value.get("days")),
                "base_wan": _number(value.get("annual_base_wan")),
                "base_source": str(value.get("base_source") or ""),
            }
            for key, value in inventory.items()
            if isinstance(value, dict)
        },
        "short_term_loan_wan": 0.0,
        "self_funded_wan": amount_wan,
    }


def build_reconstructed_vendor_case(
    template_path: str | Path,
    *,
    workspace_id: str,
    source_id: str,
    source_uri: str,
    content_hash: str,
    valuation_date: str,
    reconstruction_id: str,
) -> dict[str, Any]:
    """Build an explicit corrected reconstruction from one real workbook."""

    path = Path(template_path).resolve()
    reference = build_reference_pack(path)
    finance_input = build_finance_input_from_vendor(reference)
    finance_spec = build_vendor_finance_spec(reference, finance_input)
    indicators = dict(reference.get("indicators") or {})

    net_working_capital = _net_working_capital(reference)
    construction = _number(indicators.get("construction_investment"))
    interest = _number(indicators.get("interest_during_construction"))
    loan = _number(indicators.get("loan"))
    total_investment = round(construction + interest + net_working_capital, 6)
    capital = round(total_investment - loan, 6)
    if min(construction, loan, capital) <= 0:
        raise ValueError("vendor workbook is missing construction, loan, or equity anchors")

    breakdown = dict(finance_input.get("invest_breakdown") or {})
    original_items = [
        dict(row)
        for row in breakdown.get("construction_items") or []
        if str(row.get("category") or "") != "other"
    ]
    construction_items = [*original_items, *_other_construction_items(reference)]
    breakdown.update(
        {
            "construction_wan": construction,
            "interest_wan": interest,
            "working_capital_wan": net_working_capital,
            "construction_items": construction_items,
        }
    )

    construction_by_year = [float(value) for value in finance_input.get("construction_invest_by_year") or []]
    interest_by_year = [float(value) for value in finance_input.get("idc_by_year") or []]
    loan_by_year = [float(value) for value in finance_input.get("loan_draw_by_year") or []]
    build_years = len(construction_by_year)
    if not build_years or not (len(interest_by_year) == len(loan_by_year) == build_years):
        raise ValueError("vendor funding schedules are incomplete")
    funding_rows: list[dict[str, Any]] = []
    for index in range(build_years):
        working_capital = net_working_capital if index == build_years - 1 else 0.0
        uses = construction_by_year[index] + interest_by_year[index] + working_capital
        equity = round(uses - loan_by_year[index], 6)
        funding_rows.append(
            {
                "year": index + 1,
                "construction_investment_wan": construction_by_year[index],
                "construction_interest_wan": interest_by_year[index],
                "working_capital_wan": working_capital,
                "capital_own_wan": equity,
                "loan_wan": loan_by_year[index],
                "gov_subsidy_wan": 0.0,
            }
        )
    if abs(sum(row["capital_own_wan"] for row in funding_rows) - capital) > 0.05:
        raise ValueError("corrected funding schedule does not reconcile to equity")

    principal = [float(value) for value in finance_input.get("loan_principal_by_year") or []]
    reference_interest = [float(value) for value in finance_input.get("loan_interest_by_year") or []]
    debt = {
        "draws": [
            {"year": index + 1, "draw_wan": value}
            for index, value in enumerate(loan_by_year)
        ],
        "principal_schedule": [
            {"year": build_years + index + 1, "principal_wan": value}
            for index, value in enumerate(principal)
        ],
        "reference_interest_schedule": [
            {"year": build_years + index + 1, "interest_wan": value}
            for index, value in enumerate(reference_interest)
        ],
        "loan_rate": _number(finance_input.get("loan_rate")),
        "loan_years": int(_number(finance_input.get("loan_years"))),
        "repay_method": "principal_schedule",
        "debt_repay_sources": _debt_repay_sources(reference),
        "repayment_allocation_method": "vendor_formula_replay",
    }

    assets = [
        {
            "name": row.get("name"),
            "original_wan": row.get("original_value_wan"),
            "dep_years": row.get("depreciation_years"),
            "salvage_rate": row.get("salvage_rate"),
        }
        for row in finance_input.get("depreciation_classes") or []
    ]
    costs = dict(finance_input.get("cost_items") or {})
    variable_tokens = ("原材料", "燃料", "营业费用")
    cost_behavior = {
        "confirmed": True,
        "items": {
            name: {
                "type": "variable" if any(token in name for token in variable_tokens) else "fixed"
            }
            for name in costs
        },
    }
    tax_policy = {
        "confirmed": True,
        "vat_output_rate": 0.13,
        "vat_input_rate": 0.13,
        "income_tax_rate": 0.25,
        "surtax_base": "vat_and_consumption_tax_payable",
        "urban_maintenance_rate": 0.05,
        "education_surcharge_rate": 0.03,
        "local_education_surcharge_rate": 0.02,
    }
    domains = {
        "construction_items": construction_items,
        "products": list((finance_spec.get("revenue") or {}).get("products") or []),
        "cost_items": costs,
        "staff_detail": list(finance_input.get("staff_detail") or []),
        "asset_classes": assets,
        "wc_turnover": _working_capital_domain(finance_input, net_working_capital),
        "funding_plan": {"annual_schedule": funding_rows},
        "debt_schedule": debt,
        "amort_bases": _amortization_bases(reference),
        "distribution_policy": _distribution_policy(reference),
        "cost_behavior": cost_behavior,
        "tax_component_policy": tax_policy,
    }

    reconstruction = {
        "reconstruction_id": reconstruction_id,
        "source_uri": source_uri,
        "content_hash": content_hash,
        "locator": FORMULA_REPLAY_LOCATOR,
        "source_kind": "finance_template",
        "method": "formula_replay",
        "original_formula_available": True,
        "limitations": [
            "通用财务模板不是项目原始 BoE",
            "采用流动资金净额与逐项建设投资的纠正后重建轨",
            "仅用于 process_acceptance，不认证项目事实",
        ],
    }
    pack = {
        "project_id": workspace_id,
        "valuation_date": valuation_date,
        "domains": domains,
        "evidence_policy": "source_reconstructed",
        "project_fact_certified": False,
        "reconstruction_records": [reconstruction],
        "unresolved_inputs": ["original_project_boe"],
        "release_limitations": list(reconstruction["limitations"]),
    }
    normalized = build_fact_pack_snapshot(pack, workspace_id=workspace_id, confirm=False)
    if not normalized["depth_assessment"]["ok"]:
        raise ValueError(
            "fact-pack domains incomplete: "
            + ",".join(normalized["depth_assessment"]["missing_domains"])
        )
    evidence: list[dict[str, Any]] = []
    for domain in DOMAIN_KEYS:
        for leaf in _domain_fact_leaves(domain, normalized["domains"][domain]):
            item = {
                "domain": domain,
                "fact_path": leaf["fact_path"],
                "source_id": source_id,
                "locator": FORMULA_REPLAY_LOCATOR,
                "claimed_value": leaf["value"],
            }
            if leaf.get("unit") is not None:
                item["unit"] = leaf["unit"]
            if leaf.get("period") is not None:
                item["period"] = leaf["period"]
            evidence.append(item)
    pack["domains"] = normalized["domains"]
    pack["evidence"] = evidence

    finance_input.update(
        {
            "total_investment_wan": total_investment,
            "capital_own_wan": capital,
            "loan_wan": loan,
            "invest_breakdown": breakdown,
            "working_capital_by_year": [
                row["working_capital_wan"] for row in funding_rows
            ],
            "equity_inject_by_year": [row["capital_own_wan"] for row in funding_rows],
            "intangible_assets_wan": sum(
                _number(row.get("original_wan")) for row in domains["amort_bases"]
            ),
            "amortization_years": max(
                int(row["amort_years"]) for row in domains["amort_bases"]
            ),
            "income_tax_rate": tax_policy["income_tax_rate"],
            "vat_rate": tax_policy["vat_output_rate"],
            "vat_input_rate": tax_policy["vat_input_rate"],
            "surtax_on_vat": True,
            "surtax_vat_rate": 0.10,
        }
    )
    return {
        "reference_pack": reference,
        "finance_spec": finance_spec,
        "input_revision": finance_input,
        "fact_pack": pack,
        "reconstruction_record": reconstruction,
        "corrected_basis": {
            "construction_investment_wan": construction,
            "construction_interest_wan": interest,
            "net_working_capital_wan": net_working_capital,
            "total_investment_wan": total_investment,
            "loan_wan": loan,
            "capital_own_wan": capital,
        },
    }


def run_reconstructed_finance_case(
    template_path: str | Path,
    *,
    workspace_id: str,
    valuation_date: str,
    case_key: str,
    additional_reconstruction_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run one imported workbook through the real formal finance MCP chain."""

    from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE
    from lvke_mcp.adapters.finance_model_repository import SPEC_STORE
    from lvke_mcp.domains.finance import model_application, tables_service
    from lvke_mcp.servers.lvke_data_analysis import service as analysis_service
    from lvke_mcp.servers.lvke_finance_model.server import (
        _required_boe_pointers,
        _tool_build_basis_of_estimate,
    )
    from lvke_mcp.servers.lvke_source_files import service as source_service

    path = Path(template_path).resolve()
    raw = path.read_bytes()
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    imported = source_service.import_content(
        workspace_id,
        original_filename=path.name,
        declared_mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        content_base64=base64.b64encode(raw).decode("ascii"),
        idempotency_key=f"{case_key}-source",
        expected_sha256=digest,
        parse_immediately=True,
    )
    if not imported.get("success"):
        raise ValueError(f"source import failed: {imported}")
    source_id = str(imported.get("file_id") or "")
    source_ids = [source_id]
    source_uri = f"lvke://source-files/workspaces/{workspace_id}/files/{source_id}"
    reconstruction_id = f"srcx_{case_key}"
    case = build_reconstructed_vendor_case(
        path,
        workspace_id=workspace_id,
        source_id=source_id,
        source_uri=source_uri,
        content_hash=digest,
        valuation_date=valuation_date,
        reconstruction_id=reconstruction_id,
    )
    reconstruction_records = [
        case["reconstruction_record"],
        *list(additional_reconstruction_records or []),
    ]
    additional_facts = []
    for index, record in enumerate(additional_reconstruction_records or []):
        additional_path = Path(str(record["path"])).resolve()
        additional_raw = additional_path.read_bytes()
        additional_import = source_service.import_content(
            workspace_id,
            original_filename=additional_path.name,
            declared_mime="text/markdown",
            content_base64=base64.b64encode(additional_raw).decode("ascii"),
            idempotency_key=f"{case_key}-additional-source-{index}",
            expected_sha256=str(record["content_hash"]),
            parse_immediately=True,
        )
        if not additional_import.get("success"):
            raise ValueError(f"additional source import failed: {additional_import}")
        additional_source_id = str(additional_import["file_id"])
        source_ids.append(additional_source_id)
        additional_facts.append({
            "field": f"client_report_source_{index + 1}",
            "source_id": additional_source_id,
            "value": {"source_kind": record["source_kind"]},
            "locator": record["locator"],
            "evidence_eligibility": "source_reconstructed",
        })
    case["finance_spec"].update({
        "reconstruction_records": reconstruction_records,
        "reconstructed_source_ids": [
            item["reconstruction_id"] for item in reconstruction_records
        ],
    })

    ingested = analysis_service.ingest(workspace_id, [], source_ids)
    if not ingested.get("success"):
        raise ValueError(f"analysis ingest failed: {ingested}")
    task_id = str(ingested.get("analysis_task_id") or "")
    evidence = analysis_service.build_evidence_pack(
        workspace_id,
        task_id,
        source_ids,
        [{
            "field": "finance_template_formula_replay",
            "source_id": source_id,
            "value": case["corrected_basis"],
            "locator": {"kind": "workbook", "value": FORMULA_REPLAY_LOCATOR},
            "evidence_eligibility": "source_reconstructed",
        }, *additional_facts],
        [],
        evidence_track="source_reconstructed",
        reconstruction_records=reconstruction_records,
    )
    if not evidence.get("success"):
        raise ValueError(f"evidence pack failed: {evidence}")
    evidence_pack_id = str(evidence.get("evidence_pack_id") or "")

    prepared_pack = model_application.prepare_fact_pack({
        "workspace_id": workspace_id,
        "fact_pack": case["fact_pack"],
        "idempotency_key": f"{case_key}-fact-pack",
    })
    fact_pack_id = str(prepared_pack.get("fact_pack_id") or "")
    if not fact_pack_id:
        raise ValueError(f"fact pack prepare failed: {prepared_pack}")
    confirmed_pack = model_application.confirm_fact_pack({
        "workspace_id": workspace_id,
        "fact_pack_id": fact_pack_id,
        "idempotency_key": f"{case_key}-fact-pack-confirm",
    })
    if confirmed_pack.get("status") != "ok":
        raise ValueError(f"fact pack confirm failed: {confirmed_pack}")
    confirmed_fact_pack_id = str(confirmed_pack.get("fact_pack_id") or "")

    prepared_spec = model_application.prepare_spec({
        "workspace_id": workspace_id,
        "spec": case["finance_spec"],
        "input_revision": case["input_revision"],
        "evidence_pack_ids": [evidence_pack_id],
        "fact_pack_id": confirmed_fact_pack_id,
        "unresolved_inputs": ["original_project_boe"],
        "release_limitations": list(case["reconstruction_record"]["limitations"]),
    })
    candidate_spec_id = str(prepared_spec.get("spec_id") or "")
    if prepared_spec.get("status") != "ok" or not candidate_spec_id:
        raise ValueError(f"finance spec prepare failed: {prepared_spec}")
    confirmed_spec = model_application.confirm_spec({
        "workspace_id": workspace_id,
        "spec_id": candidate_spec_id,
        "note": "source_reconstructed process acceptance",
        "idempotency_key": f"{case_key}-spec-confirm",
    })
    if confirmed_spec.get("status") != "ok":
        raise ValueError(f"finance spec confirm failed: {confirmed_spec}")
    spec_id = str(confirmed_spec.get("spec_id") or "")
    spec_record = SPEC_STORE.get(workspace_id, spec_id) or {}
    spec_payload = spec_record.get("payload") or {}
    evidence_record = EVIDENCE_STORE.get(workspace_id, evidence_pack_id) or {}
    evidence_hash = str(evidence_record.get("content_hash") or "")

    entries = []
    for pointer in _required_boe_pointers(spec_payload):
        value: Any = spec_payload
        for part in pointer.strip("/").split("/"):
            value = value.get(part) if isinstance(value, dict) else None
        entries.append({
            "target_pointer": pointer,
            "value": value,
            "unit": "按目标字段",
            "period": valuation_date,
            "source_type": "evidence_pack",
            "source_object_id": evidence_pack_id,
            "method": "formula_replay",
            "selection_reason": "真实模板公式与表内数值显式重放",
            "locator": FORMULA_REPLAY_LOCATOR,
            "content_hash": evidence_hash,
            "evidence_eligibility": "source_reconstructed",
            "reconstruction": case["reconstruction_record"],
        })
    boe = _tool_build_basis_of_estimate({
        "workspace_id": workspace_id,
        "spec_id": spec_id,
        "evidence_pack_ids": [evidence_pack_id],
        "planning_object_ids": [],
        "entries": entries,
        "unresolved_inputs": ["original_project_boe"],
        "release_limitations": list(case["reconstruction_record"]["limitations"]),
        "idempotency_key": f"{case_key}-boe",
    })
    if not boe.get("formal_ready"):
        raise ValueError(f"basis of estimate failed: {boe}; spec={spec_payload}")
    boe_id = str(boe.get("basis_of_estimate_id") or "")

    run = model_application.run_model({
        "workspace_id": workspace_id,
        "spec_id": spec_id,
        "basis_of_estimate_id": boe_id,
        "mode": "review_candidate",
        "valuation_date": valuation_date,
        "idempotency_key": f"{case_key}-run",
    })
    run_id = str(run.get("run_id") or "")
    if run.get("status") != "ok" or not run_id:
        raise ValueError(f"finance run failed: {run}")
    technical = tables_service.validate(
        workspace_id, run_id, validation_scope="technical",
    )
    if not technical.get("success"):
        raise ValueError(f"technical table validation failed: {technical}")
    exported = tables_service.export_xlsx(workspace_id, run_id)
    if not exported.get("xlsx_resource"):
        raise ValueError(f"xlsx export failed: {exported}")
    formal = tables_service.validate(
        workspace_id, run_id, validation_scope="formal",
    )
    if not formal.get("success"):
        raise ValueError(f"formal table validation failed: {formal}")
    package_id = str(exported.get("finance_tables_package_id") or "")
    return {
        "workspace_id": workspace_id,
        "source_file_id": source_id,
        "source_file_ids": source_ids,
        "evidence_pack_id": evidence_pack_id,
        "fact_pack_id": confirmed_fact_pack_id,
        "finance_spec_id": spec_id,
        "basis_of_estimate_id": boe_id,
        "finance_run_id": run_id,
        "finance_tables_package_id": package_id,
        "xlsx_resource_uri": exported.get("xlsx_resource"),
        "xlsx_hash": exported.get("xlsx_hash"),
        "technical_validation": technical,
        "formal_validation": formal,
        "evidence_policy": "source_reconstructed",
        "project_fact_certified": False,
        "unresolved_inputs": ["original_project_boe"],
        "reconstruction_record": case["reconstruction_record"],
        "reconstruction_records": reconstruction_records,
    }


def run_reconstructed_acquisition_case(
    *,
    workspace_id: str,
    scenario: dict[str, Any],
    reconstruction_records: list[dict[str, Any]],
    unresolved_inputs: list[str],
) -> dict[str, Any]:
    """Run one Hengli purchase-price scenario through the real acquisition model."""

    from lvke_mcp.domains.asset_acquisition import backend, tables
    from lvke_mcp.servers.lvke_data_analysis import service as analysis_service
    from lvke_mcp.servers.lvke_source_files import service as source_service

    scenario_id = str(scenario["scenario_id"])
    purchase_price = _number(scenario["purchase_price_wan"])
    total_investment = _number(scenario["total_investment_wan"])
    reconstruction_ids = [
        str(item["reconstruction_id"])
        for item in reconstruction_records
    ]
    if len(reconstruction_ids) != 6:
        raise ValueError("Hengli acceptance requires all six historical statements")
    source_file_ids = []
    fact_candidates = []
    for index, record in enumerate(reconstruction_records):
        path = Path(str(record["path"])).resolve()
        raw = path.read_bytes()
        imported = source_service.import_content(
            workspace_id,
            original_filename=path.name,
            declared_mime="application/vnd.ms-excel",
            content_base64=base64.b64encode(raw).decode("ascii"),
            idempotency_key=f"hengli-{scenario_id}-source-{index}",
            expected_sha256=str(record["content_hash"]),
            parse_immediately=True,
        )
        if not imported.get("success"):
            raise ValueError(f"Hengli source import failed: {imported}")
        source_file_id = str(imported["file_id"])
        source_file_ids.append(source_file_id)
        fact_candidates.append({
            "field": f"historical_statement_{index + 1}",
            "source_id": source_file_id,
            "value": {"source_kind": record["source_kind"]},
            "locator": record["locator"],
            "evidence_eligibility": "source_reconstructed",
        })
    ingested = analysis_service.ingest(workspace_id, [], source_file_ids)
    if not ingested.get("success"):
        raise ValueError(f"Hengli analysis ingest failed: {ingested}")
    evidence = analysis_service.build_evidence_pack(
        workspace_id,
        str(ingested["analysis_task_id"]),
        source_file_ids,
        [
            *fact_candidates,
            {
                "field": "purchase_price",
                "source_id": source_file_ids[0],
                "value": purchase_price,
                "original_value": purchase_price,
                "numeric_value": purchase_price,
                "expected_unit": "万元",
                "locator": {"kind": "workbook", "value": reconstruction_records[0]["locator"]},
                "evidence_eligibility": "source_reconstructed",
            },
            {
                "field": "valuation_value",
                "source_id": source_file_ids[0],
                "value": 4027.53,
                "original_value": 4027.53,
                "numeric_value": 4027.53,
                "expected_unit": "万元",
                "locator": {"kind": "workbook", "value": reconstruction_records[0]["locator"]},
                "evidence_eligibility": "source_reconstructed",
            },
            {
                "field": "total_investment",
                "source_id": source_file_ids[0],
                "value": total_investment,
                "original_value": total_investment,
                "numeric_value": total_investment,
                "expected_unit": "万元",
                "locator": {"kind": "workbook", "value": reconstruction_records[0]["locator"]},
                "evidence_eligibility": "source_reconstructed",
            },
        ],
        [],
        evidence_track="source_reconstructed",
        reconstruction_records=reconstruction_records,
    )
    if not evidence.get("success"):
        raise ValueError(f"Hengli EvidencePack failed: {evidence}")
    evidence_pack_id = str(evidence["evidence_pack_id"])
    statement_values = (
        ("income_statement", {"revenue_wan": 19.59, "net_profit_wan": -16.14}),
        ("cash_flow", {"operating_cashflow_wan": 14.80, "cash_change_wan": 10.74}),
        ("balance_sheet", {"assets_wan": 18.61, "equity_wan": -16.14}),
        ("income_statement", {"revenue_wan": 165.82, "net_profit_wan": 21.98}),
        ("cash_flow", {"operating_cashflow_wan": 0.01, "cash_change_wan": -2.67}),
        ("balance_sheet", {"assets_wan": 59.87, "equity_wan": 5.84}),
    )
    statements = []
    for index, (statement_type, normalized_accounts) in enumerate(statement_values):
        record = reconstruction_records[index]
        is_2023 = index < 3
        statements.append({
            "entity_id": "hengli-operator",
            "period_start": "2023-11-01" if is_2023 else "2024-01-01",
            "period_end": "2023-12-31" if is_2023 else "2024-09-30",
            "statement_type": statement_type,
            "source_format": "xls",
            "normalized_accounts": normalized_accounts,
            "reconciliation": {"ok": True, "method": "table_extract"},
            "anomalies": [],
            "source_locators": [{
                "evidence_id": record["reconstruction_id"],
                "locator": record["locator"],
            }],
        })

    party_evidence = [reconstruction_ids[0]]
    parties = [
        ("scenario-buyer", "收购方（情景未选定）", ["buyer", "lender"]),
        ("source-owner", "资产出让及持有主体（报告口径）", ["seller", "asset_owner", "lessor", "license_holder"]),
        ("hengli-operator", "咸宁恒立酒店管理有限公司", ["operator", "lessee"]),
        ("source-appraiser", "评估主体（报告口径）", ["appraiser"]),
    ]
    project_parties = [{
        "entity_id": entity_id,
        "name": name,
        "roles": roles,
        "status": "confirmed",
        "evidence_ids": party_evidence,
    } for entity_id, name, roles in parties]
    lease_units = []
    for unit_id, name, annual_rent in (
        ("supermarket", "超市", 53.51),
        ("pub", "清吧", 4.80),
        ("gym", "健身房", 22.00),
    ):
        lease_units.append({
            "unit_id": unit_id,
            "asset_location": f"恒立酒店-{name}",
            "area_sqm": 1,
            "lessor_id": "source-owner",
            "lessee_id": "hengli-operator",
            "start_date": "2026-01-01",
            "end_date": "2040-12-31",
            "base_rent_wan": annual_rent,
            "pricing_unit": "annual_total",
            "payment_frequency": "annual",
            "escalation_rate": 0,
            "escalation_date": "2027-01-01",
            "rent_free_months": 0,
            "vacancy_rate": 0,
            "renewal_probability": 0,
            "deposit_wan": 0,
            "guarantee_wan": 0,
            "bad_debt_rate": 0,
            "leasing_cost_wan": 0,
            "fitout_allowance_wan": 0,
            "evidence_ids": party_evidence,
        })
    spec = {
        "version": "finance_spec.v3",
        "confirmation_status": "candidate",
        "selected_scenario_id": scenario_id,
        "industry": "hotel",
        "invest_type": "asset_acquisition",
        "revenue": {"model": "flat", "annual_revenue_wan": 511.04},
        "cost": {"cost_items": {"owner_opex_wan": 0}},
        "tax": {"income_tax_rate": 0.25},
        "asset_type": "hotel_lease",
        "project_parties": project_parties,
        "hotel_operation": {
            "rooms": 66,
            "adr": 298,
            "occupancy": 0.60,
            "operating_days": 365,
            "food_beverage_revenue": 0,
            "meeting_revenue": 0,
            "other_revenue": 0,
            "ota_commission": 0,
            "payroll": 59.49,
            "utilities": 0,
            "consumables": 121.82,
            "maintenance_capex": 0,
            "evidence_ids": reconstruction_ids,
        },
        "lease_portfolio": {"projection_years": 15, "units": lease_units},
        "transaction": {
            "acquisition_type": "asset",
            "purchase_price": purchase_price,
            "transaction_taxes": {
                "unresolved_total_investment_bridge": round(total_investment - purchase_price, 2),
            },
            "tax_burden_party": "buyer",
            "asset_scope": [{
                "scope_id": "scenario-asset-pool",
                "type": "hotel_asset_pool",
                "included": True,
                "status": "confirmed",
                "accounting_treatment": "depreciable",
                "allocation_wan": purchase_price,
                "depreciable_basis_wan": purchase_price,
                "depreciation_years": 40,
                "residual_rate": 0.05,
                "evidence_ids": party_evidence,
            }],
            "closing_date": "2026-01-01",
            "model_start_date": "2026-01-01",
            "opening_date": "2026-01-01",
            "operating_mode": "mixed_owner_operator",
            "calculation_granularity": "monthly",
            "valuation_value": 4027.53,
            "valuation_date": "2024-11-20",
            "financing_ratio": 0.80,
            "interest_rate": 0.05,
            "tenor": 15,
            "repayment": "equal_principal",
            "exit_value": purchase_price,
            "exit_year": 15,
            "closing_conditions": ["仅用于来源重建流程验收"],
            "veto_items": ["最终经营模式、交易结构和收购价均未选择"],
        },
        "historical_statements": statements,
        "decision_thresholds": {"target_project_irr": 0.06, "minimum_dscr": 1.0},
        "evidence_links": {
            field: party_evidence
            for field in (
                "transaction.purchase_price",
                "transaction.asset_scope",
                "historical_statements",
                "hotel_operation.rooms",
                "hotel_operation.adr",
                "hotel_operation.occupancy",
                "lease_portfolio.units",
            )
        },
        "evidence_policy": "source_reconstructed",
        "project_fact_certified": False,
        "reconstruction_records": reconstruction_records,
        "reconstructed_source_ids": reconstruction_ids,
        "unresolved_inputs": list(unresolved_inputs),
        "release_limitations": [
            "六档均为独立过程验收情景，不构成收购决策",
            "交易税费桥接尚未拆分，不作为项目事实认证",
        ],
        "business_decision_status": "not_selected",
        "process_acceptance_basis": [
            {
                "field": field,
                "value": (
                    purchase_price if field == "transaction.purchase_price"
                    else str(spec_value)
                ),
                "source_ref": reconstruction_records[index % len(reconstruction_records)]["source_uri"],
                "locator": reconstruction_records[index % len(reconstruction_records)]["locator"],
                "content_hash": reconstruction_records[index % len(reconstruction_records)]["content_hash"],
                "method": reconstruction_records[index % len(reconstruction_records)]["method"],
                "limitation": "仅作为来源重建计算情景，业务决策保持未选择",
            }
            for index, (field, spec_value) in enumerate(
                (
                    ("transaction.purchase_price", purchase_price),
                    ("transaction.acquisition_type", "asset"),
                    ("transaction.asset_scope", "scenario-asset-pool"),
                    ("transaction.transaction_taxes", "unresolved bridge"),
                    ("hotel_operation.operating_mode", "mixed_owner_operator"),
                    ("transaction.ppa", "unresolved"),
                    ("hotel_operation.maintenance_capex", 0),
                    ("working_capital", "unresolved"),
                )
            )
        ],
    }
    saved = backend.save_spec(
        workspace_id,
        spec,
        idempotency_key=f"hengli-{scenario_id}-save",
    )
    if not saved.get("ok"):
        raise ValueError(f"acquisition spec save failed: {saved}")
    confirmed = backend.confirm_saved_spec(
        workspace_id,
        str(saved["spec_id"]),
        note="source_reconstructed process_acceptance scenario",
        confirmation_scope="process_acceptance",
        idempotency_key=f"hengli-{scenario_id}-confirm",
    )
    if not confirmed.get("ok"):
        raise ValueError(f"acquisition spec confirmation failed: {confirmed}")
    confirmed_spec = dict(confirmed["spec"])
    run = backend.create_run(
        workspace_id,
        confirmed_spec,
        discount_rate=0.06,
        scenario_id=scenario_id,
        idempotency_key=f"hengli-{scenario_id}-run",
        scenario_change_ledger=[{
            "field": "transaction.purchase_price",
            "value": purchase_price,
            "source": str(scenario.get("cashflow_source_locator") or scenario_id),
        }],
    )
    if not run.get("ok") or run.get("validation_status") != "passed":
        raise ValueError(f"acquisition run failed: {run}")
    max_price = backend.max_price(
        workspace_id,
        str(run["run_id"]),
        upper=10_000,
        request_id=f"hengli-{scenario_id}-max-price",
    )
    if not max_price.get("ok") or max_price.get("validation_status") != "passed":
        raise ValueError(f"acquisition max-price analysis failed: {max_price}")
    run = backend.get_run(workspace_id, str(run["run_id"]))
    package = tables.render(workspace_id, str(run["run_id"]))
    if package.get("status") != "ok":
        raise ValueError(f"acquisition tables failed: {package}")
    package_id = str(package["acquisition_tables_package_id"])
    xlsx = tables.export_xlsx(workspace_id, package_id)
    csv = tables.export_csv(workspace_id, package_id)
    if not xlsx.get("xlsx_resource_uri") or len(csv.get("csv_resource_uris") or []) != 13:
        raise ValueError(f"acquisition table export failed: xlsx={xlsx}; csv={csv}")
    return {
        "workspace_id": workspace_id,
        "source_file_ids": source_file_ids,
        "source_file_id": source_file_ids[0],
        "evidence_pack_id": evidence_pack_id,
        "finance_spec_id": str(confirmed["spec_id"]),
        "finance_run_id": str(run["run_id"]),
        "finance_tables_package_id": package_id,
        "purchase_price_wan": purchase_price,
        "valuation_value_wan": 4027.53,
        "total_investment_wan": _number((run.get("result") or {}).get("total_acquisition_cost_wan")),
        "xlsx_resource_uri": xlsx["xlsx_resource_uri"],
        "csv_resource_uris": list(csv["csv_resource_uris"]),
        "evidence_policy": "source_reconstructed",
        "project_fact_certified": False,
        "reconstruction_record": reconstruction_records[0],
        "reconstruction_records": reconstruction_records,
        "unresolved_inputs": list(unresolved_inputs),
        "business_decision_status": "not_selected",
    }


def run_reconstructed_planning_case(
    finance: dict[str, Any],
    *,
    project_name: str,
    industry_code: str,
    project_type: str = "new_build",
    region: str = "湖北省",
) -> dict[str, Any]:
    """Create the real ProjectContext and deterministic planning object chain."""

    from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE
    from lvke_mcp.domains.project_planning import application as planning

    workspace_id = str(finance["workspace_id"])
    evidence_pack_id = str(finance["evidence_pack_id"])
    evidence = EVIDENCE_STORE.get(workspace_id, evidence_pack_id) or {}
    evidence_payload = evidence.get("payload") or {}
    source = next(
        (row for row in evidence_payload.get("sources") or [] if isinstance(row, dict)),
        None,
    )
    if source is None:
        raise ValueError("EvidencePack does not contain a source snapshot")
    locators = list(source.get("locators") or [])
    source_fact = next(
        (
            row for row in evidence_payload.get("fact_candidates") or []
            if isinstance(row, dict) and str(row.get("source_id") or "")
            == str(source.get("source_id") or "") and row.get("locator")
        ),
        None,
    )
    locator_value = (
        locators[0]
        if locators
        else (source_fact or {}).get("locator") or FORMULA_REPLAY_LOCATOR
    )
    locator = (
        json.dumps(locator_value, ensure_ascii=False, sort_keys=True)
        if isinstance(locator_value, (dict, list))
        else str(locator_value)
    )
    source_id = str(source.get("source_id") or source.get("snapshot_id") or finance["source_file_id"])
    source_hash = str(source.get("content_hash") or "")
    reconstruction = next(
        (
            row for row in finance.get("reconstruction_records") or []
            if isinstance(row, dict) and str(row.get("content_hash") or "") == source_hash
        ),
        (finance.get("reconstruction_records") or [finance.get("reconstruction_record") or {}])[0],
    )
    evidence_binding = {
        "source_id": source_id,
        "content_hash": source_hash,
        "locator": locator,
        "evidence_track": "source_reconstructed",
        "source_type": "source_reconstructed",
        "reconstruction_id": str(reconstruction.get("reconstruction_id") or source_id),
        "source_uri": str(reconstruction.get("source_uri") or source.get("resource_uri") or ""),
        "source_kind": str(reconstruction.get("source_kind") or "finance_template"),
        "method": str(reconstruction.get("method") or "formula_replay"),
        "limitations": list(reconstruction.get("limitations") or []),
    }

    context_result = planning.create_project_context(
        workspace_id,
        {
            "project_name": project_name,
            "industry_code": industry_code,
            "project_type": project_type,
            "region": region,
            "objective": "来源重建流程验收",
            "report_type": "generic_feasibility",
            "evidence_track": "source_reconstructed",
        },
        idempotency_key=f"{project_name}-project-context",
    )
    project_context_id = str(context_result.get("project_context_id") or "")
    if not project_context_id:
        raise ValueError(f"project context failed: {context_result}")
    validated_context = planning.validate_project_context(
        workspace_id,
        project_context_id,
        idempotency_key=f"{project_name}-project-context-validate",
    )
    if not validated_context.get("success"):
        raise ValueError(f"project context validation failed: {validated_context}")

    candidates = []
    for method, market_size, share in (
        ("report_table_extract", 10000, 0.10),
        ("template_formula_replay", 12500, 0.08),
    ):
        candidates.append({
            "method": method,
            "market_size": market_size,
            "unit": "服务单位/年",
            "period": "2026",
            "region": region,
            "target_share": share,
            "target_volume": market_size * share,
            "formula_inputs": {"market_size": market_size, "target_share": share},
            "evidence_bindings": [evidence_binding],
        })
    market_candidate = planning.prepare_market_case(
        workspace_id,
        project_context_id,
        evidence_pack_id,
        candidates,
        idempotency_key=f"{project_name}-market-prepare",
    )
    if not market_candidate.get("success"):
        raise ValueError(f"market preparation failed: {market_candidate}")
    candidate_id = str(market_candidate["market_case_id"])
    selected_market_id = str(
        market_candidate["market_case"]["candidates"][0]["candidate_id"]
    )
    rejected_market_ids = [
        str(row["candidate_id"])
        for row in market_candidate["market_case"]["candidates"]
        if str(row["candidate_id"]) != selected_market_id
    ]
    market = planning.confirm_market_case(
        workspace_id,
        candidate_id,
        selected_market_id,
        "选择与报告可定位表格口径一致的测算路径",
        rejected_market_ids,
        idempotency_key=f"{project_name}-market-confirm",
    )
    if not market.get("success"):
        raise ValueError(f"market confirmation failed: {market}")
    market_case_id = str(market["market_case_id"])

    criteria = [{"criterion_id": "cost", "weight": 1, "direction": "lower_is_better"}]
    options = [
        {
            "option_id": "report_scheme",
            "name": "报告重建方案",
            "values": {"cost": 1},
            "constraint_results": {"traceable": True},
            "evidence_bindings": {"cost": [evidence_binding]},
        },
        {
            "option_id": "template_scheme",
            "name": "模板复算方案",
            "values": {"cost": 2},
            "constraint_results": {"traceable": True},
            "evidence_bindings": {"cost": [evidence_binding]},
        },
    ]
    option_candidate = planning.prepare_option_comparison(
        workspace_id,
        project_context_id,
        "process",
        criteria,
        options,
        [{"constraint_id": "traceable", "description": "来源可定位"}],
        [market_case_id],
        idempotency_key=f"{project_name}-option-prepare",
    )
    if not option_candidate.get("success"):
        raise ValueError(f"option preparation failed: {option_candidate}")
    option = planning.confirm_option_selection(
        workspace_id,
        str(option_candidate["option_comparison_id"]),
        "report_scheme",
        "采用与盖章报告及来源定位一致的重建方案",
        ["template_scheme"],
        idempotency_key=f"{project_name}-option-confirm",
    )
    if not option.get("success"):
        raise ValueError(f"option confirmation failed: {option}")

    scale = planning.create_build_scale_case(
        workspace_id,
        project_context_id,
        market_case_id,
        {"value": 1000, "unit": "服务单位/年"},
        10000,
        0.1,
        {
            "plot_ratio_min": 0.5,
            "plot_ratio_max": 2.0,
            "building_coverage_max": 0.6,
            "green_ratio_min": 0.2,
            "green_area_m2": 2500,
        },
        [{"name": "主体设施", "floor_area_m2": 10000, "footprint_m2": 5000}],
        idempotency_key=f"{project_name}-scale",
    )
    if not scale.get("success"):
        raise ValueError(f"build scale failed: {scale}")
    scale_id = str(scale["build_scale_case_id"])
    cost = planning.create_cost_driver_set(
        workspace_id,
        project_context_id,
        scale_id,
        {
            "construction_wan": 1000,
            "civil_wan": 500,
            "equipment_wan": 250,
            "installation_wan": 100,
            "other_wan": 100,
            "reserve_wan": 50,
            "interest_wan": 50,
            "working_capital_wan": 100,
        },
        [
            {"name": "原料", "annual_amount_wan": 100},
            {"name": "能源", "annual_amount_wan": 50},
            {"name": "维护", "annual_amount_wan": 30},
        ],
        idempotency_key=f"{project_name}-cost",
    )
    labor = planning.create_labor_plan(
        workspace_id,
        project_context_id,
        scale_id,
        [{"name": "运营人员", "category": "运营", "headcount": 10, "avg_wage_yuan": 80000, "welfare_rate": 0.2}],
        idempotency_key=f"{project_name}-labor",
    )
    revenue = planning.create_revenue_driver_set(
        workspace_id,
        project_context_id,
        market_case_id,
        {"model": "flat", "annual_revenue_wan": 1000},
        8,
        mode="review_candidate",
        flat_evidence_binding=evidence_binding,
        idempotency_key=f"{project_name}-revenue",
    )
    for name, result in (("cost", cost), ("labor", labor), ("revenue", revenue)):
        if not result.get("success"):
            raise ValueError(f"{name} driver failed: {result}")
    return {
        "project_context_id": project_context_id,
        "research_parent_ids": [project_context_id, evidence_pack_id],
        "market_case_id": market_case_id,
        "option_comparison_id": str(option["option_comparison_id"]),
        "build_scale_case_id": scale_id,
        "cost_driver_set_id": str(cost["cost_driver_set_id"]),
        "labor_plan_id": str(labor["labor_plan_id"]),
        "revenue_driver_set_id": str(revenue["revenue_driver_set_id"]),
        "evidence_binding": evidence_binding,
    }


def run_reconstructed_research_case(
    finance: dict[str, Any],
    planning: dict[str, Any],
    *,
    topic: str,
    limitations: list[str],
) -> dict[str, Any]:
    """Create a partial ResearchPackage and independently confirm its quality."""

    from lvke_mcp.domains.research import application as research

    workspace_id = str(finance["workspace_id"])
    evidence_pack_id = str(finance["evidence_pack_id"])
    binding = dict(planning["evidence_binding"])
    started = research.start_agent({
        "workspace_id": workspace_id,
        "topic": topic,
        "industry": "可研项目",
        "region": "湖北省",
        "plan_items": [{"field": "market_size", "required": True}],
        "analysis_inputs": [evidence_pack_id],
        "idempotency_key": f"{topic}-research-start",
    })
    if not started.get("success"):
        raise ValueError(f"research start failed: {started}")
    submitted = research.submit_agent({
        "workspace_id": workspace_id,
        "task_id": started["task_id"],
        "report_md": "现有报告与模板已形成可定位的来源重建研究包。",
        "citations": [{
            "source_id": binding["source_id"],
            "resource_uri": f"lvke://source-files/workspaces/{workspace_id}/files/{finance['source_file_id']}",
            "locator": binding["locator"],
            "content_hash": binding["content_hash"],
        }],
        "evidence_pack_ids": [evidence_pack_id],
        "source_snapshot_ids": [finance["source_file_id"]],
        "quality_summary": {
            "query_rounds": 1,
            "usable_source_count": 1,
            "citation_coverage": 1.0,
            "missing_fields": [],
            "conflicts": [],
        },
        "market_field_bindings": [{
            "field": "market_size",
            "value": 10000,
            "unit": "服务单位/年",
            "locator": binding["locator"],
            "source_snapshot_id": finance["source_file_id"],
        }],
        "unresolved_inputs": list(limitations),
        "release_limitations": ["来源重建研究不认证项目事实"],
    })
    if not submitted.get("success") or submitted.get("status") != "partial":
        raise ValueError(f"research submit failed: {submitted}")
    confirmed = research.confirm_quality({
        "workspace_id": workspace_id,
        "research_package_id": submitted["research_package_id"],
    })
    if not confirmed.get("success") or confirmed.get("status") != "completed":
        raise ValueError(f"research quality confirmation failed: {confirmed}")
    return {
        "research_package_id": str(confirmed["research_package_id"]),
        "quality_review_id": str(confirmed["quality_review_id"]),
        "quality_review_status": str(confirmed["quality_review_status"]),
    }


def run_reconstructed_report_case(
    finance: dict[str, Any],
    planning: dict[str, Any],
    research: dict[str, Any],
    *,
    chapter_contents: list[str],
    report_key: str,
    unresolved_inputs: list[str],
) -> dict[str, Any]:
    """Build all nine chapters through report propose/diff/apply/validate."""

    from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE
    from lvke_mcp.adapters.research_repository import PACKAGE_STORE as RESEARCH_STORE
    from lvke_mcp.domains.reports import application as reports

    if len(chapter_contents) != 9:
        raise ValueError("exactly nine chapter bodies are required")
    workspace_id = str(finance["workspace_id"])
    evidence_pack_id = str(finance["evidence_pack_id"])
    research_package_id = str(research["research_package_id"])
    run_id = str(finance["finance_run_id"])
    package_id = str(finance["finance_tables_package_id"])
    finance_binding_kind = (
        "asset_acquisition" if run_id.startswith("acqrun_") else "generic_feasibility"
    )
    outline = [f"第{number}章 {title}" for number, title in enumerate(
        ("总论", "项目背景与建设必要性", "需求分析与建设规模", "总体建设方案", "投资估算与资金筹措", "财务分析与评价", "风险分析与对策", "保障措施", "结论与建议"),
        start=1,
    )]
    upstream_refs = [
        planning["project_context_id"], evidence_pack_id, research_package_id,
        planning["market_case_id"], planning["option_comparison_id"],
        planning["build_scale_case_id"], planning["cost_driver_set_id"],
        planning["labor_plan_id"], planning["revenue_driver_set_id"],
        finance["finance_spec_id"], run_id, package_id,
    ]
    if finance.get("basis_of_estimate_id"):
        upstream_refs.insert(-2, finance["basis_of_estimate_id"])
    prepared = reports.prepare({
        "workspace_id": workspace_id,
        "evidence_pack_ids": [evidence_pack_id],
        "research_package_ids": [research_package_id],
        "finance_binding": {
            "kind": finance_binding_kind,
            "run_id": run_id,
            "package_id": package_id,
        },
        "outline": outline,
        "template_version": "source-reconstructed-nine-chapter.v1",
        "evidence_policy": "source_reconstructed",
        "project_fact_certified": False,
        "reconstruction_records": list(
            finance.get("reconstruction_records") or [finance["reconstruction_record"]]
        ),
        "reconstructed_source_ids": [
            item["reconstruction_id"]
            for item in (finance.get("reconstruction_records") or [finance["reconstruction_record"]])
        ],
        "unresolved_inputs": list(unresolved_inputs),
        "release_limitations": ["仅用于 process_acceptance，不认证项目事实"],
        "project_context_id": planning["project_context_id"],
        "project_metadata": {
            "project_type": finance_binding_kind,
            "industry": "来源重建可研",
            "valuation_date": "2026-08-05",
            "currency": "CNY",
            "amount_unit": "万元",
            "tax_basis": "FinanceSpec税费口径",
            "forecast_period": 8,
        },
        "upstream_refs": upstream_refs,
    })
    if not prepared.get("success"):
        raise ValueError(f"report preparation failed: {prepared}")
    preparation_id = str(prepared["report_preparation_id"])
    preparation_hash = str(prepared["basis_hash"])
    started = reports.start({
        "workspace_id": workspace_id,
        "report_preparation_id": preparation_id,
        "chapters": outline,
        "document_snapshot": {
            "workspace_id": workspace_id,
            "report_type": "generic_feasibility",
            "content": "\n\n".join(f"# {title}\n" for title in outline),
        },
    })
    if not started.get("success"):
        raise ValueError(f"report start failed: {started}")
    revision_id = str(started["report_revision_id"])
    if finance_binding_kind == "asset_acquisition":
        from lvke_mcp.domains.asset_acquisition.backend import get_run
        from lvke_mcp.domains.asset_acquisition.tables import get_package_record

        finance_run_basis = str(get_run(workspace_id, run_id).get("spec_hash") or "")
        table_record = get_package_record(workspace_id, package_id) or {}
    else:
        from lvke_mcp.adapters.finance_tables_repository import PACKAGE_STORE as TABLE_STORE
        from lvke_mcp.domains.finance.run_service import get_workspace_finance_run

        finance_run_basis = str(
            get_workspace_finance_run(workspace_id, run_id=run_id, view="summary").get("basis_hash") or ""
        )
        table_record = TABLE_STORE.get(workspace_id, package_id) or {}
    basis_hashes = {
        "evidence_pack": str((EVIDENCE_STORE.get(workspace_id, evidence_pack_id) or {}).get("basis_hash") or ""),
        "research_package": str((RESEARCH_STORE.get(workspace_id, research_package_id) or {}).get("basis_hash") or ""),
        "finance_run": finance_run_basis,
        "finance_tables_package": str(table_record.get("basis_hash") or ""),
    }
    locator = str(planning["evidence_binding"]["locator"])
    for index, (title, body) in enumerate(zip(outline, chapter_contents), start=1):
        section = reports.list_sections(workspace_id, revision_id)
        descriptor = next(
            row for row in section.get("sections") or []
            if str(row.get("title") or "") == title
        )
        proposed = reports.propose_section({
            "workspace_id": workspace_id,
            "report_revision_id": revision_id,
            "section_id": descriptor["section_id"],
            "summary": f"{report_key} 第{index}章来源重建修订",
            "proposed_content": f"# {title}\n\n{body.strip()}\n",
            "basis": {
                "report_preparation_id": preparation_id,
                "basis_hash": preparation_hash,
                "report_revision_id": revision_id,
                "upstream_refs": upstream_refs,
                "citation_locators": [locator],
                "upstream_basis_hashes": basis_hashes,
            },
        })
        if not proposed.get("success"):
            raise ValueError(f"report chapter proposal failed: {proposed}")
        proposal_id = str(proposed["proposal_id"])
        diffed = reports.diff(workspace_id, proposal_id)
        if not diffed.get("success"):
            raise ValueError(f"report chapter diff failed: {diffed}")
        applied = reports.apply(workspace_id, proposal_id)
        if not applied.get("success"):
            raise ValueError(f"report chapter apply failed: {applied}")
        revision_id = str(applied["report_revision_id"])
        checked = reports.validate_section(workspace_id, revision_id, descriptor["section_id"])
        if not checked.get("success"):
            raise ValueError(f"report chapter validation failed: {checked}")
    readiness = reports.readiness(workspace_id, revision_id)
    if not readiness.get("success"):
        raise ValueError(f"report readiness failed: {readiness}")
    return {
        "report_preparation_id": preparation_id,
        "report_revision_id": revision_id,
        "readiness": readiness,
        "outline": outline,
        "upstream_refs": upstream_refs,
        "upstream_basis_hashes": basis_hashes,
    }


def _patch_report_section(
    *,
    workspace_id: str,
    report: dict[str, Any],
    revision_id: str,
    section_number: int,
    body: str,
    summary: str,
) -> str:
    from lvke_mcp.adapters.report_repository import PREPARATION_STORE
    from lvke_mcp.domains.reports import application as reports

    preparation_id = str(report["report_preparation_id"])
    preparation = PREPARATION_STORE.get(workspace_id, preparation_id) or {}
    title = str(report["outline"][section_number - 1])
    sections = reports.list_sections(workspace_id, revision_id)
    descriptor = next(
        row for row in sections.get("sections") or []
        if str(row.get("title") or "") == title
    )
    proposed = reports.propose_section({
        "workspace_id": workspace_id,
        "report_revision_id": revision_id,
        "section_id": descriptor["section_id"],
        "summary": summary,
        "proposed_content": f"# {title}\n\n{body.strip()}\n",
        "basis": {
            "report_preparation_id": preparation_id,
            "basis_hash": str(preparation.get("basis_hash") or ""),
            "report_revision_id": revision_id,
            "upstream_refs": list(report["upstream_refs"]),
            "citation_locators": ["section:9/source-reconstructed"],
            "upstream_basis_hashes": dict(report["upstream_basis_hashes"]),
        },
    })
    if not proposed.get("success"):
        raise ValueError(f"report remediation proposal failed: {proposed}")
    proposal_id = str(proposed["proposal_id"])
    diffed = reports.diff(workspace_id, proposal_id)
    if not diffed.get("success"):
        raise ValueError(f"report remediation diff failed: {diffed}")
    applied = reports.apply(workspace_id, proposal_id)
    if not applied.get("success"):
        raise ValueError(f"report remediation apply failed: {applied}")
    revised = str(applied["report_revision_id"])
    checked = reports.validate_section(workspace_id, revised, descriptor["section_id"])
    if not checked.get("success"):
        raise ValueError(f"report remediation validation failed: {checked}")
    return revised


def run_reconstructed_review_closure(
    finance: dict[str, Any],
    report: dict[str, Any],
    *,
    review_key: str,
) -> dict[str, Any]:
    """Create a real finding, patch the report, retest it, and close it."""

    from lvke_mcp.adapters.report_repository import REVISION_STORE
    from lvke_mcp.servers.lvke_deliverable_review import service as review

    workspace_id = str(finance["workspace_id"])
    initial_revision_id = _patch_report_section(
        workspace_id=workspace_id,
        report=report,
        revision_id=str(report["report_revision_id"]),
        section_number=9,
        body="流程验收结论待确认。来源重建不认证项目事实。",
        summary=f"{review_key} 创建审查整改样本",
    )
    project_context = {
        "industry_code": "source-reconstructed-feasibility",
        "project_type": "generic_feasibility",
        "transaction_structure": "new_build",
        "asset_type": "general",
        "evidence_track": "source_reconstructed",
    }
    prepared = review.prepare({
        "workspace_id": workspace_id,
        "target": {"target_type": "report_revision", "target_id": initial_revision_id},
        "project_context": project_context,
        "idempotency_key": f"{review_key}-review-prepare",
    })
    if not prepared.get("success"):
        raise ValueError(f"review preparation failed: {prepared}")
    started = review.start({
        "workspace_id": workspace_id,
        "review_preparation_id": prepared["review_preparation_id"],
        "mode": "quick",
        "execution": "sync",
        "deployment_mode": "enforced",
        "idempotency_key": f"{review_key}-review-start",
    })
    review_id = str(started.get("review_id") or "")
    findings_result = review.list_findings({
        "workspace_id": workspace_id,
        "review_id": review_id,
        "idempotency_key": f"{review_key}-review-list",
    })
    findings = list(findings_result.get("findings") or [])
    finding = next(
        (row for row in findings if str(row.get("rule_id") or "") == "REPORT.PLACEHOLDER"),
        None,
    )
    if finding is None:
        raise ValueError(f"expected review finding was not produced: {findings_result}")
    finding_id = str(finding["finding_id"])
    disposition = review.disposition_finding({
        "workspace_id": workspace_id,
        "review_id": review_id,
        "finding_id": finding_id,
        "disposition": "remediation_in_progress",
        "note": "通过 report_propose_section、report_diff、report_apply 修订结论章节",
        "idempotency_key": f"{review_key}-finding-remediate",
    })
    if not disposition.get("success"):
        raise ValueError(f"finding disposition failed: {disposition}")

    final_revision_id = _patch_report_section(
        workspace_id=workspace_id,
        report=report,
        revision_id=initial_revision_id,
        section_number=9,
        body="流程验收结论已完成整改与复测。来源重建仅用于过程验收，不认证项目事实；原始 BoE 缺失继续列为未解决输入。",
        summary=f"{review_key} 完成审查整改",
    )
    revision = REVISION_STORE.get(workspace_id, final_revision_id) or {}
    remediation_evidence = [{
        "source_id": final_revision_id,
        "locator": "section:9",
        "content_hash": str(revision.get("content_hash") or ""),
    }]
    retested = review.retest({
        "workspace_id": workspace_id,
        "review_id": review_id,
        "target": {"target_type": "report_revision", "target_id": final_revision_id},
        "remediation_evidence": remediation_evidence,
        "mode": "quick",
        "idempotency_key": f"{review_key}-review-retest",
    })
    if not retested.get("success") or finding_id not in (retested.get("closed_finding_ids") or []):
        raise ValueError(f"review retest failed: {retested}")
    retest_review_id = str(retested["retest_review_id"])
    closed = review.disposition_finding({
        "workspace_id": workspace_id,
        "review_id": review_id,
        "finding_id": finding_id,
        "disposition": "resolved",
        "note": "新报告修订已通过同一规则包复测",
        "closure_basis": "REPORT.PLACEHOLDER 在新 revision 中未复现",
        "before_value": finding.get("actual"),
        "after_value": "finding_not_reproduced",
        "remediation_evidence": remediation_evidence,
        "retest_review_id": retest_review_id,
        "idempotency_key": f"{review_key}-finding-close",
    })
    if not closed.get("success"):
        raise ValueError(f"finding closure failed: {closed}")
    return {
        "initial_report_revision_id": initial_revision_id,
        "report_revision_id": final_revision_id,
        "parent_review_id": review_id,
        "review_run_id": retest_review_id,
        "finding_id": finding_id,
        "finding_status": str(closed.get("finding_status") or ""),
        "closed_finding_ids": list(retested.get("closed_finding_ids") or []),
        "validation_status": str(retested.get("validation_status") or ""),
        "overall_verdict": str(retested.get("overall_verdict") or ""),
    }


def run_reconstructed_delivery_release(
    finance: dict[str, Any],
    planning: dict[str, Any],
    research: dict[str, Any],
    review: dict[str, Any],
    *,
    release_key: str,
    unresolved_inputs: list[str],
    business_decision_status: str = "not_applicable",
) -> dict[str, Any]:
    """Bind real MCP objects into one process-acceptance delivery release."""

    from lvke_mcp.runtime.storage import sha256_json
    from lvke_mcp.servers.lvke_feasibility_delivery import service as delivery

    workspace_id = str(finance["workspace_id"])
    reconstruction_records = list(
        finance.get("reconstruction_records") or [finance["reconstruction_record"]]
    )
    reconstruction = dict(reconstruction_records[0])
    started = delivery.start({
        "workspace_id": workspace_id,
        "delivery_mode": "formal_release",
        "project_context_id": planning["project_context_id"],
        "evidence_policy": "source_reconstructed",
        "release_scope": "process_acceptance",
        "project_fact_certified": False,
        "reconstructed_source_ids": [
            item["reconstruction_id"] for item in reconstruction_records
        ],
        "reconstruction_records": reconstruction_records,
        "unresolved_inputs": list(unresolved_inputs),
        "release_limitations": [
            "仅用于 process_acceptance，不认证项目事实",
            f"business_decision_status={business_decision_status}",
        ],
        "idempotency_key": f"{release_key}-delivery-start",
    })
    if not started.get("success"):
        raise ValueError(f"delivery start failed: {started}")
    run_id = str(started["delivery_run_id"])
    stages = [
        (
            "research",
            [planning["project_context_id"], finance["evidence_pack_id"]],
            [research["research_package_id"]],
        ),
        (
            "market",
            [research["research_package_id"], planning["project_context_id"], finance["evidence_pack_id"]],
            [planning["market_case_id"]],
        ),
        ("option", [planning["market_case_id"]], [planning["option_comparison_id"]]),
        (
            "scale",
            [planning["option_comparison_id"], planning["market_case_id"]],
            [planning["build_scale_case_id"]],
        ),
        (
            "drivers",
            [planning["build_scale_case_id"], planning["market_case_id"]],
            [planning["cost_driver_set_id"], planning["labor_plan_id"], planning["revenue_driver_set_id"]],
        ),
        (
            "finance_spec",
            [planning["cost_driver_set_id"], planning["labor_plan_id"], planning["revenue_driver_set_id"], finance["evidence_pack_id"]],
            [
                finance["finance_spec_id"],
                *([finance["basis_of_estimate_id"]] if finance.get("basis_of_estimate_id") else []),
            ],
        ),
        (
            "finance_run",
            [
                finance["finance_spec_id"],
                *([finance["basis_of_estimate_id"]] if finance.get("basis_of_estimate_id") else []),
            ],
            [finance["finance_run_id"]],
        ),
        ("finance_tables", [finance["finance_run_id"]], [finance["finance_tables_package_id"]]),
        (
            "report",
            [finance["finance_tables_package_id"], finance["finance_run_id"]],
            [review["report_revision_id"]],
        ),
        ("review", [review["report_revision_id"]], [review["review_run_id"]]),
    ]
    for stage_name, input_refs, output_refs in stages:
        output_objects = [delivery._resolve_object(workspace_id, str(ref)) for ref in output_refs]  # noqa: SLF001
        if any(item is None for item in output_objects):
            raise ValueError(f"delivery output object unavailable: {stage_name}:{output_refs}")
        stage_basis_hash = sha256_json({
            "input_refs": list(input_refs),
            "output_refs": list(output_refs),
            "output_basis_hashes": sorted(str((item or {}).get("basis_hash") or "") for item in output_objects),
        })
        updated = delivery.stage({
            "workspace_id": workspace_id,
            "delivery_run_id": run_id,
            "stage": stage_name,
            "status": "completed",
            "input_refs": list(input_refs),
            "output_refs": list(output_refs),
            "basis_hash": stage_basis_hash,
            "idempotency_key": f"{release_key}-stage-{stage_name}",
        })
        if not updated.get("success"):
            raise ValueError(f"delivery stage failed: {stage_name}:{updated}")
        run_id = str(updated["delivery_run_id"])
    validated = delivery.validate({
        "workspace_id": workspace_id,
        "delivery_run_id": run_id,
        "scope": "formal",
    })
    if not validated.get("success"):
        raise ValueError(f"delivery formal validation failed: {validated}")
    released = delivery.release({
        "workspace_id": workspace_id,
        "delivery_run_id": run_id,
        "release_scope": "process_acceptance",
        "release_note": "真实对象链来源重建流程验收",
        "idempotency_key": f"{release_key}-release",
    })
    if not released.get("success"):
        raise ValueError(f"delivery release failed: {released}")
    release_payload = dict(released.get("release") or {})
    return {
        "delivery_run_id": str(released["delivery_run_id"]),
        "release_id": str(released["release_id"]),
        "finance_run_id": finance["finance_run_id"],
        "finance_tables_package_id": finance["finance_tables_package_id"],
        "report_revision_id": review["report_revision_id"],
        "review_run_id": review["review_run_id"],
        "lineage_hash": str(release_payload.get("lineage_hash") or ""),
        "release_scope": str(release_payload.get("release_scope") or ""),
        "evidence_policy": str(release_payload.get("evidence_policy") or ""),
        "project_fact_certified": bool(release_payload.get("project_fact_certified")),
        "business_decision_status": business_decision_status,
        "validation": validated,
    }
