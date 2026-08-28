"""核心计算引擎：compute_financials 与依赖它重入的自定义目标、缩放重算、敏感性与情景。四者互相递归（缩放重算与自定义目标求解都要重跑主计算），是同一事务边界，按方案 §4 不拆开也不切函数体。"""

from __future__ import annotations

import copy
import math
from typing import Any, Optional

# P0/P1 modular finance package (方案 §8/§13)
from lvke_mcp.domains.finance import assets as _fin_assets
from lvke_mcp.domains.finance import capitalization as _fin_cap
from lvke_mcp.domains.finance import normalize as _fin_normalize
from lvke_mcp.domains.finance import scenarios as _fin_scenarios
from lvke_mcp.domains.finance import taxes as _fin_taxes
from lvke_mcp.domains.finance import timeline as _fin_timeline
from lvke_mcp.domains.finance.contracts import FINANCE_SCHEMA_VERSION

from .annual import (
    _build_annual,
    _construction_interest,
)

from .base import (
    DEFAULT_LOAN_RATE,
    _HAS_INVESTMENT_BREAKDOWN,
    _cost_param,
    _f,
    _fmt,
    _irr,
    _npv,
    _payback,
    _resolve_benchmark,
)

from .checks import (
    basis_of_estimate_md,
    check_consistency,
)

from .investment import (
    _classify_investment_scope,
    _lift_flat_invest_breakdown,
    _parse_invest_detail,
)

# ``InvestmentBreakdown`` 只在 ``base`` 的可选依赖 try 成功分支里绑定，
# 且下面的用法始终由 ``_HAS_INVESTMENT_BREAKDOWN`` 守卫。无条件 import 会把
# 原来的可选依赖变成硬依赖，改变缺 ``finance.spec`` 时的行为。
if _HAS_INVESTMENT_BREAKDOWN:  # pragma: no branch - 依赖存在时的常规路径
    from .base import InvestmentBreakdown

from .profiles import (
    project_nature_policy,
)

from .render import (
    _render_summary,
    _render_tables,
    _required_markers,
)

from .tax import (
    _DEFAULT_LOSS_CARRYFORWARD_YEARS,
    _compute_income_tax_with_loss_carryforward,
    _income_tax_schedule,
    _tax_spec_int,
)


def _year_driver(row: dict[str, Any], *keys: str) -> float:
    for key in keys:
        if row.get(key) is not None:
            return float(row.get(key) or 0.0)
    return 0.0


def _split_year_driver(total: float, count: int) -> list[float]:
    """Equal monthly shares with last-month residual so months sum to the year driver."""

    if count <= 0:
        return []
    base = round(total / count, 2)
    shares = [base] * count
    shares[-1] = round(total - base * (count - 1), 2)
    return shares


def _month_amount(period: dict[str, Any], key: str, shares: list[float], index: int) -> float:
    if period.get(key) is not None:
        return round(float(period.get(key) or 0.0), 2)
    if 0 <= index < len(shares):
        return shares[index]
    return 0.0


