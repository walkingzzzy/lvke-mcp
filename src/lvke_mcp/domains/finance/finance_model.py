"""可研财务测算模型（PT-1：专业化第一支柱）。

从 ``requirement.finance`` 的用户输入参数，用 finance_calc 的确定性纯函数
（npv/irr/payback）产出**真实**财务指标与制式附表 Markdown，供生成链路把
"详见第六章测算"占位替换为真实数字，并保证正文/技经指标表/附表三处勾稽一致。

设计原则：
- 只做"可研深度"的简化联动模型（±15% 合理区间），不追求审计级精度。
- 缺输入时按行业经验默认估算，并标 ``assumptions`` 以便前端/审查提示。
- 所有衍生数字来自本模块（单一真源），杜绝 LLM 心算导致的不一致。
"""

from __future__ import annotations

import copy
import math
from typing import Any, Optional

# P0/P1 modular finance package (方案 §8/§13)
from lvke_mcp.domains.finance import assets as _fin_assets
from lvke_mcp.domains.finance import capitalization as _fin_cap
from lvke_mcp.domains.finance import checks as _fin_checks
from lvke_mcp.domains.finance import debt as _fin_debt
from lvke_mcp.domains.finance import normalize as _fin_normalize
from lvke_mcp.domains.finance import scenarios as _fin_scenarios
from lvke_mcp.domains.finance import statements as _fin_statements
from lvke_mcp.domains.finance import taxes as _fin_taxes
from lvke_mcp.domains.finance import timeline as _fin_timeline
from lvke_mcp.domains.finance import working_capital as _fin_wc
from lvke_mcp.domains.finance.contracts import FINANCE_SCHEMA_VERSION

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
    if is_operating and spec and _spec_rev_model != "flat":
        try:
            from lvke_mcp.domains.finance import revenue_models

            _op_years_for_rev = max(calc_years - build_years, 1)
            _exp = revenue_models.expand(spec, _op_years_for_rev)
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
            _dep_only = _fin_assets.annual_straight_line(
                _dep_base, _dep_years, salvage_rate=_salvage_rate)               # 附表6-2 折旧
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
        result["raw"]["depreciation_class_schedule"] = _dep_class_schedule.get("rows") or []
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
        elif _dep_schedule_values:
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
    result["timeline"] = _fin_timeline.build_timeline(
        calc_years=calc_years,
        build_years=build_years,
        loan_years=loan_years,
        loan_grace_years=_grace,
        depreciation_years=_dep_y_tl,
        amortization_years=_amort_y_tl,
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


def _render_tables(r: dict[str, Any]) -> dict[str, str]:
    inv = r["investment"]
    fund = r["funding"]
    ind = r.get("indicators") or {}
    tables: dict[str, str] = {}

    # 附表1 固定资产投资估算表（交付编号：晏批注附表1）。
    # 【P2】按引擎实际持有的投资分项展示：建设投资（若提供工程建设其他费用/预备费则拆分显示）、
    #   建设期利息、流动资金。注：更细的"建筑/设备/安装工程费"三段式需上游采集颗粒度投资输入，
    #   当前输入 schema 未提供；不从单一建设投资数凭空拆分（可研不得造数），故按已有颗粒度呈现。
    _detail = inv.get("breakdown_detail")
    _other = inv.get("other")
    _reserve = inv.get("reserve")
    rows = []
    if _detail and (_detail["engineering"] or _detail["other"] or _detail["contingency"]):
        # 最细档：完整三段式（工程费用 1.1 / 工程建设其他费用 1.2 / 预备费 1.3，各带细项）。
        _eng_t = _detail["engineering_total"]
        _oth_t = _detail["other_total"]
        _con_t = _detail["contingency_total"]
        rows.append(("1", "建设投资", inv["construction"]))
        if _detail["engineering"]:
            rows.append(("1.1", "　一、工程费用", _eng_t))
            for i, (label, val) in enumerate(_detail["engineering"], 1):
                rows.append((f"1.1.{i}", f"　　{label}", val))
        if _detail["other"]:
            rows.append(("1.2", "　二、工程建设其他费用", _oth_t))
            for i, (label, val) in enumerate(_detail["other"], 1):
                rows.append((f"1.2.{i}", f"　　{label}", val))
        if _detail["contingency"]:
            rows.append(("1.3", "　三、预备费", _con_t))
            for i, (label, val) in enumerate(_detail["contingency"], 1):
                rows.append((f"1.3.{i}", f"　　{label}", val))
    elif _other or _reserve:
        # 中档：仅有分段汇总(其他/预备费)，无细项 → 拆 3 行。
        _eng = round((inv["construction"] or 0.0) - (_other or 0.0) - (_reserve or 0.0), 2)
        rows.append(("1", "建设投资", inv["construction"]))
        rows.append(("1.1", "　工程费用（建安工程）", _eng))
        if _other:
            rows.append(("1.2", "　工程建设其他费用", _other))
        if _reserve:
            rows.append(("1.3", "　预备费", _reserve))
    else:
        # 降级档：仅单一建设投资数（不造数）。
        rows.append(("1", "建设投资", inv["construction"]))
    rows.append(("2", "建设期利息", inv["interest"]))
    rows.append(("3", "流动资金", inv["working_capital"]))
    rows.append(("", "项目总投资合计", inv["total"]))
    body = "\n".join(f"| {no} | {name} | {_fmt(v)} |" for no, name, v in rows)
    tables["investment"] = "| 序号 | 项目 | 金额（万元） |\n| --- | --- | --- |\n" + body

    # 附表 资金筹措
    frows = [
        ("1", "项目资本金（自筹）", fund["capital"], fund["capital_pct"]),
        ("2", "银行贷款", fund["loan"], fund["loan_pct"]),
    ]
    if fund["subsidy"]:
        frows.append(("3", "政府补助/专项债", fund["subsidy"], fund["subsidy_pct"]))
    frows.append(("", "合计", inv["total"], 100.0))
    fbody = "\n".join(f"| {no} | {name} | {_fmt(v)} | {_fmt(p)} |" for no, name, v, p in frows)
    tables["funding"] = "| 序号 | 资金来源 | 金额（万元） | 占比（%） |\n| --- | --- | --- | --- |\n" + fbody

    # 主要技术经济指标
    mrows = [("项目总投资", "万元", inv["total"]),
             ("其中：建设投资", "万元", inv["construction"]),
             ("　　　建设期利息", "万元", inv["interest"]),
             ("　　　流动资金", "万元", inv["working_capital"]),
             ("项目资本金", "万元", fund["capital"]),
             ("银行贷款", "万元", fund["loan"])]
    if ind:
        mrows += [
            ("达产年营业收入", "万元", ind.get("revenue")),
            ("达产年总成本费用", "万元", ind.get("op_cost")),
            ("达产年净利润", "万元", ind.get("net_profit")),
            ("项目投资财务内部收益率(IRR)", "%", ind.get("project_irr_pct")),
            (f"财务净现值(Ic={ind.get('benchmark_rate_pct')}%)", "万元", ind.get("npv_wan")),
            ("静态投资回收期", "年", ind.get("static_payback_years")),
            ("动态投资回收期", "年", ind.get("dynamic_payback_years")),
            ("盈亏平衡点", "%", ind.get("bep_pct")),
        ]
    mbody = "\n".join(f"| {name} | {unit} | {_fmt(v)} |" for name, unit, v in mrows)
    tables["indicators"] = "| 指标名称 | 单位 | 数值 |\n| --- | --- | --- |\n" + mbody
    # M1 T1.1：合并逐年联动附表（key 对齐 lvke_templates.catalog 的 template_id）
    tables.update(_render_annual_tables(r))
    # 别名：3 张基础表同时以 catalog template_id 暴露，供 appendix ready 判定
    tables["investment-estimation"] = tables["investment"]
    tables["financing"] = tables["funding"]
    tables["key-indicators"] = tables["indicators"]
    return tables


def _render_summary(r: dict[str, Any]) -> str:
    """供注入生成 prompt 的"确定性财务数据块"（要求 LLM 原样引用，不得改动）。"""
    inv, fund, ind = r["investment"], r["funding"], r.get("indicators") or {}
    lines = [
        "【以下为 finance-calc 确定性测算结果，必须原样引用，不得改动、不得写“详见第六章测算”】",
        f"- 项目总投资：{_fmt(inv['total'])} 万元（建设投资 {_fmt(inv['construction'])}、建设期利息 {_fmt(inv['interest'])}、流动资金 {_fmt(inv['working_capital'])}）",
        f"- 资金筹措：资本金/自筹 {_fmt(fund['capital'])} 万元（{_fmt(fund['capital_pct'])}%）、银行贷款 {_fmt(fund['loan'])} 万元（{_fmt(fund['loan_pct'])}%）"
        + (f"、政府补助 {_fmt(fund['subsidy'])} 万元" if fund['subsidy'] else "")
        + f"；贷款期限 {fund['loan_years']} 年、年利率 {fund['loan_rate']*100:.1f}%",
    ]
    if ind:
        lines += [
            f"- 达产年：营业收入 {_fmt(ind.get('revenue'))} 万元、总成本费用 {_fmt(ind.get('op_cost'))} 万元、净利润 {_fmt(ind.get('net_profit'))} 万元",
            f"- 财务指标：IRR {_fmt(ind.get('project_irr_pct'))}%、财务净现值(Ic={ind.get('benchmark_rate_pct')}%) {_fmt(ind.get('npv_wan'))} 万元、静态回收期 {_fmt(ind.get('static_payback_years'))} 年、动态回收期 {_fmt(ind.get('dynamic_payback_years'))} 年、盈亏平衡点 {_fmt(ind.get('bep_pct'))}%",
        ]
    else:
        lines.append("- 本项目为非经营性项目，不计算 IRR/NPV，按全生命周期资金平衡分析。")
    if r.get("assumptions"):
        lines.append("- 测算假设：" + "；".join(r["assumptions"]))
    return "\n".join(lines)


def _required_markers(r: dict[str, Any]) -> list[str]:
    m = ["项目总投资", "项目资本金", "银行贷款"]
    if r.get("indicators"):
        m += ["财务内部收益率", "财务净现值", "投资回收期", "盈亏平衡点"]
    return m


def finance_tables_markdown(r: dict[str, Any]) -> str:
    """把附表拼成可直接嵌入正文的 Markdown（财务章尾部/附表区）。

    【2026-07-12】主路径改为 catalog 风格 structured 投影 → MD 适配器；
    不再以手写管道表为唯一实现。失败时回退旧 _render 结果中的 tables 字符串。
    """
    if not r.get("available"):
        return ""
    try:
        from lvke_mcp.domains.finance import table_render

        pack = table_render.build_all_structured(r)
        md = table_render.finance_tables_markdown_from_structured(pack, r)
        if md and md.strip():
            return md
    except Exception:  # noqa: BLE001
        pass
    # 回退：使用已渲染的 result['tables'] 字符串
    t = r.get("tables") or {}
    seq = [
        ("附表1 固定资产投资估算表（万元）", "investment"),
        ("附表2 建设期贷款利息表（万元）", "interest-during-construction"),
        ("附表3 流动资金估算表（万元）", "working-capital"),
        ("附表4 投资使用计划与资金筹措表（万元）", "funding"),
        ("附表5 营业收入、税金及附加和增值税估算表（万元）", "income-statement"),
        ("附表6 总成本费用估算表（万元）", "total-cost"),
        ("附表6-1 工资及附加估算表（万元）", "wage"),
        ("附表6-2 固定资产折旧费估算表（万元）", "depreciation"),
        ("附表6-3 无形资产及其他资产摊销估算表（万元）", "amortization"),
        ("附表7 利润与利润分配表（万元）", "profit-distribution"),
        ("附表8 还款付息测算表（万元）", "debt-service"),
        ("附表9 项目投资现金流量表（万元）", "cashflow"),
        ("附表10 项目资本金流量表（万元）", "capital-cashflow"),
    ]
    display_seq = [
        ("控制表 C03 财务计划现金流量表（万元）", "financial-plan"),
        ("附表（展示）主要技术经济指标表", "indicators"),
        ("附表（展示）单因素敏感性分析表", "sensitivity"),
    ]
    parts: list[str] = []
    for title, key in seq + display_seq:
        md = t.get(key)
        if md:
            parts.append(f"\n\n**{title}**\n\n{md}")
    sc = r.get("scenarios") or {}
    if sc.get("base"):
        parts.append(
            "\n\n**情景分析**\n\n"
            f"- 基准：IRR {_fmt(sc['base'].get('irr_pct'))}%\n"
            f"- 乐观：IRR {_fmt((sc.get('bull') or {}).get('irr_pct'))}%\n"
            f"- 悲观：IRR {_fmt((sc.get('bear') or {}).get('irr_pct'))}%"
        )
    body = "".join(parts)
    return body


# ══════════════════════════════════════════════════════════════════════════
# M1 T1.1：可研简化联动逐年附表（income/total_cost/debt_service/cashflow/…）
# 设计：逐年现金流与现有 operating.cashflows 同源，保证「项目 IRR 三处一致」；
#       各表彼此勾稽（经营成本=总成本-折旧-摊销-利息；利润=收入-总成本-税金）。
#       口径不改变既有 indicators/IRR，避免破坏现有行为。
# ══════════════════════════════════════════════════════════════════════════


def _construction_interest(loan: float, rate: float, build_years: int,
                           draw_plan: Optional[list[float]] = None) -> list[dict]:
    """P1：建设期利息分年表（分年提款 + 半期计息）。

    通用可研口径：建设期借款一般假设年中支用，当年借款按半年计息，往年借款按全年计息。
      第 n 年利息 = (期初累计借款 + 当年借款/2) × 利率
    参照甲方模板 ``附表2!C8=C7*rate/2*上浮`` 的半期计息思想，但不硬编码利率/上浮，
    利率与提款计划均由参数传入；缺提款计划时按建设期均匀提款。

    返回逐年 ``{period, begin_balance, draw, rate, interest, end_balance}``。
    """
    rows: list[dict] = []
    if loan <= 0 or build_years <= 0:
        return rows
    if not draw_plan:
        per = round(loan / build_years, 2)
        draw_plan = [per] * (build_years - 1) + [round(loan - per * (build_years - 1), 2)]
    begin = 0.0
    for y in range(build_years):
        draw = draw_plan[y] if y < len(draw_plan) else 0.0
        interest = round((begin + draw / 2.0) * rate, 2)  # 半期计息：当年借款按半年
        end = round(begin + draw, 2)
        rows.append({"period": y + 1, "begin_balance": round(begin, 2), "draw": round(draw, 2),
                     "rate": rate, "interest": interest, "end_balance": end})
        begin = end
    return rows


def _equal_principal_debt(loan: float, years: int, rate: float, op_years: int,
                          *, method: str = "equal_principal", grace_years: int = 0,
                          balloon_pct: float = 0.3,
                          principal_schedule: list | None = None,
                          interest_schedule: list | None = None) -> list[dict]:
    """偿债计划（P1-4）：默认等额本金；支持等额本息/到期还本/气球/甲方还本序列。"""
    return _fin_debt.build_debt_schedule(
        loan, years, rate, op_years,
        method=method or "equal_principal",
        grace_years=grace_years or 0,
        balloon_pct=balloon_pct,
        principal_schedule=principal_schedule,
        interest_schedule=interest_schedule,
    )


def _build_annual(r: dict[str, Any]) -> dict[str, Any]:
    """基于已算 indicators/operating 生成逐年结构化附表（达产恒定简化）。"""
    inv, fund, params = r["investment"], r["funding"], r["params"]
    ind = r.get("indicators") or {}
    op = r.get("operating") or {}
    build = int(params["build_years"])
    calc = int(params["calc_years"])
    op_years = max(calc - build, 1)
    loan = fund["loan"]
    rate = fund["loan_rate"]
    loan_years = int(fund["loan_years"])

    _method = (params.get("loan_repay_method") or (r.get("params") or {}).get("loan_repay_method")
               or ((r.get("raw") or {}).get("loan_repay_method")) or "equal_principal")
    _grace = int((r.get("params") or {}).get("loan_grace_years") or 0)
    _prin_sched = (r.get("raw") or {}).get("loan_principal_by_year") or (
        (r.get("params") or {}).get("loan_principal_by_year")
    )
    _int_sched = (r.get("raw") or {}).get("loan_interest_by_year")
    if _prin_sched:
        _method = "principal_schedule"
    debt = _equal_principal_debt(
        loan, loan_years, rate, op_years, method=str(_method), grace_years=_grace,
        balloon_pct=float((r.get("raw") or {}).get("loan_balloon_pct") or 0.3),
        principal_schedule=list(_prin_sched) if _prin_sched else None,
        interest_schedule=list(_int_sched) if _int_sched else None,
    )
    annual: dict[str, Any] = {"debt_service": debt}

    raw = r.get("raw") or {}
    if ind:  # 经营性项目
        def _annual_consumption_tax(index: int = 0) -> float:
            value = raw.get("consumption_tax_payable_wan")
            if isinstance(value, (list, tuple)):
                if not value:
                    return 0.0
                return max(float(value[min(index, len(value) - 1)] or 0.0), 0.0)
            return max(float(value or 0.0), 0.0)

        revenue = ind.get("revenue") or 0.0
        total_cost = ind.get("op_cost") or 0.0      # 现有口径：总成本费用（含折旧）
        dep_charge = ind.get("depreciation") or 0.0  # 现有单一非现金摊提额（折旧口径）
        tax_sur = ind.get("tax_surcharge") or 0.0
        vat_output = ind.get("vat_output") or 0.0    # PG5-a 附表5：销项税
        vat_input = ind.get("vat_input") or 0.0      # 进项税
        vat_payable = ind.get("vat_payable") or 0.0  # 应纳增值税
        op_cash_cost = round(total_cost - dep_charge, 2)   # 现金经营成本（不含折旧摊销）
        # BC-P2：逐年 P&L（spec 非 flat 时非空）。附表5收入/6总成本/7利润改按 pnl_by_year 逐年填，
        # 达产年与现状一致；pnl_by_year=None（flat/单点）时各年恒为达产值，字节级不变（老测试全绿）。
        pnl = (r.get("operating") or {}).get("pnl_by_year") or None

        # 【M1 修复】附表6-2/6-3 折旧、摊销独立取值（compute_financials 已按各自基数算好并透传），
        # 不再用 dep_charge − amort 反拆（反拆会让无形资产既进折旧基数又单独摊销，两表不自洽）。
        # 折旧+摊销 == dep_charge（非现金摊提合计），total_cost / 利润 / IRR 口径不变。
        intangible = raw.get("intangible_wan")
        amort_years = int(raw.get("amortization_years") or 10)
        if raw.get("dep_only") is not None:
            dep = round(raw.get("dep_only") or 0.0, 2)       # 附表6-2 折旧（基数已扣无形、扣残值）
            amort = round(raw.get("amort_only") or 0.0, 2)   # 附表6-3 摊销（无形/摊销年限）
        else:
            # 兜底（理论不走到：operating 块一定已透传）：退回原反拆逻辑
            amort = 0.0
            if intangible and intangible > 0 and amort_years > 0:
                amort = round(min(intangible / amort_years, dep_charge), 2)
            dep = round(dep_charge - amort, 2)

        # P0 附表6-1：工资及附加。优先 cost_items["工资及福利"]/raw.wage_wan，否则按经营成本占比估。
        # Fix-P1-1：键名已含「福利/附加」时视为总额含附加，内拆而非再 ×(1+r)。
        cost_items = raw.get("cost_items") or {}
        wage = raw.get("wage_wan")
        # Public contract defines wage_wan as the annual total including
        # welfare/surcharges.  Treating it as base salary and applying the
        # welfare rate again inflated table 6-1 and broke the cost-detail tie.
        wage_key_includes_welfare = wage is not None
        if wage is None and isinstance(cost_items, dict):
            for _k, _v in cost_items.items():
                _ks = str(_k)
                if "工资" in _ks or "薪" in _ks or "人工" in _ks:
                    wage = _f(_v)
                    if "福利" in _ks or "附加" in _ks:
                        wage_key_includes_welfare = True
                    break
        # BC-P3：工资占比/福利率/所得税率三级取值（spec.cost → config → 兜底=原硬编码 0.15/0.14/0.25）。
        _spec = r.get("spec")
        _wage_rate = _cost_param(_spec, "wage_rate", "cost")       # 原硬编码 0.15
        _welfare_rate = _cost_param(_spec, "welfare_rate", "cost")  # 原硬编码 0.14
        _income_tax_rate = _cost_param(_spec, "income_tax_rate", "tax")  # 原硬编码 0.25
        wage_estimated = False
        negative_cash_cost = op_cash_cost < -0.005
        if negative_cash_cost:
            r.setdefault("blocking_issues", []).append({
                "rule": "negative_operating_cost",
                "detail": (
                    "达产年总成本费用小于折旧/摊销，无法推导非负现金经营成本；"
                    "须显式补充 annual_operating_cost_wan、cost_items 或 operating_cost_by_year"
                ),
            })
        if wage is None:
            # BC-4a 房产：工资应按期间费用估，不得把开发成本结转额计入基数（否则虚高）。
            _wage_base = op_cash_cost
            if raw.get("property_inventory") and raw.get("period_opex") is not None:
                _wage_base = float(raw.get("period_opex") or 0.0)
            # 负现金成本由 run 固化门禁阻断；这里不继续扩散成负工资。
            wage = 0.0 if negative_cash_cost else round(_wage_base * _wage_rate, 2)
            wage_estimated = True
        wage = round(max(min(float(wage or 0.0), max(op_cash_cost, 0.0)), 0.0), 2)
        if wage_key_includes_welfare and not wage_estimated and _welfare_rate > 0:
            # 输入已是「工资及福利」总额：内拆，禁止 total 膨胀到 输入×(1+r)
            wage_total = wage
            wage = round(wage_total / (1.0 + float(_welfare_rate)), 2)
            welfare = round(wage_total - wage, 2)
        else:
            welfare = round(wage * float(_welfare_rate), 2)  # 职工福利/附加按工资占比简化

        income_rows, cost_rows, profit_rows = [], [], []
        wage_rows, dep_rows, amort_rows = [], [], []
        # 【M1 修复】折旧表原值用折旧基数（固定资产扣无形），使表内"原值×(1−残值率)/年限"复算 == 折旧额。
        # BC-4a 房产：无固定资产折旧，附表6-2 各年折旧=0、原值=0。
        _is_property_inv = bool(raw.get("property_inventory"))
        fixed_base = raw.get("dep_base")
        if fixed_base is None:
            fixed_base = 0.0 if _is_property_inv else (r["investment"].get("fixed_asset") or 0.0)
        salvage_rate = raw.get("salvage_rate") if raw.get("salvage_rate") is not None else (0.0 if _is_property_inv else 0.05)
        dep_years = int(raw.get("depreciation_years") or (0 if _is_property_inv else op_years))
        _classified_dep_rows = (
            list(raw.get("depreciation_class_schedule") or [])
            if isinstance(raw.get("depreciation_class_schedule"), list) else []
        )
        if _is_property_inv:
            dep = 0.0  # 强制：开发产品不折旧
        # 【P1-3】逐年增值税含留抵结转（方案 §6.5）：先按各年营收比例缩放销项/进项，再走留抵滚动
        #   账户——当期进项抵不完的部分结转下期，不逐年独立 max。无留抵结余时与旧 vat_payable 一致。
        _vat_out_seq: list[float] = []
        _vat_in_seq: list[float] = []
        for y in range(op_years):
            py = pnl[y] if (pnl and y < len(pnl)) else None
            _rev_v = py["revenue"] if py else revenue
            _ratio_v = (_rev_v / revenue) if revenue else 1.0
            _vat_out_seq.append(round(vat_output * _ratio_v, 2))
            _vat_in_seq.append(round(vat_input * _ratio_v, 2))
        _vat_series = _compute_vat_with_credit_carryover(_vat_out_seq, _vat_in_seq)
        _vat_had_credit = any(vr["credit_end"] > 0 for vr in _vat_series)
        for y in range(op_years):
            interest = debt[y]["interest"] if y < len(debt) else 0.0
            # 【P1-2】寿命内计提、期满归零（禁止静默延长寿命）；表内按年真实滚动。
            dep_y = (
                round(float(_classified_dep_rows[y].get("depreciation") or 0.0), 2)
                if y < len(_classified_dep_rows)
                else round(dep, 2) if (y + 1) <= max(dep_years, 0) else 0.0
            )
            amort_y = round(amort, 2) if (y + 1) <= max(amort_years, 0) else 0.0
            # BC-P2：spec 非 flat 时逐年取该年 P&L，否则用达产恒定值（现状口径不变）。
            py = pnl[y] if (pnl and y < len(pnl)) else None
            rev_y = py["revenue"] if py else revenue
            occ_y = py["op_cash_cost"] if py else op_cash_cost   # 该年现金经营成本
            tax_sur_y = py["tax_surcharge"] if py else tax_sur
            surtax_component_y = None
            # P1-3：若启用附加税以应纳增值税为基数（finance.surtax_on_vat=true），覆盖当年附加税
            if bool((r.get("raw") or {}).get("surtax_on_vat")) or bool((r.get("params") or {}).get("surtax_on_vat")):
                _vp = float((_vat_series[y]["payable"] if y < len(_vat_series) else vat_payable) or 0.0)
                # vat_add_rate 在简化路径是「营收附加率」；VAT 基数优先使用所在地法定分项。
                _policy = (r.get("raw") or {}).get("surtax_component_policy") or {}
                if _policy.get("mode") == "statutory_components":
                    surtax_component_y = _fin_taxes.surtax_components_from_tax_payable(
                        [_vp],
                        consumption_tax_by_year=[_annual_consumption_tax(y)],
                        urban_maintenance_rate=float(_policy["urban_maintenance_rate"]),
                        education_surcharge_rate=float(_policy["education_surcharge_rate"]),
                        local_education_surcharge_rate=float(_policy["local_education_surcharge_rate"]),
                    )[0]
                    tax_sur_y = surtax_component_y["total"]
                else:
                    _srate = float((r.get("raw") or {}).get("surtax_vat_rate") or 0.12)
                    tax_sur_y = round(max(_vp, 0.0) * _srate, 2)
            # 逐年表是附表和审查的唯一会计数字源。P&L 预投影可能使用
            # 平均折旧或旧税费口径，因此在分类折旧和法定附加税已解析后必须
            # 从同一年的收入、现金成本、摊提和税费重算，不得沿用旧 profit_before。
            tc_y = round(occ_y + dep_y + amort_y, 2)
            pb_y = round(rev_y - tc_y - tax_sur_y, 2)
            if py and float(py.get("profit_before") or 0.0) > 0:
                # 保留免税/减半等逐年有效税率，仅用新税前利润替换税基。
                tax_rate_y = max(
                    float(py.get("income_tax") or 0.0)
                    / float(py.get("profit_before") or 1.0),
                    0.0,
                )
            else:
                tax_rate_y = _income_tax_rate
            it_y = round(max(pb_y, 0.0) * tax_rate_y, 2)
            np_y = round(pb_y - it_y, 2)
            # 【P0-03 修复】附表7 利润表为融资后会计口径:利润总额须扣运营期利息(计入财务费用),
            #   据此重算实际所得税与会计净利润。indicators/附表9 项目投资现金流为融资前口径
            #   (利息不进融资前现金流),两套口径分离、互不污染——故此改动不影响 IRR/NPV/资本金IRR。
            #   有效税率优先按融资前该年实际税率(捕捉免税/减半优惠),缺 pnl 时用基准所得税率。
            eff_rate_y = (it_y / pb_y) if (py and pb_y > 0) else _income_tax_rate
            pb_acct = round(pb_y - interest, 2)                    # 会计利润总额(已扣运营期利息)
            it_acct = round(max(pb_acct, 0.0) * eff_rate_y, 2)     # 实际所得税(按会计利润)
            np_acct = round(pb_acct - it_acct, 2)                  # 会计净利润
            surplus_y = round(max(np_acct, 0.0) * 0.10, 2)         # 法定盈余公积(按会计净利润 10%)
            # 【P1-3】逐年增值税取留抵结转序列（当期销项先冲上期留抵再冲当期进项，抵不完结转下期）。
            _vrow = _vat_series[y] if y < len(_vat_series) else {"output": vat_output, "input_used": vat_input, "payable": vat_payable, "credit_end": 0.0}
            income_rows.append({
                "year": y + 1, "revenue": rev_y, "operating_cost": occ_y,
                "depreciation": dep_y, "tax_surtax": tax_sur_y,
                "vat_output": _vrow["output"], "vat_input": _vrow["input_used"],
                "vat_payable": _vrow["payable"], "vat_credit_end": _vrow["credit_end"],
                "consumption_tax_payable": (
                        surtax_component_y.get("consumption_tax_payable")
                        if surtax_component_y else _annual_consumption_tax(y)
                ),
                "surtax_tax_base": (
                    surtax_component_y.get("tax_base") if surtax_component_y else None
                ),
                "urban_maintenance_tax": (
                    surtax_component_y.get("urban_maintenance_tax")
                    if surtax_component_y else None
                ),
                "education_surcharge": (
                    surtax_component_y.get("education_surcharge")
                    if surtax_component_y else None
                ),
                "local_education_surcharge": (
                    surtax_component_y.get("local_education_surcharge")
                    if surtax_component_y else None
                ),
                "ebit": pb_y, "income_tax": it_y, "net_profit": np_y,
            })
            cost_rows.append({
                "year": y + 1, "operating_cost": occ_y, "depreciation": dep_y,
                "amortization": amort_y, "interest": interest,
                "total_cost": round(occ_y + dep_y + amort_y + interest, 2),
            })
            profit_rows.append({
                # total_cost 与附表6 同源:现金经营成本+折旧+摊销+利息。
                "year": y + 1, "revenue": rev_y,
                "total_cost": round(occ_y + dep_y + amort_y + interest, 2),
                "tax_surtax": tax_sur_y, "total_profit": pb_acct, "income_tax": it_acct,
                "net_profit": np_acct, "surplus_reserve": surplus_y,
                "undistributed": round(np_acct - surplus_y, 2),
            })
            # 附表6-1 工资及附加逐年
            wage_rows.append({"year": y + 1, "wage": wage, "welfare": welfare,
                              "total": round(wage + welfare, 2)})
            # 附表6-2 折旧逐年：寿命内计提，期满为 0（P1-2 不静默延长）。
            # 表内"原值×(1−残值率)/折旧年限"在计提年可复算 == 当期折旧费。
            if y < len(_classified_dep_rows):
                class_row = _classified_dep_rows[y]
                cumulative_dep = round(
                    float(class_row.get("cumulative_depreciation") or 0.0), 2
                )
                dep_rows.append({
                    "year": y + 1,
                    "original_value": round(fixed_base, 2),
                    "salvage_rate": salvage_rate,
                    "dep_years": dep_years,
                    "depreciation": dep_y,
                    "cumulative_depreciation": cumulative_dep,
                    "net_value": round(fixed_base - cumulative_dep, 2),
                    "depreciation_basis": "classified",
                    "classes": list(class_row.get("classes") or []),
                })
            else:
                cumulative_dep = round(sum(
                    dep if j < dep_years else 0.0 for j in range(y + 1)
                ), 2)
                cumulative_dep = min(cumulative_dep, round(fixed_base * (1 - salvage_rate), 2))
                dep_rows.append({"year": y + 1, "original_value": round(fixed_base, 2),
                                 "salvage_rate": salvage_rate, "dep_years": dep_years,
                                 "depreciation": dep_y, "cumulative_depreciation": cumulative_dep,
                                 "net_value": round(fixed_base - cumulative_dep, 2)})
            # 附表6-3 摊销逐年：寿命内计提，期满为 0。
            amort_rows.append({"year": y + 1, "base": round(intangible or 0.0, 2),
                               "amort_years": amort_years,
                               "amortization": amort_y})
            # DSCR = CFADS / 偿债额 = (会计净利润+折旧+摊销+利息) / (还本+付息)。
            # 会计净利润已扣息,加回利息还原为可用于偿债的现金流;原码用未扣息净利润→利息被加两次、DSCR 偏高。
            due = (debt[y]["principal"] + debt[y]["interest"]) if y < len(debt) else 0.0
            cogs_addback_y = float(py.get("inventory_cogs_addback") or 0.0) if py else 0.0
            avail = np_acct + dep_y + amort_y + interest + cogs_addback_y
            if y < len(debt):
                debt[y]["dscr"] = round(avail / due, 2) if due > 0 else None
                # ICR = EBITDA近似 / 利息（EBIT+折旧摊销）/ interest
                ebitda = round(pb_acct + interest + dep_y + amort_y, 2)
                debt[y]["icr"] = round(ebitda / interest, 2) if interest > 0 else None

                # 【P0-1修复】补充偿债资金来源分项(附表8展示用)
                # 来源 = 会计净利润 + 折旧 + 摊销(按制造业/经营类规则)
                debt[y]["repay_source_profit"] = round(np_acct, 2)
                debt[y]["repay_source_dep"] = round(dep_y, 2)
                debt[y]["repay_source_amort"] = round(amort_y, 2)
        annual["income_statement"] = income_rows
        annual["total_cost"] = cost_rows
        annual["profit_distribution"] = profit_rows
        annual["wage"] = wage_rows            # 附表6-1
        annual["depreciation_table"] = dep_rows   # 附表6-2
        annual["amortization_table"] = amort_rows  # 附表6-3
        # Confirmed repayment sources distinguish available capacity from the
        # amount actually consumed.  DSCR uses available capacity; actual use is
        # allocated pro-rata and must close to current debt service.
        source_facts = raw.get("debt_repay_sources") or []
        if isinstance(source_facts, list) and source_facts:
            def _source_kind(name: Any) -> str:
                text = str(name or "").lower()
                if "利润" in text or "profit" in text:
                    return "profit"
                if "折旧" in text or "depreciation" in text:
                    return "depreciation"
                if "摊销" in text or "amort" in text:
                    return "amortization"
                return ""

            for y, debt_row in enumerate(debt):
                bases = {
                    "profit": max(float((profit_rows[y] if y < len(profit_rows) else {}).get("distributable") or 0.0), 0.0),
                    "depreciation": max(float((dep_rows[y] if y < len(dep_rows) else {}).get("depreciation") or 0.0), 0.0),
                    "amortization": max(float((amort_rows[y] if y < len(amort_rows) else {}).get("amortization") or 0.0), 0.0),
                }
                available_parts = {key: 0.0 for key in bases}
                for fact in source_facts:
                    if not isinstance(fact, dict):
                        continue
                    kind = _source_kind(fact.get("name") or fact.get("source"))
                    if not kind:
                        continue
                    schedule = fact.get("annual_schedule_wan") or fact.get("schedule_wan")
                    if isinstance(schedule, list) and y < len(schedule):
                        value = float(schedule[y] or 0.0)
                    elif fact.get("annual_wan") is not None:
                        value = float(fact.get("annual_wan") or 0.0)
                    else:
                        share = float(fact.get("share") or 0.0)
                        share = share / 100.0 if abs(share) > 1.0 else share
                        value = bases[kind] * share
                    available_parts[kind] += max(value, 0.0)
                available = round(sum(available_parts.values()), 2)
                due = round(float(debt_row.get("principal") or 0.0) + float(debt_row.get("interest") or 0.0), 2)
                actual = round(min(available, due), 2)
                actual_parts = {key: 0.0 for key in available_parts}
                if available > 0 and actual > 0:
                    keys = list(actual_parts)
                    for key in keys[:-1]:
                        actual_parts[key] = round(actual * available_parts[key] / available, 2)
                    actual_parts[keys[-1]] = round(actual - sum(actual_parts[key] for key in keys[:-1]), 2)
                debt_row.update({
                    "repay_available_profit": round(available_parts["profit"], 2),
                    "repay_available_depreciation": round(available_parts["depreciation"], 2),
                    "repay_available_amortization": round(available_parts["amortization"], 2),
                    "repay_available": available,
                    "repay_actual_profit": actual_parts["profit"],
                    "repay_actual_depreciation": actual_parts["depreciation"],
                    "repay_actual_amortization": actual_parts["amortization"],
                    "repay_actual": actual,
                    "repay_surplus": round(available - actual, 2),
                    "repay_actual_covers_debt_service": abs(actual - due) <= 0.05,
                    "dscr": round(available / due, 2) if due > 0 else None,
                })
        if wage_estimated:
            r.setdefault("assumptions", []).append(
                f"工资及附加缺输入，按现金经营成本 15% 估为 {_fmt(wage)} 万元/年（附表6-1，可研简化）")
        if amort > 0:
            r.setdefault("assumptions", []).append(
                f"无形及其他资产摊销 {_fmt(amort)} 万元/年（附表6-3，自非现金摊提额中拆分，不改变总成本口径）")

    # 【P0-4】附表9 项目投资现金流：逐行组成(可人工复核),net 仍取 op.cashflows 保证项目 IRR 三处一致。
    #   运营期净现金流 = 营业收入 − 现金经营成本 − 税金及附加 − 调整所得税(融资前口径,与P0-03会计所得税分开)
    #                    − 流动资金增加(投产首年) + 流动资金回收+资产余值(末年);组成合计 == cfs[t](rule#13验证)。
    #   融资前口径:不含借款提款/还本/融资利息(利息影响仅体现在附表7会计利润与附表10资本金现金流)。
    cfs = list(op.get("cashflows") or [])
    inc = annual.get("income_statement") or []
    _wc = round(inv["working_capital"] or 0.0, 2)
    # 【P1-03】期末资产回收统一读 compute_financials 存的 terminal_recovery(残值+未折完账面净值),
    #   与现金流末年口径一致(rule#13 组成合计==净现金流)。缺失时(理论不走到)退回原 ×残值率。
    _salv = raw.get("terminal_recovery")
    if _salv is None:
        _salv_rate = raw.get("salvage_rate")
        _salv_rate = 0.05 if _salv_rate is None else _salv_rate
        _salv = round((inv.get("fixed_asset") or 0.0) * _salv_rate, 2)
    proj_rows, cum = [], 0.0
    for t, cf in enumerate(cfs):
        cum = round(cum + cf, 2)
        row: dict[str, Any] = {"year": t, "net_cashflow": round(cf, 2), "cumulative": cum}
        if t < build:
            row.update({"phase": "建设期", "revenue": 0.0, "op_cash_cost": 0.0,
                        "tax_surtax": 0.0, "income_tax": 0.0, "construction": round(-cf, 2),
                        "wc_change": 0.0, "recover": 0.0})
        else:
            j = t - build
            ir = inc[j] if j < len(inc) else {}
            wc_add = _wc if j == 0 else 0.0
            _wc_end = raw.get("terminal_wc_recovery")
            if _wc_end is None:
                _wc_end = _wc
            else:
                _wc_end = round(float(_wc_end or 0.0), 2)
            rec = round(float(_wc_end) + float(_salv or 0.0), 2) if j == op_years - 1 else 0.0
            # 附表9 融资前组成须与 op.cashflows 同源：达产简化用 indicators 平均摊提口径；
            # 收入表 ir 为会计/年表（寿命内真实折旧），可能与平均口径差 1–数万元。
            # 组成列优先 indicators，保证 rule#13 与项目 IRR 三处一致。
            _rev = float(
                ir.get("revenue")
                if ir.get("revenue") is not None
                else ((ind or {}).get("revenue") or 0.0)
            )
            _occ = float(ir.get("operating_cost") or 0.0)
            if bool(raw.get("property_inventory")):
                # 开发成本结转已在建设期支付，附表9项目现金流只列当期现金期间费用。
                _occ = float(raw.get("period_opex") or 0.0)
            if not _occ and ind:
                _occ = float((ind or {}).get("op_cost") or 0.0) - float((ind or {}).get("depreciation") or 0.0)
            # 【P0 附加税同源】优先用附表5 当年 tax_surtax，再退 indicators（已与增值税附加同源）
            _tax_s = float(
                ir.get("tax_surtax")
                if ir.get("tax_surtax") is not None
                else ((ind or {}).get("tax_surcharge") or 0.0)
            )
            # 调整所得税：优先附表5 融资前所得税；否则反推使组成恒等
            _net_op = float(cf)
            if j == 0:
                _net_op = round(_net_op + _wc, 2)  # 还原投产年流资投入
            if j == op_years - 1:
                _net_op = round(_net_op - _wc - float(_salv or 0.0), 2)
            if ir.get("income_tax") is not None:
                _adj_tax = round(float(ir.get("income_tax") or 0.0), 2)
                # 若用表内所得税，组成可能与 cf 有 1 元级差——以 net 为准微调附加税列不改；
                # 组成校验用反推所得税保持 rule#13（净现金流契约优先）
                _compose_tax = round(_rev - _occ - _tax_s - _net_op, 2)
                # 展示用融资前所得税；rule#13 用使组成恒等的值
                _adj_tax_display = _adj_tax
                _adj_tax = _compose_tax
            else:
                _adj_tax = round(_rev - _occ - _tax_s - _net_op, 2)
                _adj_tax_display = _adj_tax
            row.update({"phase": "运营期",
                        "revenue": round(_rev, 2),
                        "op_cash_cost": round(_occ, 2),
                        "tax_surtax": round(_tax_s, 2),
                        "income_tax": _adj_tax,  # 与净现金流恒等的调整所得税（rule#13）
                        "income_tax_financing_before": _adj_tax_display,  # 附表5 同源展示
                        "construction": 0.0, "wc_change": round(wc_add, 2), "recover": rec})
        proj_rows.append(row)
    annual["project_cashflow"] = proj_rows

    # 附表10按投资实际发生期展开：建设期资本金=建设支出-贷款提款；
    # 投产年资本金=当年流动资金增加。每年均可由组成项直接复算净现金流。
    _equity_inject_plan: list[float] = []
    _eq_raw = (raw.get("equity_inject_by_year") if isinstance(raw, dict) else None) or []
    if isinstance(_eq_raw, (list, tuple)) and any(float(x or 0) for x in _eq_raw):
        _equity_inject_plan = [round(float(x or 0.0), 2) for x in _eq_raw]
        if build > 0 and len(_equity_inject_plan) < build:
            _equity_inject_plan = _equity_inject_plan + [0.0] * (build - len(_equity_inject_plan))
    _loan_draw_plan: list[float] = []
    _loan_draws = (raw.get("loan_draw_by_year") if isinstance(raw, dict) else None) or []
    if not _loan_draws:
        _loan_draws = ((r.get("raw") or {}).get("loan_draw_by_year") or [])
    if isinstance(_loan_draws, (list, tuple)) and any(float(x or 0) for x in _loan_draws):
        _loan_draw_plan = [round(float(x or 0.0), 2) for x in _loan_draws]
        if build > 0 and len(_loan_draw_plan) < build:
            _loan_draw_plan = _loan_draw_plan + [0.0] * (build - len(_loan_draw_plan))
        elif build > 0 and len(_loan_draw_plan) > build:
            _loan_draw_plan = _loan_draw_plan[: build - 1] + [round(sum(_loan_draw_plan[build - 1 :]), 2)]
    loan_draw_per = round(loan / build, 2) if (loan > 0 and build > 0) else 0.0
    subsidy = float(fund.get("subsidy") or 0.0)
    if _equity_inject_plan:
        expected_build_equity = round(max(
            float(inv.get("construction") or 0.0)
            + float(inv.get("interest") or 0.0)
            - loan
            - subsidy,
            0.0,
        ), 2)
        planned_build_equity = round(sum(_equity_inject_plan), 2)
        excess = round(planned_build_equity - expected_build_equity, 2)
        # Some confirmed funding schedules report total project equity in the
        # construction-year row, including working capital that is not used
        # until operation starts. Rephase only the exactly explainable WC
        # amount; any other excess remains visible to consistency checks.
        if (
            excess > 0.01
            and abs(excess - float(inv.get("working_capital") or 0.0)) <= 0.01
        ):
            remaining = excess
            for index in range(len(_equity_inject_plan) - 1, -1, -1):
                reduction = min(_equity_inject_plan[index], remaining)
                _equity_inject_plan[index] = round(
                    _equity_inject_plan[index] - reduction,
                    2,
                )
                remaining = round(remaining - reduction, 2)
                if remaining <= 0:
                    break
            r.setdefault("assumptions", []).append(
                "资金计划建设期资本金含流动资金，已按实际使用时点重分期至投产年"
            )
    subsidy_draw_per = round(subsidy / build, 2) if (subsidy > 0 and build > 0) else 0.0
    capital_rows = []
    for t, cf in enumerate(cfs):
        if t < build:
            if _loan_draw_plan:
                draw = _loan_draw_plan[t] if t < len(_loan_draw_plan) else 0.0
            else:
                draw = loan_draw_per if t < build - 1 else round(loan - loan_draw_per * (build - 1), 2)
            subsidy_draw = (
                subsidy_draw_per
                if t < build - 1
                else round(subsidy - subsidy_draw_per * (build - 1), 2)
            )
            build_outlay = round(max(-float(cf), 0.0), 2)
            if _equity_inject_plan:
                capital_invest = float(_equity_inject_plan[t]) if t < len(_equity_inject_plan) else 0.0
            else:
                capital_invest = round(max(build_outlay - draw - subsidy_draw, 0.0), 2)
            adj = round(-capital_invest, 2)
            capital_rows.append({
                "year": t, "phase": "建设期",
                "capital_invest": capital_invest,
                "loan_draw": round(draw, 2),
                "subsidy_draw": round(subsidy_draw, 2),
                "revenue": 0.0,
                "recover_fixed": 0.0,
                "recover_wc": 0.0,
                "op_cash_cost": 0.0,
                "tax_surtax": 0.0,
                "income_tax": 0.0,
                # Compatibility only.  This is now the atomic cash-inflow total,
                # never the former opaque/net-style operating inflow.
                "op_inflow": 0.0, "principal": 0.0, "interest": 0.0,
                "cash_inflow": 0.0, "cash_outflow": round(-adj, 2),
                "net_cashflow": round(adj, 2),
            })
        else:
            y = t - build
            principal = round(debt[y]["principal"], 2) if y < len(debt) else 0.0
            interest = round(debt[y]["interest"], 2) if y < len(debt) else 0.0
            project_row = proj_rows[t] if t < len(proj_rows) else {}
            capital_invest = round(max(float(project_row.get("wc_change") or 0.0), 0.0), 2)
            revenue_y = round(float(project_row.get("revenue") or 0.0), 2)
            op_cash_cost_y = round(float(project_row.get("op_cash_cost") or 0.0), 2)
            tax_surtax_y = round(float(project_row.get("tax_surtax") or 0.0), 2)
            income_tax_y = round(float(project_row.get("income_tax") or 0.0), 2)
            is_terminal = y == op_years - 1
            recover_fixed = round(float(_salv or 0.0), 2) if is_terminal else 0.0
            terminal_wc = raw.get("terminal_wc_recovery")
            if terminal_wc is None:
                terminal_wc = _wc
            recover_wc = round(float(terminal_wc or 0.0), 2) if is_terminal else 0.0
            cash_inflow = round(revenue_y + recover_fixed + recover_wc, 2)
            cash_outflow = round(
                capital_invest + op_cash_cost_y + tax_surtax_y + income_tax_y
                + principal + interest,
                2,
            )
            adj = round(cash_inflow - cash_outflow, 2)
            capital_rows.append({
                "year": t, "phase": "运营期",
                "capital_invest": capital_invest, "loan_draw": 0.0,
                "subsidy_draw": 0.0,
                "revenue": revenue_y,
                "recover_fixed": recover_fixed,
                "recover_wc": recover_wc,
                "op_cash_cost": op_cash_cost_y,
                "tax_surtax": tax_surtax_y,
                "income_tax": income_tax_y,
                # Legacy alias only; formal consumers use cash_inflow/outflow
                # and the atomic components below.
                "op_inflow": round(cash_inflow - op_cash_cost_y - tax_surtax_y - income_tax_y, 2),
                "cash_inflow": cash_inflow,
                "cash_outflow": cash_outflow,
                "principal": principal,
                "interest": interest, "net_cashflow": round(adj, 2),
                "capital_invest_note": "流动资金增加对应的资本金投入" if capital_invest else "",
            })
    annual["capital_cashflow"] = capital_rows
    try:
        annual["capital_irr_pct"] = round(_irr([x["net_cashflow"] for x in capital_rows]) * 100, 2)
    except Exception:  # noqa: BLE001
        annual["capital_irr_pct"] = None

    # 建设期利息分年：优先用 P1 半期计息明细（raw.idc_rows），缺失时退简化均摊
    # Fix-P0-1：必须透传 begin_balance/rate/end_balance（勿用 begin/rate_pct/end 空键）
    idc_rows = (r.get("raw") or {}).get("idc_rows") or []
    if idc_rows:
        annual["interest_during_construction"] = [
            {
                "period": x.get("period"),
                "begin_balance": x.get("begin_balance", x.get("begin")),
                "draw": x.get("draw"),
                "rate": x.get("rate", x.get("rate_pct")),
                "interest": x.get("interest"),
                "end_balance": x.get("end_balance", x.get("end")),
                "calculation_basis": "half_year_average_balance",
            }
            for x in idc_rows
        ]
    else:
        # 降级路径也必须给出可复核滚动账户，不能只放一列利息。
        _loan_rate = float(
            raw.get("loan_rate")
            or (r.get("funding") or {}).get("loan_rate")
            or (r.get("finance_inputs") or {}).get("loan_rate")
            or 0.0
        )
        _draw_per = round(float(loan or 0.0) / max(build, 1), 2)
        _begin = 0.0
        _idc_total = float(inv.get("interest") or 0.0)
        _rows = []
        for y in range(build):
            _draw = _draw_per if y < build - 1 else round(float(loan or 0.0) - _draw_per * (build - 1), 2)
            _interest = round(_idc_total / max(build, 1), 2)
            if y == build - 1:
                _interest = round(_idc_total - sum(float(x["interest"]) for x in _rows), 2)
            _end = round(_begin + _draw, 2)
            _rows.append({
                "period": y + 1, "begin_balance": round(_begin, 2), "draw": _draw,
                "rate": _loan_rate, "interest": _interest, "end_balance": _end,
                "calculation_basis": "explicit_interest_schedule",
            })
            _begin = _end
        annual["interest_during_construction"] = _rows
    # 附表3 流动资金：优先周转天数法（P1-1）；否则汇总反解并标记 method=ratio_backsolve。
    wc_total = round(inv["working_capital"] or 0.0, 2)
    _rev_base = float((ind or {}).get("revenue") or 0.0)
    _cash_cost_base = 0.0
    if ind:
        _cash_cost_base = round(float(ind.get("op_cost") or 0.0) - float(ind.get("depreciation") or 0.0), 2)
    _wc_days = (raw or {}).get("wc_turnover_days")
    annual["working_capital"] = _fin_wc.build_working_capital(
        wc_total=wc_total,
        revenue=_rev_base,
        cash_cost=_cash_cost_base,
        wc_turnover_days=_wc_days,
        turnover=(raw or {}).get("wc_turnover") if isinstance((raw or {}).get("wc_turnover"), dict) else None,
    )
    # 投资估算流资总额与周转法数值显式并列；差异由 run 固化前
    # 的 consistency gate 阻断，禁止强制缩放或静默抹平。
    if annual["working_capital"].get("method") == "turnover_days":
        annual["working_capital"]["investment_total"] = wc_total
    else:
        # 反解路径：净额强制等于投资估算流资（勾稽不变）
        annual["working_capital"]["total"] = wc_total
        annual["working_capital"]["net_working_capital"] = wc_total

    # 【P0-5 / F13-P0-04】财务计划现金流量表(C03 控制表,不占 13 张交付编号)：
    #   逐年汇总 投资/融资/经营 三类活动现金流,输出期末现金、累计盈余资金与资金缺口,
    #   判断项目各期能否正常运营、资金链是否安全。仅经营性项目构造(非经营性走全生命周期资金平衡)。
    annual["financial_plan"] = _build_financial_plan(r, annual, debt)

    # 【P1-6】非经营性：全生命周期资金平衡表（控制表，不占 13 表编号）
    if not ind:
        fund = r.get("funding") or {}
        annual_opex = round(sum(
            float(value or 0.0) for value in ((raw or {}).get("cost_items") or {}).values()
        ), 2)
        annual["non_operating_balance"] = _fin_statements.non_operating_funding_balance(
            total_investment=float(inv.get("total") or 0.0),
            capital=float(fund.get("capital") or 0.0),
            loan=float(fund.get("loan") or 0.0),
            subsidy=float(fund.get("subsidy") or 0.0),
            annual_opex=annual_opex,
            annual_subsidy=float((raw or {}).get("annual_operating_subsidy_wan") or 0.0),
            calc_years=calc,
            build_years=build,
            debt_service=debt,
        )
        # 提升到 result 顶层供门禁（_build_annual 只返回 annual，由调用方合并）
    else:
        annual["non_operating_balance"] = None

    # 【P1-4】DSCR 已在下方循环附加；此处补 ICR（EBIT/利息）
    if ind and debt:
        # approximate EBIT ≈ 会计利润总额 + 利息 = (net+tax) + interest  or total_profit+interest
        pd = annual.get("profit_distribution") or []
        for i, drow in enumerate(debt):
            interest = float(drow.get("interest") or 0.0)
            if i < len(pd):
                # total_profit is after interest (accounting); EBIT ≈ total_profit + interest
                ebit = float(pd[i].get("total_profit") or 0.0) + interest
            else:
                ebit = None
            drow["icr"] = round(ebit / interest, 2) if (ebit is not None and interest > 0) else None

    return annual


def _build_financial_plan(r: dict[str, Any], annual: dict[str, Any],
                          debt: list[dict]) -> list[dict[str, Any]]:
    """P0-5：财务计划现金流量表(投资/融资/经营三活动 + 期末现金 + 累计盈余 + 资金缺口)。

    口径说明(与既有附表自洽、不重复计数)：
    - 建设期：资金筹措假设按需到位,融资流入 = 投资流出(建设投资+建设期利息按分年),净现金流=0,
      不在此替 P0-1 投资口径歧义报假缺口(真正资金链压力在运营期还本付息 vs 经营现金流)。
    - 运营期经营活动净现金 = 会计净利润 + 折旧 + 摊销 + 利息(还原不含息经营现金流,与附表8 CFADS 同口径)。
    - 运营期投资活动 = −流动资金增加(投产首年) + 流动资金回收 + 资产余值回收(末年)。
    - 运营期融资活动 = −(当年还本 + 当年付息)。
    - 累计盈余资金逐年滚动;任一年期末现金<0 标记资金缺口年。
    """
    ind = r.get("indicators") or {}
    if not ind:  # 非经营性项目：不构造(按全生命周期资金平衡分析,另行处理)
        return []
    params = r.get("params") or {}
    inv = r.get("investment") or {}
    build = int(params.get("build_years") or 1)
    calc = int(params.get("calc_years") or 1)
    op_years = max(calc - build, 1)

    pd_rows = annual.get("profit_distribution") or []   # 附表7(会计口径,已扣息)
    tc_rows = annual.get("total_cost") or []            # 附表6(含利息)
    wc_total = round((inv.get("working_capital") or 0.0), 2)
    # 【P1-03】资产余值回收:与 compute_financials/附表9 口径一致(残值+未折完账面净值),读 terminal_recovery。
    salvage = (r.get("raw") or {}).get("terminal_recovery")
    if salvage is None:
        salvage_rate = (r.get("raw") or {}).get("salvage_rate")
        salvage_rate = 0.05 if salvage_rate is None else salvage_rate
        salvage = round((inv.get("fixed_asset") or 0.0) * salvage_rate, 2)

    rows: list[dict[str, Any]] = []
    cum = 0.0
    input_revision = r.get("input_revision") if isinstance(r.get("input_revision"), dict) else {}
    raw = r.get("raw") if isinstance(r.get("raw"), dict) else {}
    funding_schedule = (
        input_revision.get("funding_annual_schedule")
        or raw.get("funding_annual_schedule")
        or []
    )
    funding_by_year: dict[int, dict[str, Any]] = {}
    if isinstance(funding_schedule, list):
        for item in funding_schedule:
            if not isinstance(item, dict):
                continue
            try:
                year = int(item.get("year") or item.get("period"))
            except (TypeError, ValueError):
                continue
            if year not in funding_by_year:
                funding_by_year[year] = item
    atomic_funding = (
        set(funding_by_year) == set(range(1, build + 1))
        and all(
            row.get("construction_investment_wan") not in (None, "")
            and row.get("construction_interest_wan") not in (None, "")
            and row.get("working_capital_wan") not in (None, "")
            and row.get("capital_own_wan") not in (None, "")
            and row.get("loan_wan") not in (None, "")
            and row.get("gov_subsidy_wan") not in (None, "")
            for row in funding_by_year.values()
        )
    )
    # 建设期：全部筹资包含流动资金。非流动投资逐年支出，流动资金先形成现金储备，
    # 投产首年再转为营运资本；不得让已筹资金在财务计划中“消失”。房地产开发成本
    # 属存货而非固定资产，因此这里读取建设投资+建设期利息，不读取 fixed_asset。
    non_wc_investment = round(
        float(inv.get("construction") or 0.0) + float(inv.get("interest") or 0.0), 2
    )
    total_financing = round(non_wc_investment + wc_total, 2)
    for t in range(build):
        funding_row = funding_by_year.get(t + 1, {}) if atomic_funding else {}
        if atomic_funding:
            construction_use = round(float(funding_row.get("construction_investment_wan") or 0.0), 2)
            interest_use = round(float(funding_row.get("construction_interest_wan") or 0.0), 2)
            working_use = round(float(funding_row.get("working_capital_wan") or 0.0), 2)
            invest_out = round(construction_use + interest_use + working_use, 2)
            equity_in = round(float(funding_row.get("capital_own_wan") or 0.0), 2)
            loan_in = round(float(funding_row.get("loan_wan") or 0.0), 2)
            subsidy_in = round(float(funding_row.get("gov_subsidy_wan") or 0.0), 2)
            finance_in = round(equity_in + loan_in + subsidy_in, 2)
        else:
            construction_use = round(non_wc_investment / build, 2) if t < build - 1 else round(
                non_wc_investment - sum(row["construction_investment"] + row["construction_interest"] for row in rows), 2
            )
            interest_use = 0.0
            working_use = 0.0
            invest_out = construction_use
            finance_in = (
                round(total_financing / build, 2)
                if t < build - 1
                else round(total_financing - sum(row["finance_in"] for row in rows), 2)
            )
            equity_in = finance_in
            loan_in = 0.0
            subsidy_in = 0.0
        net = round(finance_in - invest_out, 2)
        cum = round(cum + net, 2)
        rows.append({
            "period": t + 1, "phase": "建设期",
            "finance_in": finance_in, "operating_net": 0.0,
            "invest_out": invest_out, "debt_service": 0.0,
            "net_cashflow": net, "cumulative": cum, "gap": False,
            "construction_investment": construction_use,
            "construction_interest": interest_use,
            "working_capital": working_use,
            "capital_own": equity_in,
            "loan_draw": loan_in,
            "gov_subsidy": subsidy_in,
            "funding_balance_ok": abs(invest_out - finance_in) <= 0.05,
            "funding_plan_source": "confirmed_annual_schedule" if atomic_funding else "estimate_fallback",
        })
    # 运营期
    for y in range(op_years):
        pdr = pd_rows[y] if y < len(pd_rows) else {}
        tcr = tc_rows[y] if y < len(tc_rows) else {}
        interest = round(tcr.get("interest") or 0.0, 2)
        net_profit = round(pdr.get("net_profit") or 0.0, 2)   # 会计净利润(已扣息)
        dep = round(tcr.get("depreciation") or 0.0, 2)
        amort = round(tcr.get("amortization") or 0.0, 2)
        # 经营活动净现金 = 会计净利润 + 非现金摊提 + 利息(还原不含息经营现金流)
        cogs_series = (r.get("raw") or {}).get("cogs_series") or []
        cogs_addback = float(cogs_series[y] or 0.0) if y < len(cogs_series) else 0.0
        op_net = round(net_profit + dep + amort + interest + cogs_addback, 2)
        # 投资活动：投产首年投入流动资金,末年回收流动资金+资产余值
        # With a v1 atomic funding plan, working capital is already an explicit
        # use in the construction/funding year and must not be paid a second time.
        invest_out = 0.0 if atomic_funding else (wc_total if y == 0 else 0.0)
        recover = round(wc_total + salvage, 2) if y == op_years - 1 else 0.0
        invest_net = round(recover - invest_out, 2)
        # 融资活动：还本 + 付息
        principal = round(debt[y]["principal"], 2) if y < len(debt) else 0.0
        ds = round(principal + interest, 2)
        net = round(op_net + invest_net - ds, 2)
        cum = round(cum + net, 2)
        rows.append({
            "period": build + y + 1, "phase": "运营期",
            "finance_in": 0.0, "operating_net": op_net,
            "invest_out": round(invest_out - recover, 2),  # 净投资流出(负=净回收)
            "debt_service": ds, "net_cashflow": net,
            "cumulative": cum, "gap": cum < 0,
        })
    return rows


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


def _render_annual_tables(r: dict[str, Any]) -> dict[str, str]:
    """把逐年结构化附表渲染为 Markdown，key 对齐 lvke_templates.catalog 的 template_id。"""
    a = r.get("annual") or {}
    t: dict[str, str] = {}

    ds = a.get("debt_service") or []
    if ds and any(x.get("interest") for x in ds):
        body = "\n".join(
            f"| {x['year']} | {_fmt(x['begin'])} | {_fmt(x['principal'])} | {_fmt(x['interest'])} | {_fmt(x['end'])} | {_fmt(x.get('dscr'))} | {_fmt(x.get('icr'))} |"
            for x in ds)
        t["debt-service"] = ("| 运营年 | 期初借款余额 | 当期还本 | 当期付息 | 期末借款余额 | 偿债备付率(DSCR) | 利息备付率(ICR) |\n"
                             "| --- | --- | --- | --- | --- | --- | --- |\n" + body)

    inc = a.get("income_statement") or []
    if inc:
        # PG5-a：附表5 增列增值税三列（销项/进项/应纳），价外税不进净利润列。
        body = "\n".join(
            f"| {x['year']} | {_fmt(x['revenue'])} | {_fmt(x['operating_cost'])} | {_fmt(x['depreciation'])} | "
            f"{_fmt(x['tax_surtax'])} | {_fmt(x.get('vat_output'))} | {_fmt(x.get('vat_input'))} | {_fmt(x.get('vat_payable'))} | "
            f"{_fmt(x['income_tax'])} | {_fmt(x['net_profit'])} |"
            for x in inc)
        t["income-statement"] = (
            "| 运营年 | 营业收入 | 经营成本 | 折旧 | 销售税金及附加 | 销项税 | 进项税 | 应纳增值税 | 调整所得税(融资前) | 融资前净利 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n" + body
            + "\n\n> 本表所得税/净利为**融资前**口径（不含利息税盾）；融资后会计利润见附表7。"
            "销售税金及附加与附表9 同源（默认应纳增值税×附加率）。")

    tc = a.get("total_cost") or []
    if tc:
        body = "\n".join(
            f"| {x['year']} | {_fmt(x['operating_cost'])} | {_fmt(x['depreciation'])} | {_fmt(x['amortization'])} | {_fmt(x['interest'])} | {_fmt(x['total_cost'])} |"
            for x in tc)
        t["total-cost"] = ("| 运营年 | 经营成本 | 折旧费 | 摊销费 | 利息支出 | 总成本费用 |\n"
                           "| --- | --- | --- | --- | --- | --- |\n" + body)

    # 附表6-1 工资及附加估算表
    wg = a.get("wage") or []
    if wg:
        body = "\n".join(
            f"| {x['year']} | {_fmt(x['wage'])} | {_fmt(x['welfare'])} | {_fmt(x['total'])} |"
            for x in wg)
        t["wage"] = ("| 运营年 | 工资 | 职工福利及附加 | 工资及附加合计 |\n"
                     "| --- | --- | --- | --- |\n" + body)

    # 附表6-2 固定资产折旧费估算表
    dp = a.get("depreciation_table") or []
    if dp:
        body = "\n".join(
            f"| {x['year']} | {_fmt(x['original_value'])} | {_fmt(x['salvage_rate'])} | {x['dep_years']} | {_fmt(x['depreciation'])} |"
            for x in dp)
        t["depreciation"] = ("| 运营年 | 固定资产原值 | 残值率 | 折旧年限 | 当期折旧费 |\n"
                             "| --- | --- | --- | --- | --- |\n" + body)

    # 附表6-3 无形及其他资产摊销估算表
    am = a.get("amortization_table") or []
    if am:
        body = "\n".join(
            f"| {x['year']} | {_fmt(x['base'])} | {x['amort_years']} | {_fmt(x['amortization'])} |"
            for x in am)
        t["amortization"] = ("| 运营年 | 摊销基数 | 摊销年限 | 当期摊销费 |\n"
                             "| --- | --- | --- | --- |\n" + body)

    pd = a.get("profit_distribution") or []
    if pd:
        body = "\n".join(
            f"| {x['year']} | {_fmt(x['revenue'])} | {_fmt(x['total_cost'])} | {_fmt(x['total_profit'])} | {_fmt(x['income_tax'])} | {_fmt(x['net_profit'])} | {_fmt(x['undistributed'])} |"
            for x in pd)
        t["profit-distribution"] = ("| 运营年 | 营业收入 | 总成本费用 | 利润总额 | 所得税 | 净利润 | 未分配利润 |\n"
                                    "| --- | --- | --- | --- | --- | --- | --- |\n" + body)

    proj = a.get("project_cashflow") or []
    if proj:
        if any(x.get("phase") for x in proj):  # P0-4：逐行组成(营收/成本/税/调整所得税/建设投资/流资/回收)
            body = "\n".join(
                f"| {x['year']} | {x.get('phase','')} | {_fmt(x.get('revenue'))} | {_fmt(x.get('op_cash_cost'))} | "
                f"{_fmt(x.get('tax_surtax'))} | {_fmt(x.get('income_tax'))} | {_fmt(x.get('construction'))} | "
                f"{_fmt(x.get('wc_change'))} | {_fmt(x.get('recover'))} | {_fmt(x['net_cashflow'])} | {_fmt(x['cumulative'])} |"
                for x in proj)
            t["cashflow"] = (
                "| 计算期(年) | 阶段 | 营业收入 | 经营成本 | 税金及附加 | 调整所得税 | 建设投资 | 流动资金增加 | 回收(流资+余值) | 净现金流 | 累计净现金流 |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n" + body
                + "\n\n> 调整所得税为融资前口径(不含利息税盾);现金流不含借款/还本/融资利息,评价项目融资前盈利能力。")
        else:
            body = "\n".join(f"| {x['year']} | {_fmt(x['net_cashflow'])} | {_fmt(x['cumulative'])} |" for x in proj)
            t["cashflow"] = ("| 计算期(年) | 净现金流 | 累计净现金流 |\n| --- | --- | --- |\n" + body)

    cap = a.get("capital_cashflow") or []
    if cap:
        cap_irr = a.get("capital_irr_pct")
        note = f"\n\n> 资本金财务内部收益率(IRR)：{_fmt(cap_irr)}%" if cap_irr is not None else ""
        if any(x.get("phase") for x in cap):  # P0-4：逐行组成(资本金投入/经营现金流入/还本/付息)
            body = "\n".join(
                f"| {x['year']} | {x.get('phase','')} | {_fmt(x.get('capital_invest'))} | {_fmt(x.get('op_inflow'))} | "
                f"{_fmt(x.get('principal'))} | {_fmt(x.get('interest'))} | {_fmt(x['net_cashflow'])} |"
                for x in cap)
            t["capital-cashflow"] = (
                "| 计算期(年) | 阶段 | 资本金投入 | 经营现金流入 | 还本 | 付息 | 资本金净现金流 |\n"
                "| --- | --- | --- | --- | --- | --- | --- |\n" + body + note)
        else:
            body = "\n".join(f"| {x['year']} | {_fmt(x['net_cashflow'])} |" for x in cap)
            t["capital-cashflow"] = ("| 计算期(年) | 资本金净现金流 |\n| --- | --- |\n" + body + note)

    idc = a.get("interest_during_construction") or []
    if idc and any(x.get("interest") for x in idc):
        # P1：若含分年提款/期初余额/利率（半期计息明细），输出完整列；否则退简表。
        if any(("draw" in x or "rate" in x or "begin_balance" in x) for x in idc):
            body = "\n".join(
                f"| 建设期第{x['period']}年 | {_fmt(x.get('begin_balance', x.get('begin')))} | {_fmt(x.get('draw'))} | "
                f"{_fmt_rate_display(x.get('rate') if x.get('rate') is not None else x.get('rate_pct'))} | "
                f"{_fmt(x['interest'])} | "
                f"{_fmt(x.get('end_balance', x.get('end')))} |"
                for x in idc)
            t["interest-during-construction"] = (
                "| 期间 | 期初借款余额 | 当期提款 | 年利率(%) | 当期利息 | 期末借款余额 |\n"
                "| --- | --- | --- | --- | --- | --- |\n" + body
                + "\n\n> 当年提款按半年计息（提款额×利率/2），既有余额按全年计息。")
        else:
            body = "\n".join(f"| 建设期第{x['period']}年 | {_fmt(x['interest'])} |" for x in idc)
            t["interest-during-construction"] = ("| 期间 | 当期建设期利息 |\n| --- | --- |\n" + body)

    sens = r.get("sensitivity") or {}
    if sens.get("revenue"):
        deltas = sens.get("deltas") or []
        header = "| 因子 | " + " | ".join(f"{int(d*100):+d}%" if d else "基准" for d in deltas) + " |"
        sep = "| --- " * (len(deltas) + 1) + "|"
        rows = []
        for key, label in (("revenue", "营业收入"), ("op_cost", "经营成本"), ("construction", "建设投资")):
            series = sens.get(key) or []
            cells = " | ".join(_fmt(p.get("irr_pct")) for p in series)
            rows.append(f"| {label} | {cells} |")
        t["sensitivity"] = f"{header}\n{sep}\n" + "\n".join(rows) + "\n\n> 表内为项目财务内部收益率(IRR, %)对各因子变动的敏感性。"

    # 【P0-5】财务计划现金流量表（C03 控制表）：投资/融资/经营三活动 + 期末现金 + 累计盈余 + 缺口
    fp = a.get("financial_plan") or []
    if fp:
        body = "\n".join(
            f"| {x['period']} | {x['phase']} | {_fmt(x['finance_in'])} | {_fmt(x['operating_net'])} | "
            f"{_fmt(x['invest_out'])} | {_fmt(x['debt_service'])} | {_fmt(x['net_cashflow'])} | "
            f"{_fmt(x['cumulative'])} | {'⚠️缺口' if x['gap'] else '—'} |"
            for x in fp)
        min_cum = min((x["cumulative"] for x in fp), default=0.0)
        gap_years = [x["period"] for x in fp if x["gap"]]
        note = (f"\n\n> 最低累计盈余资金 {_fmt(min_cum)} 万元；"
                + (f"**资金缺口年份：第 {'、'.join(map(str, gap_years))} 期(累计现金为负,需接续融资)**"
                   if gap_years else "各期累计现金均为正,资金链可持续。"))
        t["financial-plan"] = (
            "| 计算期(年) | 阶段 | 融资流入 | 经营活动净现金 | 投资活动净流出 | 还本付息 | 当期净现金流 | 累计盈余资金 | 资金缺口 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n" + body + note)

    # 附表3 流动资金估算表（分项：应收/存货/现金 − 应付 = 新增流动资金）
    wc = a.get("working_capital") or {}
    # Zero working capital is still a valid, reviewable appendix (not a missing
    # appendix).  Render the zero rows so every available run keeps the complete
    # 13-table delivery contract, including property projects without working cash.
    if wc:
        rows = [
            ("应收账款", wc.get("receivable")),
            ("存货", wc.get("inventory")),
            ("现金", wc.get("cash")),
            ("流动资产小计", wc.get("current_assets")),
            ("减：应付账款", wc.get("payable")),
        ]
        body = "\n".join(f"| {name} | {_fmt(v)} |" for name, v in rows)
        t["working-capital"] = ("| 流动资金构成项 | 金额（万元） |\n| --- | --- |\n"
                                + body + f"\n| **新增流动资金合计** | **{_fmt(wc.get('total'))}** |")

    return t


def check_consistency(r: dict[str, Any]) -> list[dict[str, Any]]:
    """M1 T1.2：财务数值勾稽校验，返回 [{rule, ok, detail}]（供 consistency_check 门禁）。"""
    if not r.get("available"):
        return []
    inv, fund = r["investment"], r["funding"]
    ind = r.get("indicators") or {}
    annual = r.get("annual") or {}
    checks: list[dict[str, Any]] = []

    # 1. 资金筹措合计 == 总投资
    fund_sum = round(fund["capital"] + fund["loan"] + fund["subsidy"], 2)
    checks.append({"rule": "资金筹措合计=总投资", "ok": abs(fund_sum - inv["total"]) < 1.0,
                   "detail": f"筹措合计 {_fmt(fund_sum)} vs 总投资 {_fmt(inv['total'])} 万元"})

    # 2. 项目现金流表 IRR == 主要技经指标 IRR（三处一致的核心）
    if ind.get("project_irr_pct") is not None:
        cfs = [x["net_cashflow"] for x in (annual.get("project_cashflow") or [])]
        try:
            irr_tbl = round(_irr(cfs) * 100, 2)
            checks.append({"rule": "现金流表IRR=技经指标IRR", "ok": abs(irr_tbl - ind["project_irr_pct"]) < 0.5,
                           "detail": f"现金流表 {irr_tbl}% vs 指标表 {ind['project_irr_pct']}%"})
        except Exception:  # noqa: BLE001
            pass

    # 3. 各年 总成本 == 经营成本 + 折旧 + 摊销 + 利息
    tc = annual.get("total_cost") or []
    if tc:
        ok3 = all(abs(x["total_cost"] - (x["operating_cost"] + x["depreciation"] + x["amortization"] + x["interest"])) < 1.0
                  for x in tc)
        checks.append({"rule": "总成本=经营成本+折旧+摊销+利息", "ok": ok3, "detail": f"逐年校验 {len(tc)} 年"})

    # 4. 建设期利息汇总 == 投资估算中的建设期利息
    idc = annual.get("interest_during_construction") or []
    if idc:
        idc_sum = round(sum(x["interest"] for x in idc), 2)
        checks.append({"rule": "建设期利息汇总=投资估算利息", "ok": abs(idc_sum - inv["interest"]) < 1.0,
                       "detail": f"汇总 {_fmt(idc_sum)} vs 估算 {_fmt(inv['interest'])} 万元"})

    # 5. 流动资金分项净额（流动资产−流动负债）== 投资估算流动资金（附表3 勾稽）
    wc = annual.get("working_capital") or {}
    if wc.get("current_assets") is not None:
        net = round((wc.get("current_assets") or 0.0) - (wc.get("current_liabilities") or 0.0), 2)
        stated = round(float(
            wc.get("investment_total")
            if wc.get("investment_total") is not None
            else wc.get("stated_total")
            if wc.get("stated_total") is not None
            else wc.get("total") or 0.0
        ), 2)
        delta = round(net - stated, 2)
        working_capital_ok = abs(delta) <= 0.01
        check = {
            "rule": "流动资金分项净额=投资估算流动资金",
            "ok": working_capital_ok,
            "detail": f"分项净额 {_fmt(net)} vs 估算 {_fmt(stated)} 万元，差额 {_fmt(delta)} 万元",
        }
        if not working_capital_ok:
            check.update(
                {
                    "code": "working_capital_inconsistent",
                    "blocking": True,
                }
            )
        checks.append(check)

    # 6. 折旧(附表6-2)+摊销(附表6-3) 逐年合计 == 总成本表非现金摊提额（折旧+摊销），口径不重不漏
    dep_tbl = annual.get("depreciation_table") or []
    amort_tbl = annual.get("amortization_table") or []
    if dep_tbl and tc:
        ok6 = all(
            abs((dep_tbl[i]["depreciation"] + (amort_tbl[i]["amortization"] if i < len(amort_tbl) else 0.0))
                - (tc[i]["depreciation"] + tc[i]["amortization"])) < 1.0
            for i in range(min(len(dep_tbl), len(tc))))
        checks.append({"rule": "折旧表+摊销表=总成本表折旧摊销", "ok": ok6,
                       "detail": f"逐年校验 {min(len(dep_tbl), len(tc))} 年（附表6-2/6-3↔附表6）"})

    # 7.【H1】资本金现金流建设期股东净投入 == 非流动投资资金缺口。
    #    非流动投资 = 总投资 − 流动资金；贷款和政府补助优先覆盖建设期投入，股东投入补足差额。
    #    该口径同时适用于固定资产项目和房地产开发产品（存货）项目，不能再用 fixed_asset
    #    判断房地产资本金投入，否则 fixed_asset=0 会产生负的错误期望值。
    cap_cf = annual.get("capital_cashflow") or []
    build_years = int((r.get("params") or {}).get("build_years") or 1)
    if cap_cf and fund.get("loan") is not None:
        build_out = round(-sum(x["net_cashflow"] for x in cap_cf[:build_years] if x["net_cashflow"] < 0), 2)
        non_current_investment = round(
            float(inv.get("construction") or 0.0) + float(inv.get("interest") or 0.0), 2
        )
        expect = round(max(
            non_current_investment
            - float(fund.get("loan") or 0.0)
            - float(fund.get("subsidy") or 0.0),
            0.0,
        ), 2)
        tol = max(inv["total"] * 0.01, 50.0)
        checks.append({"rule": "资本金现金流建设期投入=非流动投资−贷款及补助",
                       "ok": abs(build_out - expect) < tol,
                       "detail": (
                           f"建设期股东净投入 {_fmt(build_out)} vs "
                           f"建设投资+建设期利息−贷款−补助 {_fmt(expect)} 万元"
                       )})

    # 8.【H2/M1】折旧表逐年"原值×(1−残值率)/折旧年限" == 当期折旧费（表内公式自洽、扣残值、扣无形）
    if dep_tbl:
        ok8 = all(
            (
                abs(
                    round(sum(float(item.get("depreciation") or 0.0) for item in x.get("classes") or []), 2)
                    - float(x.get("depreciation") or 0.0)
                ) < 1.0
                and all(
                    abs(
                        round(
                            float(item.get("original_value_wan") or 0.0)
                            * (1 - float(item.get("salvage_rate") or 0.0))
                            / max(int(item.get("depreciation_years") or 1), 1),
                            2,
                        ) - float(item.get("depreciation") or 0.0)
                    ) < 1.0
                    for item in x.get("classes") or [] if item.get("depreciation")
                )
            )
            if x.get("depreciation_basis") == "classified"
            else abs(
                round(x["original_value"] * (1 - x["salvage_rate"]) / max(x["dep_years"], 1), 2)
                - x["depreciation"]
            ) < 1.0
            for x in dep_tbl if x.get("depreciation")
        )
        checks.append({"rule": "折旧表原值×(1−残值率)/年限=折旧额", "ok": ok8,
                       "detail": f"逐年复算 {len(dep_tbl)} 年（附表6-2 自洽）"})

    # 9.【P0-1 / §5.1】项目总投资 = 建设投资 + 建设期利息 + 流动资金。
    #    other/reserve 是建设投资的组成，不得再与 construction 相加（否则重复）。
    #    投资口径 ambiguous 时本条不作为算术硬勾稽（由「投资口径无歧义」阻断终稿）。
    comp = round((inv.get("construction") or 0.0)
                 + (inv.get("interest") or 0.0) + (inv.get("working_capital") or 0.0), 2)
    scope_st = ((inv.get("scope_status") or {}).get("status") or "")
    gap = abs(comp - inv["total"])

    # 【P0-3修复】有差额时应标记为False+warning,不再静默通过
    if gap < 1.0:
        ok9 = True
    elif scope_st == "ambiguous":
        ok9 = False  # 改为False,不再自动通过
    elif gap < inv["total"] * 0.01:  # <1%
        ok9 = False  # 有差额即为False
    else:
        ok9 = False

    checks.append({"rule": "投资构成分项合计=总投资",
                   "ok": ok9,
                   "severity": "warning" if (gap >= 1.0 and gap < inv["total"] * 0.01) else ("error" if gap >= inv["total"] * 0.01 else None),
                   "blocking": gap >= inv["total"] * 0.01,
                   "detail": f"建设投资+利息+流资 {_fmt(comp)} vs 总投资 {_fmt(inv['total'])} 万元"
                             f"（差额 {_fmt(gap)}; scope={scope_st or 'clear'}; other/reserve 不重复计入）"})


    # 11.【P0-03】附表7 利润表总成本 == 附表6 总成本(逐年、含运营期利息)。
    #    堵住"附表6 计息、附表7 不计息"的跨表断链——此前各表内部自洽、无跨表勾稽故漏检。
    pd_rows = annual.get("profit_distribution") or []
    if tc and pd_rows:
        n = min(len(tc), len(pd_rows))
        ok11 = all(abs(pd_rows[i]["total_cost"] - tc[i]["total_cost"]) < 1.0 for i in range(n))
        checks.append({"rule": "附表7利润表总成本=附表6总成本(含息)", "ok": ok11,
                       "detail": f"逐年校验 {n} 年(利润表已计运营期利息)"})

    # 12.【P0-5】财务计划现金流量表三活动自洽：经营净现金 − 投资净流出 − 还本付息 == 当期净现金流。
    #    (invest_out 已按"净流出"存储:正=净流出、负=净回收,故此处用减号)
    fp = annual.get("financial_plan") or []
    if fp:
        ok12 = all(
            abs((x["operating_net"] + x["finance_in"] - x["invest_out"] - x["debt_service"]) - x["net_cashflow"]) < 1.0
            for x in fp)
        checks.append({"rule": "财务计划现金流三活动合计=当期净现金流", "ok": ok12,
                       "detail": f"逐年校验 {len(fp)} 期(投资/融资/经营三活动自洽)"})

    # 13.【P0-4】附表9 逐行组成合计 == 净现金流:营收−现金经营成本−税金−调整所得税−建设投资−流资增加+回收。
    #    保证逐行可人工复核、且组成拆分不改变净现金流(项目 IRR 不变)。
    proj = annual.get("project_cashflow") or []
    if proj and any(p.get("phase") for p in proj):
        ok13 = all(
            abs(round((p.get("revenue") or 0.0) - (p.get("op_cash_cost") or 0.0) - (p.get("tax_surtax") or 0.0)
                      - (p.get("income_tax") or 0.0) - (p.get("construction") or 0.0) - (p.get("wc_change") or 0.0)
                      + (p.get("recover") or 0.0), 2) - p["net_cashflow"]) < 1.0
            for p in proj)
        checks.append({"rule": "附表9组成合计=净现金流", "ok": ok13,
                       "detail": f"逐年复核 {len(proj)} 期(营收−成本−税−调整所得税−建设投资−流资+回收)"})

    # 14.【P0 附加税同源】达产年 附表5.税金及附加 == 附表9.税金及附加（运营期）
    inc = annual.get("income_statement") or []
    if proj and inc:
        op_proj = [p for p in proj if p.get("phase") == "运营期"]
        n = min(len(op_proj), len(inc))
        if n > 0:
            ok14 = all(
                abs(float(op_proj[i].get("tax_surtax") or 0.0) - float(inc[i].get("tax_surtax") or 0.0)) < 1.0
                for i in range(n)
            )
            t5 = float(inc[0].get("tax_surtax") or 0.0)
            t9 = float(op_proj[0].get("tax_surtax") or 0.0)
            checks.append({
                "rule": "附表5税金及附加=附表9税金及附加",
                "ok": ok14,
                "detail": f"运营期逐年比对 {n} 年；首年 附表5={_fmt(t5)} vs 附表9={_fmt(t9)} 万元",
                "blocking": True,
            })
            # 与 indicators 达产附加税也应对齐（若有）
            # 注意：有爬坡时 附表5 第 1 年≠达产年；应用达产年（营收最大行）比对 indicators
            ind_tax = (ind or {}).get("tax_surcharge")
            if ind_tax is not None and n > 0:
                peak_idx = 0
                peak_rev = -1.0
                for i, row in enumerate(inc[:n]):
                    rv = float(row.get("revenue") or 0.0)
                    if rv >= peak_rev:
                        peak_rev = rv
                        peak_idx = i
                t5_peak = float(inc[peak_idx].get("tax_surtax") or 0.0)
                ok14b = abs(float(ind_tax) - t5_peak) < 1.0
                checks.append({
                    "rule": "indicators附加税=附表5税金及附加",
                    "ok": ok14b,
                    "detail": (
                        f"indicators={_fmt(ind_tax)} vs 附表5达产年(第{peak_idx+1}运营年,"
                        f"营收={_fmt(peak_rev)})={_fmt(t5_peak)} 万元"
                    ),
                    "blocking": True,
                })

    # 15.【Fix-P0-2】附表10 资本金投入合计 ≈ 筹措资本金（流资对应投入已记入运营年）
    cap_rows = annual.get("capital_cashflow") or []
    fund_cap = float((fund or {}).get("capital") or 0.0)
    scope_status = ((r.get("investment") or {}).get("scope_status") or {}).get("status")
    if cap_rows and fund_cap > 0 and scope_status != "ambiguous":
        sum_ci = round(sum(float(x.get("capital_invest") or 0.0) for x in cap_rows), 2)
        ok15 = abs(sum_ci - fund_cap) < max(fund_cap * 0.01, 1.0)
        checks.append({
            "rule": "附表10资本金投入合计=筹措资本金",
            "ok": ok15,
            "detail": f"sum(capital_invest)={_fmt(sum_ci)} vs funding.capital={_fmt(fund_cap)} 万元",
            "blocking": True,
        })

    # 16.【Fix-P0-1】有贷款时附表2 应含期初/利率/期末（非仅利息摘要）
    idc_ann = annual.get("interest_during_construction") or []
    loan_amt = float((fund or {}).get("loan") or 0.0)
    if idc_ann and loan_amt > 0:
        sample = idc_ann[0] if isinstance(idc_ann[0], dict) else {}
        has_bal = sample.get("begin_balance") is not None or sample.get("begin") is not None
        has_rate = sample.get("rate") is not None or sample.get("rate_pct") is not None
        has_end = sample.get("end_balance") is not None or sample.get("end") is not None
        ok16 = bool(has_bal and has_rate and has_end)
        checks.append({
            "rule": "附表2建设期利息含期初利率期末",
            "ok": ok16,
            "detail": f"begin={has_bal} rate={has_rate} end={has_end} rows={len(idc_ann)}",
            "blocking": True,
        })

    # 10.【P2】附表1 三段式明细：工程费用+工程建设其他费用+预备费 三段合计 == 建设投资(细项不重不漏)
    det = inv.get("breakdown_detail")
    if det:
        seg_sum = round(det["engineering_total"] + det["other_total"] + det["contingency_total"], 2)
        constr = inv.get("construction") or 0.0
        checks.append({"rule": "投资明细三段合计=建设投资",
                       "ok": abs(seg_sum - constr) < max(constr * 0.01, 1.0),
                       "detail": f"三段合计 {_fmt(seg_sum)}(工程{_fmt(det['engineering_total'])}"
                                 f"+其他{_fmt(det['other_total'])}+预备{_fmt(det['contingency_total'])}) "
                                 f"vs 建设投资 {_fmt(constr)} 万元"})
    # 附加模块级门禁（投资口径/时间轴/流资方法/资金缺口/ICR）
    extra = _fin_checks.run_checks(r, engine_check=None)
    # 去重 rule 名
    seen = {c.get("rule") for c in checks}
    for c in extra:
        if c.get("rule") not in seen:
            checks.append(c)
            seen.add(c.get("rule"))
    # 【P0-4修复】偿债能力告警:ICR/DSCR<1时明确列出
    debt_service = annual.get("debt_service") or []
    if debt_service:
        icr_issues = []
        dscr_issues = []

        for idx, ds in enumerate(debt_service):
            year = ds.get("year", idx + 1)
            icr = ds.get("icr")
            dscr = ds.get("dscr")

            if icr is not None and icr < 1.0:
                severity = "error" if icr < 0.8 else "warning"
                icr_issues.append(f"第{year}年ICR={icr:.2f}")
                checks.append({
                    "rule": "利息备付率ICR>=1",
                    "ok": False,
                    "severity": severity,
                    "blocking": icr < 0.8,
                    "detail": f"第{year}年ICR={icr:.2f}<1,偿债风险(当年EBITDA不足以覆盖利息)"
                })

            if dscr is not None and dscr < 1.0:
                dscr_issues.append(f"第{year}年DSCR={dscr:.2f}")
                checks.append({
                    "rule": "偿债备付率DSCR>=1",
                    "ok": False,
                    "severity": "warning",
                    "blocking": False,
                    "detail": f"第{year}年DSCR={dscr:.2f}<1,可用偿债资金不足以覆盖当期还本付息"
                })

        # 如果多年连续<1,汇总报告
        if len(icr_issues) > 3:
            checks.append({
                "rule": "利息备付率ICR>=1",
                "ok": False,
                "severity": "error",
                "blocking": True,
                "detail": f"ICR<1年数: {len(icr_issues)}年,偿债能力严重不足,建议调整融资结构"
            })

    return checks


def basis_of_estimate_md(r: dict[str, Any]) -> str:
    """M1 T1.3：Basis of Estimate（估算依据说明），随财务章附于报告，回应"数据可辩护"。

    列出：口径与规范依据、关键假设（取值+来源+方法+理由）、精度区间提示。
    """
    if not r.get("available"):
        return ""
    bench = r.get("benchmark_rate", BENCHMARK_RATE)
    params = r.get("params") or {}
    lines = [
        "**估算依据说明（Basis of Estimate）**",
        "",
        "- 规范依据：《建设项目经济评价方法与参数（第三版）》(发改投资〔2006〕1325号)；"
        "行业财务基准收益率参照发改投资〔2013〕586号（须按最新发布复核）。",
        f"- 财务基准收益率(Ic)：{bench*100:.1f}%；计算期 {params.get('calc_years')} 年"
        f"（含建设期 {params.get('build_years')} 年）。",
        "- 估算性质：可研阶段属**估算级**（AACE Class 4/5，公认精度约 ±20%~50%），"
        "以公开锚点数据 + 财务模型推算，非审计级精度。",
        "",
        "**关键假设登记：**",
        "",
        "| 假设项 | 取值/口径 | 来源/方法 |",
        "| --- | --- | --- |",
    ]
    for a in (r.get("assumptions") or []):
        # assumptions 为文本；结构化拆分（含"按…/估算/…%"）尽力解析，失败则整条入"说明"
        lines.append(f"| 测算假设 | — | {str(a)} |")
    return "\n".join(lines) + "\n"
