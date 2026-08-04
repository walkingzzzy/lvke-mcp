"""Tax helpers (P1-3): VAT credit, surtax base, dual income-tax views.

真源模块：引擎 ``finance_model`` 通过薄封装调用本模块，禁止再维护平行实现。
"""

from __future__ import annotations

from typing import Any


def compute_vat_with_credit_carryover(
    output_by_year: list[float],
    input_by_year: list[float],
    *,
    opening_credit: float = 0.0,
) -> list[dict[str, Any]]:
    """逐年增值税（含留抵税额跨期结转）。

    返回每期 ``{output, input, input_used, payable, credit_begin, credit_end}``。
    无留抵时 payable == max(output-input, 0)，与旧单点口径一致。
    """
    credit = float(opening_credit or 0.0)
    rows: list[dict[str, Any]] = []
    n = max(len(output_by_year), len(input_by_year))
    for i in range(n):
        out_v = round(float(output_by_year[i] if i < len(output_by_year) else 0.0), 2)
        in_v = round(float(input_by_year[i] if i < len(input_by_year) else 0.0), 2)
        credit_begin = round(credit, 2)
        creditable = round(in_v + credit, 2)
        payable = round(max(out_v - creditable, 0.0), 2)
        credit_end = round(max(creditable - out_v, 0.0), 2)
        rows.append(
            {
                "output": out_v,
                "input": in_v,
                "input_used": round(min(creditable, out_v), 2),
                "payable": payable,
                "credit_begin": credit_begin,
                "credit_end": credit_end,
            }
        )
        credit = credit_end
    return rows


def surtax_from_vat_payable(
    vat_payable_by_year: list[float],
    *,
    surtax_rate: float = 0.12,
) -> list[float]:
    """兼容旧综合税率输入；新代码优先使用分项函数。"""
    rate = float(surtax_rate or 0.0)
    return [round(max(float(v or 0.0), 0.0) * rate, 2) for v in vat_payable_by_year]


def surtax_components_from_tax_payable(
    vat_payable_by_year: list[float],
    *,
    consumption_tax_by_year: list[float] | None = None,
    urban_maintenance_rate: float,
    education_surcharge_rate: float = 0.03,
    local_education_surcharge_rate: float = 0.02,
) -> list[dict[str, float]]:
    """按法定分项计算城建税、教育费附加和地方教育附加。

    税基为当期实际应纳增值税与消费税之和。城建税率必须由项目所在地
    明确为 1%/5%/7% 之一；教育费附加和地方教育附加分别独立保留，
    禁止把一个缺少所在地语义的 ``12%`` 当作通用法定税率。
    """

    urban_rate = float(urban_maintenance_rate)
    if urban_rate not in {0.01, 0.05, 0.07}:
        raise ValueError("urban_maintenance_rate must be one of 0.01, 0.05, 0.07")
    education_rate = float(education_surcharge_rate)
    local_rate = float(local_education_surcharge_rate)
    if not 0 <= education_rate <= 1 or not 0 <= local_rate <= 1:
        raise ValueError("education surcharge rates must be between 0 and 1")
    consumption = list(consumption_tax_by_year or [])
    count = max(len(vat_payable_by_year), len(consumption))
    rows: list[dict[str, float]] = []
    for index in range(count):
        vat = max(float(vat_payable_by_year[index] if index < len(vat_payable_by_year) else 0.0), 0.0)
        excise = max(float(consumption[index] if index < len(consumption) else 0.0), 0.0)
        base = round(vat + excise, 2)
        urban = round(base * urban_rate, 2)
        education = round(base * education_rate, 2)
        local = round(base * local_rate, 2)
        rows.append({
            "tax_base": base,
            "vat_payable": round(vat, 2),
            "consumption_tax_payable": round(excise, 2),
            "urban_maintenance_tax": urban,
            "education_surcharge": education,
            "local_education_surcharge": local,
            "total": round(urban + education + local, 2),
        })
    return rows


def surtax_from_revenue(revenue_by_year: list[float], rate: float) -> list[float]:
    """简化：附加税按收入比例（旧口径兜底）。"""
    r = float(rate or 0.0)
    return [round(float(rev or 0.0) * r, 2) for rev in revenue_by_year]


def income_tax_with_loss_carryforward(
    profits: list[float],
    rates: list[float],
    *,
    carryforward_years: int = 5,
) -> list[dict[str, Any]]:
    """亏损弥补结转滚动账户。

    口径与引擎历史契约一致：
    - 结转窗口：发生年后 ``carryforward_years`` 年内可用（``y - origin < window``）；
    - 亏损年 ``taxable`` 保留负利润（便于审计），``income_tax=0``；
    - 无亏损时 ``income_tax == max(profit,0)*rate``（字节级兼容旧路径）。
    """
    window = max(int(carryforward_years or 5), 0)
    # 亏损池：[发生年度索引, 剩余可抵金额]
    loss_pool: list[list[float]] = []
    out: list[dict[str, Any]] = []
    for y, raw_profit in enumerate(profits):
        rate = float(rates[y] if y < len(rates) else (rates[-1] if rates else 0.25))
        profit = round(float(raw_profit or 0.0), 2)
        # 剔除已过结转期的亏损（与引擎历史：y-origin < window）
        loss_pool = [lp for lp in loss_pool if (y - int(lp[0])) < window]
        if profit < 0:
            loss_pool.append([float(y), round(-profit, 2)])
            out.append(
                {
                    "taxable": profit,
                    "income_tax": 0.0,
                    "loss_used": 0.0,
                    "loss_balance_end": round(sum(lp[1] for lp in loss_pool), 2),
                    "profit_before": profit,
                }
            )
            continue
        remaining = profit
        used_total = 0.0
        for lp in loss_pool:
            if remaining <= 0:
                break
            use = min(lp[1], remaining)
            lp[1] = round(lp[1] - use, 2)
            remaining = round(remaining - use, 2)
            used_total = round(used_total + use, 2)
        loss_pool = [lp for lp in loss_pool if lp[1] > 0]
        taxable = round(max(remaining, 0.0), 2)
        out.append(
            {
                "taxable": taxable,
                "income_tax": round(taxable * rate, 2),
                "loss_used": used_total,
                "loss_balance_end": round(sum(lp[1] for lp in loss_pool), 2),
                "profit_before": profit,
            }
        )
    return out


def dual_income_tax(
    *,
    financing_before_profits: list[float],
    interest_by_year: list[float],
    rates: list[float],
    carryforward_years: int = 5,
) -> dict[str, Any]:
    """双口径：融资前调整所得税 vs 会计实际所得税（扣息后）。"""
    adj = income_tax_with_loss_carryforward(
        financing_before_profits, rates, carryforward_years=carryforward_years
    )
    acct_profits = [
        round(float(p or 0.0) - float(interest_by_year[i] if i < len(interest_by_year) else 0.0), 2)
        for i, p in enumerate(financing_before_profits)
    ]
    actual = income_tax_with_loss_carryforward(
        acct_profits, rates, carryforward_years=carryforward_years
    )
    return {
        "adjusted_income_tax": adj,  # 融资前（项目投资现金流）
        "actual_income_tax": actual,  # 融资后会计
        "accounting_profit_before_tax": acct_profits,
    }
