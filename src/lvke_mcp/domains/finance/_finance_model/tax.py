"""税务口径：所得税表、亏损弥补与增值税留抵。"""

from __future__ import annotations

from typing import Any, Optional

# P0/P1 modular finance package (方案 §8/§13)
from lvke_mcp.domains.finance import taxes as _fin_taxes


def _tax_spec_int(spec: Optional[dict[str, Any]], key: str) -> int:
    """从 spec.tax 读非负整数（免税期/减半期年数）。缺失/非法/负数一律按 0（=无优惠）。"""
    if not isinstance(spec, dict):
        return 0
    tax = spec.get("tax")
    if not isinstance(tax, dict):
        return 0
    try:
        v = int(float(tax.get(key)))
    except (TypeError, ValueError):
        return 0
    return v if v > 0 else 0


def _income_tax_schedule(spec: Optional[dict[str, Any]], base_rate: float, op_years: int) -> list[float]:
    """BC-1 (H4)：按 spec.tax 的免税期/减半期生成逐年所得税率序列（运营期 op_years 年）。

    「三免三减半」等税收优惠的逐年落地：前 ``tax_holiday_years`` 年税率 0（免征），
    随后 ``tax_half_years`` 年税率 ×0.5（减半），之后恢复 ``base_rate``。口径按运营期第 1 年起算
    （可研简化：达产爬坡与优惠期均自投产年计；实际税法多自「首个获利年度」起算，此处保守从投产年计，
    差异在 assumptions 说明）。无优惠（两值均 0）时全程 = base_rate，序列与恒定税率等价。
    """
    holiday = _tax_spec_int(spec, "tax_holiday_years")
    half = _tax_spec_int(spec, "tax_half_years")
    out: list[float] = []
    for y in range(max(op_years, 0)):
        if y < holiday:
            out.append(0.0)
        elif y < holiday + half:
            out.append(round(base_rate * 0.5, 6))
        else:
            out.append(base_rate)
    return out


# 【P1-3】亏损结转年限：《企业所得税法》第十八条——纳税年度亏损准予向后结转，最长不超过 5 年
# （高新技术企业/科技型中小企业延至 10 年，属特例，由 spec.tax.loss_carryforward_years 覆盖）。
_DEFAULT_LOSS_CARRYFORWARD_YEARS = 5


def _compute_income_tax_with_loss_carryforward(
    profits: list[float],
    rates: list[float],
    *,
    carryforward_years: int = _DEFAULT_LOSS_CARRYFORWARD_YEARS,
) -> list[dict[str, Any]]:
    """P1-3：逐年所得税（含亏损弥补结转）——真源 ``finance.taxes``。"""
    return _fin_taxes.income_tax_with_loss_carryforward(
        profits, rates, carryforward_years=carryforward_years,
    )


def _compute_vat_with_credit_carryover(
    vat_outputs: list[float],
    vat_inputs: list[float],
) -> list[dict[str, Any]]:
    """P1-3：逐年增值税（含留抵）——真源 ``finance.taxes``。"""
    return _fin_taxes.compute_vat_with_credit_carryover(vat_outputs, vat_inputs)
