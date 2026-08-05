"""财务计算规范 FinanceSpec（BC 混合改造 §3）。

FinanceSpec 是 B 层 LLM 的**唯一产物**，也是确定性引擎的输入契约：
LLM 只定义「算什么、用什么收入模型、参数从哪来」，绝不出 IRR/NPV 等最终数字。

设计要点：
- 纯数据结构 + 校验，无 IO、无 LLM 调用（易测、可复现）。
- ``model="flat"`` 的单点法等价现状，保证 ``spec`` 缺失/非法时行为不变。
- ``validate`` 做结构 + 数值区间校验，非法时上层回退默认 spec（永不阻断）。
- ``FINANCE_SPEC_SCHEMA`` 与 dataclass 一一对应，供 LLM structured output 约束。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SPEC_VERSION = "finance_spec.v2"
LEGACY_SPEC_VERSION = "finance_spec.v1"
LATEST_SPEC_VERSION = "finance_spec.v3"
SUPPORTED_SPEC_VERSIONS = (LEGACY_SPEC_VERSION, SPEC_VERSION, LATEST_SPEC_VERSION)
# P1-014：连字符写法是同一版本的常见别名。归一化到下划线正式写法，避免
# prepare 静默放行、validate/confirm 才报 unsupported 的口径不一致。
# 真正未知的版本（如 finance_spec.v9）不在此表，仍由 validate 显式拒绝。
SPEC_VERSION_ALIASES = {
    "finance-spec.v1": LEGACY_SPEC_VERSION,
    "finance-spec.v2": SPEC_VERSION,
    "finance-spec.v3": LATEST_SPEC_VERSION,
}


def normalize_spec_version(value: Any) -> Any:
    """Map a known version alias onto its canonical spelling, else pass through."""

    if value in (None, ""):
        return value
    return SPEC_VERSION_ALIASES.get(str(value), value)
CONFIRMED_SOURCE_HINTS = {"snapshot_fixed", "user_confirmed", "user_edited", "confirmed_spec"}
SPEC_MIGRATOR_VERSIONS = {
    "v1_to_v2": "finance_spec_migrator.v1_to_v2.1",
    "v2_to_v3": "finance_spec_migrator.v2_to_v3.1",
}


@dataclass
class ProductLine:
    """单一产品/业态的产销规格（解决 H1 单点收入）。"""

    name: str = ""                # 产品名，如「无菌利乐包装饮料·大包」
    unit: str = ""                # 计量单位，如「万箱」
    price_per_unit: float = 0.0   # 单价（元/单位 或 万元/单位，由 price_unit 标注）
    price_unit: Literal["yuan", "wan"] = "yuan"
    capacity: float = 0.0         # 达产年产量（同 unit）
    ramp: list[float] = field(default_factory=list)  # 逐年达产率曲线 [0.6,0.8,1.0,...]（H2）
    var_cost_rate: float = 0.0    # 该产品可变成本率（占其收入）


@dataclass
class RevenueSpec:
    """收入模型选型 + 参数。model 决定用哪个展开器。"""

    model: Literal[
        "product_sales", "property_sales", "tourism", "gov_payment",
        "lease_portfolio", "inventory_sales", "flat",
    ] = "flat"
    products: list[ProductLine] = field(default_factory=list)  # product_sales 用
    # property_sales（房地产去化）: 可售面积/售价/逐年去化率
    saleable_area: float = 0.0
    price_per_sqm: float = 0.0
    absorption: list[float] = field(default_factory=list)      # 逐年去化率
    # tourism（文旅客流）: 客流×客单价×爬坡
    annual_visitors: float = 0.0
    spend_per_visitor: float = 0.0
    visitor_ramp: list[float] = field(default_factory=list)
    tourism_revenue_components: list[dict[str, Any]] = field(default_factory=list)
    # gov_payment（PPP/特许经营/政府付费）
    annual_gov_payment_wan: float = 0.0
    payment_ramp: list[float] = field(default_factory=list)
    vat_refund_rate: float = 0.0          # 增值税即征即退比例（污水等 0.7）
    fiscal_subsidy_wan: float = 0.0       # 年财政运营补贴（万元）
    annual_schedule_wan: list[float] = field(default_factory=list)  # 租赁/存量销售逐年计划
    inventory_total: float = 0.0
    sales_schedule: list[float] = field(default_factory=list)
    # flat（兜底=现状单点，向后兼容）
    annual_revenue_wan: float = 0.0


@dataclass
class CostSpec:
    """成本结构（替代散落硬编码 H3）。缺省值来自 config，不写死在引擎。"""

    cost_items: dict[str, float] = field(default_factory=dict)  # 明细法（现有已支持）
    total_cost_rate: float | None = None   # 总额法兜底率（缺省从 config 取，不再写死 0.75）
    wage_rate: float | None = None         # 工资占现金经营成本（缺省 config，不再写死 0.15）
    salvage_rate: float | None = None      # 残值率（缺省 config，不再写死 0.05）


@dataclass
class TaxSpec:
    """税制（解决 H4）。"""

    income_tax_rate: float = 0.25
    tax_holiday_years: int = 0             # 免税期年数（三免三减半等）
    tax_half_years: int = 0                # 减半征收年数
    vat_rate: float = 0.13
    vat_input_rate: float = 0.10
    surtax_rate: float = 0.01


@dataclass
class InvestmentBreakdown:
    """投资明细三段式：工程费用+工程建设其他费用+预备费（解决附表1明细展开）。

    提供此字段后，附表1从几行汇总变成多级明细。引擎已支持三段式渲染，
    此 dataclass 为 LLM/用户提供输入通道。各细项单位：万元。
    """

    # 1.1 工程费用明细（construction_detail）
    civil_wan: float | None = None              # 建筑工程费
    equipment_wan: float | None = None          # 设备及工器具购置费
    installation_wan: float | None = None       # 安装工程费

    # 1.2 工程建设其他费用明细（other_detail）
    land_wan: float | None = None               # 土地使用费
    management_wan: float | None = None         # 建设管理费
    design_wan: float | None = None             # 设计费
    consulting_wan: float | None = None         # 咨询费
    supervision_wan: float | None = None        # 监理费
    bidding_wan: float | None = None            # 招标代理费
    test_run_wan: float | None = None           # 联合试运转费

    # 1.3 预备费明细（contingency_detail）
    basic_wan: float | None = None              # 基本预备费
    price_wan: float | None = None              # 价差预备费

    def to_finance_input(self) -> dict[str, Any]:
        """转换为引擎 finance 输入的 invest_breakdown 格式。"""
        construction = {}
        if self.civil_wan is not None:
            construction["civil_wan"] = self.civil_wan
        if self.equipment_wan is not None:
            construction["equipment_wan"] = self.equipment_wan
        if self.installation_wan is not None:
            construction["installation_wan"] = self.installation_wan

        other = {}
        if self.land_wan is not None:
            other["land_wan"] = self.land_wan
        if self.management_wan is not None:
            other["management_wan"] = self.management_wan
        if self.design_wan is not None:
            other["design_wan"] = self.design_wan
        if self.consulting_wan is not None:
            other["consulting_wan"] = self.consulting_wan
        if self.supervision_wan is not None:
            other["supervision_wan"] = self.supervision_wan
        if self.bidding_wan is not None:
            other["bidding_wan"] = self.bidding_wan
        if self.test_run_wan is not None:
            other["test_run_wan"] = self.test_run_wan

        contingency = {}
        if self.basic_wan is not None:
            contingency["basic_wan"] = self.basic_wan
        if self.price_wan is not None:
            contingency["price_wan"] = self.price_wan

        result = {}
        if construction:
            result["construction_detail"] = construction
        if other:
            result["other_detail"] = other
        if contingency:
            result["contingency_detail"] = contingency

        return result if result else {}


@dataclass
class CustomCalc:
    """C 层定制计算钩子（spec 表达不了时才用）。"""

    target: str = ""             # 作用目标，如 "depreciation" / "revenue_extra"
    code: str = ""               # LLM 生成的受限 Python 片段
    reason: str = ""             # 为何需要定制（审计留痕）


@dataclass
class FinanceSpec:
    version: str = SPEC_VERSION
    industry: str = ""
    invest_type: str = ""
    policy_version: str = ""
    industry_profile_version: str = ""
    selected_scenario_id: str = "base"
    revenue: RevenueSpec = field(default_factory=RevenueSpec)
    cost: CostSpec = field(default_factory=CostSpec)
    tax: TaxSpec = field(default_factory=TaxSpec)
    investment: InvestmentBreakdown | None = None  # 【新增】投资明细三段式
    custom: list[CustomCalc] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)  # LLM 给的假设文字理由（进 BoE）
    source_hint: str = "llm_spec"   # llm_spec | fallback_default | user_edited

    confirmation_status: str = "candidate"
    field_sources: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectParty:
    entity_id: str = ""
    name: str = ""
    roles: list[str] = field(default_factory=list)
    status: str = "pending"
    valid_from: str = ""
    valid_to: str = ""
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class HotelOperationSpec:
    rooms: float = 0.0
    adr: float | list[float] = 0.0
    occupancy: float | list[float] = 0.0
    operating_days: int = 365
    food_beverage_revenue: float | list[float] = 0.0
    meeting_revenue: float | list[float] = 0.0
    other_revenue: float | list[float] = 0.0
    ota_commission: float | list[float] = 0.0
    payroll: float | list[float] = 0.0
    utilities: float | list[float] = 0.0
    consumables: float | list[float] = 0.0
    maintenance_capex: float | list[float] = 0.0
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class LeaseUnit:
    unit_id: str = ""
    asset_location: str = ""
    area_sqm: float = 0.0
    lessor_id: str = ""
    lessee_id: str = ""
    start_date: str = ""
    end_date: str = ""
    base_rent_wan: float = 0.0
    pricing_unit: str = "annual_total"
    payment_frequency: str = "annual"
    escalation_rate: float = 0.0
    escalation_date: str = ""
    rent_free_months: int = 0
    vacancy_rate: float = 0.0
    renewal_probability: float = 0.0
    deposit_wan: float = 0.0
    guarantee_wan: float = 0.0
    bad_debt_rate: float = 0.0
    leasing_cost_wan: float = 0.0
    fitout_allowance_wan: float = 0.0
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class TransactionSpec:
    acquisition_type: str = "asset"
    purchase_price: float = 0.0
    transaction_taxes: dict[str, float] = field(default_factory=dict)
    tax_burden_party: str = "buyer"
    asset_scope: list[dict[str, Any]] = field(default_factory=list)
    closing_date: str = ""
    valuation_value: float = 0.0
    valuation_date: str = ""
    financing_ratio: float = 0.0
    interest_rate: float = 0.0
    tenor: int = 0
    repayment: str = "equal_principal"
    exit_value: float = 0.0
    exit_year: int = 0
    closing_conditions: list[str] = field(default_factory=list)
    veto_items: list[str] = field(default_factory=list)
    red_flags: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class HistoricalStatement:
    entity_id: str = ""
    period_start: str = ""
    period_end: str = ""
    statement_type: str = ""
    source_format: str = ""
    normalized_accounts: dict[str, float] = field(default_factory=dict)
    reconciliation: dict[str, Any] = field(default_factory=dict)
    anomalies: list[dict[str, Any]] = field(default_factory=list)
    source_locators: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FinanceSpecV3(FinanceSpec):
    """FinanceSpec v3 asset-acquisition extension.

    v3 is intentionally additive.  Existing v1/v2 specs remain readable and are
    migrated without inventing hotel, lease, transaction, party, or statement data.
    """

    version: str = LATEST_SPEC_VERSION
    asset_type: str = "hotel_lease"
    project_parties: list[ProjectParty] = field(default_factory=list)
    hotel_operation: HotelOperationSpec | dict[str, Any] = field(default_factory=dict)
    solar_operation: dict[str, Any] = field(default_factory=dict)
    lease_portfolio: dict[str, Any] = field(default_factory=dict)
    transaction: TransactionSpec | dict[str, Any] = field(default_factory=dict)
    historical_statements: list[HistoricalStatement] = field(default_factory=list)
    financing: dict[str, Any] = field(default_factory=dict)
    scenario_dimensions: dict[str, Any] = field(default_factory=dict)
    decision_thresholds: dict[str, Any] = field(default_factory=dict)
    evidence_links: dict[str, Any] = field(default_factory=dict)


def migrate_spec_v1_to_v2(spec: dict[str, Any]) -> dict[str, Any]:
    """Mechanically migrate a legacy spec without inventing business values."""
    out = dict(spec or {})
    version = normalize_spec_version(out.get("version"))
    if version not in (None, "", LEGACY_SPEC_VERSION, SPEC_VERSION):
        return out
    out["version"] = SPEC_VERSION
    source_hint = str(out.get("source_hint") or "")
    confirmed = source_hint in CONFIRMED_SOURCE_HINTS
    out.setdefault("confirmation_status", "confirmed" if confirmed else "candidate")
    out.setdefault("selected_scenario_id", "base")
    out.setdefault("policy_version", "")
    out.setdefault("industry_profile_version", "")
    out.setdefault("field_sources", {})
    return out


def migrate_spec_v2_to_v3(spec: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2 to v3 without fabricating acquisition-domain facts."""

    if not isinstance(spec, dict):
        return {}
    version = normalize_spec_version(spec.get("version"))
    if version in (None, "", LEGACY_SPEC_VERSION):
        source = migrate_spec_v1_to_v2(spec)
    elif version in (SPEC_VERSION, LATEST_SPEC_VERSION):
        source = dict(spec)
    else:
        return dict(spec)
    out = dict(source)
    out["version"] = LATEST_SPEC_VERSION
    for key, default in (
        ("asset_type", "hotel_lease"),
        ("project_parties", []),
        ("hotel_operation", {}),
        ("solar_operation", {}),
        ("lease_portfolio", {"units": []}),
        ("transaction", {}),
        ("historical_statements", []),
        ("financing", {}),
        ("scenario_dimensions", {}),
        ("decision_thresholds", {}),
        ("evidence_links", {}),
    ):
        out.setdefault(key, default)
    out.setdefault("confirmation_status", "candidate")
    out.setdefault("field_sources", {})
    out.setdefault("selected_scenario_id", "base")
    # A migration never confirms facts that were absent from the source spec.
    if version != LATEST_SPEC_VERSION:
        out["confirmation_status"] = "candidate"
    out.pop("confirmed_at", None)
    out.pop("confirmed_by", None)
    return out


