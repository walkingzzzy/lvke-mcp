"""Cross-domain orchestration: research, planning context and the finance run.

P1-027: 统一路由解析器。江夏光伏资产收购必须解析为
``project_type=acquisition, transaction_structure=asset_transfer,
asset_type=solar_power, finance_kind=asset_acquisition``；
只有明确的 ``new_build + none`` 才能进入通用模型。
血缘一致性校验在创建下游对象前以 ``PROJECT_ROUTE_CONFLICT`` 阻断。
"""

from __future__ import annotations

from datetime import date
from typing import Any


from lvke_mcp.runtime.storage import sha256_json
from lvke_mcp.domains.reports._doc_service.outline import REPORT_CHAPTERS

from .assumptions import _field_values
from .finance_align import _scenario_inputs

# 光伏资产收购关键词：用于路由解析器识别项目类型
_ACQUISITION_KEYWORDS = frozenset({
    "收购", "资产收购", "股权收购", "并购", "转让", "购买",
    "acquisition", "asset_transfer", "equity_transfer",
})
_SOLAR_KEYWORDS = frozenset({
    "光伏", "太阳能", "电站", "光伏发电", "solar", "photovoltaic",
    "pv", "江夏",
})
_NEW_BUILD_KEYWORDS = frozenset({
    "新建", "建设", "新建项目", "新建设", "new_build", "greenfield",
})


def _resolve_project_route(intent: dict[str, Any], sentence: str) -> dict[str, Any]:
    """统一路由解析器：从 intent 和原始句子推导项目路线。

    Returns:
        dict with keys: project_type, transaction_structure, asset_type,
        finance_kind, finance_kind_label, route_source, confidence
    """
    haystack = f"{sentence} {intent.get('project_name', '')} {intent.get('project_nature', '')}".lower()
    industry = intent.get("industry") or {}
    industry_code = str(industry.get("industry_code") or "").lower()

    # 默认值（通用新建）
    result = {
        "project_type": "new_build",
        "transaction_structure": "none",
        "asset_type": "general",
        "finance_kind": "generic_feasibility",
        "finance_kind_label": "通用可行性研究",
        "route_source": "default",
        "confidence": 0.5,
    }

    # 检测光伏收购
    has_acquisition = any(kw in haystack for kw in _ACQUISITION_KEYWORDS)
    has_solar = any(kw in haystack for kw in _SOLAR_KEYWORDS)

    if has_acquisition and has_solar:
        result.update({
            "project_type": "acquisition",
            "transaction_structure": "asset_transfer",
            "asset_type": "solar_power",
            "finance_kind": "asset_acquisition",
            "finance_kind_label": "资产收购（光伏）",
            "route_source": "keyword_match",
            "confidence": 0.95,
        })
        return result

    # 仅含收购关键词（非光伏资产收购，如酒店收购）
    if has_acquisition:
        result.update({
            "project_type": "acquisition",
            "transaction_structure": "asset_transfer",
            "asset_type": "general",
            "finance_kind": "asset_acquisition",
            "finance_kind_label": "资产收购（通用）",
            "route_source": "keyword_match",
            "confidence": 0.85,
        })
        return result

    # 仅含光伏关键词（非收购，可能是新建光伏电站）
    if has_solar:
        result.update({
            "asset_type": "solar_power",
            "route_source": "keyword_match",
            "confidence": 0.75,
        })
        return result

    # 显式声明新建的项目
    if any(kw in haystack for kw in _NEW_BUILD_KEYWORDS):
        result["route_source"] = "keyword_match"
        result["confidence"] = 0.7
        return result

    return result


