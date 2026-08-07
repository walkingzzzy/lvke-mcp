"""报告数据构造与 markdown 渲染。"""

from __future__ import annotations

import copy
import json
from typing import Any



from .base import (
    _hash,
    _num,
    _pct,
    _pct_ratio,
)

from .evidence import (
    _current_evidence_matches_run,
)

from .store import (
    _load,
)


def build_acquisition_report_data(
    workspace_id: str,
    run: dict[str, Any],
) -> dict[str, Any]:
    """Project a bound, validated run/spec into the acquisition report contract."""

    state = _load(workspace_id)
    spec_row = state["specs"].get(str(run.get("spec_id") or "")) or {}
    spec = copy.deepcopy(spec_row.get("spec") or {})
    if not isinstance(spec, dict) or _hash(spec) != run.get("spec_hash"):
        raise RuntimeError("run spec snapshot is missing or does not match spec_hash")
    evidence_ok, current_evidence = _current_evidence_matches_run(
        workspace_id,
        run,
        spec,
    )
    if not evidence_ok:
        raise RuntimeError(
            "run evidence binding is stale or no longer valid: "
            f"snapshot={run.get('evidence_binding_hash')} current={current_evidence.get('binding_hash')}"
        )
    transaction = copy.deepcopy(spec.get("transaction") or {})
    source_ledger = [
        {
            "field": binding.get("source_path"),
            "evidence_ids": [binding.get("evidence_id")],
            "source": {
                "file_id": binding.get("file_id"),
                "locator": binding.get("locator"),
                "source_sha256": binding.get("source_sha256"),
                "source_size_bytes": binding.get("source_size_bytes"),
                "parse_job": binding.get("parse_job"),
                "attempt": binding.get("attempt"),
                "evidence_content_hash": binding.get("evidence_content_hash"),
                "binding_hash": binding.get("binding_hash"),
            },
            "validation_status": "bound",
        }
        for binding in current_evidence.get("bindings") or []
    ]
    parties = copy.deepcopy(spec.get("project_parties") or [])
    licenses = copy.deepcopy(transaction.get("licenses") or [])
    for party in parties:
        if isinstance(party, dict) and "license_holder" in (party.get("roles") or []):
            licenses.append({
                "license_type": "license_holder_role",
                "holder_id": party.get("entity_id"),
                "holder_name": party.get("name"),
                "status": party.get("status"),
                "evidence_ids": copy.deepcopy(party.get("evidence_ids") or []),
            })
    matrices = [
        copy.deepcopy(value)
        for value in state["scenario_matrices"].values()
        if value.get("run_id") == run.get("run_id")
        and value.get("spec_hash") == run.get("spec_hash")
        and value.get("input_hash") == run.get("input_hash")
    ]
    matrices.sort(key=lambda value: str(value.get("created_at") or ""))
    result = run.get("result") or {}
    asset_type = str(result.get("asset_type") or spec.get("asset_type") or "hotel_lease")
    valuation = float(transaction.get("valuation_value") or 0.0)
    purchase = float(result.get("purchase_price_wan") or transaction.get("purchase_price") or 0.0)
    report = {
        "schema_version": "asset_acquisition_report_data.v1",
        "asset_type": asset_type,
        "bindings": {
            "workspace_id": workspace_id,
            "run_id": run.get("run_id"),
            "spec_id": run.get("spec_id"),
            "spec_hash": run.get("spec_hash"),
            "input_hash": run.get("input_hash"),
            "model_version": run.get("model_version"),
            "spec_snapshot_hash": run.get("spec_snapshot_hash"),
            "evidence_binding_version": run.get("evidence_binding_version"),
            "evidence_binding_hash": run.get("evidence_binding_hash"),
            "validation_status": run.get("validation_status"),
            "consistency_ok": bool(run.get("consistency_ok")),
        },
        "source_processing_ledger": source_ledger,
        "party_relationships": parties,
        "asset_boundary": copy.deepcopy(transaction.get("asset_scope") or []),
        "license_ledger": licenses,
        "historical_financial_comparison": copy.deepcopy(spec.get("historical_statements") or []),
        "valuation_transaction_bridge": {
            "valuation_value_wan": valuation,
            "purchase_price_wan": purchase,
            "purchase_price_vs_valuation_wan": purchase - valuation,
            "transaction_tax_wan": result.get("transaction_tax_wan"),
            "total_acquisition_cost_wan": result.get("total_acquisition_cost_wan"),
            "valuation_date": transaction.get("valuation_date"),
            "closing_date": transaction.get("closing_date"),
        },
        "maximum_acceptable_price": copy.deepcopy(
            run.get("max_acquisition_price_analysis")
            or {"status": "not_calculated", "validation_status": "not_run"}
        ),
        "scenario_matrices": matrices,
        "red_flags": copy.deepcopy(transaction.get("red_flags") or []),
        "closing_conditions": copy.deepcopy(transaction.get("closing_conditions") or []),
        "veto_items": copy.deepcopy(transaction.get("veto_items") or []),
        "validation_summary": {
            "validation_status": run.get("validation_status"),
            "consistency_ok": bool(run.get("consistency_ok")),
            "formal_spec_valid": bool(run.get("formal_spec_valid")),
            "evidence_status": current_evidence.get("status"),
            "evidence_formal_ok": bool(current_evidence.get("formal_ok")),
            "evidence_binding_hash": current_evidence.get("binding_hash"),
            "open_blocking_issues": [
                copy.deepcopy(issue) for issue in (run.get("issues") or [])
                if issue.get("blocking") and issue.get("status") == "open"
            ],
        },
    }
    if asset_type == "solar_power":
        report["solar_operation"] = copy.deepcopy(result.get("solar_operation") or {})
        report["solar_operating_ledger"] = copy.deepcopy(result.get("annual_summary") or [])
    else:
        report["lease_ledger"] = copy.deepcopy(
            (spec.get("lease_portfolio") or {}).get("units") or []
        )
    report["report_data_hash"] = _hash(report)
    return report