def migrate_spec_to_v3(spec: dict[str, Any]) -> dict[str, Any]:
    """Apply the mandated v1 -> v2 -> v3 or v2 -> v3 migration chain."""

    migrated, _trace = migrate_spec_to_v3_with_trace(spec)
    return migrated


def _canonical_spec_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def migrate_spec_to_v3_with_trace(
    spec: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Migrate to v3 and retain every mandated hop without circular hashes."""

    source = dict(spec or {})
    raw_version = source.get("version") or LEGACY_SPEC_VERSION
    source_version = normalize_spec_version(raw_version)
    if source_version != raw_version:
        source["version"] = source_version
    if source_version not in SUPPORTED_SPEC_VERSIONS:
        return dict(source), {
            "source_spec_version": source_version,
            "target_spec_version": source_version,
            "source_hash": _canonical_spec_hash(source),
            "target_payload_hash": _canonical_spec_hash(source),
            "steps": [],
        }
    steps: list[dict[str, Any]] = []
    current = source
    if source_version == LEGACY_SPEC_VERSION:
        next_value = migrate_spec_v1_to_v2(current)
        steps.append({
            "step": "v1_to_v2",
            "migrator_version": SPEC_MIGRATOR_VERSIONS["v1_to_v2"],
            "source_version": LEGACY_SPEC_VERSION,
            "target_version": SPEC_VERSION,
            "before_hash": _canonical_spec_hash(current),
            "after_hash": _canonical_spec_hash(next_value),
            "diff": spec_migration_diff(current, next_value),
        })
        current = next_value
    if source_version in {LEGACY_SPEC_VERSION, SPEC_VERSION}:
        next_value = migrate_spec_v2_to_v3(current)
        steps.append({
            "step": "v2_to_v3",
            "migrator_version": SPEC_MIGRATOR_VERSIONS["v2_to_v3"],
            "source_version": SPEC_VERSION,
            "target_version": LATEST_SPEC_VERSION,
            "before_hash": _canonical_spec_hash(current),
            "after_hash": _canonical_spec_hash(next_value),
            "diff": spec_migration_diff(current, next_value),
        })
        current = next_value
    migrated = dict(current)
    if not steps:
        existing = migrated.get("migration_trace")
        if isinstance(existing, dict):
            return migrated, dict(existing)
        return migrated, {
            "source_spec_version": source_version,
            "target_spec_version": LATEST_SPEC_VERSION,
            "source_hash": _canonical_spec_hash(source),
            "target_payload_hash": _canonical_spec_hash(migrated),
            "steps": [],
        }
    trace = {
        "source_spec_version": source_version,
        "target_spec_version": LATEST_SPEC_VERSION,
        "source_hash": _canonical_spec_hash(source),
        "target_payload_hash": _canonical_spec_hash(migrated),
        "steps": steps,
    }
    migrated["migration_trace"] = trace
    return migrated, trace


def spec_migration_diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a deterministic top-level field migration ledger."""

    rows: list[dict[str, Any]] = []
    keys = sorted(set(before or {}) | set(after or {}))
    for key in keys:
        old = (before or {}).get(key)
        new = (after or {}).get(key)
        if old != new:
            rows.append({"field": key, "before": old, "after": new})
    return rows


def mark_spec_confirmed(spec: dict[str, Any]) -> dict[str, Any]:
    version = normalize_spec_version((spec or {}).get("version"))
    out = (
        migrate_spec_to_v3(spec)
        if isinstance(spec, dict) and version == LATEST_SPEC_VERSION
        else migrate_spec_v1_to_v2(spec)
    )
    out["confirmation_status"] = "confirmed"
    out.pop("confirmed_by", None)
    out.pop("confirmed_at", None)
    if out.get("source_hint") not in CONFIRMED_SOURCE_HINTS:
        out["source_hint"] = "confirmed_spec"
    return out


def validate_for_formal(spec: dict[str, Any]) -> tuple[bool, list[str]]:
    ok, errors = validate(spec)
    version = normalize_spec_version((spec or {}).get("version"))
    out = (
        migrate_spec_to_v3(spec)
        if isinstance(spec, dict) and version == LATEST_SPEC_VERSION
        else migrate_spec_v1_to_v2(spec)
    )
    if out.get("confirmation_status") != "confirmed":
        errors.append("FinanceSpec 尚未确认，LLM candidate 不得用于正式交付")
    if not out.get("selected_scenario_id"):
        errors.append("FinanceSpec 缺 selected_scenario_id")
    if out.get("custom"):
        errors.append("正式交付禁止执行 FinanceSpec.custom 动态代码")
    if out.get("version") == LATEST_SPEC_VERSION:
        _validate_v3_formal(out, errors)
    return (ok and not errors), errors


def _validate_v3_formal(spec: dict[str, Any], errors: list[str]) -> None:
    """Fail closed for a formal acquisition run; confirmed empty shells fail."""

    def missing(value: Any) -> bool:
        return value is None or value == "" or value == [] or value == {}

    def number(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    thresholds = spec.get("decision_thresholds") or {}
    if not isinstance(thresholds, dict) or not thresholds:
        errors.append("finance_spec.v3 正式运行缺 decision_thresholds")
    else:
        target_irr = number(thresholds.get("target_project_irr"))
        if target_irr <= 0 or target_irr > 5:
            errors.append("decision_thresholds.target_project_irr 应在0~5之间")
        if thresholds.get("minimum_dscr") is not None and number(thresholds.get("minimum_dscr")) <= 0:
            errors.append("decision_thresholds.minimum_dscr 必须大于0")

    asset_type = str(spec.get("asset_type") or "hotel_lease")
    if asset_type not in {"hotel_lease", "solar_power"}:
        errors.append(f"finance_spec.v3 正式运行不支持 asset_type: {asset_type}")
    transaction = spec.get("transaction") or {}
    financing_ratio = number(transaction.get("financing_ratio")) if isinstance(transaction, dict) else 0.0
    valuation_value = number(transaction.get("valuation_value")) if isinstance(transaction, dict) else 0.0
    parties = spec.get("project_parties") or []
    required_roles = {"buyer", "seller", "asset_owner", "operator"}
    if asset_type == "hotel_lease":
        required_roles.update({"license_holder", "lessor", "lessee"})
    if financing_ratio > 0:
        required_roles.add("lender")
    if valuation_value > 0:
        required_roles.add("appraiser")
    present_roles = {
        str(role)
        for party in parties if isinstance(party, dict)
        for role in (party.get("roles") or [])
    }
    if not parties:
        errors.append("finance_spec.v3 正式运行缺 project_parties")
    missing_roles = sorted(required_roles - present_roles)
    if missing_roles:
        errors.append(f"finance_spec.v3 正式运行缺主体角色: {missing_roles}")
    for index, party in enumerate(parties if isinstance(parties, list) else []):
        if not isinstance(party, dict):
            continue
        if not str(party.get("name") or "").strip():
            errors.append(f"project_parties[{index}] 缺主体名称")
        if party.get("status") != "confirmed":
            errors.append(f"project_parties[{index}] 主体状态未确认")
        if not (party.get("evidence_ids") or []):
            errors.append(f"project_parties[{index}] 缺主体证据")

    if asset_type == "solar_power":
        solar = spec.get("solar_operation") or {}
        for field_name in (
            "installed_capacity_mw", "tariff_yuan_per_kwh", "annual_opex_wan",
            "maintenance_capex_wan", "remaining_operating_years",
            "curtailment_rate", "degradation_rate",
        ):
            if not isinstance(solar, dict) or missing(solar.get(field_name)):
                errors.append(f"finance_spec.v3 正式运行缺 solar_operation.{field_name}")
        if isinstance(solar, dict) and missing(solar.get("annual_generation_mwh")) and missing(solar.get("utilization_hours")):
            errors.append("finance_spec.v3 正式运行缺 solar_operation.annual_generation_mwh 或 utilization_hours")
        if isinstance(solar, dict) and not (solar.get("evidence_ids") or []):
            errors.append("finance_spec.v3 正式运行缺 solar_operation.evidence_ids")
    else:
        hotel = spec.get("hotel_operation") or {}
        for field_name in (
            "rooms", "adr", "occupancy", "operating_days", "food_beverage_revenue",
            "meeting_revenue", "other_revenue", "ota_commission", "payroll", "utilities",
            "consumables", "maintenance_capex",
        ):
            if not isinstance(hotel, dict) or missing(hotel.get(field_name)):
                errors.append(f"finance_spec.v3 正式运行缺 hotel_operation.{field_name}")
        if isinstance(hotel, dict) and not (hotel.get("evidence_ids") or []):
            errors.append("finance_spec.v3 正式运行缺 hotel_operation.evidence_ids")

        portfolio = spec.get("lease_portfolio") or {}
        units = portfolio.get("units") if isinstance(portfolio, dict) else None
        if not isinstance(units, list) or not units:
            errors.append("finance_spec.v3 正式运行缺 lease_portfolio.units")
        for index, unit in enumerate(units if isinstance(units, list) else []):
            if not isinstance(unit, dict):
                continue
            for field_name in (
                "unit_id", "asset_location", "area_sqm", "lessor_id", "lessee_id",
                "start_date", "end_date", "base_rent_wan", "pricing_unit",
                "payment_frequency", "escalation_rate", "escalation_date",
                "rent_free_months", "vacancy_rate", "renewal_probability",
                "deposit_wan", "guarantee_wan", "bad_debt_rate", "leasing_cost_wan",
                "fitout_allowance_wan",
            ):
                if missing(unit.get(field_name)):
                    errors.append(f"lease_portfolio.units[{index}] 缺 {field_name}")
            if not (unit.get("evidence_ids") or []):
                errors.append(f"lease_portfolio.units[{index}] 缺合同/收款证据")

    for field_name in (
        "acquisition_type", "purchase_price", "transaction_taxes", "tax_burden_party",
        "asset_scope", "closing_date", "valuation_value", "valuation_date",
        "financing_ratio", "closing_conditions", "veto_items",
    ):
        if not isinstance(transaction, dict) or missing(transaction.get(field_name)):
            errors.append(f"finance_spec.v3 正式运行缺 transaction.{field_name}")
    if isinstance(transaction, dict):
        if number(transaction.get("purchase_price")) <= 0:
            errors.append("finance_spec.v3 正式运行 transaction.purchase_price 必须大于0")
        if number(transaction.get("valuation_value")) <= 0:
            errors.append("finance_spec.v3 正式运行 transaction.valuation_value 必须大于0")
        if transaction.get("tax_burden_party") not in {"buyer", "seller", "shared"}:
            errors.append("finance_spec.v3 正式运行 transaction.tax_burden_party 未裁决")
        if financing_ratio > 0:
            for field in ("interest_rate", "tenor", "repayment"):
                if missing(transaction.get(field)) or (
                    field in {"interest_rate", "tenor"} and number(transaction.get(field)) <= 0
                ):
                    errors.append(f"finance_spec.v3 融资收购缺 transaction.{field}")
            if transaction.get("repayment") not in {
                "equal_principal", "equal_payment", "annuity", "bullet", "interest_only",
            }:
                errors.append("finance_spec.v3 transaction.repayment 未裁决或不支持")
        scopes = transaction.get("asset_scope") or []
        for index, scope in enumerate(scopes if isinstance(scopes, list) else []):
            if not isinstance(scope, dict):
                errors.append(f"transaction.asset_scope[{index}] 非对象")
                continue
            for field in ("scope_id", "type", "included", "status", "evidence_ids"):
                if missing(scope.get(field)):
                    errors.append(f"transaction.asset_scope[{index}] 缺 {field}")
            if scope.get("status") != "confirmed":
                errors.append(f"transaction.asset_scope[{index}] 资产边界未确认")
            if scope.get("conflicts") and not str(scope.get("resolution") or "").strip():
                errors.append(f"transaction.asset_scope[{index}] 冲突未裁决")
            if not scope.get("included", True):
                continue
            treatment = str(scope.get("accounting_treatment") or "").strip()
            if treatment not in {"depreciable", "amortizable", "non_depreciable", "expensed"}:
                errors.append(
                    f"transaction.asset_scope[{index}] 缺有效 accounting_treatment"
                )
            elif treatment in {"depreciable", "amortizable"}:
                if number(scope.get("depreciable_basis_wan")) <= 0:
                    errors.append(
                        f"transaction.asset_scope[{index}] 缺 depreciable_basis_wan"
                    )
                if number(scope.get("depreciation_years")) <= 0:
                    errors.append(
                        f"transaction.asset_scope[{index}] 缺 depreciation_years"
                    )
                residual = number(scope.get("residual_rate"))
                if residual < 0 or residual > 1:
                    errors.append(
                        f"transaction.asset_scope[{index}] residual_rate 应在0~1"
                    )
            elif treatment == "non_depreciable" and not str(
                scope.get("non_depreciable_reason") or ""
            ).strip():
                errors.append(
                    f"transaction.asset_scope[{index}] 非折旧处理缺理由"
                )
        for index, flag in enumerate(transaction.get("red_flags") or []):
            if not isinstance(flag, dict):
                errors.append(f"transaction.red_flags[{index}] 非对象")
                continue
            if flag.get("status") not in {"closed", "resolved"}:
                errors.append(f"transaction.red_flags[{index}] 尚未关闭")
            if not (flag.get("evidence_ids") or []) or not str(flag.get("resolution") or "").strip():
                errors.append(f"transaction.red_flags[{index}] 缺裁决证据/理由")

    statements = spec.get("historical_statements") or []
    statement_types = {
        str(row.get("statement_type"))
        for row in statements if isinstance(row, dict)
    }
    missing_statements = sorted({"balance_sheet", "income_statement", "cash_flow"} - statement_types)
    if missing_statements:
        errors.append(f"finance_spec.v3 正式运行缺历史报表类型: {missing_statements}")
    for index, statement in enumerate(statements if isinstance(statements, list) else []):
        if not isinstance(statement, dict):
            continue
        for field in ("entity_id", "period_start", "period_end", "source_format", "normalized_accounts"):
            if missing(statement.get(field)):
                errors.append(f"historical_statements[{index}] 缺 {field}")
        if not (statement.get("source_locators") or []):
            errors.append(f"historical_statements[{index}] 缺页码/单元格来源定位")
        reconciliation = statement.get("reconciliation") or {}
        if not isinstance(reconciliation, dict) or reconciliation.get("ok") is not True:
            errors.append(f"historical_statements[{index}] 未通过勾稽复核")

    evidence_links = spec.get("evidence_links") or {}
    if not isinstance(evidence_links, dict) or not evidence_links:
        errors.append("finance_spec.v3 正式运行缺 evidence_links")
    else:
        required_evidence = [
            "transaction.purchase_price", "transaction.asset_scope", "historical_statements",
        ]
        required_evidence.extend(
            [
                "solar_operation.installed_capacity_mw",
                "solar_operation.tariff_yuan_per_kwh",
                "solar_operation.annual_opex_wan",
            ]
            if asset_type == "solar_power"
            else [
                "hotel_operation.rooms", "hotel_operation.adr",
                "hotel_operation.occupancy", "lease_portfolio.units",
            ]
        )
        if asset_type == "solar_power":
            solar = spec.get("solar_operation") or {}
            required_evidence.append(
                "solar_operation.annual_generation_mwh"
                if not missing(solar.get("annual_generation_mwh"))
                else "solar_operation.utilization_hours"
            )
        for field in required_evidence:
            if not (evidence_links.get(field) or []):
                errors.append(f"finance_spec.v3 正式运行缺关键字段证据: {field}")


_REVENUE_MODELS = (
    "product_sales", "property_sales", "tourism", "gov_payment",
    "lease_portfolio", "inventory_sales", "flat",
)


def _normalize_ratio_seq(seq: Any) -> list[float]:
    """把 ramp/absorption 等修成 [0,1.5] 浮点列表。

    接受 list/tuple；单个数字（含 str）→ 单元素列表；None/空 → []。
    关键：LLM 常写 ``\"ramp\": 1`` 而非 ``[1.0]``。
    """
    if seq is None or seq == "" or seq == []:
        return []
    if isinstance(seq, (list, tuple)):
        out: list[float] = []
        for x in seq:
            fv = _f(x)
            if fv is None:
                continue
            out.append(max(0.0, min(1.5, float(fv))))
        return out
    fv = _f(seq)
    if fv is not None:
        return [max(0.0, min(1.5, float(fv)))]
    return []


def coerce_llm_spec(spec: dict[str, Any], requirement: dict[str, Any] | None = None) -> dict[str, Any]:
    """把 LLM 常见不合法输出修成可 validate 的形态（不造业务数，只修结构/越界）。

    典型修复：
    - product_sales 无 products → 回退 flat，并尽量带上 annual_revenue_wan
    - price_unit 缺省 → yuan
    - ramp 标量 1 → [1.0]
    - 单价与达产营收数量级不符时按营收反推（assumptions 留痕）
    - tax/cost 比例越界 → 夹紧；vat_input_rate>0.35 重置
    """
    if not isinstance(spec, dict):
        return {}
    out = dict(spec)
    req = requirement if isinstance(requirement, dict) else {}
    fin = (req.get("finance") or {}) if isinstance(req.get("finance"), dict) else {}

    rev = dict(out.get("revenue") or {}) if isinstance(out.get("revenue"), dict) else {}
    for k in list(rev.keys()):
        if rev[k] is None:
            rev.pop(k, None)
    model = rev.get("model") or "flat"
    if model not in _REVENUE_MODELS:
        model = "flat"
        rev["model"] = "flat"

    # 已知达产营收：供 flat 回退
    known_rev = _f(rev.get("annual_revenue_wan"))
    if known_rev is None or known_rev <= 0:
        known_rev = _f(fin.get("annual_revenue_wan"))
    if known_rev is not None and known_rev > 0:
        rev.setdefault("annual_revenue_wan", float(known_rev))

    if model == "product_sales":
        products = rev.get("products") or []
        if not isinstance(products, list) or not products:
            rev["model"] = "flat"
            model = "flat"
            out.setdefault("assumptions", [])
            if isinstance(out["assumptions"], list):
                out["assumptions"].append(
                    "coerce: product_sales 无 products，回退 flat（保留 annual_revenue_wan）"
                )
        else:
            fixed_products = []
            for p in products:
                if not isinstance(p, dict):
                    continue
                pp = dict(p)
                if pp.get("price_unit") not in ("yuan", "wan"):
                    pp["price_unit"] = "yuan"
                # 负数归零
                for k in ("price_per_unit", "capacity", "var_cost_rate"):
                    v = _f(pp.get(k))
                    if v is not None and v < 0:
                        pp[k] = 0.0
                # ramp：标量/字符串 → 列表（LLM 常写 ramp: 1）
                if "ramp" in pp:
                    pp["ramp"] = _normalize_ratio_seq(pp.get("ramp"))
                fixed_products.append(pp)

            # 单价数量级：达产营收(万) 应约等于 price×capacity（unit 以「万」开头）
            if known_rev and known_rev > 0 and len(fixed_products) == 1:
                p0 = fixed_products[0]
                cap = _f(p0.get("capacity"), 0.0) or 0.0
                price = _f(p0.get("price_per_unit"), 0.0) or 0.0
                unit = str(p0.get("unit") or "")
                pu = p0.get("price_unit") or "yuan"
                if cap > 0 and price > 0:
                    if pu == "yuan":
                        implied = price * cap if unit.startswith("万") else price * cap / 10000.0
                    else:
                        implied = price * cap * (10000.0 if unit.startswith("万") else 1.0)
                    if implied > 0:
                        ratio = float(known_rev) / float(implied)
                        if ratio >= 5.0 or ratio <= 0.2:
                            if pu == "yuan" and unit.startswith("万"):
                                new_price = float(known_rev) / cap
                            elif pu == "yuan":
                                new_price = float(known_rev) * 10000.0 / cap
                            else:
                                new_price = float(known_rev) / (
                                    cap * (10000.0 if unit.startswith("万") else 1.0)
                                )
                            p0["price_per_unit"] = round(new_price, 6)
                            fixed_products[0] = p0
                            out.setdefault("assumptions", [])
                            if isinstance(out["assumptions"], list):
                                out["assumptions"].append(
                                    f"coerce: 产品单价与达产营收数量级不符"
                                    f"（implied={implied:.2f}万 vs 给定={known_rev:.2f}万），"
                                    f"已按营收反推 price_per_unit={p0['price_per_unit']}"
                                )

            rev["products"] = fixed_products
            if not fixed_products:
                rev["model"] = "flat"
                model = "flat"
                out.setdefault("assumptions", [])
                if isinstance(out["assumptions"], list):
                    out["assumptions"].append(
                        "coerce: product_sales products 全非法，回退 flat"
                    )

    if model == "property_sales":
        if _f(rev.get("saleable_area"), 0.0) <= 0 or _f(rev.get("price_per_sqm"), 0.0) <= 0:
            if known_rev and known_rev > 0:
                rev["model"] = "flat"
                rev["annual_revenue_wan"] = float(known_rev)
                out.setdefault("assumptions", [])
                if isinstance(out["assumptions"], list):
                    out["assumptions"].append(
                        "coerce: property_sales 缺面积/单价，回退 flat"
                    )
    if model == "tourism":
        from lvke_mcp.domains.finance.revenue_models import normalize_tourism_revenue

        normalized_revenue, component_errors = normalize_tourism_revenue(rev)
        components = normalized_revenue.get("tourism_revenue_components") or []
        if not component_errors:
            rev = normalized_revenue
        has_components = isinstance(components, list) and bool(components)
        if _f(rev.get("annual_visitors"), 0.0) <= 0 or (
            _f(rev.get("spend_per_visitor"), 0.0) <= 0 and not has_components
        ):
            if known_rev and known_rev > 0:
                rev["model"] = "flat"
                rev["annual_revenue_wan"] = float(known_rev)
                out.setdefault("assumptions", [])
                if isinstance(out["assumptions"], list):
                    out["assumptions"].append(
                        "coerce: tourism 缺客流/客单价，回退 flat"
                    )
    if model == "gov_payment":
        pay = _f(rev.get("annual_gov_payment_wan"), 0.0) or _f(rev.get("annual_revenue_wan"), 0.0)
        if pay is None or pay <= 0:
            if known_rev and known_rev > 0:
                rev["model"] = "flat"
                rev["annual_revenue_wan"] = float(known_rev)
                out.setdefault("assumptions", [])
                if isinstance(out["assumptions"], list):
                    out["assumptions"].append(
                        "coerce: gov_payment 缺付费额，回退 flat"
                    )
        vr = _f(rev.get("vat_refund_rate"))
        if vr is not None:
            rev["vat_refund_rate"] = max(0.0, min(1.0, vr))
    if model in {"lease_portfolio", "inventory_sales"}:
        schedule = rev.get("annual_schedule_wan") or []
        if not isinstance(schedule, list) or not schedule or any((_f(value) or 0) < 0 for value in schedule):
            if known_rev and known_rev > 0:
                rev["annual_schedule_wan"] = [float(known_rev)]
            else:
                rev["model"] = "flat"
                out.setdefault("assumptions", []).append(
                    f"coerce: {model} 缺逐年计划，回退 flat"
                )

    for key in ("absorption", "visitor_ramp", "payment_ramp"):
        if key in rev:
            rev[key] = _normalize_ratio_seq(rev.get(key))

    out["revenue"] = rev

    tax = dict(out.get("tax") or {}) if isinstance(out.get("tax"), dict) else {}
    for k in list(tax.keys()):
        if tax[k] is None:
            tax.pop(k, None)
    itr = _f(tax.get("income_tax_rate"), 0.25)
    if itr is not None:
        tax["income_tax_rate"] = max(0.0, min(0.45, itr))
    for k in ("tax_holiday_years", "tax_half_years"):
        v = _f(tax.get(k), 0.0)
        if v is not None and v < 0:
            tax[k] = 0
    for k in ("vat_rate", "vat_input_rate", "surtax_rate"):
        v = _f(tax.get(k))
        if v is not None:
            tax[k] = max(0.0, min(1.0, v))
    vir = _f(tax.get("vat_input_rate"))
    if vir is not None and vir > 0.35:
        tax["vat_input_rate"] = 0.10
        out.setdefault("assumptions", [])
        if isinstance(out["assumptions"], list):
            out["assumptions"].append(
                f"coerce: vat_input_rate={vir} 过高，已重置为 0.10（综合进项率）"
            )
    # 语义标注：surtax_rate>5% 极可能是「增值税附加综合率」而非营收附加率
    sr = _f(tax.get("surtax_rate"))
    if sr is not None and sr > 0.05:
        tax["surtax_rate_semantic"] = "vat_surcharge_combined"
        out.setdefault("assumptions", [])
        if isinstance(out["assumptions"], list):
            out["assumptions"].append(
                f"coerce: surtax_rate={sr*100:.1f}% 按增值税附加综合率理解"
                f"（引擎将优先 surtax_on_vat 路径），非营收×{sr*100:.1f}%"
            )
    if tax:
        out["tax"] = tax

    cost = dict(out.get("cost") or {}) if isinstance(out.get("cost"), dict) else {}
    for k in list(cost.keys()):
        if cost[k] is None:
            cost.pop(k, None)
    for k in ("total_cost_rate", "wage_rate", "salvage_rate", "welfare_rate"):
        v = _f(cost.get(k))
        if v is not None:
            cost[k] = max(0.0, min(1.0, v))
    if cost:
        out["cost"] = cost

    out["version"] = SPEC_VERSION
    out["confirmation_status"] = "candidate"
    out.setdefault("selected_scenario_id", "base")
    out.setdefault("field_sources", {})
    return out


def validate(spec: dict[str, Any]) -> tuple[bool, list[str]]:
    """结构 + 数值合法性校验。返回 (ok, errors)。非法时上层重试或回退默认 spec。"""
    errs: list[str] = []
    raw_version = spec.get("version") if isinstance(spec, dict) else None
    # P1-014：先归一化连字符别名，再判定是否受支持。否则 prepare 已按别名迁移，
    # 这里却报 unsupported，形成同一 spec 在两个工具下版本口径不一致。
    version = normalize_spec_version(raw_version)
    if version not in (None, "", *SUPPORTED_SPEC_VERSIONS):
        errs.append(f"unsupported FinanceSpec version {raw_version}")
    confirmation_status = spec.get("confirmation_status") if isinstance(spec, dict) else None
    if confirmation_status not in (None, "", "candidate", "confirmed"):
        errs.append(f"invalid confirmation_status {confirmation_status}")
    if not isinstance(spec, dict):
        return False, ["spec 非 dict"]

    asset_type = str(spec.get("asset_type") or "")
    solar_acquisition = version in {SPEC_VERSION, LATEST_SPEC_VERSION} and asset_type == "solar_power"
    rev = spec.get("revenue") or {}
    if not isinstance(rev, dict):
        return False, ["revenue 非 dict"]
    model = rev.get("model")
    # Solar acquisition revenue is computed from the controlled annual
    # ``solar_operation`` contract.  It must not be forced through the generic
    # project-finance revenue models used by ordinary feasibility studies.
    if solar_acquisition and model in (None, "", "solar_power"):
        model = None
    elif model not in _REVENUE_MODELS:
        errs.append(f"未知收入模型 {model}")

    if model == "product_sales":
        products = rev.get("products") or []
        if not products:
            errs.append("product_sales 缺 products")
        for i, p in enumerate(products):
            if not isinstance(p, dict):
                errs.append(f"products[{i}] 非 dict")
                continue
            if _f(p.get("price_per_unit")) is None or _f(p.get("price_per_unit"), 0.0) < 0:
                errs.append(f"products[{i}] price_per_unit 非法")
            if p.get("price_unit") not in (None, "yuan", "wan"):
                errs.append(f"products[{i}] price_unit 非法 {p.get('price_unit')}")
            if _f(p.get("capacity"), 0.0) < 0:
                errs.append(f"products[{i}] capacity 为负")
    if model == "property_sales":
        if _f(rev.get("saleable_area"), 0.0) <= 0 or _f(rev.get("price_per_sqm"), 0.0) <= 0:
            errs.append("property_sales 缺 saleable_area / price_per_sqm")
    if model == "tourism":
        from lvke_mcp.domains.finance.revenue_models import normalize_tourism_revenue

        normalized_revenue, component_errors = normalize_tourism_revenue(rev)
        errs.extend(
            str(item.get("message") or item.get("code"))
            for item in component_errors
        )
        components = normalized_revenue.get("tourism_revenue_components") or []
        if _f(rev.get("annual_visitors"), 0.0) <= 0 or (
            _f(rev.get("spend_per_visitor"), 0.0) <= 0 and not components
        ):
            errs.append("tourism 缺 annual_visitors / spend_per_visitor")
        if components and not isinstance(components, list):
            errs.append("tourism_revenue_components 必须是数组")
        elif isinstance(components, list):
            for i, component in enumerate(components):
                if not isinstance(component, dict) or not str(component.get("name") or "").strip():
                    errs.append(f"tourism_revenue_components[{i}] 缺 name")
                    continue
                basis = str(component.get("basis") or "")
                if basis == "per_visitor":
                    if _f(component.get("price_per_visitor_yuan"), 0.0) <= 0:
                        errs.append(f"tourism_revenue_components[{i}] 缺 price_per_visitor_yuan")
                elif basis == "fixed_annual":
                    if _f(component.get("annual_revenue_wan"), 0.0) < 0:
                        errs.append(f"tourism_revenue_components[{i}] annual_revenue_wan 非法")
                else:
                    errs.append(f"tourism_revenue_components[{i}] basis 非法")
    if model == "gov_payment":
        pay = _f(rev.get("annual_gov_payment_wan"), 0.0) or _f(rev.get("annual_revenue_wan"), 0.0)
        if pay is None or pay <= 0:
            errs.append("gov_payment 缺 annual_gov_payment_wan / annual_revenue_wan")
        vr = _f(rev.get("vat_refund_rate"), 0.0)
        if vr is not None and not (0.0 <= vr <= 1.0):
            errs.append("gov_payment.vat_refund_rate 越界（应在 0~1）")
    if model in {"lease_portfolio", "inventory_sales"}:
        schedule = rev.get("annual_schedule_wan") or []
        if not isinstance(schedule, list) or not schedule:
            errs.append(f"{model} 缺 annual_schedule_wan")
        elif any(_f(value) is None or float(_f(value) or 0) < 0 for value in schedule):
            errs.append(f"{model}.annual_schedule_wan 含非法值")

    # 达产率/去化率须在 [0, 1.5]，防 LLM 给出离谱值
    for key in ("ramp", "absorption", "visitor_ramp", "payment_ramp"):
        seq = rev.get(key)
        if key == "ramp":
            # ramp 在各 product 里
            for p in (rev.get("products") or []):
                if isinstance(p, dict):
                    _check_ratio_seq(p.get("ramp"), "products.ramp", errs)
            continue
        _check_ratio_seq(seq, key, errs)

    tax = spec.get("tax") or {}
    if isinstance(tax, dict):
        if not (0.0 <= _f(tax.get("income_tax_rate"), 0.25) <= 0.45):
            errs.append("所得税率越界（应在 0~0.45）")
        if _f(tax.get("tax_holiday_years"), 0.0) < 0 or _f(tax.get("tax_half_years"), 0.0) < 0:
            errs.append("免税期/减半期为负")

    cost = spec.get("cost") or {}
    if isinstance(cost, dict):
        for k in ("total_cost_rate", "wage_rate", "salvage_rate"):
            v = cost.get(k)
            if v is not None and not (0.0 <= _f(v, 0.0) <= 1.0):
                errs.append(f"cost.{k} 越界（应在 0~1）")

    if version == LATEST_SPEC_VERSION:
        _validate_v3(spec, errs)

    return (not errs), errs


_PARTY_ROLES = {
    "buyer", "seller", "asset_owner", "operator", "license_holder",
    "lessor", "lessee", "lender", "guarantor", "appraiser",
}
_PARTY_STATUSES = {"pending", "confirmed", "rejected", "unknown", "not_applicable"}


def _validate_v3(spec: dict[str, Any], errs: list[str]) -> None:
    asset_type = str(spec.get("asset_type") or "hotel_lease")
    if asset_type not in {"hotel_lease", "solar_power"}:
        errs.append(f"asset_type 非法: {asset_type}")
    parties = spec.get("project_parties") or []
    if not isinstance(parties, list):
        errs.append("project_parties 非数组")
    else:
        entity_ids: set[str] = set()
        for index, party in enumerate(parties):
            if not isinstance(party, dict):
                errs.append(f"project_parties[{index}] 非对象")
                continue
            entity_id = str(party.get("entity_id") or "").strip()
            if not entity_id:
                errs.append(f"project_parties[{index}] 缺 entity_id")
            elif entity_id in entity_ids:
                errs.append(f"project_parties entity_id 重复: {entity_id}")
            entity_ids.add(entity_id)
            roles = party.get("roles") or []
            invalid = [role for role in roles if role not in _PARTY_ROLES]
            if invalid:
                errs.append(f"project_parties[{index}] 非法角色: {invalid}")
            if party.get("status", "pending") not in _PARTY_STATUSES:
                errs.append(f"project_parties[{index}] 非法状态")

    hotel = spec.get("hotel_operation") or {}
    if not isinstance(hotel, dict):
        errs.append("hotel_operation 非对象")
    elif hotel:
        if _f(hotel.get("rooms"), 0.0) <= 0:
            errs.append("hotel_operation.rooms 必须大于0")
        if int(_f(hotel.get("operating_days"), 365) or 0) not in range(1, 367):
            errs.append("hotel_operation.operating_days 应在1~366")
        for key in ("occupancy",):
            values = hotel.get(key)
            sequence = values if isinstance(values, list) else [values]
            if any(_f(value) is None or not 0 <= float(_f(value) or 0) <= 1 for value in sequence):
                errs.append(f"hotel_operation.{key} 应在0~1")

    solar = spec.get("solar_operation") or {}
    if not isinstance(solar, dict):
        errs.append("solar_operation 非对象")
    elif asset_type == "solar_power":
        capacity = _f(solar.get("installed_capacity_mw"), 0.0) or 0.0
        generation = _f(solar.get("annual_generation_mwh"), 0.0) or 0.0
        hours = _f(solar.get("utilization_hours"), 0.0) or 0.0
        tariff = _f(solar.get("tariff_yuan_per_kwh"), 0.0) or 0.0
        years = _f(solar.get("remaining_operating_years"), 0.0) or 0.0
        if capacity <= 0:
            errs.append("solar_operation.installed_capacity_mw 必须大于0")
        if generation <= 0 and hours <= 0:
            errs.append("solar_operation 缺有效 annual_generation_mwh 或 utilization_hours")
        if generation > 0 and hours > 0 and capacity > 0:
            implied = capacity * hours
            if abs(generation - implied) / max(generation, implied) > 0.05:
                errs.append("solar_operation 发电量与装机容量×利用小时偏差超过5%")
        if tariff <= 0:
            errs.append("solar_operation.tariff_yuan_per_kwh 必须大于0")
        if years <= 0:
            errs.append("solar_operation.remaining_operating_years 必须大于0")
        for key in ("curtailment_rate", "degradation_rate"):
            value = _f(solar.get(key), 0.0)
            if value is None or not 0 <= value <= 1:
                errs.append(f"solar_operation.{key} 应在0~1")
        for key in ("annual_opex_wan", "maintenance_capex_wan"):
            values = solar.get(key)
            sequence = values if isinstance(values, list) else [values]
            if any((_f(value) is None or float(_f(value) or 0) < 0) for value in sequence):
                errs.append(f"solar_operation.{key} 不得为负")

    portfolio = spec.get("lease_portfolio") or {}
    if not isinstance(portfolio, dict):
        errs.append("lease_portfolio 非对象")
    else:
        units = portfolio.get("units") or []
        if not isinstance(units, list):
            errs.append("lease_portfolio.units 非数组")
        for index, unit in enumerate(units if isinstance(units, list) else []):
            if not isinstance(unit, dict) or not str(unit.get("unit_id") or "").strip():
                errs.append(f"lease_portfolio.units[{index}] 缺 unit_id")
                continue
            for key in ("vacancy_rate", "renewal_probability", "bad_debt_rate", "escalation_rate"):
                value = _f(unit.get(key), 0.0)
                if value is None or not 0 <= value <= 1:
                    errs.append(f"lease_portfolio.units[{index}].{key} 应在0~1")

    transaction = spec.get("transaction") or {}
    if not isinstance(transaction, dict):
        errs.append("transaction 非对象")
    elif transaction:
        if transaction.get("acquisition_type") not in {"asset", "equity", "mixed"}:
            errs.append("transaction.acquisition_type 非法")
        if _f(transaction.get("purchase_price"), 0.0) <= 0:
            errs.append("transaction.purchase_price 必须大于0")
        ratio = _f(transaction.get("financing_ratio"), 0.0)
        if ratio is None or not 0 <= ratio <= 1:
            errs.append("transaction.financing_ratio 应在0~1")
        scopes = transaction.get("asset_scope") or []
        if scopes and not isinstance(scopes, list):
            errs.append("transaction.asset_scope 非数组")
        for index, scope in enumerate(scopes if isinstance(scopes, list) else []):
            if not isinstance(scope, dict):
                errs.append(f"transaction.asset_scope[{index}] 非对象")
            elif scope.get("status") not in {None, "", "pending", "confirmed", "rejected", "unknown"}:
                errs.append(f"transaction.asset_scope[{index}] 非法状态")

    statements = spec.get("historical_statements") or []
    if not isinstance(statements, list):
        errs.append("historical_statements 非数组")
    for index, statement in enumerate(statements if isinstance(statements, list) else []):
        if not isinstance(statement, dict):
            errs.append(f"historical_statements[{index}] 非对象")
            continue
        if statement.get("statement_type") not in {"balance_sheet", "income_statement", "cash_flow"}:
            errs.append(f"historical_statements[{index}].statement_type 非法")

    thresholds = spec.get("decision_thresholds")
    if thresholds is not None:
        if not isinstance(thresholds, dict):
            errs.append("decision_thresholds 非对象")
        else:
            target = _f(thresholds.get("target_project_irr"))
            if target is not None and not 0 < target <= 5:
                errs.append("decision_thresholds.target_project_irr 应在0~5之间")
            minimum_dscr = _f(thresholds.get("minimum_dscr"))
            if thresholds.get("minimum_dscr") is not None and (
                minimum_dscr is None or minimum_dscr <= 0
            ):
                errs.append("decision_thresholds.minimum_dscr 必须大于0")


def _check_ratio_seq(seq: Any, name: str, errs: list[str]) -> None:
    if not seq:
        return
    if not isinstance(seq, (list, tuple)):
        errs.append(f"{name} 非序列")
        return
    for v in seq:
        fv = _f(v)
        if fv is None or not (0.0 <= fv <= 1.5):
            errs.append(f"{name} 含越界值 {v}")
            break


def _f(v: Any, default: float | None = None) -> float | None:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


# JSON Schema（供 LLM structured output / tool 约束），与上面 dataclass 一一对应。
FINANCE_SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "version": {"type": "string"},
        "industry": {"type": "string"},
        "invest_type": {"type": "string"},
        "policy_version": {"type": "string"},
        "industry_profile_version": {"type": "string"},
        "selected_scenario_id": {"type": "string"},
        "revenue": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "enum": list(_REVENUE_MODELS)},
                "products": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "unit": {"type": "string"},
                            "price_per_unit": {"type": "number"},
                            "price_unit": {"type": "string", "enum": ["yuan", "wan"]},
                            "capacity": {"type": "number"},
                            "ramp": {"type": "array", "items": {"type": "number"}},
                            "var_cost_rate": {"type": "number"},
                        },
                    },
                },
                "saleable_area": {"type": "number"},
                "price_per_sqm": {"type": "number"},
                "absorption": {"type": "array", "items": {"type": "number"}},
                "annual_visitors": {"type": "number"},
                "visitor_unit": {
                    "type": "string",
                    "description": "客流计量单位，如 人次 或 万人次。",
                },
                "spend_per_visitor": {"type": "number"},
                "ticket_price_yuan": {
                    "type": "number", "minimum": 0,
                    "description": "兼容别名：门票逐客流收入，规范化为 tourism_revenue_components。",
                },
                "secondary_spend_yuan": {
                    "type": "number", "minimum": 0,
                    "description": "兼容别名：二次消费逐客流收入，规范化为 tourism_revenue_components。",
                },
                "fixed_annual_revenue_wan": {
                    "type": "number", "minimum": 0,
                    "description": "兼容别名：固定年度收入，单位万元。",
                },
                "other_revenue_wan": {
                    "type": "number", "minimum": 0,
                    "description": "兼容别名：其他固定年度收入，单位万元。",
                },
                "visitor_ramp": {"type": "array", "items": {"type": "number"}},
                "tourism_revenue_components": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "name": {"type": "string", "minLength": 1},
                            "basis": {"type": "string", "enum": ["per_visitor", "fixed_annual"]},
                            "price_per_visitor_yuan": {"type": "number", "minimum": 0},
                            "participation_rate": {"type": "number", "minimum": 0, "maximum": 1},
                            "annual_revenue_wan": {"type": "number", "minimum": 0},
                            "ramp": {"type": "array", "items": {"type": "number", "minimum": 0}},
                        },
                        "required": ["name", "basis"],
                    },
                    "description": "文旅收入产品树；逐客流收入与固定年度收入分别建模。",
                },
                "annual_gov_payment_wan": {"type": "number"},
                "payment_ramp": {"type": "array", "items": {"type": "number"}},
                "vat_refund_rate": {"type": "number"},
                "fiscal_subsidy_wan": {"type": "number"},
                "annual_schedule_wan": {"type": "array", "items": {"type": "number"}},
                "inventory_total": {"type": "number"},
                "sales_schedule": {"type": "array", "items": {"type": "number"}},
                "annual_revenue_wan": {"type": "number"},
            },
            "required": ["model"],
            "additionalProperties": False,
        },
        "cost": {
            "type": "object",
            "properties": {
                "cost_items": {"type": "object"},
                "total_cost_rate": {"type": ["number", "null"]},
                "wage_rate": {"type": ["number", "null"]},
                "salvage_rate": {"type": ["number", "null"]},
            },
        },
        "tax": {
            "type": "object",
            "properties": {
                "income_tax_rate": {"type": "number"},
                "tax_holiday_years": {"type": "integer"},
                "tax_half_years": {"type": "integer"},
                "vat_rate": {"type": "number"},
                "vat_input_rate": {"type": "number"},
                "surtax_rate": {"type": "number"},
            },
        },
        "custom": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "code": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        },
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "source_hint": {"type": "string"},
        "confirmation_status": {"type": "string", "enum": ["candidate", "confirmed"]},
        "field_sources": {"type": "object"},
    },
    "required": ["revenue"],
    "additionalProperties": False,
}


