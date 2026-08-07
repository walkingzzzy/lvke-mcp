"""投资明细：三段式常量、扁平投资提升、明细解析与范围分类。"""

from __future__ import annotations

from typing import Any, Optional

# P0/P1 modular finance package (方案 §8/§13)
from lvke_mcp.domains.finance import normalize as _fin_normalize

from .base import (
    _f,
)


# 【P2】附表1 三段式颗粒度明细定义(对齐 lvke_templates.catalog investment-estimation 行模板)。
# 键 = 输入 dict 字段名;值 = 附表行中文标签。仅当上游显式提供才渲染,缺项按 0(不造数)。
_INVEST_ENGINEERING = [
    ("civil", "建筑工程费"), ("equipment", "设备及工器具购置费"), ("installation", "安装工程费"),
]


_INVEST_OTHER = [
    ("land", "土地使用费/拆迁补偿"), ("management", "项目管理费"), ("design", "勘察设计费"),
    ("consulting", "咨询服务费(含可研编制)"), ("supervision", "工程监理费"),
    ("bidding", "招标代理费"), ("test_run", "联合试运转费"),
]


_INVEST_CONTINGENCY = [
    ("basic", "基本预备费"), ("price", "价差预备费"),
]


_FLAT_LIFT_NOTE = "invest_breakdown 为扁平明细形态"


def _lift_flat_invest_breakdown(
    bd: dict[str, Any], assumptions: list[str],
) -> dict[str, Any]:
    """兼容扁平投资分项形态，禁止静默丢弃设备/安装费（压测缺陷 FIN-1）。

    requirement 层历史写法把工程费用三项直接平铺在 invest_breakdown 顶层：
        {"construction_wan": 建筑工程费, "equipment_wan": 设备购置费,
         "installation_wan": 安装工程费, "other_wan": 其他费用,
         "basic_reserve_rate": 基本预备费率}
    其中 construction_wan 语义是"建筑工程费"，与引擎嵌套形态中
    construction_wan="建设投资合计"冲突。引擎此前只解析嵌套
    construction_detail，顶层设备/安装费被静默忽略——固定资产原值只剩
    建筑费+利息，折旧/现金流/IRR/NPV 全部失真，可行性结论可能反转
    （实测 IRR 10.98% vs 3.04%）。

    触发条件（同时满足才提升，避免误伤规范嵌套输入）：
      1. 顶层出现 equipment_wan / installation_wan（引擎从不在顶层写这两个键）；
      2. 未提供嵌套 construction_detail。
    提升规则（确定性算术，全部来自显式输入，不造数）：
      - construction_detail = {civil: construction_wan, equipment, installation}
      - 无 reserve_wan/contingency_detail 且给了 basic_reserve_rate 时，
        基本预备费 = 费率 × (工程费用 + 其他费用)
      - construction_wan 重算为 建设投资 = 工程费用 + 其他费用 + 预备费
    """
    if not isinstance(bd, dict) or not bd:
        return bd
    if isinstance(bd.get("construction_detail"), dict):
        return bd
    equipment = _f(bd.get("equipment_wan"))
    installation = _f(bd.get("installation_wan"))
    if equipment is None and installation is None:
        return bd
    civil = _f(bd.get("civil_wan"))
    if civil is None:
        civil = _f(bd.get("construction_wan"))
    eng_sum = round(
        sum(v for v in (civil, equipment, installation) if v is not None), 2
    )
    other = _f(bd.get("other_wan")) or 0.0
    reserve = _f(bd.get("reserve_wan"))
    lifted = dict(bd)
    detail: dict[str, float] = {}
    if civil is not None:
        detail["civil_wan"] = round(civil, 2)
    if equipment is not None:
        detail["equipment_wan"] = round(equipment, 2)
    if installation is not None:
        detail["installation_wan"] = round(installation, 2)
    lifted["construction_detail"] = detail
    reserve_note = ""
    rate = _f(bd.get("basic_reserve_rate"))
    if (
        reserve is None
        and rate is not None
        and rate > 0
        and not isinstance(bd.get("contingency_detail"), dict)
    ):
        reserve = round((eng_sum + other) * rate, 2)
        lifted["contingency_detail"] = {"basic_wan": reserve}
        reserve_note = (
            f"，基本预备费按费率 {rate:.1%} 计 {reserve:,.2f} 万元"
            "(基数=工程费用+其他费用)"
        )
    lifted["construction_wan"] = round(eng_sum + other + (reserve or 0.0), 2)
    assumptions.append(
        f"{_FLAT_LIFT_NOTE}：顶层设备/安装费已并入工程费用三项"
        f"(合计 {eng_sum:,.2f} 万元)，建设投资按 工程费用+其他费用+预备费 重算为 "
        f"{lifted['construction_wan']:,.2f} 万元{reserve_note}"
        "（原 construction_wan 视为建筑工程费）"
    )
    return lifted