def render_markdown(run: dict[str, Any], report_data: dict[str, Any] | None = None) -> str:
    result = run.get("result") or {}
    ind = result.get("indicators") or {}
    report_data = report_data or {}
    asset_type = str(result.get("asset_type") or report_data.get("asset_type") or "hotel_lease")
    is_solar = asset_type == "solar_power"

    def text(value: Any) -> str:
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return str(value if value not in (None, "") else "—").replace("|", "¦").replace("\n", " ")

    lines = [
        "# 资产收购可行性研究报告",
        "",
        f"> 财务运行：`{run.get('run_id')}`；场景：`{run.get('scenario_id')}`；模型：`{run.get('model_version')}`",
        f"> Spec：`{run.get('spec_hash')}`；事实版本：`{run.get('spec_id')}`",
        f"> 证据绑定：`{run.get('evidence_binding_hash')}`；版本：`{run.get('evidence_binding_version')}`",
        "",
        "## 一、交易概览",
        "",
        f"- 收购价格：{result.get('purchase_price_wan', 0):,.2f} 万元",
        f"- 交易税费：{result.get('transaction_tax_wan', 0):,.2f} 万元",
        f"- 总收购成本：{result.get('total_acquisition_cost_wan', 0):,.2f} 万元",
        "",
        "## 二、核心财务指标",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 项目 IRR | {_pct(ind.get('project_irr_pct'))} |",
        f"| 资本金 IRR | {_pct(ind.get('equity_irr_pct'))} |",
        f"| NPV | {_num(ind.get('npv_wan'))} 万元 |",
        f"| 最低 DSCR | {_num(ind.get('minimum_dscr'))} |",
        f"| 最低 ICR | {_num(ind.get('minimum_icr'))} |",
    ]
    if is_solar:
        solar = result.get("solar_operation") or {}
        lines.extend([
            "",
            "## 三、光伏电站运营",
            "",
            f"- 装机容量：{_num(solar.get('installed_capacity_mw'))} MW",
            f"- 基准发电量：{_num(solar.get('base_generation_mwh'))} MWh",
            f"- 上网电价：{_num(solar.get('tariff_yuan_per_kwh'))} 元/kWh",
            f"- 限电率：{_pct_ratio(solar.get('curtailment_rate'))}",
            f"- 年衰减率：{_pct_ratio(solar.get('degradation_rate'))}",
            "",
            "| 年度 | 理论发电量(MWh) | 上网电量(MWh) | 售电收入(万元) | 运维费(万元) | 维护性资本开支(万元) | 所得税(万元) | 债务服务(万元) | 项目现金流(万元) | 资本金现金流(万元) |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for row in result.get("annual_summary") or []:
            lines.append(
                f"| {text(row.get('year'))} | {_num(row.get('gross_generation_mwh'))} | "
                f"{_num(row.get('sold_generation_mwh'))} | {_num(row.get('revenue_wan'))} | "
                f"{_num(row.get('operating_cost_wan'))} | {_num(row.get('maintenance_capex_wan'))} | "
                f"{_num(row.get('income_tax_wan'))} | {_num(row.get('debt_service_wan'))} | "
                f"{_num(row.get('project_cf_wan'))} | {_num(row.get('equity_cf_wan'))} |"
            )
    else:
        lines.extend([
            f"| 最低租金覆盖率 | {_num(ind.get('minimum_tenant_rent_coverage'))} |",
            f"| 租约覆盖年限 | {_num(ind.get('lease_coverage_years'))} 年 |",
            f"| 合同收入占比 | {_pct_ratio(ind.get('contract_income_ratio'))} |",
            f"| 未锁定收入占比 | {_pct_ratio(ind.get('unlocked_income_ratio'))} |",
            "",
            "## 三、酒店经营与租约",
            "",
            "| 年度 | ADR(元) | 入住率 | RevPAR(元) | EBITDAR(万元) | 可支付租金(万元) |",
            "|---:|---:|---:|---:|---:|---:|",
        ])
        for row in (result.get("hotel_operation") or {}).get("years") or []:
            lines.append(
                f"| {row['year']} | {row['adr_yuan']:.2f} | {row['occupancy']:.2%} | "
                f"{row['revpar_yuan']:.2f} | {row['ebitdar_wan']:.2f} | {row['affordable_rent_wan']:.2f} |"
            )
    lines.extend(["", "## 四、主体关系", ""])
    parties = report_data.get("party_relationships") or []
    if parties:
        lines.extend(["| 主体ID | 名称 | 角色 | 状态 | 证据 |", "|---|---|---|---|---|"])
        for row in parties:
            lines.append(
                f"| {text(row.get('entity_id'))} | {text(row.get('name'))} | "
                f"{text(row.get('roles') or [])} | {text(row.get('status'))} | "
                f"{text(row.get('evidence_ids') or [])} |"
            )
    else:
        lines.append("- 无已绑定主体。")
    lines.extend(["", "## 五、资产边界与权证许可", ""])
    assets = report_data.get("asset_boundary") or []
    if assets:
        lines.extend(["| 资产ID | 类型 | 是否纳入 | 面积(㎡) | 状态 | 冲突/裁决 |", "|---|---|---:|---:|---|---|"])
        for row in assets:
            lines.append(
                f"| {text(row.get('scope_id'))} | {text(row.get('type'))} | "
                f"{text(row.get('included'))} | {text(row.get('area_sqm'))} | "
                f"{text(row.get('status'))} | {text(row.get('conflicts') or row.get('resolution'))} |"
            )
    licenses = report_data.get("license_ledger") or []
    lines.extend(["", "### 5.1 权证/许可台账", ""])
    if licenses:
        lines.extend(["| 许可/角色 | 持有人 | 状态 | 证据 |", "|---|---|---|---|"])
        for row in licenses:
            lines.append(
                f"| {text(row.get('license_type') or row.get('type'))} | "
                f"{text(row.get('holder_name') or row.get('holder_id'))} | "
                f"{text(row.get('status'))} | {text(row.get('evidence_ids') or [])} |"
            )
    else:
        lines.append("- 无已绑定许可记录。")
    if is_solar:
        lines.extend(["", "## 六、光伏运营关键参数", ""])
        solar = report_data.get("solar_operation") or {}
        lines.extend([
            "| 参数 | 值 |", "|---|---:|",
            f"| 装机容量(MW) | {text(solar.get('installed_capacity_mw'))} |",
            f"| 基准发电量(MWh) | {text(solar.get('base_generation_mwh'))} |",
            f"| 上网电价(元/kWh) | {text(solar.get('tariff_yuan_per_kwh'))} |",
            f"| 限电率 | {text(solar.get('curtailment_rate'))} |",
            f"| 年衰减率 | {text(solar.get('degradation_rate'))} |",
        ])
    else:
        lines.extend(["", "## 六、租约台账", ""])
        leases = report_data.get("lease_ledger") or []
        if leases:
            lines.extend(["| 单元 | 位置 | 面积(㎡) | 出租人/承租人 | 起止日 | 基础租金(万元) | 证据 |", "|---|---|---:|---|---|---:|---|"])
            for row in leases:
                lines.append(
                    f"| {text(row.get('unit_id'))} | {text(row.get('asset_location'))} | "
                    f"{text(row.get('area_sqm'))} | {text(row.get('lessor_id'))}/{text(row.get('lessee_id'))} | "
                    f"{text(row.get('start_date'))}—{text(row.get('end_date'))} | "
                    f"{text(row.get('base_rent_wan'))} | {text(row.get('evidence_ids') or [])} |"
                )
        else:
            lines.append("- 无已绑定租约。")
    lines.extend(["", "## 七、历史财务对比与勾稽", ""])
    statements = report_data.get("historical_financial_comparison") or []
    if statements:
        lines.extend(["| 主体 | 期间 | 报表类型 | 数值勾稽 | 异常 | 来源定位 |", "|---|---|---|---|---|---|"])
        for row in statements:
            lines.append(
                f"| {text(row.get('entity_id'))} | {text(row.get('period_start'))}—{text(row.get('period_end'))} | "
                f"{text(row.get('statement_type'))} | {text(row.get('reconciliation'))} | "
                f"{text(row.get('anomalies') or [])} | {text(row.get('source_locators') or [])} |"
            )
    else:
        lines.append("- 无已绑定历史报表。")
    bridge = report_data.get("valuation_transaction_bridge") or {}
    max_price_analysis = report_data.get("maximum_acceptable_price") or {}
    max_price_result = max_price_analysis.get("result") or {}
    lines.extend([
        "", "## 八、估值—成交价桥接", "",
        "| 项目 | 金额/日期 |", "|---|---:|",
        f"| 评估值(万元) | {text(bridge.get('valuation_value_wan'))} |",
        f"| 收购价(万元) | {text(bridge.get('purchase_price_wan'))} |",
        f"| 收购价较评估值差额(万元) | {text(bridge.get('purchase_price_vs_valuation_wan'))} |",
        f"| 交易税费(万元) | {text(bridge.get('transaction_tax_wan'))} |",
        f"| 总收购成本(万元) | {text(bridge.get('total_acquisition_cost_wan'))} |",
        f"| 评估日/交割日 | {text(bridge.get('valuation_date'))}/{text(bridge.get('closing_date'))} |",
        "", "### 8.1 最高可接受收购价", "",
        f"- 计算状态：{text(max_price_analysis.get('status'))}；验证状态：{text(max_price_analysis.get('validation_status'))}。",
        f"- 最高可接受价：{text(max_price_result.get('max_acquisition_price_wan'))} 万元。",
        f"- 目标IRR/最低DSCR：{text((max_price_analysis.get('parameters') or {}).get('target_irr'))}/"
        f"{text((max_price_analysis.get('parameters') or {}).get('min_dscr'))}。",
        f"- 求解哈希：`{text(max_price_analysis.get('analysis_hash'))}`；引擎版本：{text(max_price_analysis.get('engine_version'))}。",
        "", "## 九、独立情景矩阵", "",
    ])
    matrices = report_data.get("scenario_matrices") or []
    if matrices:
        if is_solar:
            lines.extend(["| 矩阵/场景 | 变更 | 收购价(万元) | 上网电价(元/kWh) | 年发电量(MWh) | 利用小时 | 年运维费(万元) | 项目IRR | 最低DSCR |", "|---|---|---:|---:|---:|---:|---:|---:|---:|"])
        else:
            lines.extend(["| 矩阵/场景 | 变更 | 收购价(万元) | 市场租金 | 入住率 | 项目IRR | 最低DSCR |", "|---|---|---:|---:|---|---:|---:|"])
        for matrix in matrices:
            for row in matrix.get("rows") or []:
                indicators = row.get("indicators") or {}
                prefix = (
                    f"| {text(matrix.get('matrix_id'))}/{text(row.get('scenario_id'))} | "
                    f"{text(row.get('changes'))} | {text(row.get('purchase_price_wan'))} | "
                )
                if is_solar:
                    lines.append(
                        prefix + f"{text(row.get('tariff_yuan_per_kwh'))} | "
                        f"{text(row.get('annual_generation_mwh'))} | {text(row.get('utilization_hours'))} | "
                        f"{text(row.get('annual_opex_wan'))} | {text(indicators.get('project_irr_pct'))} | "
                        f"{text(indicators.get('minimum_dscr'))} |"
                    )
                else:
                    lines.append(
                        prefix + f"{text(row.get('market_rent'))} | {text(row.get('occupancy'))} | "
                        f"{text(indicators.get('project_irr_pct'))} | {text(indicators.get('minimum_dscr'))} |"
                    )
    else:
        lines.append("- 当前 run 未绑定独立情景矩阵。")
    lines.extend(["", "## 十、红旗、成交条件与否决事项", ""])
    for row in report_data.get("red_flags") or []:
        lines.append(f"- 红旗 `{text(row.get('code'))}`：{text(row.get('status'))}；{text(row.get('resolution'))}")
    for item in report_data.get("closing_conditions") or []:
        lines.append(f"- 成交条件：{text(item)}")
    for item in report_data.get("veto_items") or []:
        lines.append(f"- 否决事项：{text(item)}")
    lines.extend(["", "## 十一、资料处理与字段证据台账", ""])
    ledger = report_data.get("source_processing_ledger") or []
    if ledger:
        lines.extend(["| 字段 | 证据ID | 来源 | 绑定状态 |", "|---|---|---|---|"])
        for row in ledger:
            lines.append(
                f"| {text(row.get('field'))} | {text(row.get('evidence_ids'))} | "
                f"{text(row.get('source'))} | {text(row.get('validation_status'))} |"
            )
    lines.extend([
        "", "## 十二、确定性校验与工件绑定", "",
        f"- 运行校验：{text(run.get('validation_status'))}",
        f"- Spec 校验：{text(run.get('formal_spec_valid'))}",
        f"- 证据绑定：{text(run.get('evidence_status'))}；`{text(run.get('evidence_binding_hash'))}`",
        f"- 数值一致性：{text(run.get('consistency_ok'))}",
        "- 本报告数字仅绑定上述不可变 run、输入快照与内容哈希。",
    ])
    return "\n".join(lines) + "\n"
