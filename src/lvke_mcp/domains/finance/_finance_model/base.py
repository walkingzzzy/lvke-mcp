"""基准利率、参数缓存、IRR/NPV/回收期计算底座与格式化原语。含两个可选依赖兜底 try 块。"""

from __future__ import annotations

from typing import Any, Optional

# P0/P1 modular finance package (方案 §8/§13)


# 【P2】投资明细三段式输入通道
try:
    from lvke_mcp.domains.finance.spec import InvestmentBreakdown
    _HAS_INVESTMENT_BREAKDOWN = True
except ImportError:
    _HAS_INVESTMENT_BREAKDOWN = False


try:  # finance_calc 为纯函数层，可进程内直接复用
    from lvke_mcp.domains.finance.calculations import irr as _irr, npv as _npv, payback_period as _payback
except Exception:  # noqa: BLE001 - 兜底：finance_calc 不可用时用内联实现
    def _npv(cashflows, rate):  # type: ignore
        return sum(cf / ((1.0 + rate) ** t) for t, cf in enumerate(cashflows))

    def _irr(cashflows, *, guess=0.1):  # type: ignore
        # 兜底二分区间与主实现 calculations.irr 对齐（[-0.99, 10]），
        # 避免极端高 IRR 场景在兜底路径误报「不在范围内」而主路径可算。
        lo, hi = -0.99, 10.0
        flo = _npv(cashflows, lo)
        fhi = _npv(cashflows, hi)
        if flo * fhi > 0:
            raise ValueError("IRR 不在 [-99%, 1000%] 范围内，可能存在多解或现金流异常")
        for _ in range(200):
            mid = (lo + hi) / 2
            fm = _npv(cashflows, mid)
            if abs(fm) < 1e-7:
                return mid
            if fm * flo < 0:
                hi = mid
            else:
                lo, flo = mid, fm
        return (lo + hi) / 2

    class _PB:  # type: ignore
        def __init__(self, s):
            self.static_years = s
            self.dynamic_years = s

    def _payback(cashflows, *, rate=0.0):  # type: ignore
        cum = 0.0
        for t, cf in enumerate(cashflows):
            prev = cum
            cum += cf / ((1.0 + rate) ** t)
            if cum >= 0 and t > 0:
                gap = cum - prev
                frac = (-prev) / gap if gap > 0 else 0.0
                return _PB((t - 1) + frac)
        return _PB(None)


BENCHMARK_RATE = 0.08  # 行业基准折现率 Ic（可研常用 8%；缺省兜底，实际按 config/finance_params.yaml 分行业解析）


DEFAULT_LOAN_RATE = 0.045  # 缺省贷款年利率


# ── M1 T1.3：权威基准参数配置化（config/finance_params.yaml，来源发改投资〔2006〕1325/〔2013〕586）──
_PARAMS_CACHE: dict[str, Any] | None = None


def _load_finance_params() -> dict[str, Any]:
    """加载 config/finance_params.yaml（失败/无 yaml 时用内置默认，静默降级）。"""
    global _PARAMS_CACHE
    if _PARAMS_CACHE is not None:
        return _PARAMS_CACHE
    data: dict[str, Any] = {}
    try:
        import os
        import yaml  # type: ignore

        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, "config", "finance_params.yaml")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001 - 无 yaml/文件缺失时内置默认
        data = {}
    _PARAMS_CACHE = {
        "social_discount_rate": data.get("social_discount_rate", 0.08),
        "default_benchmark_rate": data.get("default_benchmark_rate", BENCHMARK_RATE),
        "default_loan_rate": data.get("default_loan_rate", DEFAULT_LOAN_RATE),
        "industry_benchmark_rate": data.get("industry_benchmark_rate", {}) or {},
        "industry_profit_margin": data.get("industry_profit_margin", {}) or {},
        "industry_profit_margin_default": data.get("industry_profit_margin_default", 0.08),
        "industry_payback_max": data.get("industry_payback_max", {}) or {},
        "industry_payback_max_default": data.get("industry_payback_max_default", 12),
        "project_nature": data.get("project_nature", {}) or {},
        "min_capital_ratio": data.get("min_capital_ratio", {}) or {},
        # BC-P3：成本/税/残值缺省参数（消灭引擎散落硬编码 H3）。兜底值 = 原引擎硬编码值，
        # 保证 config 缺该段/缺 yaml 时行为与改造前字节一致（老测试全绿）。
        "cost_defaults": data.get("cost_defaults", {}) or {},
    }
    return _PARAMS_CACHE


# BC-P3：成本/税/残值缺省的内置兜底（= 原引擎散落硬编码值，第三级兜底）。
_COST_FALLBACK = {
    "total_cost_rate": 0.75, "wage_rate": 0.15, "welfare_rate": 0.14,
    "salvage_rate": 0.05, "surtax_rate": 0.01, "bep_fixed_cost_ratio": 0.30,
    "income_tax_rate": 0.25, "vat_rate": 0.13, "vat_input_rate": 0.10,
}


def _cost_param(spec: Optional[dict[str, Any]], key: str,
                spec_section: str = "cost", spec_key: str = "") -> float:
    """参数取值（灵活默认，可覆盖）：

    1. ``spec.{section}.{key}`` / fin 经 spec 传入
    2. ``config/finance_params.yaml`` → ``cost_defaults``
    3. ``config/feasibility_params_cn_default.v1.yaml`` 行业习惯默认（缺省即用，无需会签）
    4. 内置 ``_COST_FALLBACK``

    项目层随时可用 fin/spec 覆盖；**不**要求每次人工确认才允许计算。
    """
    sk = spec_key or key
    if spec:
        sect = (spec.get(spec_section) or {}) if isinstance(spec, dict) else {}
        v = sect.get(sk) if isinstance(sect, dict) else None
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    cfg = _load_finance_params().get("cost_defaults") or {}
    if key in cfg and cfg[key] is not None:
        try:
            return float(cfg[key])
        except (TypeError, ValueError):
            pass
    # 行业习惯默认：直接可用，允许被上级覆盖
    try:
        from lvke_mcp.domains.finance.feasibility_params import load_feasibility_params

        fp = load_feasibility_params()
        if key == "income_tax_rate":
            rate = (fp.get("income_tax") or {}).get("default_rate")
            if rate is not None:
                return float(rate)
    except Exception:  # noqa: BLE001
        pass
    return float(_COST_FALLBACK[key])


def _resolve_benchmark(industry: str = "") -> tuple[float, str]:
    """按行业关键词解析财务基准收益率；返回 (rate, 依据说明)。"""
    p = _load_finance_params()
    ind = str(industry or "")
    for kw, rate in (p.get("industry_benchmark_rate") or {}).items():
        if kw and kw in ind:
            return float(rate), f"按行业「{kw}」取财务基准收益率 {float(rate)*100:.1f}%（发改投资〔2013〕586号量级，须复核）"
    rate = float(p.get("default_benchmark_rate", BENCHMARK_RATE))
    return rate, f"缺省财务基准收益率 {rate*100:.1f}%（《方法与参数》第三版，须按行业复核）"


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt(v: Optional[float], nd: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:,.{nd}f}"


def _fmt_rate_display(v: Any) -> str:
    """年利率展示：0.045 → 4.50%（兼容已是百分数的输入）。"""
    if v is None or v == "":
        return "—"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    pct = x * 100.0 if abs(x) <= 1.0 else x
    if abs(pct - round(pct)) < 1e-9:
        return f"{int(round(pct))}%"
    return f"{pct:.2f}%"
