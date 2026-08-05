"""Deterministic finance calculations shared by domain and server adapters.

所有公式与口径与 ``skills/financial-modeling/irr-npv/SKILL.md`` 一致。

公式提醒:
- NPV = Σ CF_t / (1+r)^t   (t 从 0 开始)
- IRR: 令 NPV(IRR) = 0
- 静态投资回收期: 累计现金流首次 ≥ 0 的年份(线性插值)
- 动态投资回收期: 累计折现现金流首次 ≥ 0 的年份(线性插值)

实现策略:
- 不依赖 ``numpy_financial``(避免新增依赖);IRR 用 Newton-Raphson + 二分回退;
  NPV / 回收期手写,数值稳定性满足可研深度(±15%)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

_MAX_NEWTON_ITER = 80
_NEWTON_TOL = 1e-7


def npv(cashflows: Sequence[float], rate: float) -> float:
    """计算净现值。

    Args:
        cashflows: 第 0 年起的逐年现金流(投资为负,收入为正)。
        rate: 折现率(小数,如 0.08)。

    Returns:
        NPV 数值。

    Raises:
        ValueError: ``cashflows`` 为空或 ``rate <= -1``。
    """

    if not cashflows:
        raise ValueError("cashflows 不能为空")
    if rate <= -1:
        raise ValueError("rate 必须 > -1")
    total = 0.0
    for t, cf in enumerate(cashflows):
        total += cf / ((1.0 + rate) ** t)
    return total


def xnpv(cashflows: Sequence[float], dates: Sequence[date], rate: float) -> float:
    """Calculate NPV using explicit dates and an Actual/365 year fraction."""

    if not cashflows or len(cashflows) != len(dates):
        raise ValueError("cashflows 与 dates 必须非空且长度一致")
    if rate <= -1:
        raise ValueError("rate 必须 > -1")
    base = dates[0]
    if any(value < base for value in dates):
        raise ValueError("dates[0] 必须是最早日期")
    return sum(
        float(cashflow) / ((1.0 + rate) ** ((value - base).days / 365.0))
        for cashflow, value in zip(cashflows, dates)
    )


def _xnpv_derivative(
    cashflows: Sequence[float], dates: Sequence[date], rate: float
) -> float:
    base = dates[0]
    return sum(
        -((value - base).days / 365.0)
        * float(cashflow)
        / ((1.0 + rate) ** (((value - base).days / 365.0) + 1.0))
        for cashflow, value in zip(cashflows, dates)
    )


def xirr(
    cashflows: Sequence[float],
    dates: Sequence[date],
    *,
    guess: float = 0.1,
) -> float:
    """Calculate date-aware IRR with deterministic Newton and bisection fallback."""

    if not cashflows or len(cashflows) != len(dates):
        raise ValueError("cashflows 与 dates 必须非空且长度一致")
    if not _has_sign_change(cashflows):
        raise ValueError("现金流没有正负号变化,XIRR 不存在")
    if any(value < dates[0] for value in dates):
        raise ValueError("dates[0] 必须是最早日期")
    normalized_cashflows = _normalize_cashflows(cashflows)

    rate = float(guess)
    for _ in range(_MAX_NEWTON_ITER):
        if rate <= -0.999999:
            break
        value = xnpv(normalized_cashflows, dates, rate)
        if abs(value) < _NEWTON_TOL:
            return rate
        derivative = _xnpv_derivative(normalized_cashflows, dates, rate)
        if abs(derivative) < 1e-12:
            break
        next_rate = rate - value / derivative
        if next_rate <= -0.999999:
            break
        if abs(next_rate - rate) < _NEWTON_TOL:
            return next_rate
        rate = next_rate

    low, high = -0.9999, 10.0
    low_value = xnpv(normalized_cashflows, dates, low)
    high_value = xnpv(normalized_cashflows, dates, high)
    if low_value * high_value > 0:
        raise ValueError("XIRR 不在 [-99.99%,1000%] 范围内或存在多解")
    for _ in range(200):
        middle = (low + high) / 2.0
        middle_value = xnpv(normalized_cashflows, dates, middle)
        if abs(middle_value) < _NEWTON_TOL:
            return middle
        if low_value * middle_value < 0:
            high = middle
        else:
            low, low_value = middle, middle_value
    return (low + high) / 2.0


def break_even_analysis(
    *,
    fixed_cost_wan: float,
    unit_price_yuan: float,
    unit_variable_cost_yuan: float,
    expected_volume: float,
) -> dict[str, float | str]:
    """Calculate volume/price break-even points and the expected safety margin."""

    if fixed_cost_wan < 0 or unit_variable_cost_yuan < 0:
        raise ValueError("固定成本和单位变动成本不得为负")
    if unit_price_yuan <= 0 or expected_volume <= 0:
        raise ValueError("单价和预计销量必须大于 0")
    contribution = unit_price_yuan - unit_variable_cost_yuan
    if contribution <= 0:
        raise ValueError("单位贡献毛利必须大于 0")
    break_even_volume = fixed_cost_wan * 10000.0 / contribution
    break_even_price = unit_variable_cost_yuan + fixed_cost_wan * 10000.0 / expected_volume
    safety_margin = (expected_volume - break_even_volume) / expected_volume
    if safety_margin < 0:
        strength = "insufficient"
    elif safety_margin < 0.3:
        strength = "weak"
    else:
        strength = "adequate"
    return {
        "unit_contribution_margin_yuan": contribution,
        "break_even_volume": break_even_volume,
        "break_even_price_yuan": break_even_price,
        "safety_margin_ratio": safety_margin,
        "safety_margin_percent": safety_margin * 100.0,
        "resilience": strength,
    }


def _npv_derivative(cashflows: Sequence[float], rate: float) -> float:
    """对 rate 求 NPV 的一阶导(供 Newton 迭代用)。

    d(NPV)/dr = Σ -t * CF_t / (1+r)^(t+1)
    """

    total = 0.0
    for t, cf in enumerate(cashflows):
        if t == 0:
            continue
        total += -t * cf / ((1.0 + rate) ** (t + 1))
    return total


def _has_sign_change(cashflows: Sequence[float]) -> bool:
    seen_pos = False
    seen_neg = False
    for cf in cashflows:
        if cf > 0:
            seen_pos = True
        elif cf < 0:
            seen_neg = True
        if seen_pos and seen_neg:
            return True
    return False


def _normalize_cashflows(cashflows: Sequence[float]) -> list[float]:
    """Keep root solving invariant when cash-flow units or magnitudes change."""

    scale = max(abs(float(value)) for value in cashflows)
    if scale == 0:
        raise ValueError("现金流不得全为零")
    return [float(value) / scale for value in cashflows]


def cashflow_sign_changes(cashflows: Sequence[float]) -> int:
    """Return the number of non-zero cash-flow sign transitions.

    Descartes' rule gives this count operational meaning for IRR: two or more
    transitions mean a single reported IRR is not a safe decision metric.
    """

    signs = [1 if value > 0 else -1 for value in cashflows if value != 0]
    return sum(left != right for left, right in zip(signs, signs[1:]))


def irr_roots(
    cashflows: Sequence[float],
    *,
    minimum_rate: float = -0.99,
    maximum_rate: float = 10.0,
    samples: int = 20000,
) -> list[float]:
    """Find distinct real IRR roots in a bounded decision range.

    The scan is deliberately deterministic and is used for diagnosis, not as
    a replacement for NPV/MIRR when cash flows have multiple sign changes.
    """

    if not _has_sign_change(cashflows):
        return []
    if minimum_rate <= -1 or maximum_rate <= minimum_rate:
        raise ValueError("invalid IRR scan range")
    normalized_cashflows = _normalize_cashflows(cashflows)
    count = max(int(samples), 1000)
    step = (maximum_rate - minimum_rate) / count
    roots: list[float] = []

    def add_root(value: float) -> None:
        if all(abs(value - existing) > 1e-7 for existing in roots):
            roots.append(value)

    left = minimum_rate
    left_value = npv(normalized_cashflows, left)
    for index in range(1, count + 1):
        right = minimum_rate + step * index
        right_value = npv(normalized_cashflows, right)
        if abs(left_value) < _NEWTON_TOL:
            add_root(left)
        if left_value * right_value < 0:
            lo, hi = left, right
            f_lo = left_value
            for _ in range(100):
                mid = (lo + hi) / 2
                f_mid = npv(normalized_cashflows, mid)
                if abs(f_mid) < _NEWTON_TOL or hi - lo < 1e-12:
                    add_root(mid)
                    break
                if f_lo * f_mid < 0:
                    hi = mid
                else:
                    lo, f_lo = mid, f_mid
            else:
                add_root((lo + hi) / 2)
        left, left_value = right, right_value
    if abs(left_value) < _NEWTON_TOL:
        add_root(left)
    return sorted(roots)


def irr(
    cashflows: Sequence[float],
    *,
    guess: float = 0.1,
) -> float:
    """计算内部收益率。

    使用 Newton-Raphson;如果收敛失败回退到二分法(区间 ``[-0.99, 10]``)。

    Args:
        cashflows: 第 0 年起的逐年现金流。
        guess: 初值。

    Returns:
        IRR(小数,如 0.125 表示 12.5%)。

    Raises:
        ValueError: 现金流符号未变化(IRR 不存在)或所有算法均不收敛。
    """

    if not _has_sign_change(cashflows):
        raise ValueError("现金流没有正负号变化,IRR 不存在")
    normalized_cashflows = _normalize_cashflows(cashflows)

    rate = guess
    for _ in range(_MAX_NEWTON_ITER):
        try:
            value = npv(normalized_cashflows, rate)
        except (ZeroDivisionError, OverflowError):
            break
        if abs(value) < _NEWTON_TOL:
            return rate
        derivative = _npv_derivative(normalized_cashflows, rate)
        if abs(derivative) < 1e-12:
            break
        next_rate = rate - value / derivative
        if next_rate <= -0.999:  # 避免跌出可行域
            next_rate = (rate - 0.999) / 2
        if abs(next_rate - rate) < _NEWTON_TOL:
            return next_rate
        rate = next_rate

    # 回退:二分法
    lo, hi = -0.99, 10.0
    f_lo = npv(normalized_cashflows, lo)
    f_hi = npv(normalized_cashflows, hi)
    if f_lo * f_hi > 0:
        raise ValueError("IRR 不在 [-99%, 1000%] 范围内,可能存在多解或现金流异常")
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(normalized_cashflows, mid)
        if abs(f_mid) < _NEWTON_TOL:
            return mid
        if f_mid * f_lo < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


@dataclass
class PaybackResult:
    """投资回收期计算结果。"""

    static_years: float | None
    """静态投资回收期(年,可线性插值);若现金流不足回收则为 ``None``。"""

    dynamic_years: float | None
    """动态投资回收期(年,带折现);若现金流不足回收则为 ``None``。"""

    cumulative_static: list[float]
    """逐年累计静态现金流。"""

    cumulative_discounted: list[float]
    """逐年累计折现现金流。"""


def payback_period(
    cashflows: Sequence[float],
    *,
    rate: float = 0.0,
) -> PaybackResult:
    """计算静态与动态投资回收期。

    Args:
        cashflows: 第 0 年起的逐年现金流。
        rate: 折现率(为 0 时动态回收期 = 静态回收期)。

    Returns:
        :class:`PaybackResult`,含两种回收期与累计现金流序列。
    """

    if not cashflows:
        raise ValueError("cashflows 不能为空")
    if rate <= -1:
        raise ValueError("rate 必须 > -1")

    cum_static: list[float] = []
    cum_disc: list[float] = []
    running_static = 0.0
    running_disc = 0.0
    for t, cf in enumerate(cashflows):
        running_static += cf
        running_disc += cf / ((1.0 + rate) ** t)
        cum_static.append(running_static)
        cum_disc.append(running_disc)

    def _find_payback(cum: list[float]) -> float | None:
        for t, c in enumerate(cum):
            if c >= 0:
                if t == 0:
                    return 0.0
                prev = cum[t - 1]
                # 线性插值:在 (t-1, t) 区间,从 prev 增到 c,插值出过 0 点
                gap = c - prev
                if gap <= 0:
                    return float(t)
                fraction = (-prev) / gap
                return (t - 1) + fraction
        return None

    return PaybackResult(
        static_years=_find_payback(cum_static),
        dynamic_years=_find_payback(cum_disc),
        cumulative_static=cum_static,
        cumulative_discounted=cum_disc,
    )


def sensitivity_irr(
    base_cashflows: Sequence[float],
    factors: dict[str, dict],
    deltas: Sequence[float],
) -> dict[str, list[dict[str, float]]]:
    """单因素敏感性扫描:逐因子按 ``deltas`` 浮动,重算 IRR。

    Args:
        base_cashflows: 基准现金流。
        factors: 因子定义。形如:
            ``{"revenue": {"years": [1,2,3], "value_per_year": 1000.0}, ...}``
            每个因子描述哪些年份的现金流分量受该因子影响,以及"原始值"是多少。
            浮动 +δ 时,这些年份的现金流变化量为 ``+δ * value_per_year``。
        deltas: 浮动比例列表,如 ``[-0.2, -0.1, 0, 0.1, 0.2]``。

    Returns:
        ``{factor_name: [{"delta": d, "irr": v}, ...]}``。
    """

    result: dict[str, list[dict[str, float]]] = {}
    for factor_name, spec in factors.items():
        years = spec.get("years") or []
        value_per_year = float(spec.get("value_per_year", 0.0))
        series: list[dict[str, float]] = []
        for d in deltas:
            cfs = list(base_cashflows)
            delta_per_year = d * value_per_year
            for y in years:
                if 0 <= y < len(cfs):
                    cfs[y] = cfs[y] + delta_per_year
            try:
                irr_val = irr(cfs)
            except ValueError:
                irr_val = float("nan")
            series.append({"delta": float(d), "irr": float(irr_val)})
        result[factor_name] = series
    return result