def _parse_invest_detail(bd: dict[str, Any]) -> Optional[dict[str, Any]]:
    """解析 invest_breakdown 下的三段式颗粒度明细。无任何明细时返回 None。

    读取 ``construction_detail`` / ``other_detail`` / ``contingency_detail`` 三个子 dict,
    各含标准键(见 _INVEST_* 定义)。返回 {engineering:[(label,val)], other:[...], contingency:[...],
    engineering_total, other_total, contingency_total}。缺项不计入(不造数)。
    """
    def _pick(sub: Any, spec: list[tuple[str, str]]) -> list[tuple[str, float]]:
        if not isinstance(sub, dict):
            return []
        out: list[tuple[str, float]] = []
        for key, label in spec:
            v = _f(sub.get(f"{key}_wan"))
            if v is None:
                v = _f(sub.get(key))
            if v is not None:
                out.append((label, round(v, 2)))
        return out

    def _items(value: Any) -> list[tuple[str, float]]:
        out: list[tuple[str, float]] = []
        for index, item in enumerate(value if isinstance(value, list) else []):
            if not isinstance(item, dict):
                continue
            amount = _f(item.get("amount_wan"))
            if amount is None:
                quantity = _f(item.get("quantity"))
                indicator = _f(
                    item.get("indicator_yuan")
                    if item.get("indicator_yuan") is not None
                    else item.get("indicator")
                )
                if quantity is not None and indicator is not None:
                    amount = round(quantity * indicator / 10000.0, 2)
            if amount is None:
                continue
            out.append((str(item.get("name") or f"明细{index + 1}"), round(amount, 2)))
        return out

    eng = _items(bd.get("construction_items") or bd.get("engineering_items"))
    if not eng:
        eng = _pick(bd.get("construction_detail"), _INVEST_ENGINEERING)
    oth = _items(bd.get("other_items"))
    if not oth:
        oth = _pick(bd.get("other_detail"), _INVEST_OTHER)
    if not oth:
        summary_other = _f(bd.get("other_wan"))
        if summary_other is not None:
            oth = [("工程建设其他费合计", round(summary_other, 2))]
    con = _items(bd.get("contingency_items"))
    if not con:
        con = _pick(bd.get("contingency_detail"), _INVEST_CONTINGENCY)
    if not con:
        summary_reserve = _f(bd.get("reserve_wan"))
        if summary_reserve is not None:
            con = [("预备费合计", round(summary_reserve, 2))]
    if not (eng or oth or con):
        return None
    return {
        "engineering": eng, "other": oth, "contingency": con,
        "engineering_total": round(sum(v for _, v in eng), 2),
        "other_total": round(sum(v for _, v in oth), 2),
        "contingency_total": round(sum(v for _, v in con), 2),
    }


# 【P0-1 / F13-P13-01·02】投资口径统一与历史歧义判定（方案 §5.1/§5.4）。
# 规范定义：
#   项目总投资 = 建设投资 + 建设期融资费用（利息等）+ 流动资金
#   建设投资 = 工程费用 + 工程建设其他费用（other）+ 预备费（reserve）  ← other/reserve 是组成，非另加
# 该函数只“判定口径 + 打状态标记”，绝不改动既有算术：歧义样例照旧出匡算预览数值，
# 但状态标 ambiguous 且下游发布门禁据此阻断终稿（方案 §9.4）。
_SCOPE_TOL = 1.0  # 万元级容差：口径判定按四舍五入到分的输入比对


def _classify_investment_scope(
    total, construction, other, reserve, interest, working,
):
    """P0-1：委托 finance.normalize（保留原函数名供测试/兼容）。"""
    return _fin_normalize.classify_investment_scope(
        total, construction, other, reserve, interest, working,
    )