def _check_route_consistency(
    route: dict[str, Any],
    project_context: dict[str, Any],
    finance_kind: str,
) -> list[str]:
    """检查血缘一致性。任何冲突以 PROJECT_ROUTE_CONFLICT 阻断。"""
    blockers: list[str] = []
    expected_route = route.get("finance_kind", "generic_feasibility")
    ctx_project_type = str(project_context.get("project_type") or "")
    ctx_transaction = str(project_context.get("transaction_structure") or "")

    # 路线冲突：下游对象 project_type 与路由推断不一致
    if expected_route == "asset_acquisition" and ctx_project_type != "acquisition":
        blockers.append(
            f"PROJECT_ROUTE_CONFLICT:route={expected_route},"
            f"context.project_type={ctx_project_type}"
        )
    if expected_route == "generic_feasibility" and ctx_project_type == "acquisition":
        blockers.append(
            f"PROJECT_ROUTE_CONFLICT:route={expected_route},"
            f"context.project_type={ctx_project_type}"
        )

    # finance_kind 不一致
    if finance_kind and finance_kind != expected_route:
        blockers.append(
            f"PROJECT_ROUTE_CONFLICT:route={expected_route},"
            f"finance_kind={finance_kind}"
        )

    # 光伏收购要求 transaction_structure=asset_transfer
    if route.get("asset_type") == "solar_power" and expected_route == "asset_acquisition":
        if ctx_transaction not in ("asset_transfer", ""):
            blockers.append(
                f"PROJECT_ROUTE_CONFLICT:solar_acquisition_expects_asset_transfer,"
                f"got={ctx_transaction}"
            )

    return blockers


