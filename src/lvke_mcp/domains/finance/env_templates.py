"""环保投资与成本模板加载（M5 T5.4）。

从 config/env_templates.yaml 按行业解析"三废"治理措施 + 环保投资/运行成本量级，
供影响效果章（环境影响）grounding 与成本要素结构化使用。参数为可研估算级，
须按环评批复与地方标准复核。参数缺失时回退内置 default，不抛异常。
"""

from __future__ import annotations

from typing import Any

_PARAMS_CACHE: dict[str, Any] | None = None

_BUILTIN_DEFAULT: dict[str, Any] = {
    "env_invest_ratio": 0.03,
    "env_opex_ratio": 0.02,
    "eia_level": "报告表",
    "measures": [
        "废水：清污分流 + 污水处理达标纳管或回用",
        "废气：收集治理后达标排放",
        "固废：一般固废综合利用，危废合规委外处置",
        "噪声：设备减振隔声，厂界达标",
    ],
}


def _load_params() -> dict[str, Any]:
    global _PARAMS_CACHE
    if _PARAMS_CACHE is not None:
        return _PARAMS_CACHE
    data: dict[str, Any] = {}
    try:
        import os
        import yaml  # type: ignore

        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "config", "env_templates.yaml"), "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001
        data = {}
    _PARAMS_CACHE = data or {}
    return _PARAMS_CACHE


def resolve_env_template(industry: str = "") -> dict[str, Any]:
    """合并 default 与命中行业的环保模板。返回含 measures/env_invest_ratio/eia_level/_matched。"""
    data = _load_params()
    base = dict(data.get("default") or _BUILTIN_DEFAULT)
    ind = str(industry or "")
    for kw, override in (data.get("industry") or {}).items():
        if kw and kw in ind and isinstance(override, dict):
            base.update(override)
            base["_matched"] = kw
            break
    return base


def env_profile(industry: str = "") -> dict[str, Any]:
    """影响效果章 grounding 用的环保画像：三废治理措施 + 投资/运行费率 + 环评等级。

    不依赖具体投资额，只给行业口径与措施清单；成本额估算走 env_cost_estimate。
    """
    tpl = resolve_env_template(industry)
    return {
        "available": bool(tpl.get("measures")),
        "industry": industry,
        "matched": tpl.get("_matched", ""),
        "eia_level": tpl.get("eia_level", ""),
        "env_invest_ratio": tpl.get("env_invest_ratio"),
        "env_opex_ratio": tpl.get("env_opex_ratio"),
        "measures": list(tpl.get("measures") or []),
    }


def env_cost_estimate(industry: str = "", *, construction_wan: float = 0.0,
                      revenue_wan: float = 0.0) -> dict[str, Any]:
    """按行业环保投资率/运行费率给出环保投资与年运行费估算（可研估算级）。

    环保投资 = 建设投资 × env_invest_ratio；年环保运行费 = 营业收入 × env_opex_ratio。
    供成本要素结构化（环保运行费进总成本）与投资估算参考。
    """
    tpl = resolve_env_template(industry)
    try:
        c = float(construction_wan or 0.0)
        rev = float(revenue_wan or 0.0)
    except (TypeError, ValueError):
        c = rev = 0.0
    invest = round(c * float(tpl.get("env_invest_ratio") or 0.0), 2)
    opex = round(rev * float(tpl.get("env_opex_ratio") or 0.0), 2)
    return {
        "available": bool(tpl),
        "industry": industry,
        "matched": tpl.get("_matched", ""),
        "eia_level": tpl.get("eia_level", ""),
        "env_invest_wan": invest,
        "env_opex_wan": opex,
        "env_invest_ratio": tpl.get("env_invest_ratio"),
        "env_opex_ratio": tpl.get("env_opex_ratio"),
        "measures": list(tpl.get("measures") or []),
    }
