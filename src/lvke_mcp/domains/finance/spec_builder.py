# -*- coding: utf-8 -*-
"""BC-P4：B 层 spec_builder（LLM 定规范，绝不算数）。

职责：把项目描述 + requirement 交给 LLM，让它**只产出 FinanceSpec 规范**
（选收入模型 / 填参数 / 写假设理由），绝不生成 IRR/NPV/回收期等最终数字——
算术由确定性引擎 finance_model 完成（方案 §1.3 弃用 A 方案的核心红线）。

护栏（与现有 LLM 降级策略一致）：
- 复用现有网关（LLM_API_KEY / LLM_BASE_URL / _normalize_base_url + OpenAI SDK）。
- structured output（response_format=json_object）强制返回 JSON。
- 产物必过 spec.validate()，非法则回退默认 spec（flat 单点法=现状行为）。
- 任何异常（无网关 / 超时 / 解析失败 / 校验失败）全部静默回退，永不阻断财务。

主入口：``build_finance_spec(project_brief, requirement, *, model="") -> dict``
"""

from __future__ import annotations

import json
import os
from typing import Any

from lvke_mcp.domains.finance import spec as spec_mod

_SYS = (
    "你是可行性研究报告的资深财务建模专家。根据项目描述与业主输入，输出一份"
    "【财务计算规范 FinanceSpec】的 JSON。严格遵守：\n"
    "1. 你【只定义规范与参数】，绝不计算 IRR/NPV/回收期/净利润或任何最终财务数字"
    "——所有计算由确定性引擎完成，你算的数字一律无效。\n"
    "2. 按行业选收入模型 revenue.model：制造/加工/食品/工业=product_sales；"
    "房地产/商品房=property_sales（开发产品按存货、不折旧、销售成本随去化结转）；"
    "文旅/景区/酒店=tourism；污水/固废/市政特许经营/PPP 政府付费=gov_payment；"
    "其余或拿不准=flat。\n"
    "   **重要**：若项目只给出「达产年营收(万元)」而没有产品量价/面积/客流明细，"
    "必须用 revenue.model=flat 并填 annual_revenue_wan，禁止空 products 的 product_sales。\n"
    "   product_sales 时 products 至少 1 条，每条含 name/unit/price_per_unit/price_unit/capacity；"
    "price_unit 只能是 yuan 或 wan；**ramp 必须是 JSON 数组**（如 [1.0] 或 [0.6,0.8,1.0]），"
    "禁止写标量 1 或 \"1\"。\n"
    "   单价与达产营收须同数量级：达产营收(万元)≈price_per_unit(元)×capacity（unit=万件时）；"
    "例 28000/200→price_per_unit=140，不是 14。\n"
    "   vat_input_rate 为进项综合抵扣率（约 0.08~0.15），禁止填 0.7。\n"
    "   gov_payment 填 annual_gov_payment_wan（年政府付费）、payment_ramp（爬坡）、"
    "vat_refund_rate（增值税即征即退比例，污水常见 0.7）、fiscal_subsidy_wan（年财政补贴）。\n"
    "3. 单位约定（务必遵守，否则金额错 1 万倍）：product_sales 的 products[].unit "
    "填产量计量单位（如「万箱」「万吨」「台」），price_per_unit 填对应【单个单位】的单价"
    "（元）、price_unit=\"yuan\"；引擎按 单价(元) × 产量 × 单位系数 换算为万元"
    "（unit 以「万」开头则 ×10000）。\n"
    "4. 达产率 ramp / 去化率 absorption / 客流爬坡 visitor_ramp 为逐年系数（0~1.5），"
    "投产初期爬坡、达产后填 1.0；products[].var_cost_rate 填该产品可变成本占其收入的比例"
    "（0~1，如原材料+燃料动力占比），供引擎按「固定+可变」逐年建模。\n"
    "5. 税制 tax（务必按行业+政策识别，别只用默认值）：\n"
    "   - income_tax_rate：一般 0.25；高新技术企业 0.15；小微/西部大开发等按政策；\n"
    "   - tax_holiday_years / tax_half_years：识别「三免三减半」类税收优惠——"
    "环保/节能/公共基础设施/农业/软件集成电路等常享，免税期填免征年数、减半期填减半年数，"
    "无优惠填 0；\n"
    "   - vat_rate：制造 0.13、交通运输/建筑 0.09、现代服务 0.06、农产品 0.09；"
    "vat_input_rate 进项综合抵扣率（服务/文旅偏低）。\n"
    "   - surtax_rate：**是营收比例的附加税兜底**，通常 0.01 左右；"
    "**禁止**把城建税+教育附加 12% 填进 surtax_rate（那是增值税附加口径，引擎另有 VAT 路径）。\n"
    "   凡填了优惠，必须在 assumptions 写明依据（政策名/文号或行业惯例），拿不准则填 0 并说明「未识别到明确优惠，按无优惠保守处理」。\n"
    "6. 成本 cost（可选）：total_cost_rate 总成本费用率、wage_rate 工资占现金经营成本比、"
    "salvage_rate 残值率——按行业经验给，拿不准留空由引擎按行业默认取值。\n"
    "7. 只输出符合给定结构的 JSON，不要多余文字、不要 ``` 代码块、不要计算过程。"
)


