"""建设内容与规模智能推导 + 劳动定员（M5 T5.1 / T5.5）。

从"用地面积 + 目标产能 + 行业"，在用地合规约束（容积率/绿化率/建筑密度）下推导：
厂房/办公建筑面积、总建筑面积、层高建议；并按行业推算劳动定员。

结果为"可微调初稿"（可研阶段估算级），参数来自 config/industry_params.yaml（可复核）。
"""

from __future__ import annotations

from typing import Any

_MU_TO_M2 = 666.67  # 1 亩 ≈ 666.67 ㎡
_PARAMS_CACHE: dict[str, Any] | None = None


def _load_params() -> dict[str, Any]:
    global _PARAMS_CACHE
    if _PARAMS_CACHE is not None:
        return _PARAMS_CACHE
    data: dict[str, Any] = {}
    try:
        import os
        import yaml  # type: ignore

        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "config", "industry_params.yaml"), "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001
        data = {}
    _PARAMS_CACHE = data or {}
    return _PARAMS_CACHE


def _resolve(industry: str = "") -> dict[str, Any]:
    """合并 default 与命中行业的参数。"""
    data = _load_params()
    base = dict(data.get("default") or {
        "plot_ratio": 1.0, "green_ratio": 0.15, "building_coverage": 0.40,
        "workshop_share": 0.70, "office_share": 0.15, "floor_height_m": 6.0,
        "headcount_per_10k_m2": 60, "avg_wage_wan": 9.0,
    })
    ind = str(industry or "")
    for kw, override in (data.get("industry") or {}).items():
        if kw and kw in ind and isinstance(override, dict):
            base.update(override)
            base["_matched"] = kw
            break
    return base


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def infer_build_scale(land_area_mu: float, capacity: str = "", industry: str = "") -> dict[str, Any]:
    """由用地面积(亩)+行业推导建设内容规模。返回结构化 + 可解释说明。"""
    p = _resolve(industry)
    land = _f(land_area_mu)
    if land <= 0:
        return {"available": False, "reason": "缺少用地面积"}
    land_m2 = round(land * _MU_TO_M2, 0)
    plot_ratio = _f(p.get("plot_ratio"), 1.0)
    total_floor = round(land_m2 * plot_ratio, 0)
    workshop = round(total_floor * _f(p.get("workshop_share"), 0.70), 0)
    office = round(total_floor * _f(p.get("office_share"), 0.15), 0)
    aux = round(max(total_floor - workshop - office, 0), 0)
    green = round(land_m2 * _f(p.get("green_ratio"), 0.15), 0)
    floor_h = _f(p.get("floor_height_m"), 6.0)
    floor_note = "≤8m（常规厂房）" if floor_h <= 8 else ">8m（含航吊/大型机械设备）"
    return {
        "available": True,
        "matched_industry": p.get("_matched", "（缺省参数）"),
        "capacity": capacity,
        "land_area_mu": land, "land_area_m2": land_m2,
        "plot_ratio": plot_ratio, "green_ratio": _f(p.get("green_ratio"), 0.15),
        "building_coverage": _f(p.get("building_coverage"), 0.40),
        "total_floor_area_m2": total_floor,
        "workshop_area_m2": workshop, "office_area_m2": office, "aux_area_m2": aux,
        "green_area_m2": green,
        "floor_height_m": floor_h, "floor_height_note": floor_note,
        "explain": (f"按用地 {land} 亩（≈{land_m2:.0f}㎡）、容积率 {plot_ratio} 推算总建筑面积约 "
                    f"{total_floor:.0f}㎡（厂房 {workshop:.0f}、办公配套 {office:.0f}、辅助 {aux:.0f}），"
                    f"绿化率 {_f(p.get('green_ratio'),0.15)*100:.0f}%，主厂房层高 {floor_h}m（{floor_note}）。"
                    f"数值为可研估算级，须结合业主与地方规划复核。"),
    }


def infer_headcount(build_area_m2: float = 0.0, land_area_mu: float = 0.0, industry: str = "") -> dict[str, Any]:
    """按行业每万㎡建筑面积定员推算劳动定员（生产/管理/销售分类）。"""
    p = _resolve(industry)
    area = _f(build_area_m2)
    if area <= 0 and land_area_mu:
        area = _f(land_area_mu) * _MU_TO_M2 * _f(p.get("plot_ratio"), 1.0)
    if area <= 0:
        return {"available": False, "reason": "缺少建筑面积/用地面积"}
    per = _f(p.get("headcount_per_10k_m2"), 60)
    total = max(int(round(area / 10000.0 * per)), 1)
    production = int(round(total * 0.70))
    management = int(round(total * 0.20))
    sales = max(total - production - management, 0)
    avg_wage = _f(p.get("avg_wage_wan"), 9.0)
    labor_cost_wan = round(total * avg_wage, 2)  # 年工资及福利总额（万元），供汇入财务 cost_items
    return {
        "available": True, "matched_industry": p.get("_matched", "（缺省参数）"),
        "total": total, "production": production, "management": management, "sales": sales,
        "avg_wage_wan": avg_wage, "labor_cost_wan": labor_cost_wan,
        "explain": (f"按建筑面积约 {area:.0f}㎡、行业定员强度 {per:.0f} 人/万㎡，推算新增定员约 {total} 人"
                    f"（生产 {production}、管理 {management}、销售 {sales}）；按人均年工资 {avg_wage:.1f} 万元估算"
                    f"年工资及福利约 {labor_cost_wan:.0f} 万元，据此汇入总成本费用表。"),
    }


def build_scale_summary(requirement: dict[str, Any]) -> dict[str, Any]:
    """从 requirement 聚合建设规模 + 定员推导（供服务层/生成链路复用）。

    读取 requirement 的 land_area_mu / output_scale / industry；先推建设规模，
    再用推得的总建筑面积推定员。任一缺失则相应部分 available=False，不抛错。
    """
    req = requirement or {}
    land = _f(req.get("land_area_mu"))
    industry = str(req.get("industry") or "")
    capacity = str(req.get("output_scale") or "")
    scale = infer_build_scale(land, capacity=capacity, industry=industry) if land > 0 else {
        "available": False, "reason": "缺少用地面积（requirement.land_area_mu）"}
    build_area = _f(scale.get("total_floor_area_m2")) if scale.get("available") else 0.0
    headcount = infer_headcount(build_area_m2=build_area, land_area_mu=land, industry=industry)
    return {
        "available": bool(scale.get("available") or headcount.get("available")),
        "industry": industry,
        "build_scale": scale,
        "headcount": headcount,
    }