# v3 schema is separate so v2 structured-output clients remain byte-compatible.
# Server validation/migration endpoints expose this schema for acquisition work.
FINANCE_SPEC_V3_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **FINANCE_SPEC_SCHEMA["properties"],
        "version": {"type": "string", "const": LATEST_SPEC_VERSION},
        "asset_type": {"type": "string", "enum": ["hotel_lease", "solar_power"]},
        "project_parties": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string"},
                    "name": {"type": "string"},
                    "roles": {"type": "array", "items": {"type": "string", "enum": sorted(_PARTY_ROLES)}},
                    "status": {"type": "string", "enum": sorted(_PARTY_STATUSES)},
                    "valid_from": {"type": "string"},
                    "valid_to": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["entity_id", "name", "roles", "status", "evidence_ids"],
            },
        },
        "hotel_operation": {"type": "object"},
        "solar_operation": {"type": "object"},
        "lease_portfolio": {
            "type": "object",
            "properties": {"units": {"type": "array", "items": {"type": "object"}}},
        },
        "transaction": {"type": "object"},
        "historical_statements": {"type": "array", "items": {"type": "object"}},
        "financing": {"type": "object"},
        "scenario_dimensions": {"type": "object"},
        "decision_thresholds": {
            "type": "object",
            "properties": {
                "target_project_irr": {"type": "number", "exclusiveMinimum": 0, "maximum": 5},
                "minimum_dscr": {"type": ["number", "null"], "exclusiveMinimum": 0},
            },
            "required": ["target_project_irr"],
        },
        "evidence_links": {"type": "object"},
        "migration_trace": {"type": "object"},
    },
    "required": [
        "version", "project_parties", "hotel_operation", "lease_portfolio",
        "transaction", "historical_statements", "revenue", "cost", "tax",
        "financing", "scenario_dimensions", "decision_thresholds", "evidence_links",
        "confirmation_status",
    ],
}