def _normalize_base_url(raw: str) -> str:
    """OpenAI 兼容网关 base_url 规范化（与 hermes doc_agent_api 等价）。"""
    raw = (raw or "").strip().rstrip("/")
    if not raw:
        return raw
    if not raw.endswith("/v1"):
        raw = raw + "/v1"
    return raw


def _fast_model() -> str:
    """读取 MCP 自有的 LLM 快速模型配置（无 hermes 网关依赖）。"""
    return (
        os.environ.get("LVKE_LLM_FAST_MODEL", "")
        or os.environ.get("LLM_FAST_MODEL", "")
        or os.environ.get("LLM_MODEL", "")
    )


def _default_spec(requirement: dict[str, Any]) -> dict[str, Any]:
    """回退默认 spec = 现状单点法（flat），保证零改动等价旧行为。"""
    req = requirement if isinstance(requirement, dict) else {}
    fin = (req.get("finance") or {}) if isinstance(req.get("finance"), dict) else {}
    s = spec_mod.FinanceSpec(
        industry=str(req.get("industry") or ""),
        invest_type=str(req.get("invest_type") or ""),
        source_hint="fallback_default",
    )
    s.revenue.model = "flat"
    try:
        s.revenue.annual_revenue_wan = float(fin.get("annual_revenue_wan") or 0.0)
    except (TypeError, ValueError):
        s.revenue.annual_revenue_wan = 0.0
    if isinstance(fin.get("cost_items"), dict):
        s.cost.cost_items = dict(fin["cost_items"])
    # 人工确认 spec 优先于 flat 默认；保存于 requirement.finance，作为确定性 run 的固化规范。
    manual = fin.get("finance_spec")
    if isinstance(manual, dict):
        candidate = dict(manual)
        candidate.setdefault("version", spec_mod.SPEC_VERSION)
        candidate.setdefault("industry", str(req.get("industry") or ""))
        candidate.setdefault("invest_type", str(req.get("invest_type") or ""))
        candidate.setdefault("source_hint", "user_confirmed")
        ok, _errs = spec_mod.validate(candidate)
        if ok:
            return candidate
    return s.to_dict()


def _build_prompt(project_brief: str, requirement: dict[str, Any]) -> str:
    req = requirement if isinstance(requirement, dict) else {}
    fin = (req.get("finance") or {}) if isinstance(req.get("finance"), dict) else {}
    lines = [
        "【项目描述】",
        str(project_brief or "")[:2000],
        "",
        "【结构化要素】",
        f"- 行业：{req.get('industry') or '（未知）'}",
        f"- 投资性质：{req.get('invest_type') or '（未知）'}",
        f"- 用地面积（亩）：{req.get('land_area_mu') or '（未提供）'}",
        f"- 产能/规模描述：{req.get('output_scale') or '（未提供）'}",
        f"- 已知达产年营收（万元，可空）：{fin.get('annual_revenue_wan') or '（未提供）'}",
        "",
        "请按 FinanceSpec 结构输出 JSON（含 revenue.model + 对应参数 + tax 税制 + assumptions 假设理由）。",
        "字段名参照：revenue.model / revenue.products[{name,unit,price_per_unit,price_unit,"
        "capacity,ramp,var_cost_rate}] / revenue.saleable_area / revenue.price_per_sqm / "
        "revenue.absorption / revenue.annual_visitors / revenue.spend_per_visitor / "
        "revenue.visitor_ramp / revenue.annual_gov_payment_wan / revenue.payment_ramp / "
        "revenue.vat_refund_rate / revenue.fiscal_subsidy_wan / revenue.annual_revenue_wan / "
        "tax.{income_tax_rate,tax_holiday_years,tax_half_years,vat_rate,vat_input_rate,surtax_rate} / "
        "cost.{total_cost_rate,wage_rate,salvage_rate} / assumptions。",
    ]
    return "\n".join(lines)


