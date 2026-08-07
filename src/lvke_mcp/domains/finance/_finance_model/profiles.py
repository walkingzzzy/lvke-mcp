"""项目性质政策与行业参考。"""

from __future__ import annotations

from typing import Any

# P0/P1 modular finance package (方案 §8/§13)

from .base import (
    _load_finance_params,
    _resolve_benchmark,
)


def project_nature_policy(invest_type: str = "") -> dict[str, Any]:
    """P1：按项目性质（政府/企业投资）返回基准收益率的适用性口径。

    依据发改投资〔2006〕1325号第九条 + 〔2013〕586号：行业财务基准收益率
    **仅适用于政府投资项目及按政府要求做经济评价的项目**；核准制/备案制的
    企业投资项目可依投资者要求自选最低可接受收益率，不得硬套行业基准做达标判定。
    返回 {nature, benchmark_is_mandatory, note}。
    """
    p = _load_finance_params()
    pn = p.get("project_nature") or {}
    nature = "gov" if str(invest_type) in ("government", "gov") else "enterprise"
    cfg = pn.get(nature) or {}
    # 兼容两种键名：benchmark_mandatory / benchmark_is_hard_gate（YAML 用后者）；
    # 两者都缺时才退回 nature=="gov" 默认，确保 YAML 配置真正生效（非死键）。
    if "benchmark_mandatory" in cfg:
        mandatory = bool(cfg.get("benchmark_mandatory"))
    elif "benchmark_is_hard_gate" in cfg:
        mandatory = bool(cfg.get("benchmark_is_hard_gate"))
    else:
        mandatory = nature == "gov"
    default_note = (
        "政府投资项目：行业财务基准收益率为达标判定基准（发改投资〔2013〕586号）"
        if nature == "gov" else
        "企业投资项目：行业基准仅作参考，达标判定用投资者自选的最低可接受收益率"
        "（发改投资〔2006〕1325号第九条）")
    return {"nature": nature, "benchmark_is_mandatory": mandatory,
            "note": str(cfg.get("note") or default_note)}


def industry_reference(industry: str = "") -> dict[str, Any]:
    """M3：返回该行业的外部基准（利润率/回收期上限/基准收益率），供双路径交叉验证。"""
    p = _load_finance_params()
    ind = str(industry or "")

    def _match(table: dict, default):
        for kw, v in (table or {}).items():
            if kw and kw in ind:
                return v, kw
        return default, ""

    margin, m_kw = _match(p.get("industry_profit_margin"), p.get("industry_profit_margin_default"))
    payback, p_kw = _match(p.get("industry_payback_max"), p.get("industry_payback_max_default"))
    bench, _ = _resolve_benchmark(industry)
    return {"profit_margin": float(margin), "profit_margin_kw": m_kw,
            "payback_max": float(payback), "payback_kw": p_kw, "benchmark_rate_pct": round(bench * 100, 1)}