def _start_research(
    workspace_id: str,
    intent: dict[str, Any],
    route: dict[str, Any],
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    from lvke_mcp.domains.research import application as research

    industry = dict(intent.get("industry") or {})
    research_mode = route.get("finance_kind", "generic_feasibility")
    # 光伏收购项目使用 project_delivery 研究模式
    if research_mode == "asset_acquisition":
        research_mode = "project_delivery"
    return research.start_agent(
        {
            "workspace_id": workspace_id,
            "topic": f"{intent.get('project_name')}公开研究缺口登记",
            "industry": industry.get("industry_label"),
            "region": intent.get("region") or "待确认",
            "profile": "quick",
            "verify_urls": True,
            "research_mode": research_mode,
            "research_brief": {
                "purpose": "登记零材料技术预估所需的公开研究缺口",
                "evidence_boundary": "会话仅用于采集公开来源；未提交带 locator 的来源前不得形成 ResearchPackage。",
            },
            "plan_items": [
                "识别行业、地区和政策公开资料",
                "收集可验证来源及其 locator",
                "缺少来源时保持研究会话和下游报告为 partial",
            ],
            "subqueries": ["行业公开资料", "地区政策与统计口径", "可比项目公开信息"],
            "source_policy": {"public_sources_only": True},
            "idempotency_key": idempotency_key,
        }
    )


def _create_project_context(
    workspace_id: str,
    intent: dict[str, Any],
    route: dict[str, Any],
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    from lvke_mcp.domains.project_planning import application as planning

    industry = dict(intent.get("industry") or {})
    project_type = route.get("project_type", "new_build")
    transaction_structure = route.get("transaction_structure", "none")
    asset_type = route.get("asset_type", "general")
    evidence_track = "controlled_assumption"
    if route.get("finance_kind") == "asset_acquisition":
        evidence_track = "source_reconstructed"
    tags = ["zero_material", "estimate_preview"]
    if route.get("asset_type") == "solar_power":
        tags.append("solar_power")
    if route.get("finance_kind") == "asset_acquisition":
        tags.append("asset_acquisition")
    return planning.create_project_context(
        workspace_id,
        {
            "project_name": intent.get("project_name"),
            "industry_code": industry.get("industry_code"),
            "project_type": project_type,
            "region": intent.get("region") or "待确认",
            "objective": "形成零材料技术预估和关键参数确认项",
            "report_type": "feasibility_study",
            "transaction_structure": transaction_structure,
            "asset_type": asset_type,
            "evidence_track": evidence_track,
            "description": intent.get("sentence"),
            "tags": tags,
        },
        idempotency_key=idempotency_key,
    )


def _solar_acquisition_spec(
    intent: dict[str, Any], assumption_package: dict[str, Any],
) -> dict[str, Any]:
    """Build the acquisition-domain solar spec from explicit/controlled inputs."""
    values = _field_values(assumption_package)
    assumption_units = {
        str(item.get("name")): str(item.get("unit") or "")
        for item in assumption_package.get("fields") or []
        if isinstance(item, dict)
    }

    def number(*names: str, default: float = 0.0) -> float:
        for name in names:
            value = values.get(name)
            if isinstance(value, (int, float)) and float(value) > 0:
                return float(value)
        return default

    purchase = number("purchase_price_wan", "purchase_price", "acquisition_price_wan", default=5200.0)
    capacity = number("installed_capacity_mw", "capacity_mw", default=10.0)
    generation = number("annual_generation_mwh", "generation_mwh", default=11500.0)
    tariff = number("tariff_yuan_per_kwh", "tariff", default=0.42)
    utilization = number("utilization_hours", default=generation / max(capacity, 1.0))
    target_irr = number("target_project_irr", default=0.08)
    minimum_dscr = number("minimum_dscr", default=1.2)
    opex = number("annual_opex_wan", "annual_opex", default=40.0)
    maintenance = number("maintenance_capex_wan", "maintenance_capex", default=10.0)
    years = int(number("remaining_operating_years", default=20.0))
    financing_ratio = number("financing_ratio", "loan_ratio", default=0.60)
    interest_rate = number("interest_rate", "loan_rate", default=0.05)
    tenor = int(number("tenor", "loan_years", default=10.0))
    controlled = [
        {
            "field": field, "value": value, "unit": assumption_units.get(source_name) or unit,
            "basis": "零材料受控行业种子或句中明确输入，无项目原始资料支撑",
            "impact": impact, "sensitivity": sensitivity,
            "validation_condition": "须以权属、并网协议、PPA、历史发电与财务报表替换后重算",
        }
        for field, value, unit, impact, sensitivity, source_name in (
            ("transaction.purchase_price", purchase, "万元", "决定收购成本与全部收益指标", "critical", "purchase_price_wan"),
            ("solar_operation.installed_capacity_mw", capacity, "MW", "决定发电规模与折旧基数", "critical", "installed_capacity_mw"),
            ("solar_operation.annual_generation_mwh", generation, "MWh", "直接决定售电收入", "critical", "annual_generation_mwh"),
            ("solar_operation.tariff_yuan_per_kwh", tariff, "元/kWh", "直接决定售电单价", "critical", "tariff_yuan_per_kwh"),
            ("solar_operation.annual_opex_wan", opex, "万元/年", "影响经营净现金流", "high", "annual_opex_wan"),
            ("solar_operation.maintenance_capex_wan", maintenance, "万元/年", "影响维护资本支出", "medium", "maintenance_capex_wan"),
            ("solar_operation.remaining_operating_years", years, "年", "决定运营期与退出年度", "high", "remaining_operating_years"),
            ("transaction.financing_ratio", financing_ratio, "比例", "决定杠杆与偿债压力", "high", "financing_ratio"),
            ("transaction.interest_rate", interest_rate, "比例/年", "决定利息与 DSCR", "high", "interest_rate"),
            ("transaction.tenor", tenor, "年", "决定偿债期限分布", "medium", "tenor"),
            ("decision_thresholds.target_project_irr", target_irr, "比例", "作为收益判据门槛", "high", "target_project_irr"),
            ("decision_thresholds.minimum_dscr", minimum_dscr, "倍", "作为偿债安全门槛", "high", "minimum_dscr"),
        )
    ]
    return {
        "version": "finance_spec.v3", "finance_kind": "asset_acquisition",
        "delivery_mode": "estimate_preview", "controlled_assumptions": controlled,
        "asset_type": "solar_power", "industry": "solar_power",
        "invest_type": "asset_acquisition", "confirmation_status": "candidate",
        "selected_scenario_id": "base",
        "solar_operation": {
            "installed_capacity_mw": capacity, "annual_generation_mwh": generation,
            "utilization_hours": utilization, "tariff_yuan_per_kwh": tariff,
            "annual_opex_wan": opex, "maintenance_capex_wan": maintenance,
            "remaining_operating_years": years, "curtailment_rate": 0.0,
            "degradation_rate": 0.005, "evidence_ids": [],
        },
        "transaction": {
            "acquisition_type": "asset", "purchase_price": purchase,
            "transaction_taxes": {}, "tax_burden_party": "buyer",
            "asset_scope": [{"scope_id": "solar-asset", "type": "solar_power_asset",
                             "included": True, "status": "pending",
                             "accounting_treatment": "depreciable",
                             "depreciable_basis_wan": purchase,
                             "depreciation_years": years, "evidence_ids": []}],
            "closing_date": date.today().isoformat(),
            "model_start_date": date.today().isoformat(),
            "valuation_value": purchase, "valuation_date": date.today().isoformat(),
            "financing_ratio": financing_ratio, "interest_rate": interest_rate,
            "tenor": tenor, "repayment": "equal_principal",
            "exit_value": 0.0, "exit_year": years, "closing_conditions": [],
            "veto_items": [], "calculation_granularity": "annual",
        },
        "financing": {}, "tax": {"income_tax_rate": 0.25},
        "decision_thresholds": {"target_project_irr": target_irr, "minimum_dscr": minimum_dscr},
        "project_parties": [], "historical_statements": [],
        "evidence_links": {}, "evidence_policy": "controlled_assumption",
        "project_fact_certified": False,
        "unresolved_inputs": ["权属、并网协议、PPA、历史发电和财务报表待补齐"],
        "release_limitations": ["零材料受控假设仅用于技术预览"],
    }


def _execute_solar_acquisition_preview(
    workspace_id: str, intent: dict[str, Any], assumption_package: dict[str, Any],
    route: dict[str, Any], research: dict[str, Any], project_context: dict[str, Any],
    lineage_key: str,
) -> dict[str, Any]:
    """Run Jiangxia through the dedicated acquisition service, never generic finance."""
    from lvke_mcp.servers.lvke_asset_acquisition import service as acquisition

    spec = _solar_acquisition_spec(intent, assumption_package)
    checked = acquisition.validate_spec(spec)
    if not checked.get("valid"):
        return {
            "status": "model_blocked", "route": route, "finance_kind": "asset_acquisition",
            "acceptance_level": "technical_pass", "system_success": True,
            "business_success": False, "formal_ready": False, "research": research,
            "project_context": project_context, "finance_validation": checked,
            "blockers": list(checked.get("blockers") or ["acquisition_spec_invalid"]),
            "object_refs": {"research_task_id": str(research.get("task_id") or ""),
                            "project_context_id": str(project_context.get("project_context_id") or "")},
        }
    saved = acquisition.save_spec(workspace_id, spec, f"zmd-acq-save-{lineage_key}")
    if not saved.get("spec_id"):
        return {"status": "model_blocked", "route": route, "finance_kind": "asset_acquisition",
                "system_success": True, "business_success": False, "formal_ready": False,
                "research": research, "project_context": project_context, "spec": saved,
                "blockers": list(saved.get("blockers") or ["acquisition_spec_save_failed"])}
    confirmed = acquisition.confirm_spec(workspace_id, str(saved["spec_id"]),
                                         "零材料光伏收购技术预览", f"zmd-acq-confirm-{lineage_key}",
                                         confirmation_scope="project_candidate")
    if not confirmed.get("spec_id"):
        return {"status": "model_blocked", "route": route, "finance_kind": "asset_acquisition",
                "system_success": True, "business_success": False, "formal_ready": False,
                "research": research, "project_context": project_context, "spec": saved,
                "confirmation": confirmed, "blockers": list(confirmed.get("blockers") or ["acquisition_spec_confirm_failed"])}
    run = acquisition.run_model(workspace_id, str(confirmed["spec_id"]), 0.08, "base",
                                f"zmd-acq-run-{lineage_key}")
    run_id = str(run.get("run_id") or "")
    if not run_id:
        return {"status": "model_blocked", "route": route, "finance_kind": "asset_acquisition",
                "system_success": True, "business_success": False, "formal_ready": False,
                "research": research, "project_context": project_context, "spec": confirmed,
                "run": run, "blockers": list(run.get("blockers") or ["acquisition_run_failed"])}
    package = acquisition.render_tables(workspace_id, run_id, f"zmd-acq-tables-{lineage_key}")
    package_id = str(package.get("finance_tables_package_id") or package.get("acquisition_tables_package_id") or "")
    csv_export = acquisition.export_tables_csv(workspace_id, package_id, f"zmd-acq-csv-{lineage_key}") if package_id else {}
    xlsx_export = acquisition.export_tables(workspace_id, package_id, f"zmd-acq-xlsx-{lineage_key}") if package_id else {}
    return {
        "status": "upstream_partial", "route": route, "finance_kind": "asset_acquisition",
        "acceptance_level": "technical_pass", "system_success": True, "business_success": False,
        "formal_ready": False, "research": research, "project_context": project_context,
        "finance_spec": confirmed, "finance_run": run, "tables": package,
        "csv_export": csv_export, "xlsx_export": xlsx_export,
        "report_preparation": {"finance_binding": {"kind": "asset_acquisition", "run_id": run_id,
                                                       "package_id": package_id},
                               "research_package_ids": [], "outline": list(REPORT_CHAPTERS)},
        "blockers": ["research_evidence_pending", "project_fact_evidence_pending"],
        "object_refs": {"research_task_id": str(research.get("task_id") or ""),
                        "project_context_id": str(project_context.get("project_context_id") or ""),
                        "acquisition_spec_id": str(confirmed["spec_id"]),
                        "acquisition_run_id": run_id,
                        "acquisition_tables_package_id": package_id},
    }


def execute(
    workspace_id: str,
    intent: dict[str, Any],
    assumption_package: dict[str, Any],
    *,
    operation_key: str,
) -> dict[str, Any]:
    """Execute only through existing domain boundaries; never grant release."""

    from lvke_mcp.domains.finance import model_application as finance
    from lvke_mcp.domains.finance import tables_service as tables

    lineage_key = sha256_json(
        {
            "intent_id": intent.get("delivery_intent_id"),
            "assumption_package_id": assumption_package.get("assumption_package_id"),
            "operation_key": operation_key,
        }
    ).removeprefix("sha256:")[:24]

    # P1-027: 解析项目路由，替代硬编码 new_build + none
    sentence = str(intent.get("sentence") or "")
    route = _resolve_project_route(intent, sentence)
    finance_kind = route.get("finance_kind", "generic_feasibility")

    research = _start_research(
        workspace_id,
        intent,
        route,
        idempotency_key=f"zmd-research-{lineage_key}",
    )
    project_context = _create_project_context(
        workspace_id,
        intent,
        route,
        idempotency_key=f"zmd-context-{lineage_key}",
    )

    # P1-027: 血缘一致性校验
    route_blockers = _check_route_consistency(
        route, project_context, finance_kind
    )
    if route_blockers:
        return {
            "status": "route_conflict",
            "stage": "planning_ready",
            "route": route,
            "research": research,
            "project_context": project_context,
            "blockers": route_blockers,
            "warnings": ["项目路由解析与下游对象存在血缘冲突，禁止回落通用模型"],
            "object_refs": {
                "research_task_id": str(research.get("task_id") or ""),
                "project_context_id": str(project_context.get("project_context_id") or ""),
            },
            "resource_uris": [
                *list(research.get("resource_uris") or []),
                *list(project_context.get("resource_uris") or []),
            ],
        }

    if finance_kind == "asset_acquisition" and route.get("asset_type") == "solar_power":
        return _execute_solar_acquisition_preview(
            workspace_id, intent, assumption_package, route, research,
            project_context, lineage_key,
        )

    spec, finance_inputs, scenario_context = _scenario_inputs(assumption_package)
    # 注入 finance_kind 到 spec 中
    if isinstance(spec, dict):
        spec["invest_type"] = finance_kind
        spec["finance_kind"] = finance_kind

    validation = finance.validate_spec({"spec": spec, "for_formal": False})
    if not validation.get("valid"):
        return {
            "status": "model_blocked",
            "stage": "planning_ready",
            "route": route,
            "research": research,
            "project_context": project_context,
            "finance_validation": validation,
            "blockers": ["finance_spec_validation_failed", *list(validation.get("blockers") or [])],
            "warnings": [],
            "object_refs": {
                "research_task_id": str(research.get("task_id") or ""),
                "project_context_id": str(project_context.get("project_context_id") or ""),
            },
            "resource_uris": [
                *list(research.get("resource_uris") or []),
                *list(project_context.get("resource_uris") or []),
            ],
        }
    prepared = finance.prepare_spec(
        {
            "workspace_id": workspace_id,
            "spec": spec,
            "input_revision": finance_inputs,
            "evidence_pack_ids": [],
        }
    )
    # FinanceRun 之前做尺度对账：算术自洽不代表业务尺度成立。
    from .scale_guard import check_project_scale

    scale_check = check_project_scale(
        industry_code=str((intent.get("industry") or {}).get("industry_code") or ""),
        explicit_inputs=intent.get("explicit_inputs"),
        field_values=_field_values(assumption_package),
        project_context=project_context,
        input_revision={**dict(spec), "input_revision": dict(finance_inputs or {})},
    )
    candidate_spec_id = str(prepared.get("spec_id") or "")
    if not candidate_spec_id:
        preparation_blockers = ["finance_spec_prepare_failed", *list(prepared.get("blockers") or [])]
        if not scale_check["ok"]:
            preparation_blockers.extend(
                sorted({str(item.get("code")) for item in scale_check["issues"]})
            )
        return {
            "status": "model_blocked",
            "stage": "planning_ready",
            "route": route,
            "research": research,
            "project_context": project_context,
            "finance_preparation": prepared,
            "blockers": preparation_blockers,
            "warnings": [str(item.get("detail")) for item in scale_check["advisories"]],
            "object_refs": {
                "research_task_id": str(research.get("task_id") or ""),
                "project_context_id": str(project_context.get("project_context_id") or ""),
            },
            "resource_uris": [
                *list(research.get("resource_uris") or []),
                *list(project_context.get("resource_uris") or []),
                *list(prepared.get("resource_uris") or []),
            ],
        }
    confirmed = finance.confirm_spec(
        {
            "workspace_id": workspace_id,
            "spec_id": candidate_spec_id,
            "note": "零材料技术预估使用受控假设确认 FinanceSpec；结果仅绑定当前输入快照。",
            "idempotency_key": f"zmd-confirm-{lineage_key}",
        }
    )
    confirmed_spec_id = str(confirmed.get("spec_id") or "")
    if not confirmed_spec_id:
        return {
            "status": "model_blocked",
            "stage": "planning_ready",
            "route": route,
            "research": research,
            "project_context": project_context,
            "finance_preparation": prepared,
            "finance_confirmation": confirmed,
            "blockers": ["finance_spec_confirm_failed", *list(confirmed.get("blockers") or [])],
            "warnings": [],
            "object_refs": {
                "research_task_id": str(research.get("task_id") or ""),
                "project_context_id": str(project_context.get("project_context_id") or ""),
                "finance_candidate_spec_id": candidate_spec_id,
            },
            "resource_uris": [
                *list(research.get("resource_uris") or []),
                *list(project_context.get("resource_uris") or []),
                *list(prepared.get("resource_uris") or []),
                *list(confirmed.get("resource_uris") or []),
            ],
        }
    if not scale_check["ok"]:
        return {
            "status": "model_blocked",
            "stage": "planning_ready",
            "route": route,
            "research": research,
            "project_context": project_context,
            "finance_preparation": prepared,
            "finance_confirmation": confirmed,
            "scale_check": scale_check,
            "blockers": [
                *sorted({str(item.get("code")) for item in scale_check["issues"]}),
            ],
            "warnings": [str(item.get("detail")) for item in scale_check["advisories"]],
            "object_refs": {
                "research_task_id": str(research.get("task_id") or ""),
                "project_context_id": str(project_context.get("project_context_id") or ""),
                "finance_candidate_spec_id": candidate_spec_id,
                "finance_confirmed_spec_id": confirmed_spec_id,
            },
            "resource_uris": [
                *list(research.get("resource_uris") or []),
                *list(project_context.get("resource_uris") or []),
                *list(prepared.get("resource_uris") or []),
                *list(confirmed.get("resource_uris") or []),
            ],
        }
    finance_run = finance.run_model(
        {
            "workspace_id": workspace_id,
            "spec_id": confirmed_spec_id,
            "mode": "estimate_preview",
            "valuation_date": date.today().isoformat(),
            "selected_scenario_id": scenario_context["scenario_id"],
            "idempotency_key": f"zmd-finance-run-{lineage_key}",
        }
    )
    finance_run_id = str(finance_run.get("run_id") or "")
    if not finance_run_id:
        return {
            "status": "model_blocked",
            "stage": "planning_ready",
            "route": route,
            "research": research,
            "project_context": project_context,
            "finance_preparation": prepared,
            "finance_confirmation": confirmed,
            "finance_run": finance_run,
            "blockers": ["finance_run_failed", *list(finance_run.get("blockers") or [])],
            "warnings": [],
            "object_refs": {
                "research_task_id": str(research.get("task_id") or ""),
                "project_context_id": str(project_context.get("project_context_id") or ""),
                "finance_spec_id": confirmed_spec_id,
            },
            "resource_uris": [
                *list(research.get("resource_uris") or []),
                *list(project_context.get("resource_uris") or []),
                *list(confirmed.get("resource_uris") or []),
                *list(finance_run.get("resource_uris") or []),
            ],
        }
    rendered = tables.render(workspace_id, finance_run_id)
    package_id = str(rendered.get("finance_tables_package_id") or "")
    if not package_id:
        return {
            "status": "artifact_failed",
            "stage": "finance_ready",
            "route": route,
            "research": research,
            "project_context": project_context,
            "finance_run": finance_run,
            "tables": rendered,
            "blockers": ["finance_tables_render_failed", *list(rendered.get("blockers") or [])],
            "warnings": [],
            "object_refs": {
                "research_task_id": str(research.get("task_id") or ""),
                "project_context_id": str(project_context.get("project_context_id") or ""),
                "finance_spec_id": confirmed_spec_id,
                "finance_run_id": finance_run_id,
            },
            "resource_uris": [
                *list(finance_run.get("resource_uris") or []),
                *list(rendered.get("resource_uris") or []),
            ],
        }
    csv_export = tables.export_csv(workspace_id, finance_run_id)
    xlsx_export = tables.export_xlsx(workspace_id, finance_run_id)
    from lvke_mcp.domains.reports import application as report_generation

    # P1-027: 根据路由选择 finance_binding kind
    finance_binding_kind = "asset_acquisition" if finance_kind == "asset_acquisition" else "generic_feasibility"
    # 资产收购使用 acquisition report type
    report_kind = "asset_acquisition" if finance_kind == "asset_acquisition" else "generic_feasibility"
    report_preparation = report_generation.prepare(
        {
            "workspace_id": workspace_id,
            "evidence_pack_ids": [],
            "research_package_ids": [],
            "finance_binding": {
                "kind": finance_binding_kind,
                "run_id": finance_run_id,
                "package_id": package_id,
            },
            "outline": [
                "项目识别与交付边界",
                "受控假设与关键参数确认",
                "财务技术预估",
                "十三表与工件清单",
                "资料缺口与后续行动",
            ],
            "template_version": "zero-material-estimate-preview.v1",
        }
    )
    export_blockers = [
        *([] if csv_export.get("csv_resource_uris") else ["finance_tables_csv_export_failed"]),
        *([] if xlsx_export.get("xlsx_resource") else ["finance_tables_xlsx_export_failed"]),
    ]
    return {
        "status": "upstream_partial" if not export_blockers else "artifact_failed",
        "stage": "tables_ready" if not export_blockers else "finance_ready",
        "route": route,
        "finance_kind": finance_kind,
        "acceptance_level": "technical_pass",
        "system_success": True,
        "business_success": False,
        "formal_ready": False,
        "research": research,
        "project_context": project_context,
        "finance_preparation": prepared,
        "finance_confirmation": confirmed,
        "finance_run": finance_run,
        "tables": rendered,
        "csv_export": csv_export,
        "xlsx_export": xlsx_export,
        "report_preparation": report_preparation,
        "blockers": [
            "research_evidence_pending",
            "planning_market_evidence_pending",
            *list(report_preparation.get("blockers") or []),
            *export_blockers,
        ],
        "warnings": [
            "研究、规划与财务由 MCP 状态机编排，不依赖自由文本编排。",
            "受控假设只能用于 estimate_preview，不得升级为正式项目证据。",
        ],
        "object_refs": {
            "research_task_id": str(research.get("task_id") or ""),
            "project_context_id": str(project_context.get("project_context_id") or ""),
            "finance_spec_id": confirmed_spec_id,
            "finance_run_id": finance_run_id,
            "finance_tables_package_id": package_id,
            "csv_manifest_id": str(csv_export.get("csv_manifest_id") or ""),
            "report_preparation_id": str(report_preparation.get("report_preparation_id") or ""),
        },
        "resource_uris": sorted(
            {
                *list(research.get("resource_uris") or []),
                *list(project_context.get("resource_uris") or []),
                *list(prepared.get("resource_uris") or []),
                *list(confirmed.get("resource_uris") or []),
                *list(finance_run.get("resource_uris") or []),
                *list(rendered.get("resource_uris") or []),
                *list(csv_export.get("resource_uris") or []),
                *list(xlsx_export.get("resource_uris") or []),
                *list(report_preparation.get("resource_uris") or []),
            }
            - {""}
        ),
    }