def _call_llm_json(
    client: Any,
    *,
    use_model: str,
    system: str,
    user: str,
) -> tuple[Any, str]:
    """单次 chat completions → (parsed_obj_or_None, raw_content)。"""
    resp = client.chat.completions.create(
        model=use_model,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or "{}"
    try:
        return json.loads(content), content
    except Exception:  # noqa: BLE001
        return None, content


def _normalize_llm_raw(raw: dict[str, Any], requirement: dict[str, Any]) -> dict[str, Any]:
    raw = dict(raw)
    raw.setdefault("version", spec_mod.SPEC_VERSION)
    raw.setdefault("industry", str((requirement or {}).get("industry") or ""))
    raw.setdefault("invest_type", str((requirement or {}).get("invest_type") or ""))
    return spec_mod.coerce_llm_spec(raw, requirement if isinstance(requirement, dict) else {})


def _retry_prompt(base_prompt: str, errs: list[str], prev_preview: str) -> str:
    err_txt = "\n".join(f"- {e}" for e in (errs or [])[:12]) or "- (unknown)"
    return (
        f"{base_prompt}\n\n"
        "【上次输出校验失败，请只修下列错误后重发完整 JSON】\n"
        f"{err_txt}\n"
        "约束重申：仅有达产营收、无产品量价时必须 revenue.model=flat；"
        "ramp 必须是数组；price_per_unit 与达产营收同数量级；"
        "vat_input_rate 约 0.08~0.15；surtax_rate 勿填 0.12。\n"
        f"【上次草稿摘录】\n{prev_preview[:900]}\n"
    )


def build_finance_spec(project_brief: str, requirement: dict[str, Any], *,
                       model: str = "") -> dict[str, Any]:
    """调 LLM 产出 FinanceSpec dict；失败或非法则回退默认 spec（永不阻断）。

    返回的 dict 恒合法（要么 LLM 产物过校验，要么默认 spec）。source_hint 标注来源：
    ``llm_spec``（LLM 有效）/
    ``fallback_default``（无网关）/
    ``llm_invalid``（调了 LLM 但校验失败）/
    ``llm_error``（调了 LLM 但异常）。

    G2：校验失败时带 errors 再问 LLM **至多 1 次**，仍失败才 fallback。
    """
    fallback = _default_spec(requirement)
    api_key = os.environ.get("LLM_API_KEY")
    base_url = _normalize_base_url(os.environ.get("LLM_BASE_URL", ""))
    use_model = (model or "").strip() or _fast_model()
    if not (api_key and base_url and use_model):
        fallback["source_hint"] = "fallback_default"
        fallback.setdefault("assumptions", []).append(
            "无 LLM 网关配置，使用 fallback_default FinanceSpec"
        )
        return fallback  # 无网关 → 回退，与现有降级策略一致

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url, timeout=120.0, max_retries=2)
        prompt = _build_prompt(project_brief, requirement)
        req = requirement if isinstance(requirement, dict) else {}

        raw_obj, content = _call_llm_json(
            client, use_model=use_model, system=_SYS, user=prompt,
        )
        attempts = 1
        last_errs: list[str] = []
        last_preview = ""

        if not isinstance(raw_obj, dict):
            last_errs = ["response_not_dict"]
            last_preview = str(content)[:800]
        else:
            fixed = _normalize_llm_raw(raw_obj, req)
            fixed["source_hint"] = "llm_spec"
            ok, errs = spec_mod.validate(fixed)
            if ok:
                fixed["_llm_attempts"] = attempts
                return fixed
            last_errs = list(errs[:20])
            last_preview = json.dumps(fixed, ensure_ascii=False, default=str)[:1200]

        # G2：带 errors 重试 1 次
        retry_user = _retry_prompt(prompt, last_errs, last_preview)
        raw2, content2 = _call_llm_json(
            client, use_model=use_model, system=_SYS, user=retry_user,
        )
        attempts = 2
        if isinstance(raw2, dict):
            fixed2 = _normalize_llm_raw(raw2, req)
            fixed2["source_hint"] = "llm_spec"
            ok2, errs2 = spec_mod.validate(fixed2)
            if ok2:
                fixed2["_llm_attempts"] = attempts
                fixed2.setdefault("assumptions", []).append(
                    "LLM spec 经校验失败后 1 次重试通过"
                )
                return fixed2
            last_errs = list(errs2[:20])
            last_preview = json.dumps(fixed2, ensure_ascii=False, default=str)[:1200]
        else:
            last_errs = last_errs or ["response_not_dict"]
            last_preview = str(content2)[:800]

        fallback["source_hint"] = "llm_invalid"
        fallback.setdefault("assumptions", []).append(
            f"LLM spec 校验失败（含 {attempts} 次尝试）回退默认："
            + "；".join(last_errs[:5])
        )
        fallback["_validate_errors"] = list(last_errs[:20])
        fallback["_llm_raw_preview"] = last_preview
        fallback["_llm_attempts"] = attempts
        return fallback
    except Exception as exc:  # noqa: BLE001 - 任何失败都回退，不阻断财务
        fallback["source_hint"] = "llm_error"
        fallback.setdefault("assumptions", []).append(
            f"spec 生成异常回退默认（{str(exc)[:80]}）")
        fallback["_validate_errors"] = [f"{type(exc).__name__}: {exc}"]
        return fallback