def _compute_monthly_from_year_drivers(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Primary monthly loop. Year totals are allocation drivers, not post-tax results.

    Period-level overrides win. Equal month split is used only when a period has
    no override (no seasonality / workday calendar). Tax and NOL always roll
    month by month; annual tables consume these monthly sums.
    """

    annual = result.get("annual") if isinstance(result.get("annual"), dict) else {}
    income = list(annual.get("income_statement") or [])
    plan = list(annual.get("financial_plan") or [])
    build_years = max(int((result.get("params") or {}).get("build_years") or 0), 0)
    periods = list((result.get("timeline") or {}).get("monthly_periods") or [])
    grouped: dict[int, list[dict[str, Any]]] = {}
    for period in periods:
        if not isinstance(period, dict):
            continue
        year = int(period.get("year_index") or 1)
        grouped.setdefault(year, []).append(period)
    nol = 0.0
    detail: list[dict[str, Any]] = []
    for year in sorted(grouped):
        months = grouped[year]
        count = max(len(months), 1)
        operating_index = year - build_years
        source = income[operating_index - 1] if operating_index > 0 and operating_index - 1 < len(income) else {}
        plan_row = plan[year - 1] if year - 1 < len(plan) and isinstance(plan[year - 1], dict) else {}
        construction = operating_index <= 0 or str(plan_row.get("phase") or "") == "建设期"
        revenue = 0.0 if construction else _year_driver(source, "revenue", "revenue_wan")
        cost = 0.0 if construction else _year_driver(source, "operating_cost", "operating_cost_wan")
        dep = 0.0 if construction else _year_driver(source, "depreciation", "depreciation_wan")
        tax_dep = 0.0 if construction else _year_driver(source, "tax_depreciation", "tax_depreciation_wan") or dep
        rate = float(source.get("income_tax_rate") or 0.0)
        construction_wan = _year_driver(plan_row, "construction_investment", "construction_investment_wan") if construction else 0.0
        loan_draw = _year_driver(plan_row, "loan_draw", "loan_draw_wan") if construction else 0.0
        principal = 0.0 if construction else _year_driver(plan_row, "principal", "principal_repay_wan")
        interest = 0.0 if construction else _year_driver(plan_row, "interest", "interest_wan")
        rev_shares = _split_year_driver(revenue, count)
        cost_shares = _split_year_driver(cost, count)
        dep_shares = _split_year_driver(dep, count)
        tax_dep_shares = _split_year_driver(tax_dep, count)
        capex_shares = _split_year_driver(construction_wan, count)
        draw_shares = _split_year_driver(loan_draw, count)
        principal_shares = _split_year_driver(principal, count)
        interest_shares = _split_year_driver(interest, count)
        for index, period in enumerate(months):
            month_rev = _month_amount(period, "revenue_wan", rev_shares, index)
            month_cost = _month_amount(period, "operating_cost_wan", cost_shares, index)
            month_dep = _month_amount(period, "depreciation_wan", dep_shares, index)
            month_tax_dep = _month_amount(period, "tax_depreciation_wan", tax_dep_shares, index)
            pretax = month_rev - month_cost - month_dep
            temp = month_dep - month_tax_dep
            taxable_raw = pretax + temp
            if taxable_raw < 0:
                nol = round(nol - taxable_raw, 2)
                taxable = 0.0
            else:
                used = min(nol, taxable_raw)
                nol = round(nol - used, 2)
                taxable = round(taxable_raw - used, 2)
            current_tax = round(max(taxable, 0.0) * rate, 2) if rate else 0.0
            deferred = round(temp * rate, 2) if rate else 0.0
            detail.append({
                **period,
                "revenue_wan": month_rev,
                "operating_cost_wan": month_cost,
                "depreciation_wan": month_dep,
                "tax_depreciation_wan": month_tax_dep,
                "construction_investment_wan": _month_amount(
                    period, "construction_investment_wan", capex_shares, index
                ),
                "loan_draw_wan": _month_amount(period, "loan_draw_wan", draw_shares, index),
                "principal_repay_wan": _month_amount(
                    period, "principal_repay_wan", principal_shares, index
                ),
                "interest_wan": _month_amount(period, "interest_wan", interest_shares, index),
                "income_tax_wan": current_tax,
                "deferred_tax_wan": deferred,
                "loss_carryforward_wan": nol,
            })
    return detail


def _apply_monthly_aggregates_to_annual(
    result: dict[str, Any],
    detail: list[dict[str, Any]],
) -> None:
    """Annual tax/NOL and overridden month totals come from the monthly loop."""

    annual = result.get("annual") if isinstance(result.get("annual"), dict) else {}
    if not annual or not detail:
        return
    build_years = max(int((result.get("params") or {}).get("build_years") or 0), 0)
    by_year: dict[int, list[dict[str, Any]]] = {}
    for row in detail:
        by_year.setdefault(int(row.get("year_index") or 0), []).append(row)
    income = list(annual.get("income_statement") or [])
    plan = list(annual.get("financial_plan") or [])
    for year, rows in by_year.items():
        operating_index = year - build_years
        if (
            operating_index > 0
            and operating_index - 1 < len(income)
            and isinstance(income[operating_index - 1], dict)
        ):
            target = income[operating_index - 1]
            target["income_tax"] = round(sum(float(item.get("income_tax_wan") or 0.0) for item in rows), 2)
            target["current_income_tax"] = target["income_tax"]
            target["deferred_tax"] = round(
                sum(float(item.get("deferred_tax_wan") or 0.0) for item in rows), 2
            )
            target["loss_carryforward"] = float(rows[-1].get("loss_carryforward_wan") or 0.0)
            target["revenue"] = round(sum(float(item.get("revenue_wan") or 0.0) for item in rows), 2)
            target["operating_cost"] = round(
                sum(float(item.get("operating_cost_wan") or 0.0) for item in rows), 2
            )
            target["depreciation"] = round(
                sum(float(item.get("depreciation_wan") or 0.0) for item in rows), 2
            )
        if year - 1 < len(plan) and isinstance(plan[year - 1], dict):
            plan[year - 1]["construction_investment"] = round(
                sum(float(item.get("construction_investment_wan") or 0.0) for item in rows), 2
            )
            plan[year - 1]["loan_draw"] = round(
                sum(float(item.get("loan_draw_wan") or 0.0) for item in rows), 2
            )
            plan[year - 1]["principal"] = round(
                sum(float(item.get("principal_repay_wan") or 0.0) for item in rows), 2
            )
            plan[year - 1]["interest"] = round(
                sum(float(item.get("interest_wan") or 0.0) for item in rows), 2
            )
    annual["income_statement"] = income
    annual["financial_plan"] = plan
    annual["monthly_sourced"] = True


def _monthly_detail_from_annual(result: dict[str, Any]) -> list[dict[str, Any]]:
    return _compute_monthly_from_year_drivers(result)


def compute_financials(finance: dict[str, Any], *, invest_type: str = "", build_period_months: Optional[int] = None, industry: str = "", spec: Optional[dict[str, Any]] = None, _apply_custom: bool = True, _revenue_scale: float = 1.0, _op_cost_scale: float = 1.0, _construction_scale: float = 1.0, _with_analysis: bool = True) -> dict[str, Any]:
    """核心：从 finance 输入算出指标 + 现金流 + 附表数据。

    【P1-5】敏感性/情景重跑护栏（方案 §10.1/§10.2：改输入重跑整模型，非在最终现金流加减常数）：
    - ``_revenue_scale`` / ``_op_cost_scale`` / ``_construction_scale``：价/成本/投资单因素缩放钮，
      在 P&L 定义点精确施加，令税费、增值税、终值、偿债随之联动重算。三者默认 1.0 时字节级不变。
    - ``_with_analysis``：默认 True 时算敏感性/情景；重跑时置 False 防递归（sensitivity→compute→sensitivity）。
      价缩放施加在成本核算之后（成本不随价联动），成本缩放只动现金经营成本（折旧独立）。

    返回 dict：indicators / investment / funding / operating / cashflow / assumptions /
    tables(各附表 markdown) / summary_md（供注入 prompt 的确定性数据块）/ markers。

    BC 混合改造（方案 §7.1）：``spec`` 为 B 层 LLM 产出的 FinanceSpec dict。
    - ``spec=None`` 或收入模型为 ``flat`` 时，行为与改造前**完全一致**（向后兼容硬门槛）。
    - ``spec`` 非空且收入模型非 flat 时，收入侧改吃 revenue_models 展开的逐年序列（H1/H2）。
    改造只扩展**输入来源**，不改算术：IRR/NPV/回收期仍由 finance_calc 纯函数求解。

    ``_apply_custom``：内部再入守卫（BC-P6）。默认 True 时，在标准模型算完后按
    ``spec.custom`` 跑 C 层受限沙箱定制计算并回灌重算（须 ``FINANCE_SANDBOX=1``）；
    回灌重算时置 False，防止无限递归。外部调用方无需传此参数。
    """
    fin = finance or {}

    # 【P2-8修复】输入完整性校验
    validation_errors = []
    validation_warnings = []

    # 检查total_investment_wan
    total_inv = _f(fin.get("total_investment_wan"))
    bd = fin.get("invest_breakdown") or {}

    if total_inv is None or total_inv <= 0:
        if bd:
            validation_errors.append({
                "field": "total_investment_wan",
                "severity": "error",
                "message": "必须提供 total_investment_wan，不能只提供 invest_breakdown 分项"
            })

    # 检查total_investment_wan与invest_breakdown一致性
    if total_inv and total_inv > 0 and bd:
        construction = _f(bd.get("construction_wan"))
        equipment = _f(bd.get("equipment_wan"))
        installation = _f(bd.get("installation_wan"))
        other = _f(bd.get("other_wan"))

        # 粗略估算分项总投资
        if equipment or installation:
            eng_sum = sum(v for v in [construction, equipment, installation] if v is not None)
            other_val = other or 0.0
            rate = _f(bd.get("basic_reserve_rate")) or 0.0
            reserve = (eng_sum + other_val) * rate if rate > 0 else 0.0
            estimated = eng_sum + other_val + reserve
        else:
            estimated = construction or 0.0

        if estimated > 0:
            gap = abs(estimated - total_inv)
            gap_pct = gap / total_inv

            if gap_pct > 0.05:  # >5%
                validation_warnings.append({
                    "field": "invest_breakdown",
                    "severity": "warning",
                    "message": f"分项推算总投资 {estimated:.2f}万 与输入 {total_inv:.2f}万 差异 {gap:.2f}万({gap_pct*100:.1f}%)"
                })

    # 如果有error级别的校验失败，直接返回
    if validation_errors:
        return {
            "available": False,
            "reason": "input_validation_failed",
            "validation_errors": validation_errors,
            "validation_warnings": validation_warnings,
        }

    # 【P2-8修复】投资口径不一致时直接报错
    fin = finance or {}

    # 【P2-9修复】行业识别字段规范化和规则映射
    industry = str(industry or fin.get("industry") or "").strip()
    invest_type = str(invest_type or fin.get("invest_type") or "").strip()

    # 行业规则映射表
    INDUSTRY_RULES = {
        "电力": {"benchmark_rate": 0.08, "debt_service_model": "profit+dep+amort", "category": "基础设施"},
        "电力、热力生产和供应业": {"benchmark_rate": 0.08, "debt_service_model": "profit+dep+amort", "category": "基础设施"},
        "新能源": {"benchmark_rate": 0.08, "debt_service_model": "profit+dep+amort", "category": "基础设施"},
        "光伏": {"benchmark_rate": 0.08, "debt_service_model": "profit+dep+amort", "category": "新能源"},
        "风电": {"benchmark_rate": 0.08, "debt_service_model": "profit+dep+amort", "category": "新能源"},
        "储能": {"benchmark_rate": 0.08, "debt_service_model": "profit+dep+amort", "category": "新能源"},
        "制造业": {"benchmark_rate": 0.10, "debt_service_model": "profit+dep", "category": "制造业"},
        "公共设施管理": {"benchmark_rate": 0.06, "debt_service_model": "cashflow", "category": "公共服务"},
        "交通运输": {"benchmark_rate": 0.07, "debt_service_model": "profit+dep+amort", "category": "基础设施"},
    }

    # 行业模糊匹配
    industry_rule = None
    if industry:
        # 精确匹配
        if industry in INDUSTRY_RULES:
            industry_rule = INDUSTRY_RULES[industry]
        else:
            # 模糊匹配(关键词)
            for key, rule in INDUSTRY_RULES.items():
                if key in industry or industry in key:
                    industry_rule = rule
                    break

    # 如果未匹配到,使用默认规则(电力/基础设施)
    if not industry_rule:
        industry_rule = INDUSTRY_RULES["电力、热力生产和供应业"]
        if not industry:
            industry = "未指定(默认:电力、热力生产和供应业)"

    # 回写规范化后的industry到fin
    fin["industry"] = industry
    if not fin.get("invest_type"):
        fin["invest_type"] = invest_type if invest_type else "经营性"

    # 【P2-9】将行业规则应用到计算中(后续可扩展)
    # 当前保持向后兼容,规则映射暂时只用于文档说明
    # 未来可根据industry_rule调整基准收益率、偿债模型等

    # 【P2 扩展】从 spec.investment 合并投资明细到 finance 输入
    if spec and isinstance(spec, dict) and _HAS_INVESTMENT_BREAKDOWN:
        spec_investment = spec.get("investment")
        if spec_investment and isinstance(spec_investment, dict):
            # 将 spec.investment 转换为 invest_breakdown 格式
            try:
                inv_obj = InvestmentBreakdown(**spec_investment)
                breakdown_input = inv_obj.to_finance_input()
                if breakdown_input:
                    # 合并到 fin["invest_breakdown"]（spec 优先，不覆盖用户直接输入）
                    existing_bd = fin.get("invest_breakdown") or {}
                    merged_bd = {**breakdown_input, **existing_bd}
                    fin["invest_breakdown"] = merged_bd
            except Exception:  # noqa: BLE001
                pass  # spec.investment 格式不对时静默跳过，不阻断计算

    assumptions: list[str] = []
    bench, bench_note = _resolve_benchmark(industry)  # M1 T1.3：分行业财务基准收益率
    explicit_discount_rate = _f(fin.get("discount_rate"))
    if explicit_discount_rate is not None:
        if not 0 < explicit_discount_rate < 1:
            return {
                "available": False,
                "reason": "discount_rate_invalid",
                "validation_errors": [{
                    "field": "discount_rate",
                    "severity": "error",
                    "message": "discount_rate 必须在 0~1 之间",
                }],
            }
        bench = explicit_discount_rate
        bench_note = f"折现率按项目显式输入 {bench * 100:.2f}%"
    assumptions.append(bench_note)
    # P1：基准收益率适用性口径（政府投资项目才套行业基准达标判定；企业项目用投资者自定最低收益率）
    nature_policy = project_nature_policy(invest_type)
    if nature_policy.get("note"):
        assumptions.append(nature_policy["note"])

    total = _f(fin.get("total_investment_wan"))
    if total is None or total <= 0:
        return {"available": False, "reason": "缺少项目总投资，无法测算", "assumptions": ["未提供项目总投资"]}

    # ── 投资构成 ──
    bd = fin.get("invest_breakdown") or {}
    # 【FIN-1】扁平明细形态提升：顶层 equipment/installation 不再被静默丢弃
    bd = _lift_flat_invest_breakdown(bd, assumptions)
    construction = _f(bd.get("construction_wan"))
    other = _f(bd.get("other_wan"))
    reserve = _f(bd.get("reserve_wan"))
    interest = _f(bd.get("interest_wan"))
    working = _f(bd.get("working_capital_wan"))
    # 【P0-1】投资口径分类（用倒算前的原始输入快照判定用户真实意图，方案 §5.4）。
    #   不改任何算术：歧义样例仍出原匡算数值,仅附 scope_status,由 §9.4 门禁决定可否发布终稿。
    scope_status = _classify_investment_scope(total, construction, other, reserve, interest, working)
    is_ent = invest_type == "enterprise"

    # 【P2】颗粒度投资明细（可选）：三段式细项，提供时用于渲染完整附表1，并回填汇总数。
    #   construction_detail：工程费用三项（civil/equipment/installation）
    #   other_detail：工程建设其他费用(land/management/design/consulting/supervision/bidding/test_run)
    #   contingency_detail：预备费(basic/price)
    # 不造数原则：仅当上游显式提供明细才用;缺项按 0 计;不从单一建设投资数反向拆分。
    detail = _parse_invest_detail(bd)
    if detail:
        _eng_sum = detail["engineering_total"]
        _other_sum = detail["other_total"]
        _cont_sum = detail["contingency_total"]
        # 明细提供时,以明细汇总回填分段汇总数(优先于可能缺失的 other_wan/reserve_wan)
        if _other_sum > 0 and other is None:
            other = _other_sum
        if _cont_sum > 0 and reserve is None:
            reserve = _cont_sum
        # 建设投资缺失时由三段明细汇总倒算(工程+其他+预备费)
        if construction is None and (_eng_sum + _other_sum + _cont_sum) > 0:
            construction = round(_eng_sum + _other_sum + _cont_sum, 2)
            assumptions.append("建设投资按投资明细三段(工程费用+工程建设其他费用+预备费)汇总")

    # P1-5 construction sensitivity: scale the construction-period investment
    # at its definition point.  The previous implementation forwarded
    # ``_construction_scale`` into reruns but never applied it, producing a
    # professional-looking sensitivity table whose construction IRR/NPV never
    # changed.  Working capital is held constant; construction and capitalised
    # IDC move together, while funding sources retain their original ratios.
    _funding_scale = 1.0
    _construction_scaled = False
    if _construction_scale != 1.0 and construction is not None:
        _total_before_scale = total
        _construction_before_scale = construction
        _interest_before_scale = interest or 0.0
        construction = round(_construction_before_scale * _construction_scale, 2)
        if interest is not None:
            interest = round(_interest_before_scale * _construction_scale, 2)
        total = round(
            total
            + (construction - _construction_before_scale)
            + ((interest or 0.0) - _interest_before_scale),
            2,
        )
        if detail:
            for segment in ("engineering", "other", "contingency"):
                detail[segment] = [
                    (label, round(value * _construction_scale, 2))
                    for label, value in detail.get(segment, [])
                ]
            detail["engineering_total"] = round(sum(value for _, value in detail["engineering"]), 2)
            detail["other_total"] = round(sum(value for _, value in detail["other"]), 2)
            detail["contingency_total"] = round(sum(value for _, value in detail["contingency"]), 2)
        _funding_scale = total / _total_before_scale if _total_before_scale else 1.0
        _construction_scaled = True
        assumptions.append(
            f"建设投资敏感性重跑：建设期投资按 {_construction_scale:.2f} 倍调整，"
            "流动资金保持基准，资本金/贷款/补助按原筹资比例同步调整"
        )

    if working is None:
        # 默认：企业项目总投资×4%；若配置了铺底比例且后续有全额流资路径可被 fin 覆盖
        _wc_ratio = 0.04 if is_ent else 0.0
        try:
            from lvke_mcp.domains.finance.feasibility_params import load_feasibility_params
            _fp = load_feasibility_params()
            # 仅当调用方显式要求「铺底」口径时用 initial ratio；默认仍 4% 全额估算
            if bool(fin.get("use_initial_working_capital_ratio")):
                _ir = (_fp.get("working_capital") or {}).get(
                    "initial_working_capital_ratio_of_total_wc"
                )
                if _ir is not None:
                    # 先估全额流资 4% 再取铺底比例
                    _full = round(total * (0.04 if is_ent else 0.0), 2)
                    working = round(_full * float(_ir), 2)
                    assumptions.append(
                        f"铺底流动资金按全额流资×{float(_ir)*100:.0f}% 估为 {_fmt(working)} 万元"
                    )
        except Exception:  # noqa: BLE001
            pass
        if working is None:
            working = round(total * _wc_ratio, 2)
            if working > 0:
                assumptions.append(f"流动资金按总投资 4% 估算为 {_fmt(working)} 万元")

    # ── 资金筹措（先于建设期利息解析：半期计息需要贷款额/利率/建设期）──
    subsidy = _f(fin.get("gov_subsidy_wan")) or 0.0
    capital = _f(fin.get("capital_own_wan"))
    cap_ratio = _f(fin.get("capital_own_ratio"))
    loan = _f(fin.get("loan_wan"))
    loan_ratio = _f(fin.get("loan_ratio"))
    if _funding_scale != 1.0:
        subsidy = round(subsidy * _funding_scale, 2)
        if capital is not None:
            capital = round(capital * _funding_scale, 2)
        if loan is not None:
            loan = round(loan * _funding_scale, 2)
    if capital is None and cap_ratio is not None:
        capital = round(total * cap_ratio, 2)
    if loan is None and loan_ratio is not None:
        loan = round(total * loan_ratio, 2)
    if capital is None and loan is not None:
        capital = round(total - loan - subsidy, 2)
    if loan is None and capital is not None:
        loan = round(max(total - capital - subsidy, 0.0), 2)
    if capital is None and loan is None:
        capital = round(total * 0.35, 2)
        loan = round(total - capital - subsidy, 2)
        assumptions.append("未提供资金结构，按资本金 35% + 贷款估算")
    capital = capital or 0.0
    loan = loan or 0.0
    cap_pct = round(capital / total * 100, 2) if total else 0.0
    loan_pct = round(loan / total * 100, 2) if total else 0.0
    sub_pct = round(subsidy / total * 100, 2) if total else 0.0

    loan_years = int(_f(fin.get("loan_years")) or 10)
    loan_rate = _f(fin.get("loan_rate"))
    if loan_rate is None:
        loan_rate = DEFAULT_LOAN_RATE
        if loan > 0:
            assumptions.append(f"贷款年利率按基准 {loan_rate*100:.1f}% 估算")
    calc_years = int(_f(fin.get("calc_period_years")) or 12)
    build_years = max(1, math.ceil((build_period_months or 12) / 12))

    # ── 建设期利息：优先分年提款+半期计息（P1），缺贷款时退总投资 3% 估 ──
    idc_rows: list[dict] = []
    if interest is None:
        if loan > 0:
            _loan_draw_plan = fin.get("loan_draw_by_year") or fin.get("loan_draw_plan") or None
            if isinstance(_loan_draw_plan, (list, tuple)):
                _loan_draw_plan = [round(_f(x) or 0.0, 2) for x in _loan_draw_plan]
            else:
                _loan_draw_plan = None
            idc_rows = _construction_interest(
                loan, loan_rate, build_years, draw_plan=_loan_draw_plan)
            interest = round(sum(r["interest"] for r in idc_rows), 2)
            assumptions.append(
                f"建设期利息按分年提款+半期计息估为 {_fmt(interest)} 万元"
                f"（贷款 {_fmt(loan)} 万元、利率 {loan_rate*100:.2f}%、建设期 {build_years} 年，"
                f"当年提款按半年计息）")
        else:
            interest = round(total * 0.03, 2)
            assumptions.append(f"无贷款输入，建设期利息按总投资 3% 兜底估为 {_fmt(interest)} 万元")
    if construction is None:
        # 建设投资 = 总投资 - 建设期利息 - 流动资金（其他费用/预备费并入建设投资口径）
        construction = round(total - interest - working, 2)
        assumptions.append("建设投资按总投资扣除建设期利息与流动资金倒算")
    if _construction_scale != 1.0 and not _construction_scaled:
        # The common flat-input path derives construction only after financing
        # and IDC have been resolved.  Apply the scenario here as well; otherwise
        # the sensitivity rerun receives a scale but produces the base cashflow.
        _total_before_scale = total
        _construction_before_scale = construction
        _interest_before_scale = interest or 0.0
        construction = round(_construction_before_scale * _construction_scale, 2)
        interest = round(_interest_before_scale * _construction_scale, 2)
        total = round(
            total
            + (construction - _construction_before_scale)
            + (interest - _interest_before_scale),
            2,
        )
        _late_funding_scale = total / _total_before_scale if _total_before_scale else 1.0
        capital = round(capital * _late_funding_scale, 2)
        loan = round(loan * _late_funding_scale, 2)
        subsidy = round(subsidy * _late_funding_scale, 2)
        cap_pct = round(capital / total * 100, 2) if total else 0.0
        loan_pct = round(loan / total * 100, 2) if total else 0.0
        sub_pct = round(subsidy / total * 100, 2) if total else 0.0
        if idc_rows:
            for row in idc_rows:
                for key in ("draw", "opening", "closing", "interest"):
                    if isinstance(row.get(key), (int, float)):
                        row[key] = round(float(row[key]) * _construction_scale, 2)
        assumptions.append(
            f"建设投资敏感性重跑：倒算建设投资按 {_construction_scale:.2f} 倍调整，"
            "流动资金保持基准，资本金/贷款/补助按原筹资比例同步调整"
        )
    # 固定资产原值先按建设投资+建设期利息占位；收入模型展开后若判定房产存货，会用资本化映射覆盖。
    fixed_asset = round(construction + interest, 2)

    # 【P2 修复】自动判定经营性项目：有收入数据即为经营性
    revenue = _f(fin.get("annual_revenue_wan")) or _f(fin.get("revenue_wan"))
    _has_revenue_spec = (spec and (spec.get("revenue") or {}).get("model") and
                         (spec.get("revenue") or {}).get("model") != "flat")
    is_operating = (
        False
        if fin.get("is_operating") is False
        else bool(fin.get("is_operating")) or bool(revenue) or _has_revenue_spec
    )

    # 【P1-5】价格单因素缩放：在收入定义点施加，令销项税/所得税/终值等随之联动重算（默认 1.0 不变）。
    if revenue is not None and _revenue_scale != 1.0:
        revenue = round(revenue * _revenue_scale, 2)

    # BC-P2 收入模型展开（H1/H2）：spec 收入模型非 flat 时，把「多产品×单价×产量×达产率
    # 曲线 / 去化 / 客流 / 政府付费」展开为逐年营收序列 revenue_series。flat/None 时
    # revenue_series=None，收入侧完全走现状单点路径（字节级不变，保证老测试全绿）。
    revenue_series: Optional[list[float]] = None
    var_cost_series: Optional[list[float]] = None
    _spec_rev_model = ((spec or {}).get("revenue") or {}).get("model", "flat")
    # BC-4a：property_sales 成本侧开关（开发产品=存货，不折旧、销售成本随去化结转）
    _property_inventory = False
    _absorption_series: Optional[list[float]] = None
    # BC-4b：gov_payment 增值税退税比例（即征即退，如污水 70%）
    _vat_refund_rate = 0.0
    _effective_revenue_spec = spec
    _fiscal_support_policy = (
        dict(fin.get("fiscal_support_policy") or {})
        if isinstance(fin.get("fiscal_support_policy"), dict) else {}
    )
    if is_operating and spec and _spec_rev_model != "flat":
        try:
            from lvke_mcp.domains.finance import revenue_models

            _op_years_for_rev = max(calc_years - build_years, 1)
            _effective_revenue_spec = copy.deepcopy(spec)
            _effective_revenue = _effective_revenue_spec.setdefault("revenue", {})
            if isinstance(fin.get("fare_multiplier_by_year"), list):
                _effective_revenue["fare_multiplier_by_year"] = list(
                    fin.get("fare_multiplier_by_year") or []
                )
            if (
                _spec_rev_model == "rail_transit"
                and _fiscal_support_policy.get("mode")
                == "actual_cash_and_debt_service_gap"
            ):
                # Gap support is a financing cash inflow resolved after profit
                # and debt service.  It must not be embedded in fare revenue.
                _effective_revenue["annual_fiscal_support_wan"] = 0.0
                _effective_revenue["fiscal_support_ramp"] = []
            _exp = revenue_models.expand(_effective_revenue_spec, _op_years_for_rev)
            _rev_by_year = [round(_f(x) or 0.0, 2) for x in (_exp.get("revenue_by_year") or [])]
            _var_by_year = [round(_f(x) or 0.0, 2) for x in (_exp.get("var_cost_by_year") or [])]
            if _rev_by_year and any(v > 0 for v in _rev_by_year):
                # 【P1-5】价格缩放也施加到逐年序列（spec 驱动路径），与单点口径一致（默认 1.0 不变）。
                if _revenue_scale != 1.0:
                    _rev_by_year = [round(v * _revenue_scale, 2) for v in _rev_by_year]
                revenue_series = _rev_by_year
                var_cost_series = _var_by_year if any(v > 0 for v in _var_by_year) else None
                # 达产年营收 = 序列稳定段（取最大值代表达产），供 indicators 口径展示。
                revenue = max(_rev_by_year)
                assumptions.append(
                    f"收入按{_exp.get('note', '收入模型')}展开为逐年序列"
                    f"（达产年营收 {_fmt(revenue)} 万元，投产初期按达产率爬坡）")
                # BC-4a：房产去化成本侧标记
                if _exp.get("cost_side") == "inventory_cogs" or _spec_rev_model == "property_sales":
                    _property_inventory = True
                    _abs = _exp.get("absorption") or []
                    if _abs:
                        _absorption_series = [round(_f(x) or 0.0, 6) for x in _abs]
                # BC-4b：政府付费增值税退税比例
                if _spec_rev_model == "gov_payment":
                    _vat_refund_rate = max(0.0, min(1.0, _f(_exp.get("vat_refund_rate")) or 0.0))
            else:
                # 非 flat 展开无正营收（空 products / 缺参数）→ 保留 fin 单点营收，避免 indicators 空
                revenue_series = var_cost_series = None
                assumptions.append(
                    f"收入模型「{_spec_rev_model}」展开无有效正营收，回退 annual_revenue_wan 单点"
                    f"（当前 {_fmt(revenue)} 万元）")
        except Exception as _exc:  # noqa: BLE001 - 展开失败退单点法，不阻断
            revenue_series = var_cost_series = None
            _property_inventory = False
            _absorption_series = None
            assumptions.append(f"收入模型展开失败回退单点法（{str(_exc)[:60]}）")

    # §5.1 资产资本化映射：投资项 → 固定资产 / 无形 / 其他 / 开发存货
    _intangible_in = _f(fin.get("intangible_assets_wan")) or 0.0
    _other_assets_in = _f(fin.get("other_assets_wan")) or 0.0
    _asset_map = _fin_cap.map_assets(
        construction=construction or 0.0,
        interest=interest or 0.0,
        intangible_wan=_intangible_in,
        other_assets_wan=_other_assets_in,
        property_inventory=_property_inventory,
        capitalize_interest=True,
    )
    if _property_inventory:
        fixed_asset = 0.0  # 开发产品不进固定资产
        assumptions.append(_asset_map.get("note") or "开发产品按存货核算")
    else:
        fixed_asset = float(_asset_map.get("fixed_asset_gross") or fixed_asset)
    result: dict[str, Any] = {
        "available": True,
        "invest_type": invest_type,
        "investment": {
            "total": total, "construction": construction, "other": other,
            "reserve": reserve, "interest": interest, "working_capital": working,
            "fixed_asset": fixed_asset,
            "breakdown_detail": detail,  # 【P2】颗粒度三段式明细(无则 None)
            "scope_status": scope_status,  # 【P0-1】投资口径分类(ok/ambiguous/...)+ 歧义码,供 §9.4 门禁
            "asset_map": _asset_map,  # §5.1 资本化映射
        },
        "funding": {
            "capital": capital, "capital_pct": cap_pct, "loan": loan, "loan_pct": loan_pct,
            "subsidy": subsidy, "subsidy_pct": sub_pct, "loan_years": loan_years,
            "loan_rate": loan_rate,
        },
        "params": {"calc_years": calc_years, "build_years": build_years, "is_operating": is_operating},
        "benchmark_rate": bench,
        "benchmark_applicability": nature_policy,  # P1：基准收益率适用性口径
        "industry": industry,
        "assumptions": assumptions,
        # P0：原始输入透传给 _build_annual，用于工资(6-1)/折旧(6-2)/摊销(6-3)/流动资金(附表3)子表
        "raw": {
            "workspace_id": fin.get("workspace_id") or "",
            # Keep the resolved revenue drivers in the immutable run snapshot so
            # report/review tooling can reconcile component claims, not only the total.
            "revenue_spec": copy.deepcopy((spec or {}).get("revenue") or {}),
            "fare_multiplier_by_year": list(fin.get("fare_multiplier_by_year") or []),
            "renewal_capex_plan": copy.deepcopy(fin.get("renewal_capex_plan") or []),
            "fiscal_support_policy": copy.deepcopy(_fiscal_support_policy),
            "project_metadata": copy.deepcopy(fin.get("project_metadata") or {}),
            "cost_items": fin.get("cost_items") or {},
            "annual_operating_subsidy_wan": _f(fin.get("annual_operating_subsidy_wan")) or 0.0,
            "wage_wan": _f(fin.get("wage_wan")),                     # 工资及附加年额（缺则从 cost_items 拆）
            "intangible_wan": _intangible_in,  # 无形及其他资产原值（摊销基数）
            "other_assets_wan": _other_assets_in,
            "amortization_years": int(_f(fin.get("amortization_years")) or 10),
            "depreciation_years": int(_f(fin.get("depreciation_years")) or max(calc_years - build_years, 1)),
            "depreciation_classes": (
                list(fin.get("depreciation_classes") or [])
                if isinstance(fin.get("depreciation_classes"), list) else []
            ),
            "staff_detail": (
                list(fin.get("staff_detail") or fin.get("wage_detail") or fin.get("labor_plan") or [])
                if isinstance(fin.get("staff_detail") or fin.get("wage_detail") or fin.get("labor_plan"), list)
                else []
            ),
            "invest_breakdown": (
                dict(fin.get("invest_breakdown") or {})
                if isinstance(fin.get("invest_breakdown"), dict) else {}
            ),
            "salvage_rate": _f(fin.get("salvage_rate")) if _f(fin.get("salvage_rate")) is not None else _cost_param(spec, "salvage_rate"),
            "wc_turnover_days": _f(fin.get("wc_turnover_days")),     # 流动资金周转天数（缺则按占比法）
            "wc_turnover": fin.get("wc_turnover") if isinstance(fin.get("wc_turnover"), dict) else None,
            "funding_annual_schedule": (
                list(fin.get("funding_annual_schedule") or [])
                if isinstance(fin.get("funding_annual_schedule"), list) else []
            ),
            "construction_investment_by_year": list(fin.get("construction_investment_by_year") or []),
            "construction_interest_by_year": list(fin.get("construction_interest_by_year") or []),
            "working_capital_by_year": list(fin.get("working_capital_by_year") or []),
            "equity_inject_by_year": list(fin.get("equity_inject_by_year") or []),
            "loan_draw_by_year": list(fin.get("loan_draw_by_year") or []),
            "debt_repay_sources": list(fin.get("debt_repay_sources") or []),
            "distribution_policy": dict(fin.get("distribution_policy") or {}),
            "cost_behavior": dict(fin.get("cost_behavior") or {}),
            "tax_component_policy": dict(fin.get("tax_component_policy") or {}),
            "cost_behavior_confirmed": bool(fin.get("cost_behavior_confirmed")),
            "tax_component_policy_confirmed": bool(fin.get("tax_component_policy_confirmed")),
            "amort_bases": list(fin.get("amort_bases") or []),
            "loan_repay_method": fin.get("loan_repay_method") or "equal_principal",
            "loan_grace_years": int(_f(fin.get("loan_grace_years")) or 0),
            "loan_balloon_pct": _f(fin.get("loan_balloon_pct")) if _f(fin.get("loan_balloon_pct")) is not None else 0.3,
            "surtax_on_vat": bool(fin.get("surtax_on_vat")),
            "surtax_vat_rate": _f(fin.get("surtax_vat_rate")),
            "idc_rows": idc_rows,   # P1：分年提款+半期计息的建设期利息逐年（缺贷款时为空，回退均摊）
            "asset_map": _asset_map,
            # 十三表正式级事实与证据包；仅透传给门禁，不参与算术造数。
            "finance_fact_pack": (
                dict(fin.get("finance_fact_pack") or fin.get("fact_pack") or {})
                if isinstance(fin.get("finance_fact_pack") or fin.get("fact_pack"), dict)
                else {}
            ),
        },
        # BC-P1：FinanceSpec 透传（默认 None=现状行为）。P2 收入侧、P3 参数侧按此读取；
        # 存进 result 供 audit_db 落 spec_json/spec_hash（可复现关键，方案 §8）。
        "spec": spec or None,
    }

    # ── 经营性：营收/成本/利润/现金流/IRR/NPV/回收期 ──
    indicators: dict[str, Any] = {}
    if is_operating and revenue and revenue > 0:
        # 【M3 修复】增值税口径显式化：本模型营收/成本按**不含税**口径参与利润与现金流测算
        # （增值税为价外税，不进利润表）。若上游录入的是**含税**营收（甲方 Excel 常见），
        # 传 finance.revenue_tax_inclusive=true，此处按销项税率换算为不含税：不含税=含税/(1+税率)。
        _vat_rate_in = _f(fin.get("vat_rate")) or 0.13  # 销项适用税率（缺省制造/一般纳税人 13%）
        if bool(fin.get("revenue_tax_inclusive")):
            _div = 1 + _vat_rate_in
            revenue = round(revenue / _div, 2)
            if revenue_series:
                revenue_series = [round(v / _div, 2) for v in revenue_series]
            if var_cost_series:
                var_cost_series = [round(v / _div, 2) for v in var_cost_series]
            assumptions.append(
                f"营收按含税口径录入，已按销项税率 {int(_vat_rate_in*100)}% 换算为不含税"
                f"（不含税=含税/(1+{_vat_rate_in:.2f})）参与利润/现金流测算")
        else:
            assumptions.append("营收按不含税口径参与利润/现金流测算（增值税为价外税，不进利润表）")
        # BC-P3：税金/成本/残值率改三级取值（spec.tax/cost → config → 兜底=原硬编码）。
        # 用户真源 fin 字段优先于 spec/config（工作台表单可直接驱动税率/优惠）。
        vat_add_rate = _cost_param(spec, "surtax_rate", "tax")  # 营业税金及附加率（营收比例兜底，原硬编码 0.01）
        # 【P0 附加税同源】surtax_on_vat=true（或显式 surtax_vat_rate）时：附加税=应纳增值税×附加率，
        # 与附表5/7/9 一致。工作区正式入口会默认注入 true；底层纯函数保留显式输入契约。
        _surtax_on_vat = bool(fin.get("surtax_on_vat"))
        if (not _surtax_on_vat) and _f(fin.get("surtax_vat_rate")) is not None:
            _surtax_on_vat = True
        _surtax_vat_rate = _f(fin.get("surtax_vat_rate"))
        if _surtax_vat_rate is None:
            _surtax_vat_rate = 0.12  # 城建+教育附加等合并默认
        _urban_maintenance_rate = _f(fin.get("urban_maintenance_rate"))
        _education_surcharge_rate = _f(fin.get("education_surcharge_rate"))
        _local_education_surcharge_rate = _f(fin.get("local_education_surcharge_rate"))
        _statutory_surtax_components = _urban_maintenance_rate in {0.01, 0.05, 0.07}
        if _statutory_surtax_components:
            _education_surcharge_rate = (
                0.03 if _education_surcharge_rate is None else _education_surcharge_rate
            )
            _local_education_surcharge_rate = (
                0.02
                if _local_education_surcharge_rate is None
                else _local_education_surcharge_rate
            )
            _surtax_vat_rate = round(
                float(_urban_maintenance_rate)
                + float(_education_surcharge_rate)
                + float(_local_education_surcharge_rate),
                8,
            )
        elif _urban_maintenance_rate is not None:
            assumptions.append(
                "城建税率未明确为1%/5%/7%，本次仅保留旧综合率预览；正式交付须按项目所在地确认"
            )

        def _consumption_tax(index: int = 0) -> float:
            value = fin.get("consumption_tax_payable_wan")
            if isinstance(value, (list, tuple)):
                if not value:
                    return 0.0
                return max(float(value[min(index, len(value) - 1)] or 0.0), 0.0)
            return max(float(value or 0.0), 0.0)

        def _surtax_for(vat_value: float, index: int = 0) -> tuple[float, dict[str, float] | None]:
            if not _statutory_surtax_components:
                return round(max(vat_value, 0.0) * float(_surtax_vat_rate), 2), None
            component = _fin_taxes.surtax_components_from_tax_payable(
                [vat_value],
                consumption_tax_by_year=[_consumption_tax(index)],
                urban_maintenance_rate=float(_urban_maintenance_rate),
                education_surcharge_rate=float(_education_surcharge_rate),
                local_education_surcharge_rate=float(_local_education_surcharge_rate),
            )[0]
            return component["total"], component
        # 语义护栏：LLM 常把「城建+教育附加≈12%」误写入 surtax_rate（本字段是**营收比例**兜底，
        # 默认约 1%）。若 surtax_rate>5% 且未显式关闭 VAT 附加，则改走增值税附加路径，
        # 避免 28000×12%=3360 这种不合理营收附加并与爬坡年 附表5 断链。
        if (not bool(fin.get("surtax_on_vat") is False)) and vat_add_rate is not None and float(vat_add_rate) > 0.05:
            if not _surtax_on_vat:
                _surtax_on_vat = True
                _surtax_vat_rate = float(vat_add_rate)
                assumptions.append(
                    f"spec.tax.surtax_rate={float(vat_add_rate)*100:.1f}% 超过营收附加合理上限(5%)，"
                    f"已按「应纳增值税×附加率」解释（与城建/教育附加口径一致）"
                )
                vat_add_rate = 0.01  # 营收比例兜底恢复保守默认，避免双轨
        # 写入 raw 供年表/附表9读取（禁止平行实现）
        result["raw"]["surtax_on_vat"] = _surtax_on_vat
        result["raw"]["surtax_vat_rate"] = _surtax_vat_rate
        result["raw"]["surtax_revenue_rate"] = vat_add_rate
        result["raw"]["consumption_tax_payable_wan"] = copy.deepcopy(
            fin.get("consumption_tax_payable_wan") or 0.0
        )
        result["raw"]["surtax_component_policy"] = {
            "mode": "statutory_components" if _statutory_surtax_components else "legacy_combined_rate",
            "base": "vat_and_consumption_tax_payable",
            "urban_maintenance_rate": _urban_maintenance_rate,
            "education_surcharge_rate": _education_surcharge_rate,
            "local_education_surcharge_rate": _local_education_surcharge_rate,
            "combined_rate": _surtax_vat_rate,
            "location_rate_confirmed": _statutory_surtax_components,
        }
        _fin_itr = _f(fin.get("income_tax_rate"))
        income_tax_rate = _fin_itr if _fin_itr is not None else _cost_param(spec, "income_tax_rate", "tax")
        # 把 fin 层税收优惠合入 effective_tax_spec（fin 优先，spec 兜底），供 BC-1 序列消费。
        _spec_tax = dict(((spec or {}).get("tax") or {})) if isinstance(spec, dict) else {}
        _eff_tax = dict(_spec_tax)
        for _tk in ("tax_holiday_years", "tax_half_years", "loss_carryforward_years"):
            if _tk in fin and fin.get(_tk) is not None:
                try:
                    _eff_tax[_tk] = int(float(fin.get(_tk)))
                except (TypeError, ValueError):
                    pass
        if _fin_itr is not None:
            _eff_tax["income_tax_rate"] = income_tax_rate
        _eff_tax_spec = {"tax": _eff_tax} if _eff_tax else (spec if isinstance(spec, dict) else None)
        # 【H2 修复】折旧基数须扣残值：应折旧额 = 原值 ×(1−残值率)，末年再回收残值，
        # 合计恰为 100% 原值（原实现全额折旧 100% 又末年回收 5%，等于多提税盾）。
        # 【M2 修复】折旧年限用用户输入 depreciation_years（缺省=运营年数），不再恒用 op_years。
        # 【M1 修复】折旧与摊销独立计算：折旧基数=固定资产扣除无形资产（无形资产在附表6-3
        #   单独摊销，不得同时进折旧基数）；depreciation 指标 = 折旧 + 摊销 合计（非现金摊提），
        #   保持总成本/利润/勾稽口径连续。附表6-2/6-3 各自"原值×(1−残值率)/年限"复算自洽。
        # BC-4a：property_sales 开发产品=存货（建标〔2000〕205号）——不计提折旧、不计残值，
        #   开发成本随去化结转为销售成本；期末回收未售开发产品账面余额。
        _op_years_dep = max(calc_years - build_years, 1)
        _salvage_rate = _f(fin.get("salvage_rate"))
        if _salvage_rate is None:
            _salvage_rate = _cost_param(spec, "salvage_rate", "cost")  # 默认 0.05
        if _property_inventory:
            _salvage_rate = 0.0  # 房产：无固定资产残值
        _renewal_schedule = _fin_assets.renewal_capex_schedule(
            list(fin.get("renewal_capex_plan") or []),
            op_years=_op_years_dep,
        )
        _dep_class_schedule = (
            _fin_assets.classified_depreciation_schedule(
                list(fin.get("depreciation_classes") or []), op_years=_op_years_dep,
            )
            if not _property_inventory and isinstance(fin.get("depreciation_classes"), list)
            else {}
        )
        _dep_schedule_values = [
            round(float(row.get("depreciation") or 0.0), 2)
            for row in (_dep_class_schedule.get("rows") or [])
        ]
        _renewal_dep_values = list(_renewal_schedule.get("depreciation_by_year") or [])
        if _dep_schedule_values:
            _salvage_rate = float(_dep_class_schedule.get("weighted_salvage_rate") or 0.0)
        # 【P1-2 / F13-P1-02】折旧/摊销年限尊重用户输入，禁止静默延长到运营期。
        #   - 缺省 → 默认运营年数（显式 default，非改写）
        #   - 用户年限 < 运营期 → 按用户年限计提，期满后当年折旧为 0（assets 表滚动）
        #   - 用户年限 > 运营期 → 计算期内部分计提，期末按账面净值回收（P1-03）
        _dep_in_raw = _f(fin.get("depreciation_years"))
        _amort_in_raw = _f(fin.get("amortization_years"))
        _dep_life = _fin_assets.resolve_life_years(
            int(_dep_in_raw) if _dep_in_raw is not None else None, _op_years_dep)
        _amort_life = _fin_assets.resolve_life_years(
            int(_amort_in_raw) if _amort_in_raw is not None else 10, _op_years_dep)
        _dep_years_in = int(_dep_life["years"])
        _amort_years_in = int(_amort_life["years"])
        _intangible = float(_asset_map.get("intangible_original") or 0.0)
        _dep_years = _dep_years_in
        if _dep_schedule_values:
            _dep_years = int(_dep_class_schedule.get("max_life_years") or _dep_years_in)
            _dep_life = {
                "years": _dep_years,
                "source": "classified_assets",
                "rewritten": False,
                "class_count": len(_dep_class_schedule.get("classes") or []),
            }
        _amort_years = _amort_years_in if _intangible > 0 else _amort_years_in
        # 折旧基数：优先资本化映射的 fixed_asset_dep_base（已扣无形/其他资产）
        if _property_inventory:
            _dep_base = 0.0
        elif _dep_schedule_values:
            _dep_base = float(_dep_class_schedule.get("original_value_wan") or 0.0)
        else:
            _dep_base = float(_asset_map.get("fixed_asset_dep_base"))
            if _dep_base is None:
                _dep_base = max(fixed_asset - _intangible, 0.0)
        if _property_inventory:
            # 房产：开发成本不折旧；无形资产若有仍可摊销（少见，保留）。
            _dep_only = 0.0
            _dep_years = 0
            _dep_base = 0.0
            _amort_only = _fin_assets.annual_straight_line(
                _intangible, _amort_years, salvage_rate=0.0) if _intangible > 0 else 0.0
            _amort_active_years = min(_amort_years, _op_years_dep) if _intangible > 0 else 0
            _dep_avg = 0.0
            _amort_avg = round(_amort_only * _amort_active_years / max(_op_years_dep, 1), 2) if _intangible > 0 else 0.0
            depreciation = round(_amort_avg, 2)
            assumptions.append(
                "房地产去化口径（建标〔2000〕205号）：开发产品按存货核算，不计提折旧、不计残值；"
                "开发成本随去化比例结转为销售成本，期末回收未售开发产品账面余额")
        elif _dep_schedule_values:
            _dep_only = _dep_schedule_values[0]
            _amort_only = _fin_assets.annual_straight_line(
                _intangible, _amort_years, salvage_rate=0.0) if _intangible > 0 else 0.0
            _amort_active_years = min(_amort_years, _op_years_dep) if _intangible > 0 else 0
            _dep_avg = float(_dep_class_schedule.get("annual_average_wan") or 0.0)
            _amort_avg = round(
                _amort_only * _amort_active_years / max(_op_years_dep, 1), 2
            ) if _intangible > 0 else 0.0
            depreciation = round(_dep_avg + _amort_avg, 2)
            assumptions.append(
                f"固定资产按 {len(_dep_class_schedule.get('classes') or [])} 个资产类别分别计提折旧，"
                "未用单一计算期替代分类使用年限"
            )
        else:
            _spec_map = spec if isinstance(spec, dict) else {}
            _dep_method = str(
                fin.get("depreciation_method")
                or (_spec_map.get("cost") or {}).get("depreciation_method")
                or "straight_line"
            )
            _tax_dep_method = str(
                fin.get("tax_depreciation_method")
                or (_spec_map.get("tax") or {}).get("depreciation_method")
                or _dep_method
            )
            _dep_year_splits = [
                _fin_assets.depreciation_charge(
                    _dep_base,
                    _dep_years,
                    salvage_rate=_salvage_rate,
                    method=_dep_method,
                    year_index=year_index,
                    tax_method=_tax_dep_method,
                )
                for year_index in range(1, max(_op_years_dep, 1) + 1)
            ]
            _dep_split = _dep_year_splits[0] if _dep_year_splits else {}
            _dep_only = float(_dep_split.get("book_depreciation_wan") or 0.0)
            result.setdefault("raw", {})
            if isinstance(result.get("raw"), dict):
                result["raw"]["tax_depreciation_schedule"] = [
                    float(item.get("tax_depreciation_wan") or 0.0)
                    if year_index <= _dep_years else 0.0
                    for year_index, item in enumerate(_dep_year_splits, start=1)
                ]
                result["raw"]["book_depreciation_schedule"] = [
                    float(item.get("book_depreciation_wan") or 0.0)
                    if year_index <= _dep_years else 0.0
                    for year_index, item in enumerate(_dep_year_splits, start=1)
                ]
            _amort_only = _fin_assets.annual_straight_line(
                _intangible, _amort_years, salvage_rate=0.0) if _intangible > 0 else 0.0
            # 达产恒定 P&L 用「运营期平均摊提」避免短寿命时总成本被高估到全寿命年额：
            # 运营期总成本中的年折旧 = 年折旧额 × min(寿命,运营期) / 运营期（总额守恒）
            _dep_active_years = min(_dep_years, _op_years_dep)
            _amort_active_years = min(_amort_years, _op_years_dep) if _intangible > 0 else 0
            _dep_avg = round(_dep_only * _dep_active_years / max(_op_years_dep, 1), 2)
            _amort_avg = round(_amort_only * _amort_active_years / max(_op_years_dep, 1), 2) if _intangible > 0 else 0.0
            depreciation = round(_dep_avg + _amort_avg, 2)  # 非现金摊提合计（进总成本，运营期平均）
            if _dep_life.get("shorter_than_op"):
                assumptions.append(
                    f"折旧年限 {_dep_years} 年短于运营期 {_op_years_dep} 年：按用户寿命计提，"
                    f"期满后零折旧（不静默延长寿命，P1-2）")
        if _renewal_schedule.get("items"):
            if _dep_schedule_values:
                _base_dep_values = list(_dep_schedule_values)
            else:
                _base_dep_values = [
                    float(row.get("depreciation") or 0.0)
                    for row in _fin_assets.yearly_dep_schedule(
                        original=_dep_base,
                        annual=_dep_only,
                        years=_dep_years,
                        op_years=_op_years_dep,
                        salvage_rate=_salvage_rate,
                    )
                ]
            _dep_schedule_values = [
                round(
                    (_base_dep_values[index] if index < len(_base_dep_values) else 0.0)
                    + (_renewal_dep_values[index] if index < len(_renewal_dep_values) else 0.0),
                    2,
                )
                for index in range(_op_years_dep)
            ]
            _combined_rows = []
            _cumulative_dep = 0.0
            _renewal_cumulative = list(
                _renewal_schedule.get("cumulative_capex_by_year") or []
            )
            for index, charge in enumerate(_dep_schedule_values):
                _cumulative_dep = round(_cumulative_dep + charge, 2)
                _combined_rows.append({
                    "year": index + 1,
                    "original_value": round(
                        _dep_base
                        + (_renewal_cumulative[index] if index < len(_renewal_cumulative) else 0.0),
                        2,
                    ),
                    "depreciation": charge,
                    "cumulative_depreciation": _cumulative_dep,
                    "depreciation_basis": "renewal_composite",
                    "classes": list(
                        (_dep_class_schedule.get("rows") or [{}])[index].get("classes") or []
                    ) if index < len(_dep_class_schedule.get("rows") or []) else [],
                })
            result["investment"]["renewal_capex_total"] = _renewal_schedule["total_capex_wan"]
            result["investment"]["fixed_asset_additions_total"] = round(
                fixed_asset + float(_renewal_schedule["total_capex_wan"]), 2,
            )
            assumptions.append(
                f"运营期更新投资 {_fmt(_renewal_schedule['total_capex_wan'])} 万元按投用年进入现金流、折旧和期末净值"
            )
        else:
            _combined_rows = list(_dep_class_schedule.get("rows") or [])
        # 透传独立折旧/摊销与折旧基数给 _build_annual
        result["raw"]["dep_only"] = _dep_only
        result["raw"]["amort_only"] = _amort_only
        result["raw"]["dep_avg"] = _dep_avg
        result["raw"]["amort_avg"] = _amort_avg
        result["raw"]["dep_base"] = round(_dep_base, 2)
        result["raw"]["salvage_rate"] = _salvage_rate
        result["raw"]["depreciation_years"] = _dep_years
        result["raw"]["amortization_years"] = _amort_years
        result["raw"]["dep_life_meta"] = _dep_life
        result["raw"]["depreciation_classes"] = _dep_class_schedule.get("classes") or []
        result["raw"]["depreciation_class_schedule"] = _combined_rows
        result["raw"]["renewal_capex_schedule"] = _renewal_schedule
        result["raw"]["amort_life_meta"] = _amort_life
        result["raw"]["property_inventory"] = _property_inventory
        result["raw"]["vat_refund_rate"] = _vat_refund_rate
        # BC-4a：开发成本结转序列（建设投资 × 当年去化率）；period opex 仍来自 cost_items。
        _cogs_series: Optional[list[float]] = None
        _dev_cost_base = round(construction or 0.0, 2)  # 开发成本≈建设投资（不含建设期利息，利息另计财务费用）
        if _property_inventory and _absorption_series:
            _cogs_series = [round(_dev_cost_base * a, 2) for a in _absorption_series]
            result["raw"]["cogs_series"] = _cogs_series
            result["raw"]["development_cost_wan"] = _dev_cost_base
        # M5 T5.2/T5.4：成本要素明细法（原材料/燃料动力/工资/环保/其他）自下而上；否则 75% 总额法
        cost_items = fin.get("cost_items") or {}
        cash_cost = sum((_f(v) or 0.0) for v in cost_items.values()) if isinstance(cost_items, dict) else 0.0
        # B-2: vendor year-by-year opex series (input params, not outputs).
        # When present, do not scale peak cost_items by revenue ratio.
        _opex_by_year_raw = fin.get("operating_cost_by_year") or fin.get("opex_by_year") or []
        _opex_by_year: list[float] = []
        if isinstance(_opex_by_year_raw, (list, tuple)):
            for _item in _opex_by_year_raw:
                if isinstance(_item, dict):
                    _opex_by_year.append(round(_f(_item.get("value")) or 0.0, 2))
                else:
                    _opex_by_year.append(round(_f(_item) or 0.0, 2))
        if _op_cost_scale != 1.0:
            _opex_by_year = [
                round(value * _op_cost_scale, 2) for value in _opex_by_year
            ]
        if _opex_by_year and any(v > 0 for v in _opex_by_year):
            _peak_opex = round(max(_opex_by_year), 2)
            if cash_cost <= 0:
                cash_cost = _peak_opex
            result.setdefault("raw", {})
            result["raw"]["operating_cost_by_year"] = list(_opex_by_year)
            result["raw"]["cost_path"] = "user_operating_cost_by_year"
            assumptions.append(
                f"现金经营成本按输入逐年序列（{_opex_by_year[0]:.2f}…{_opex_by_year[-1]:.2f} 万元，"
                f"共 {len(_opex_by_year)} 年；达产峰值 {_peak_opex:.2f}）"
            )
        else:
            _opex_by_year = []
        # G1-C：成本路径策略（finance.cost_policy 或 spec.cost.cost_policy）
        #   user_items   — 默认：有明细时达产现金成本以 cost_items 为准
        #   hybrid       — 同默认，但优先尝试固定+可变拆分（与现状 hybrid 一致）
        #   spec_variable— 达产现金成本优先产品 var_cost 峰值（+可选期间费），不因 var>items 丢弃
        _cost_policy = str(
            fin.get("cost_policy")
            or ((spec or {}).get("cost") or {}).get("cost_policy")
            or "user_items"
        ).strip().lower()
        if _cost_policy in ("prefer_spec_var", "spec_var", "variable"):
            _cost_policy = "spec_variable"
        if _cost_policy not in ("user_items", "hybrid", "spec_variable"):
            _cost_policy = "user_items"
        result.setdefault("raw", {})
        result["raw"]["cost_policy"] = _cost_policy
        # 房产：明细法的 cost_items 视作期间费用（销售/管理），达产年 COGS 取最大去化年结转额
        _cogs_peak = round(max(_cogs_series), 2) if _cogs_series else 0.0
        if _property_inventory:
            _period_opex = cash_cost if (cost_items and cash_cost > 0) else 0.0
            if not (cost_items and cash_cost > 0):
                # 无明细时按营收×期间费率估销售/管理费（可研简化，不把开发成本当经营成本率）
                _period_rate = min(_cost_param(spec, "total_cost_rate", "cost"), 0.20)
                _period_opex = round(revenue * _period_rate, 2)
                assumptions.append(
                    f"房产期间费用（销售/管理）按营收 {_period_rate*100:.0f}% 估算（缺明细时的可研简化）")
            else:
                _parts = "、".join(f"{k} {_fmt(_f(v))}" for k, v in cost_items.items() if _f(v))
                assumptions.append(
                    f"房产期间费用按明细汇总（{_parts}）+ 开发成本随去化结转（峰值 {_fmt(_cogs_peak)} 万元）")
            op_cost = round(_period_opex + _cogs_peak + depreciation, 2)
            result["raw"]["period_opex"] = _period_opex
        elif _cost_policy == "spec_variable":
            # 先占位：达产现金成本在 var_cost 展开后决定；此处用 items/rate 作回退
            if cost_items and cash_cost > 0:
                op_cost = round(cash_cost + depreciation, 2)
                _parts = "、".join(f"{k} {_fmt(_f(v))}" for k, v in cost_items.items() if _f(v))
                assumptions.append(
                    f"cost_policy=spec_variable：暂以明细（{_parts}）为底，"
                    f"若产品 var_cost 更高则以 var 为准 + 折旧 {_fmt(depreciation)} 万元")
            else:
                cost_rate = _cost_param(spec, "total_cost_rate", "cost")
                op_cost = round(revenue * cost_rate, 2)
                assumptions.append(
                    f"cost_policy=spec_variable 且无明细：总成本费用率 {int(cost_rate*100)}% 作底")
            if not _opex_by_year:
                result["raw"]["cost_path"] = "spec_variable_pending"
        elif cost_items and cash_cost > 0:
            op_cost = round(cash_cost + depreciation, 2)  # 总成本费用 = 现金经营成本 + 折旧
            _parts = "、".join(f"{k} {_fmt(_f(v))}" for k, v in cost_items.items() if _f(v))
            assumptions.append(
                f"总成本按明细法自下而上汇总（{_parts}）+ "
                f"运营期平均折旧/摊销 {_fmt(depreciation)} 万元、"
                f"所得税率 {int(income_tax_rate*100)}%"
            )
            if not _opex_by_year:
                result["raw"]["cost_path"] = "user_cost_items"
        else:
            cost_rate = _cost_param(spec, "total_cost_rate", "cost")  # 总成本费用率（原硬编码 0.75）
            op_cost = round(revenue * cost_rate, 2)
            assumptions.append(f"总成本费用率按 {int(cost_rate*100)}% 估算、所得税率 {int(income_tax_rate*100)}%（缺明细时的可研简化口径）")
            if not _opex_by_year:
                result["raw"]["cost_path"] = "spec_or_config_total_cost_rate"
        # 【P1-5】成本单因素缩放：只动现金经营成本（折旧独立、不随成本缩放），令利润/所得税联动重算。
        if _op_cost_scale != 1.0:
            _cash_only = round((op_cost - depreciation) * _op_cost_scale, 2)
            op_cost = round(_cash_only + depreciation, 2)
        # PG5-a 附表5 增值税（先算，供附加税同源）
        vat_rate = _vat_rate_in
        vat_input_rate = _f(fin.get("vat_input_rate")) or 0.10
        _cash_cost_for_vat = round(op_cost - depreciation, 2)
        vat_output = round(revenue * vat_rate, 2)
        vat_input = round(max(_cash_cost_for_vat, 0.0) * vat_input_rate, 2)
        vat_payable = round(max(vat_output - vat_input, 0.0), 2)
        # 附加税：【P0 同源】默认按应纳增值税×附加率；仅显式关闭 surtax_on_vat 时用营收比例。
        if _surtax_on_vat:
            tax_surcharge, _peak_surtax_components = _surtax_for(vat_payable)
            result["raw"]["surtax_components_peak_year"] = _peak_surtax_components
            assumptions.append(
                f"营业税金及附加按实际应纳增值税与消费税之和×{float(_surtax_vat_rate)*100:.1f}% 计算"
                f"（达产年 {_fmt(tax_surcharge)} 万元；与附表5/7/9 同源）")
        else:
            tax_surcharge = round(revenue * vat_add_rate, 2)
            assumptions.append(
                f"营业税金及附加按营收×{vat_add_rate*100:.2f}% 简化估算"
                f"（surtax_on_vat=false；达产年 {_fmt(tax_surcharge)} 万元）")
        # BC-4b：增值税即征即退（如污水 70%）→ 实缴增值税减少，退税额作经营现金流入
        vat_refund = round(vat_payable * _vat_refund_rate, 2) if _vat_refund_rate > 0 else 0.0
        vat_net_payable = round(max(vat_payable - vat_refund, 0.0), 2)
        if _vat_refund_rate > 0:
            assumptions.append(
                f"增值税即征即退比例 {_vat_refund_rate:.0%}：达产年应纳 {_fmt(vat_payable)} 万元、"
                f"退税 {_fmt(vat_refund)} 万元、实缴 {_fmt(vat_net_payable)} 万元（计入经营现金流）")
        profit_before = round(revenue - op_cost - tax_surcharge, 2)
        income_tax = round(max(profit_before, 0.0) * income_tax_rate, 2)
        net_profit = round(profit_before - income_tax, 2)
        # 房地产开发成本已在建设期投资现金流中支付；销售时的存货成本结转仅影响
        # 会计利润和所得税，不能再次作为项目现金支出。故项目经营现金流需加回 COGS。
        _inventory_cogs_addback = _cogs_peak if _property_inventory else 0.0
        op_cashflow = round(
            net_profit + depreciation + vat_refund + _inventory_cogs_addback, 2
        )

        op_years = max(calc_years - build_years, 1)
        # BC-1（H4）逐年所得税率：按 effective tax（fin 优先）免税期/减半期生成序列。
        tax_rate_sched = _income_tax_schedule(_eff_tax_spec, income_tax_rate, op_years)
        _holiday_y = _tax_spec_int(_eff_tax_spec, "tax_holiday_years")
        _half_y = _tax_spec_int(_eff_tax_spec, "tax_half_years")
        _tax_incentive = (_holiday_y > 0 or _half_y > 0)
        # BC-3 现金经营成本拆分：产品级可变成本率给出的 var_cost_series 若可用（达产年可变成本
        #   ≤ 达产年现金经营成本，避免固定成本为负），则按「固定 + 逐年可变」建模——非达产年
        #   固定成本不随营收等比缩水，比纯比例法更贴实际；否则退回原比例法。达产年恒等于
        #   op_cash_cost（固定 + 达产可变 = 现金经营成本），保证达产口径与勾稽字节级不变。
        # BC-4a：房产路径现金成本 = 期间费用（固定）+ 当年开发成本结转（随去化），不走比例法。
        op_cash_cost = round(op_cost - depreciation, 2)   # 达产年现金经营成本（不含折旧）
        _period_opex_y = round((result.get("raw") or {}).get("period_opex") or 0.0, 2) if _property_inventory else 0.0
        _var_full = round(max(var_cost_series), 2) if var_cost_series else 0.0
        # spec_variable 仍必须保留可识别的固定现金成本。工资、修理、管理等不得整段随产量归零。
        if (
            (not _property_inventory)
            and _cost_policy == "spec_variable"
            and var_cost_series
            and _var_full > 0
        ):
            _fixed_names = ("工资", "人工", "福利", "修理", "维修", "管理", "保险", "租赁", "环保")
            _fixed_floor = round(sum(
                float(v or 0.0) for k, v in (cost_items or {}).items()
                if any(name in str(k) for name in _fixed_names)
            ), 2)
            # 用户明细高于纯 var 的差额也视为期间/固定费用；两者取高，避免固定成本被压成 0。
            _period_add = max(_fixed_floor, round(max(cash_cost - _var_full, 0.0), 2))
            op_cash_cost = round(_var_full + _period_add, 2)
            op_cost = round(op_cash_cost + depreciation, 2)
            # 重算税与利润（与达产口径联动）
            _cash_cost_for_vat = op_cash_cost
            vat_input = round(max(_cash_cost_for_vat, 0.0) * vat_input_rate, 2)
            vat_payable = round(max(vat_output - vat_input, 0.0), 2)
            if _surtax_on_vat:
                tax_surcharge, _peak_surtax_components = _surtax_for(vat_payable)
                result["raw"]["surtax_components_peak_year"] = _peak_surtax_components
            else:
                tax_surcharge = round(revenue * vat_add_rate, 2)
            vat_refund = round(vat_payable * _vat_refund_rate, 2) if _vat_refund_rate > 0 else 0.0
            vat_net_payable = round(max(vat_payable - vat_refund, 0.0), 2)
            profit_before = round(revenue - op_cost - tax_surcharge, 2)
            income_tax = round(max(profit_before, 0.0) * income_tax_rate, 2)
            net_profit = round(profit_before - income_tax, 2)
            op_cashflow = round(net_profit + depreciation + vat_refund, 2)
            result["raw"]["cost_path"] = "spec_variable"
            result["raw"]["cost_path_detail"] = {
                "peak_cash_opex": op_cash_cost,
                "peak_variable": _var_full,
                "period_add": _period_add,
                "fixed_cash": _period_add,
                "fixed_floor_from_items": _fixed_floor,
                "note": (
                    "cost_policy=spec_variable：达产现金成本=产品 var_cost 峰值"
                    + (f"+固定/期间费 {_period_add}" if _period_add else "（未识别固定项，正式交付需复核）")
                    + "；LLM/产品 var_cost_rate 已落地"
                ),
            }
            assumptions.append(
                f"成本路径 spec_variable：达产现金成本 {_fmt(op_cash_cost)}="
                f"可变 {_fmt(_var_full)}"
                + (f"+固定/期间 {_fmt(_period_add)}" if _period_add else "")
                + "（产品 var 优先，非 ignore）"
            )

        _use_var_split = (
            (not _property_inventory)
            and bool(var_cost_series)
            and 0.0 < _var_full <= op_cash_cost
        )
        # hybrid 策略与默认 user_items 在可拆分时行为相同；spec_variable 已强制用 var
        if _cost_policy == "hybrid" and var_cost_series and _var_full > op_cash_cost:
            # hybrid 显式要求拆分时仍不造负固定：保持 ignore 并告警
            pass
        _fixed_cash = round(op_cash_cost - _var_full, 2) if _use_var_split else 0.0
        # 成本路径审计：有 cost_items 时达产现金成本以用户明细为准；var_cost 仅作逐年拆分
        if _use_var_split:
            result["raw"]["cost_path"] = (
                "spec_variable" if _cost_policy == "spec_variable"
                else "hybrid_user_items_plus_product_var"
            )
            result["raw"]["cost_path_detail"] = {
                "peak_cash_opex": op_cash_cost,
                "peak_variable": _var_full,
                "fixed_cash": _fixed_cash,
                "cost_policy": _cost_policy,
                "note": (
                    "达产现金经营成本=用户 cost_items（+注入）或 spec_variable 调整后；"
                    "爬坡年=固定现金+产品可变成本序列；"
                    "spec.total_cost_rate 不覆盖用户明细"
                ),
            }
            assumptions.append(
                f"成本路径 hybrid：达产现金成本 {_fmt(op_cash_cost)} 万元（用户明细）="
                f"固定 {_fmt(_fixed_cash)} + 达产可变 {_fmt(_var_full)}；"
                f"爬坡年可变随产品量价，固定不随营收等比缩水"
            )
        elif var_cost_series and _var_full > op_cash_cost and _cost_policy != "spec_variable":
            result["raw"]["cost_path"] = "user_cost_items_var_ignored"
            result["raw"]["cost_path_detail"] = {
                "peak_cash_opex": op_cash_cost,
                "peak_variable": _var_full,
                "cost_policy": _cost_policy,
                "note": (
                    "产品 var_cost 达产可变成本高于用户 cost_items 现金成本，"
                    "为免固定成本为负已忽略 var 拆分；LLM var_cost_rate 未落地。"
                    "若需落地请设 finance.cost_policy=spec_variable"
                ),
            }
            assumptions.append(
                f"成本路径警告：产品可变成本达产 {_fmt(_var_full)} > 用户明细现金成本 {_fmt(op_cash_cost)}，"
                f"已忽略 var_cost 拆分（请调低 var_cost_rate、提高 cost_items，"
                f"或 cost_policy=spec_variable）"
            )
        elif not (cost_items and cash_cost > 0) and _cost_policy != "spec_variable":
            result["raw"].setdefault("cost_path", "spec_or_config_total_cost_rate")
        result["raw"]["cost_policy"] = _cost_policy
        # BC-1：即便 flat（无 revenue_series），只要有税收优惠也需逐年建 P&L（否则优惠落不了地）；
        # 无 revenue_series 且无优惠时 pnl_by_year=None，运营期恒为 op_cashflow（字节级不变，老测试全绿）。
        # BC-4a：房产有 absorption 时强制建逐年 P&L（销售成本随去化）。
        # B-2: keep cost_path when opex series present
        if _opex_by_year:
            result.setdefault("raw", {})
            result["raw"]["cost_path"] = "user_operating_cost_by_year"
            result["raw"]["operating_cost_by_year"] = list(_opex_by_year)

        pnl_by_year: Optional[list[dict[str, float]]] = None
        if revenue and revenue > 0 and (
            revenue_series or _tax_incentive or _property_inventory or _dep_schedule_values
        ):
            pnl_by_year = []
            if revenue_series:
                _series = list(revenue_series[:op_years])
                _series += [_series[-1]] * (op_years - len(_series)) if _series else [revenue] * op_years
                _var_seq = list(var_cost_series[:op_years]) if var_cost_series else None
                if _var_seq is not None:
                    _var_seq += [_var_seq[-1]] * (op_years - len(_var_seq)) if _var_seq else [0.0] * op_years
            else:
                _series = [revenue] * op_years           # flat + 有优惠：营收恒定，仅税率逐年变
                _var_seq = None
            _cogs_seq = None
            if _cogs_series:
                _cogs_seq = list(_cogs_series[:op_years])
                _cogs_seq += [0.0] * (op_years - len(_cogs_seq))
            # 【P1-3】先算逐年利润总额与税率，再统一走亏损结转滚动账户求所得税（不逐年独立 max）。
            _profits_y: list[float] = []
            _rates_y: list[float] = []
            _rows_pre: list[dict[str, float]] = []
            for j in range(op_years):
                rev_y = _series[j]
                ratio = rev_y / revenue if revenue else 0.0
                _noncash_y = depreciation
                if _dep_schedule_values:
                    _class_dep_y = (
                        _dep_schedule_values[j]
                        if j < len(_dep_schedule_values) else 0.0
                    )
                    _amort_y_model = (
                        _amort_only if j < max(_amort_years, 0) else 0.0
                    )
                    _noncash_y = round(_class_dep_y + _amort_y_model, 2)
                if _property_inventory:
                    cogs_y = _cogs_seq[j] if _cogs_seq is not None else 0.0
                    # B-2: year opex series overrides constant period_opex; COGS still follows absorption
                    if _opex_by_year:
                        _po = (
                            _opex_by_year[j]
                            if j < len(_opex_by_year)
                            else _opex_by_year[-1]
                        )
                        cash_cost_y = round(float(_po) + cogs_y, 2)
                    else:
                        cash_cost_y = round(_period_opex_y + cogs_y, 2)
                elif _opex_by_year:
                    cash_cost_y = round(
                        float(
                            _opex_by_year[j]
                            if j < len(_opex_by_year)
                            else _opex_by_year[-1]
                        ),
                        2,
                    )
                elif _use_var_split and _var_seq is not None:
                    cash_cost_y = round(_fixed_cash + _var_seq[j], 2)   # 固定 + 逐年可变（BC-3）
                else:
                    cash_cost_y = round(op_cash_cost * ratio, 2)       # 现金经营成本随营收等比（可研简化）
                op_cost_y = round(cash_cost_y + _noncash_y, 2)
                # 【P0 附加税同源】逐年附加税与达产口径一致：优先增值税附加，否则营收比例
                if _surtax_on_vat:
                    _vo = round(rev_y * vat_rate, 2)
                    _vi = round(max(cash_cost_y, 0.0) * vat_input_rate, 2)
                    _vp = round(max(_vo - _vi, 0.0), 2)
                    tax_sur_y, _component_y = _surtax_for(_vp, j)
                else:
                    tax_sur_y = round(rev_y * vat_add_rate, 2)
                profit_y = round(rev_y - op_cost_y - tax_sur_y, 2)
                rate_y = tax_rate_sched[j] if j < len(tax_rate_sched) else income_tax_rate
                _profits_y.append(profit_y)
                _rates_y.append(rate_y)
                _rows_pre.append({
                    "revenue": rev_y,
                    "op_cost": op_cost_y,
                    "op_cash_cost": cash_cost_y,
                    "inventory_cogs_addback": cogs_y if _property_inventory else 0.0,
                    "noncash_charge": _noncash_y,
                    "tax_surcharge": tax_sur_y,
                    "profit_before": profit_y,
                })
            # 【P1-3】亏损弥补结转：亏损年 tax=0 且累计可抵，盈利年先冲抵再计税（最长结转 5 年）。
            _cf_years = _tax_spec_int(_eff_tax_spec, "loss_carryforward_years") or _DEFAULT_LOSS_CARRYFORWARD_YEARS
            _tax_rows = _compute_income_tax_with_loss_carryforward(
                _profits_y, _rates_y, carryforward_years=_cf_years)
            _had_loss_offset = any(tr["loss_used"] > 0 for tr in _tax_rows)
            for j in range(op_years):
                profit_y = _rows_pre[j]["profit_before"]
                tax_y = _tax_rows[j]["income_tax"]
                net_y = round(profit_y - tax_y, 2)
                # BC-4b：逐年增值税退税（按营收比例缩放达产年退税额）
                rev_y = _rows_pre[j]["revenue"]
                ratio_r = rev_y / revenue if revenue else 0.0
                vat_refund_y = round(vat_refund * ratio_r, 2) if vat_refund > 0 else 0.0
                pnl_by_year.append({
                    **_rows_pre[j],
                    "income_tax": tax_y, "net_profit": net_y,
                    "taxable_income": _tax_rows[j]["taxable"],
                    "loss_used": _tax_rows[j]["loss_used"],
                    "loss_balance_end": _tax_rows[j]["loss_balance_end"],
                    "vat_refund": vat_refund_y,
                    "op_cashflow": round(
                        net_y + float(_rows_pre[j].get("noncash_charge", depreciation)) + vat_refund_y
                        + float(_rows_pre[j].get("inventory_cogs_addback") or 0.0),
                        2,
                    ),
                })
            if _had_loss_offset:
                assumptions.append(
                    "所得税已按亏损弥补结转口径逐年测算（亏损年免税并累计，盈利年先弥补以前年度亏损"
                    f"再计税，结转期 {_cf_years} 年，依《企业所得税法》第十八条）")
        if _tax_incentive:
            _parts = []
            if _holiday_y > 0:
                _parts.append(f"前 {_holiday_y} 年免征")
            if _half_y > 0:
                _parts.append(f"随后 {_half_y} 年减半（税率×0.5）")
            assumptions.append(
                f"所得税优惠：{'、'.join(_parts)}，之后恢复 {int(income_tax_rate*100)}%"
                f"（自投产年起算的可研简化口径；实际税法多自首个获利年度起算，须人工按项目认定复核）")

        # 现金流序列：建设期分年投入(负)，运营期经营净现金流(正)，末年回收流动资金+固定资产余值
        cashflows: list[float] = []
        # B-2: prefer vendor construction outlay schedule over uniform split
        _outlay_raw = fin.get("construction_outlay_by_year") or fin.get("build_outlay_by_year") or []
        _outlay_plan: list[float] = []
        if isinstance(_outlay_raw, (list, tuple)) and any(_f(x) for x in _outlay_raw):
            for _item in _outlay_raw:
                if isinstance(_item, dict):
                    _outlay_plan.append(round(_f(_item.get("value")) or 0.0, 2))
                else:
                    _outlay_plan.append(round(_f(_item) or 0.0, 2))
            # pad/truncate to build_years
            if len(_outlay_plan) < build_years:
                _outlay_plan = _outlay_plan + [0.0] * (build_years - len(_outlay_plan))
            elif len(_outlay_plan) > build_years:
                # collapse surplus into last build year (keep total)
                _head = _outlay_plan[: build_years - 1]
                _tail = round(sum(_outlay_plan[build_years - 1 :]), 2)
                _outlay_plan = _head + [_tail]
            # scale to match construction+interest total if slight drift
            # Trust the vendor year plan as input; do not re-scale to
            # construction+interest totals (that re-injects IDC already timed).
            _target = round((construction or 0.0) + (interest or 0.0), 2)
            _sum = round(sum(_outlay_plan), 2)
            result.setdefault("raw", {})
            result["raw"]["construction_outlay_sum"] = _sum
            result["raw"]["construction_interest_total"] = _target
            if _target > 0 and _sum > 0 and abs(_sum - _target) / max(_target, 1.0) > 0.05:
                assumptions.append(
                    f"建设期分年计划合计 {_fmt(_sum)} 与建设投资+利息 {_fmt(_target)} 偏差"
                    f"{abs(_sum-_target)/_target*100:.1f}%（保留分年计划，不强制重分摊）")
            result["raw"]["construction_outlay_by_year"] = list(_outlay_plan)
            _eq = fin.get("equity_inject_by_year") or []
            if isinstance(_eq, (list, tuple)) and any(_f(x) for x in _eq):
                result["raw"]["equity_inject_by_year"] = [
                    round(_f(x) or 0.0, 2) for x in _eq
                ]
            result["raw"]["build_phasing"] = "user_construction_outlay_by_year"
            _ld = fin.get("loan_draw_by_year") or []
            if isinstance(_ld, (list, tuple)) and any(_f(x) for x in _ld):
                result["raw"]["loan_draw_by_year"] = [
                    round(_f(x) or 0.0, 2) for x in _ld
                ]
            outlay_text = " / ".join(_fmt(v) for v in _outlay_plan)
            assumptions.append(
                f"建设期投资按输入分年计划（{outlay_text} 万元）"
            )
            for _amt in _outlay_plan:
                cashflows.append(-round(float(_amt), 2))
        else:
            per_build = round((construction + interest) / build_years, 2)
            result.setdefault("raw", {})
            result["raw"]["build_phasing"] = "uniform_construction_interest"
            for _y in range(build_years):
                cashflows.append(-per_build)
        # 流动资金在投产首年投入
        # 【P1-03】期末资产回收 = 残值 + 计算期内未折完/未摊完的账面净值。
        #   原口径只回收 fixed_asset×残值率;当折旧/摊销年限 > 计算期(运营年数)时,资产尚有未折完账面净值,
        #   漏回收会系统性低估终值与 IRR。未折完额 = 年折旧×(折旧年限−运营期);折旧年限≤运营期时该项=0,
        #   即"达产恒定简化"默认样例(折旧年限=运营期)口径不变、IRR 不变。残值率用 _salvage_rate(与折旧同源)。
        # BC-4a 房产：期末回收 = 未售开发产品账面余额（开发成本 − 已结转销售成本），无固定资产残值。
        if _property_inventory:
            _cogs_charged = round(sum(_cogs_series or []), 2)
            _unsold_inventory = round(max(_dev_cost_base - _cogs_charged, 0.0), 2)
            salvage = _unsold_inventory
            result["raw"]["terminal_recovery"] = salvage
            result["raw"]["terminal_meta"] = {
                "terminal_recovery": salvage,
                "method": "unsold_development_inventory",
                "development_cost": _dev_cost_base,
                "cogs_charged": _cogs_charged,
                "unsold_inventory": _unsold_inventory,
            }
            if _unsold_inventory > 0:
                assumptions.append(
                    f"期末回收未售开发产品账面余额 {_fmt(_unsold_inventory)} 万元"
                    f"（开发成本 {_fmt(_dev_cost_base)} − 已结转 {_fmt(_cogs_charged)}）")
            _salvage_residual = 0.0
            _dep_unrecovered = 0.0
            _amort_unrecovered = 0.0
        elif _dep_class_schedule.get("rows"):
            _salvage_residual = round(
                float(_dep_class_schedule.get("salvage_value_wan") or 0.0), 2
            )
            _class_terminal = round(
                float(_dep_class_schedule.get("terminal_book_value_wan") or 0.0), 2
            )
            _dep_unrecovered = round(max(_class_terminal - _salvage_residual, 0.0), 2)
            _amort_unrecovered = round(
                _amort_only * max(_amort_years - op_years, 0), 2
            ) if _intangible > 0 else 0.0
            salvage = round(_class_terminal + _amort_unrecovered, 2)
            result["raw"]["terminal_recovery"] = salvage
            result["raw"]["terminal_meta"] = {
                "terminal_recovery": salvage,
                "method": "classified_asset_book_value_plus_amort_unrecovered",
                "class_terminal_book_value": _class_terminal,
                "salvage": _salvage_residual,
                "unrecovered_dep": _dep_unrecovered,
                "unrecovered_amort": _amort_unrecovered,
                "classes": _dep_class_schedule.get("classes") or [],
            }
            if _dep_unrecovered > 0 or _amort_unrecovered > 0:
                assumptions.append(
                    f"计算期({op_years}年)末按资产类别回收未折完账面净值 "
                    f"{_fmt(round(_dep_unrecovered + _amort_unrecovered, 2))} 万元"
                )
        else:
            _term = _fin_assets.terminal_recovery(
                original=_dep_base,
                annual_dep=_dep_only,
                dep_years=_dep_years,
                op_years=op_years,
                salvage_rate=_salvage_rate,
                amort_original=_intangible,
                annual_amort=_amort_only,
                amort_years=_amort_years if _intangible > 0 else 0,
            )
            # 固定资产原值口径的残值仍按 fixed_asset 计（与旧样例对齐），未折完按折旧基数路径
            _salvage_residual = round(fixed_asset * _salvage_rate, 2)
            _dep_unrecovered = _term["unrecovered_dep"]
            _amort_unrecovered = _term["unrecovered_amort"]
            # 当寿命>运营期：用残值 + 未折完（与旧 P1-03 测试契约一致）
            salvage = round(_salvage_residual + _dep_unrecovered + _amort_unrecovered, 2)
            result["raw"]["terminal_recovery"] = salvage
            result["raw"]["terminal_meta"] = _term
            if _dep_unrecovered > 0 or _amort_unrecovered > 0:
                assumptions.append(
                    f"计算期({op_years}年)短于折旧/摊销年限,期末回收未折完账面净值 "
                    f"{_fmt(round(_dep_unrecovered + _amort_unrecovered, 2))} 万元"
                    f"（P1-03:除残值 {_fmt(_salvage_residual)} 万元外,补回未折完部分,避免低估终值）")
        if _renewal_schedule.get("items"):
            renewal_terminal = round(
                float(_renewal_schedule.get("terminal_book_value_wan") or 0.0), 2,
            )
            salvage = round(salvage + renewal_terminal, 2)
            result["raw"]["terminal_recovery"] = salvage
            result["raw"].setdefault("terminal_meta", {})
            result["raw"]["terminal_meta"].update({
                "renewal_terminal_book_value": renewal_terminal,
                "renewal_capex_total": _renewal_schedule.get("total_capex_wan"),
                "terminal_recovery": salvage,
            })
        # B-2: optional vendor terminal recovery overrides (input rows on CF sheet)
        _wc_recover = round(float(working or 0.0), 2)
        if fin.get("terminal_fixed_asset_recover_wan") is not None:
            salvage = round(_f(fin.get("terminal_fixed_asset_recover_wan")) or 0.0, 2)
            result.setdefault("raw", {})
            result["raw"]["terminal_recovery"] = salvage
            result["raw"]["terminal_meta"] = {
                "terminal_recovery": salvage,
                "method": "user_terminal_fixed_asset_recover",
            }
            assumptions.append(
                f"期末固定资产余值回收按输入 {_fmt(salvage)} 万元（来自甲方现金流量表行）")
        if fin.get("terminal_working_capital_recover_wan") is not None:
            _wc_recover = round(_f(fin.get("terminal_working_capital_recover_wan")) or 0.0, 2)
            result.setdefault("raw", {})
            result["raw"]["terminal_wc_recovery"] = _wc_recover
            assumptions.append(
                f"期末流动资金回收按输入 {_fmt(_wc_recover)} 万元（来自甲方现金流量表行）")

        for j in range(op_years):
            cf = pnl_by_year[j]["op_cashflow"] if pnl_by_year else op_cashflow
            renewal_capex = float(
                (_renewal_schedule.get("capex_by_year") or [0.0] * op_years)[j]
            )
            cf -= renewal_capex
            if j == 0:
                cf -= working  # P1-c：投产年投入【全额】流动资金（现行可研口径，非"×30%铺底"旧制）
            if j == op_years - 1:
                cf += _wc_recover + salvage  # 末年回收全额流动资金 + 固定资产余值
            cashflows.append(round(cf, 2))

        try:
            irr_v = _irr(cashflows)
        except Exception as _irr_exc:  # noqa: BLE001
            irr_v = None
            assumptions.append(
                f"项目 IRR 未能求出（{type(_irr_exc).__name__}: {str(_irr_exc)[:80]}）；"
                f"现金流期数={len(cashflows)}"
            )
        try:
            npv_v = _npv(cashflows, bench)
        except Exception:  # noqa: BLE001
            npv_v = None
        discount_scenarios = []
        for scenario_rate in (fin.get("discount_rate_scenarios") or []):
            try:
                rate_value = float(scenario_rate)
                if not 0 < rate_value < 1:
                    raise ValueError
                discount_scenarios.append({
                    "discount_rate": rate_value,
                    "npv_wan": round(_npv(cashflows, rate_value), 2),
                })
            except (TypeError, ValueError):
                continue
        result["discount_rate_scenarios"] = discount_scenarios
        try:
            pb = _payback(cashflows, rate=bench)
            static_pb = pb.static_years
            dyn_pb = pb.dynamic_years
        except Exception:  # noqa: BLE001
            static_pb = dyn_pb = None

        # 指标摘要的“达产年”必须与逐年利润表使用同一个期间。
        # 取首次达到峰值收入的运营年，禁止把全运营期平均折旧称为达产年折旧。
        if pnl_by_year:
            peak_revenue = max(float(row.get("revenue") or 0.0) for row in pnl_by_year)
            peak_row = next(
                row for row in pnl_by_year
                if abs(float(row.get("revenue") or 0.0) - peak_revenue) <= 1e-9
            )
            revenue = round(float(peak_row.get("revenue") or 0.0), 2)
            op_cost = round(float(peak_row.get("op_cost") or 0.0), 2)
            tax_surcharge = round(float(peak_row.get("tax_surcharge") or 0.0), 2)
            profit_before = round(float(peak_row.get("profit_before") or 0.0), 2)
            income_tax = round(float(peak_row.get("income_tax") or 0.0), 2)
            net_profit = round(float(peak_row.get("net_profit") or 0.0), 2)
            depreciation = round(float(peak_row.get("noncash_charge") or 0.0), 2)
            op_cashflow = round(float(peak_row.get("op_cashflow") or 0.0), 2)
            peak_cash_cost = round(float(peak_row.get("op_cash_cost") or 0.0), 2)
            vat_output = round(revenue * vat_rate, 2)
            vat_input = round(max(peak_cash_cost, 0.0) * vat_input_rate, 2)
            vat_payable = round(max(vat_output - vat_input, 0.0), 2)
            vat_refund = round(float(peak_row.get("vat_refund") or 0.0), 2)
            vat_net_payable = round(max(vat_payable - vat_refund, 0.0), 2)
            assumptions.append(
                "达产年指标取首次达到峰值收入的逐年利润表期间；"
                f"该年折旧/摊销 {_fmt(depreciation)} 万元，不使用全运营期平均值"
            )

        # 盈亏平衡点（简化：固定成本占比近似）
        _bep_fc = _cost_param(spec, "bep_fixed_cost_ratio", "cost")  # 盈亏固定成本占比（原硬编码 0.3）
        bep = round(op_cost * _bep_fc / max(revenue - op_cost * (1 - _bep_fc), 1) * 100, 2) if revenue else None

        indicators = {
            "revenue": revenue, "op_cost": op_cost, "tax_surcharge": tax_surcharge,
            "vat_output": vat_output, "vat_input": vat_input, "vat_payable": vat_payable,
            "vat_refund": vat_refund, "vat_net_payable": vat_net_payable,
            "vat_rate": vat_rate, "vat_input_rate": vat_input_rate,
            "profit_before": profit_before, "income_tax": income_tax, "net_profit": net_profit,
            "depreciation": depreciation, "op_cashflow": op_cashflow,
            "project_irr_pct": round(irr_v * 100, 2) if irr_v is not None else None,
            "npv_wan": round(npv_v, 2) if npv_v is not None else None,
            "static_payback_years": round(static_pb, 2) if static_pb is not None else None,
            "dynamic_payback_years": round(dyn_pb, 2) if dyn_pb is not None else None,
            "bep_pct": bep,
            "benchmark_rate_pct": round(bench * 100, 1),
        }
        result["operating"] = {
            "revenue": revenue, "op_cost": op_cost, "net_profit": net_profit,
            "cashflows": cashflows,
            "pnl_by_year": pnl_by_year,   # BC-P2：逐年 P&L（spec 非 flat 时非空，供逐年附表）
        }
    else:
        result["operating"] = {
            "is_operating": bool(is_operating),
            "revenue": revenue,
            "skipped_reason": (
                "not_operating" if not is_operating
                else ("missing_or_zero_revenue" if not revenue or revenue <= 0 else "unknown")
            ),
        }
        if not is_operating:
            assumptions.append("非经营性项目：不计算 IRR/NPV，按全生命周期资金平衡分析")
        elif not revenue or revenue <= 0:
            assumptions.append(
                "经营性项目但达产营收缺失或≤0，未生成 IRR/NPV 指标"
                "（请提供 annual_revenue_wan 或有效收入模型参数）"
            )
            # 仍给出空壳 indicators，避免下游把「缺键」当成「未计算」
            indicators = {
                "revenue": revenue or 0.0,
                "project_irr_pct": None,
                "npv_wan": None,
                "static_payback_years": None,
                "dynamic_payback_years": None,
            }

    # 【P0-1/P0-2】schema 信封 + 统一时间轴
    _schema = _fin_normalize.normalize_finance_inputs(fin)
    # 用已解析的分项刷新 scope（与 investment.scope_status 同源）
    _schema["scope_status"] = (result.get("investment") or {}).get("scope_status") or _schema.get("scope_status")
    result["finance_schema_version"] = FINANCE_SCHEMA_VERSION
    result["schema"] = _schema
    _grace = int(_f(fin.get("loan_grace_years")) or 0)
    _dep_y_tl = int((result.get("raw") or {}).get("depreciation_years") or max(calc_years - build_years, 1))
    _amort_y_tl = int((result.get("raw") or {}).get("amortization_years") or 10)
    _timeline_mode = str((fin.get("timeline") or {}).get("mode") or "annual")
    result["timeline"] = _fin_timeline.build_timeline(
        calc_years=calc_years,
        build_years=build_years,
        build_period_months=fin.get("build_period_months"),
        loan_years=loan_years,
        loan_grace_years=_grace,
        depreciation_years=_dep_y_tl,
        amortization_years=_amort_y_tl,
        mode=_timeline_mode,
    )
    result["params"]["loan_grace_years"] = _grace
    result["params"]["loan_repay_method"] = fin.get("loan_repay_method") or "equal_principal"
    # S2 T-debt: vendor 还本/付息序列（输入侧）→ 偿债表用 principal_schedule
    result.setdefault("raw", {})
    if isinstance(result.get("raw"), dict):
        _ps = fin.get("loan_principal_by_year") or fin.get("debt_principal_by_year")
        _is = fin.get("loan_interest_by_year") or fin.get("debt_interest_by_year")
        if isinstance(_ps, (list, tuple)) and any(_f(x) for x in _ps):
            result["raw"]["loan_principal_by_year"] = [round(_f(x) or 0.0, 2) for x in _ps]
            result["params"]["loan_repay_method"] = "principal_schedule"
            result["raw"]["loan_repay_method"] = "principal_schedule"
            assumptions.append(
                "还款还本序列按甲方「还本」输入行（引擎重算利息/余额/DSCR，不抄指标输出）"
            )
        if isinstance(_is, (list, tuple)) and any(_f(x) for x in _is):
            result["raw"]["loan_interest_by_year"] = [round(_f(x) or 0.0, 2) for x in _is]

    result["indicators"] = indicators
    # M1 T1.1：逐年联动附表 + 敏感性 + 情景（在渲染前算好，供 _render_tables 合并）
    result["annual"] = _build_annual(result)
    if result.get("timeline", {}).get("mode") == "monthly":
        monthly = _compute_monthly_from_year_drivers(result)
        result["monthly_detail"] = monthly
        _apply_monthly_aggregates_to_annual(result, monthly)
        result["monthly_detail_excluded_from_delivery_count"] = True
    result["project_metadata"] = copy.deepcopy(fin.get("project_metadata") or {})
    if (result.get("annual") or {}).get("non_operating_balance") is not None:
        result["non_operating_balance"] = result["annual"]["non_operating_balance"]
    # 【P1-5】敏感性/情景改「改输入重跑整模型」（方案 §10）：存原始调用上下文供重跑，
    #   _with_analysis=False 时跳过（重跑分支，防 sensitivity→compute→sensitivity 递归）。
    if _with_analysis:
        result["_call"] = {
            "finance": finance, "invest_type": invest_type,
            "build_period_months": build_period_months, "industry": industry, "spec": spec,
        }
        result["sensitivity"] = _build_sensitivity(result)
        result["scenarios"] = _build_scenarios(result)
        result.pop("_call", None)  # 瞬态上下文用完即删，不落入返回结构/审计
    else:
        result["sensitivity"] = {}
        result["scenarios"] = {}
    result["tables"] = _render_tables(result)
    result["summary_md"] = _render_summary(result)
    result["markers"] = _required_markers(result)
    result["basis_of_estimate_md"] = basis_of_estimate_md(result)  # M1 T1.3：估算依据说明

    # BC-P6：C 层定制计算回灌（默认关，须 FINANCE_SANDBOX=1 且 spec.custom 非空）。
    # 契约（方案 §6/§7）：定制结果作为输入覆盖回灌引擎重算，重算结果须过与 B 层
    # 相同的 check_consistency；任一勾稽不过或沙箱异常，丢弃定制项、回退 B 层结果。
    # 算术护栏不变——定制片段只产出「输入值」，IRR/NPV 仍由 finance_calc 纯函数求解。
    if _apply_custom:
        custom_result = _apply_custom_calcs(finance, result, invest_type=invest_type,
                                            build_period_months=build_period_months,
                                            industry=industry, spec=spec)
        if custom_result is not None:
            return custom_result
    return result


# C 层受限沙箱支持的定制目标白名单（映射到 finance 输入键；重算走确定性引擎）。
# 未在白名单内的 target 记录到 assumptions 后忽略，绝不静默改数。
_CUSTOM_TARGET_INPUTS = {
    "annual_revenue_wan", "wage_wan", "intangible_assets_wan",
    "amortization_years", "depreciation_years", "salvage_rate", "loan_rate",
}


def _apply_custom_calcs(finance: dict[str, Any], base_result: dict[str, Any], *,
                        invest_type: str, build_period_months: Optional[int],
                        industry: str, spec: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """BC-P6：跑 spec.custom 受限沙箱，回灌重算并过勾稽。采纳返回新 result，否则 None。

    - 默认关（sandbox.enabled() 为 False）或无 spec.custom → 返回 None（走 B 层）。
    - 定制结果按 target 白名单覆盖 finance 输入（``cost_items.<名>`` 前缀作追加成本行），
      重算后跑 check_consistency；全 ok 才采纳，否则回退 B 并在 assumptions 记录。
    """
    if not isinstance(spec, dict):
        return None
    customs = spec.get("custom") or []
    if not customs:
        return None
    try:
        from lvke_mcp.domains.finance import sandbox
    except Exception:  # noqa: BLE001 - 沙箱模块不可用即走 B 层
        return None
    if not sandbox.enabled():
        return None

    # 供定制片段只读引用的输入快照（不含可变引擎内部对象）
    base_inputs = {
        "investment": dict(base_result.get("investment") or {}),
        "funding": dict(base_result.get("funding") or {}),
        "indicators": dict(base_result.get("indicators") or {}),
        "params": dict(base_result.get("params") or {}),
    }
    try:
        outputs = sandbox.apply_custom_calcs(spec, base_inputs)
    except Exception as exc:  # noqa: BLE001 - 沙箱整体异常回退 B
        return _custom_fallback(base_result, f"定制计算异常，已回退标准模型（{str(exc)[:60]}）")
    if not outputs:
        return None

    override_fin = dict(finance or {})
    applied: list[str] = []
    for target, payload in outputs.items():
        if not isinstance(payload, dict) or "value" not in payload:
            # 含 error 或结构异常的定制项跳过（不采纳，不污染）
            continue
        value = payload.get("value")
        if target.startswith("cost_items."):
            name = target[len("cost_items."):].strip()
            fv = _f(value)
            if name and fv is not None:
                ci = dict(override_fin.get("cost_items") or {})
                ci[name] = fv
                override_fin["cost_items"] = ci
                applied.append(f"{target}={_fmt(fv)}")
        elif target in _CUSTOM_TARGET_INPUTS:
            fv = _f(value)
            if fv is not None:
                override_fin[target] = fv
                applied.append(f"{target}={_fmt(fv)}")
        else:
            applied.append(f"（忽略未知定制目标 {target}）")

    if not any("=" in a for a in applied):
        # 没有任何有效覆盖被应用 → 走 B 层
        return None

    # 回灌重算（_apply_custom=False 防递归），再过与 B 相同的勾稽门禁
    try:
        recomputed = compute_financials(
            override_fin, invest_type=invest_type, build_period_months=build_period_months,
            industry=industry, spec=spec, _apply_custom=False)
    except Exception as exc:  # noqa: BLE001 - 重算失败回退 B
        return _custom_fallback(base_result, f"定制计算重算失败，已回退标准模型（{str(exc)[:60]}）")
    if not recomputed.get("available"):
        return _custom_fallback(base_result, "定制计算重算结果不可用，已回退标准模型")

    # 采纳门禁：只在定制「新引入」了基线没有的勾稽失败时才回退。
    # 基线固有的软标记（如 _sample 的 §5.4 投资口径歧义、流资 ratio_backsolve）不牵连——
    # 这些是输入自带的口径问题，与本次定制无关，由 §9.4 发布门禁另行阻断终稿。
    # 对齐主测试 test_p001_sample_is_flagged_ambiguous_in_result 的「排除软标记判硬勾稽」约定。
    _SOFT_RULES = {"投资口径无歧义", "流动资金分项为业务周转驱动"}
    base_failed = {
        c.get("rule") for c in (check_consistency(base_result) or []) if not c.get("ok")
    }
    checks = check_consistency(recomputed)
    new_failed = [
        c for c in checks
        if not c.get("ok")
        and c.get("rule") not in _SOFT_RULES
        and c.get("rule") not in base_failed
    ]
    if new_failed:
        failed = "；".join(c.get("rule", "") for c in new_failed)
        return _custom_fallback(base_result, f"定制计算引入新的勾稽失败（{failed}），已回退标准模型")

    # 采纳：定制结果通过勾稽
    recomputed.setdefault("assumptions", []).append(
        "C 层定制计算已通过勾稽并采纳：" + "、".join(a for a in applied if "=" in a))
    recomputed["custom_applied"] = [a for a in applied if "=" in a]
    # 采纳后重建随 assumptions 变化的展示件（BoE 引用 assumptions）
    recomputed["basis_of_estimate_md"] = basis_of_estimate_md(recomputed)
    return recomputed


def _custom_fallback(base_result: dict[str, Any], note: str) -> dict[str, Any]:
    """C 层定制未采纳：在 B 层结果上记录回退原因并重建 BoE，返回 B 结果。"""
    base_result.setdefault("assumptions", []).append(note)
    base_result["custom_applied"] = []
    base_result["basis_of_estimate_md"] = basis_of_estimate_md(base_result)
    return base_result


def _rerun_scaled(r: dict[str, Any], *, rev: float = 1.0, cost: float = 1.0,
                  constr: float = 1.0) -> Optional[dict[str, Any]]:
    """【P1-5】按缩放钮重跑整模型，返回重算后的 result（税费/终值/偿债全联动）。

    读 compute_financials 存下的原始调用参数 ``_call``，以 ``_with_analysis=False``（防递归）
    与 ``_apply_custom=False``（敏感性不重复跑 C 层沙箱）重跑。任一异常返回 None（调用方降级）。
    """
    call = r.get("_call")
    if not call:
        return None
    try:
        return compute_financials(
            call["finance"], invest_type=call.get("invest_type", ""),
            build_period_months=call.get("build_period_months"),
            industry=call.get("industry", ""), spec=call.get("spec"),
            _apply_custom=False, _with_analysis=False,
            _revenue_scale=rev, _op_cost_scale=cost, _construction_scale=constr,
        )
    except Exception:  # noqa: BLE001 - 重跑失败时调用方降级
        return None


def _build_sensitivity(r: dict[str, Any]) -> dict[str, Any]:
    """单因素敏感性：真源 ``finance.scenarios.build_sensitivity``（整模型重跑）。"""
    if not r.get("_call"):
        return {}
    return _fin_scenarios.build_sensitivity(
        r,
        rerun=lambda rev=1.0, cost=1.0, constr=1.0, **_kw: _rerun_scaled(
            r, rev=rev, cost=cost, constr=constr,
        ),
    )


def _build_scenarios(r: dict[str, Any]) -> dict[str, Any]:
    """情景分析：真源 ``finance.scenarios.build_scenarios``（整模型重跑）。"""
    if not r.get("_call"):
        return {}
    return _fin_scenarios.build_scenarios(
        r,
        rerun=lambda rev=1.0, cost=1.0, constr=1.0, **_kw: _rerun_scaled(
            r, rev=rev, cost=cost, constr=constr,
        ),
    )
