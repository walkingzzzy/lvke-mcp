"""Frozen, read-only replay of the six Hengli negotiation scenarios (MCP 域内版).

The source report deliberately links purchase price to a required room rent so
that its six after-tax IRRs remain close to 6%.  That linked-price table is a
vendor *reference* track only.  Corrected acquisition runs must use the
independent scenario dimensions implemented by :mod:`finance.acquisition`.

本模块为既有六场景恒力参考轨的只读复刻：原样保留全部符号，仅改
``payback_from_period_rows`` 指向域内 ``reference_track``，``_root`` 指向
MCP 自有 ``config/hengli_reference_scenarios.json``。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from lvke_mcp.servers.finance_calc.calculations import irr, npv

from .reference_track import payback_from_period_rows

IRR_TOLERANCE_PERCENTAGE_POINTS = 0.01
AMOUNT_TOLERANCE_WAN = 0.01
PAYBACK_TOLERANCE_YEARS = 0.01
EXPECTED_PRICES = [2000, 2200, 2400, 2600, 2800, 3000]
GOLDEN_DATA_ROOT_ENV = "LVKE_GOLDEN_DATA_ROOT"


class HengliReferenceDataError(RuntimeError):
    """The immutable reference source cannot be trusted or located."""


def _root() -> Path:
    return Path(__file__).resolve().parent


def _config_path() -> Path:
    return _root() / "config" / "hengli_reference_scenarios.json"


def _data_root(data_root: str | Path | None = None) -> Path:
    configured = data_root
    if configured is None:
        configured = os.environ.get(GOLDEN_DATA_ROOT_ENV) or _root()
    candidate = Path(configured).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HengliReferenceDataError(
            f"HENGLI_GOLDEN_DATA_ROOT_MISSING: {candidate}"
        ) from exc
    if not resolved.is_dir():
        raise HengliReferenceDataError(
            f"HENGLI_GOLDEN_DATA_ROOT_NOT_DIRECTORY: {resolved}"
        )
    return resolved


def resolve_hengli_source_path(
    relative_path: str | Path,
    *,
    data_root: str | Path | None = None,
) -> Path:
    """Resolve one configured source inside the selected private-data root."""

    raw = relative_path.as_posix() if isinstance(relative_path, Path) else str(relative_path)
    if not raw or "\x00" in raw or "\\" in raw:
        raise HengliReferenceDataError(
            f"HENGLI_SOURCE_PATH_INVALID: {raw or '<empty>'}"
        )
    posix = PurePosixPath(raw)
    windows = PureWindowsPath(raw)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in raw.split("/"))
    ):
        raise HengliReferenceDataError(f"HENGLI_SOURCE_PATH_ESCAPE: {raw}")
    root = _data_root(data_root)
    candidate = root.joinpath(*posix.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HengliReferenceDataError(
            f"HENGLI_REFERENCE_SOURCE_MISSING: {raw}"
        ) from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise HengliReferenceDataError(
            f"HENGLI_SOURCE_PATH_ESCAPE: {raw}"
        ) from exc
    if not resolved.is_file():
        raise HengliReferenceDataError(
            f"HENGLI_REFERENCE_SOURCE_MISSING: {raw}"
        )
    return resolved


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_hengli_reference(
    *,
    data_root: str | Path | None = None,
) -> dict[str, Any]:
    """Load the immutable registry and fail closed unless its DOC is exact."""

    path = _config_path()
    raw = json.loads(path.read_text(encoding="utf-8"))
    value = copy.deepcopy(raw)
    source_meta = value.setdefault("source", {})
    source = resolve_hengli_source_path(
        str(source_meta.get("relative_path") or ""),
        data_root=data_root,
    )
    expected_sha = str(source_meta.get("sha256") or "")
    try:
        expected_size = int(source_meta.get("size_bytes") or 0)
    except (TypeError, ValueError) as exc:
        raise HengliReferenceDataError(
            "HENGLI_REFERENCE_SOURCE_METADATA_INVALID: size_bytes"
        ) from exc
    if not expected_sha or expected_size <= 0:
        raise HengliReferenceDataError(
            "HENGLI_REFERENCE_SOURCE_METADATA_INVALID: sha256/size_bytes"
        )
    actual_sha = _sha256_file(source)
    actual_size = source.stat().st_size
    if actual_sha != expected_sha or actual_size != expected_size:
        raise HengliReferenceDataError(
            "HENGLI_REFERENCE_SOURCE_INTEGRITY_MISMATCH: "
            f"{source_meta.get('relative_path')}"
        )
    source_meta["integrity_status"] = "passed"
    source_meta["actual_sha256"] = actual_sha
    source_meta["actual_size_bytes"] = actual_size
    value["config_hash"] = _canonical_hash(raw)
    value["reference_hash"] = _canonical_hash({
        "config_hash": value["config_hash"],
        "source_sha256": expected_sha,
        "source_size_bytes": expected_size,
    })
    return value


def _period_rows(values: list[Any], table: str, row: str) -> list[dict[str, Any]]:
    return [
        {
            "period": index,
            "value": float(value),
            "source_locator": f"{table}!row:{row}:period:{index}",
        }
        for index, value in enumerate(values, 1)
    ]


def _solve_track(
    row: dict[str, Any], *, values_field: str, stated_irr_field: str,
    stated_npv_field: str, stated_payback_field: str, source_row: str,
    benchmark_rate: float,
) -> dict[str, Any]:
    values = [float(value) for value in (row.get(values_field) or [])]
    table = str(row.get("cashflow_source_locator") or "")
    period_rows = _period_rows(values, table, source_row)
    solved_irr = float(irr([0.0, *values]) * 100.0) if values else None
    # The source table labels the initial investment as period 1 and its Excel
    # NPV formula discounts every displayed period, including that investment.
    solved_npv = float(npv([0.0, *values], benchmark_rate)) if values else None
    payback = payback_from_period_rows(period_rows, rate=benchmark_rate) if values else {
        "static_years": None, "dynamic_years": None,
    }
    stated_irr = row.get(stated_irr_field)
    stated_npv = row.get(stated_npv_field)
    stated_payback = row.get(stated_payback_field)
    irr_delta = solved_irr - float(stated_irr) if solved_irr is not None and stated_irr is not None else None
    npv_delta = solved_npv - float(stated_npv) if solved_npv is not None and stated_npv is not None else None
    payback_delta = (
        float(payback["static_years"]) - float(stated_payback)
        if payback.get("static_years") is not None and stated_payback is not None else None
    )
    # Five scenario summary NPVs are printed as integers.  Their independently
    # solved values are compared to the report's display precision, while the
    # 3000-wan row is printed to 0.01 wan and uses the formal amount tolerance.
    npv_display_tolerance = (
        AMOUNT_TOLERANCE_WAN if float(row.get("purchase_price_wan") or 0) == 3000 else 0.5
    )
    return {
        "cashflows_wan": values,
        "source_locators": period_rows,
        "stated_irr_pct": stated_irr,
        "solved_irr_pct": solved_irr,
        "irr_delta_percentage_points": irr_delta,
        "irr_within_tolerance": irr_delta is not None and abs(irr_delta) <= IRR_TOLERANCE_PERCENTAGE_POINTS,
        "stated_npv_wan": stated_npv,
        "solved_npv_wan": solved_npv,
        "npv_delta_wan": npv_delta,
        "npv_display_tolerance_wan": npv_display_tolerance,
        "npv_within_display_precision": npv_delta is not None and abs(npv_delta) <= npv_display_tolerance,
        "stated_static_payback_years": stated_payback,
        "solved_static_payback_years": payback.get("static_years"),
        "payback_delta_years": payback_delta,
        "payback_within_tolerance": payback_delta is not None and abs(payback_delta) <= PAYBACK_TOLERANCE_YEARS,
        "solved_dynamic_payback_years": payback.get("dynamic_years"),
    }


def replay_hengli_reference(value: dict[str, Any] | None = None) -> dict[str, Any]:
    """Independently replay cash-flow, IRR, NPV and payback for all six rows."""

    data = value or load_hengli_reference()
    benchmark_rate = float(data.get("benchmark_rate") or 0.0)
    scenarios: list[dict[str, Any]] = []
    blocking_issues: list[dict[str, Any]] = []
    manual_review_status = str((data.get("source") or {}).get("manual_review_status") or "pending")
    if manual_review_status != "approved":
        blocking_issues.append({
            "code": "HENGLI_REFERENCE_MANUAL_REVIEW_REQUIRED",
            "blocking": True,
            "detail": "六档参考轨仍是人工重建表，必须由已认证使用者逐项核对原DOC后方可批准。",
            "manual_review_status": manual_review_status,
        })
    for row in data.get("scenarios") or []:
        pre_tax = _solve_track(
            row, values_field="project_pre_tax_cashflows_wan",
            stated_irr_field="pre_tax_irr_pct", stated_npv_field="pre_tax_npv_wan",
            stated_payback_field="pre_tax_payback_years", source_row="3",
            benchmark_rate=benchmark_rate,
        )
        after_tax = _solve_track(
            row, values_field="project_after_tax_cashflows_wan",
            stated_irr_field="after_tax_irr_pct", stated_npv_field="after_tax_npv_wan",
            stated_payback_field="after_tax_payback_years", source_row="5",
            benchmark_rate=benchmark_rate,
        )
        equity_status = str(row.get("equity_cashflow_status") or "missing")
        # available → ok; not_present_in_source_report → documented source gap (still
        # blocks complete_full, but expert-reference may use project track only);
        # other statuses → blocking gap until clarified.
        if equity_status == "available":
            pass
        elif equity_status == "not_present_in_source_report":
            blocking_issues.append({
                "code": "HENGLI_EQUITY_CASHFLOW_SOURCE_MISSING",
                "scenario_id": row.get("scenario_id"),
                "blocking": True,
                "severity": "blocks_full_acquisition_reference",
                "expert_reference_ok_with_project_track": True,
                "detail": (
                    "原报告未附资本金现金流量表；不得自动发明序列。"
                    "项目投资税前/税后轨仍可作专家参考重放。"
                ),
            })
        else:
            blocking_issues.append({
                "code": "HENGLI_EQUITY_CASHFLOW_SOURCE_MISSING",
                "scenario_id": row.get("scenario_id"),
                "blocking": True,
                "detail": "原报告正文提到资本金现金流，但附表仅提供项目投资现金流；不得自动发明参考轨。",
            })
        scenarios.append({
            "scenario_id": row.get("scenario_id"),
            "purchase_price_wan": row.get("purchase_price_wan"),
            "room_rent_wan": row.get("room_rent_wan"),
            "annual_income_wan": row.get("annual_income_wan"),
            "project_pre_tax": pre_tax,
            "project_after_tax": after_tax,
            "equity_after_tax": {"status": equity_status, "cashflows_wan": []},
        })
    numerical_passed = all(
        track[track_name][check]
        for track in scenarios
        for track_name in ("project_pre_tax", "project_after_tax")
        for check in ("irr_within_tolerance", "npv_within_display_precision", "payback_within_tolerance")
    )
    manual_blocking = any(
        issue.get("code") == "HENGLI_REFERENCE_MANUAL_REVIEW_REQUIRED"
        for issue in blocking_issues
    )
    equity_blocking = any(
        issue.get("code") == "HENGLI_EQUITY_CASHFLOW_SOURCE_MISSING"
        for issue in blocking_issues
    )
    # complete_full: historic boolean (all blockers clear)
    # complete_project_track: numerical project tracks OK (expert-reference usable)
    complete_project_track = bool(numerical_passed)
    complete_full = bool(numerical_passed and not blocking_issues)
    return {
        "version": "hengli_reference_replay.v1",
        "read_only": True,
        "reference_hash": data.get("reference_hash"),
        "source": data.get("source"),
        "benchmark_rate": benchmark_rate,
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
        "numerical_passed": numerical_passed,
        "complete_project_track": complete_project_track,
        "complete_full": complete_full,
        # backward compatible: complete == full acquisition reference readiness
        "complete": complete_full,
        "expert_reference_usable": complete_project_track,
        "blocking_issues": blocking_issues,
        "blocking_summary": {
            "manual_review_pending": manual_blocking,
            "equity_cashflow_gap": equity_blocking,
        },
    }


def synthesize_equity_cashflow_assumption(
    scenario: dict[str, Any],
    *,
    authorized: bool = False,
    actor: str = "",
    note: str = "",
) -> dict[str, Any]:
    """OPTIONAL capital-CF proxy from project CF + financing split.

    Default authorized=False → refused. Never silently marks equity available.
    Output is C-grade assumption for expert discussion only unless professional
    approval workflow promotes it (out of scope of this pure function).
    """
    if not authorized:
        return {
            "ok": False,
            "error": "CAPITAL_CF_SYNTHESIS_UNAUTHORIZED",
            "detail": "资本金现金流假设生成默认关闭；须专业授权与假设披露",
        }
    project = list(scenario.get("project_after_tax_cashflows_wan") or [])
    if len(project) < 2:
        return {"ok": False, "error": "PROJECT_CF_MISSING"}
    total_inv = float(scenario.get("total_investment_wan") or 0.0)
    equity = float(scenario.get("equity_wan") or 0.0)
    if total_inv <= 0:
        return {"ok": False, "error": "TOTAL_INVESTMENT_MISSING"}
    equity_share = max(min(equity / total_inv, 1.0), 0.0) if equity else 0.0
    # Year0: equity outflow ≈ -equity; later years: residual after debt service proxy
    # Transparent naive model: Y0 = -equity; Yt = project[t] * equity_share for t>0
    series = [-round(equity, 6)]
    for value in project[1:]:
        series.append(round(float(value) * equity_share, 6))
    return {
        "ok": True,
        "status": "assumption_candidate",
        "evidence_grade": "C",
        "equity_cashflow_status": "assumption_not_source",
        "cashflows_wan": series,
        "method": "project_after_tax_scaled_by_equity_share",
        "equity_share": equity_share,
        "actor": actor,
        "note": note
        or "非原件资本金CF；不得作为未披露假设的 formal reference",
        "blocking_for_complete_full": True,
    }


def validate_hengli_reference(value: dict[str, Any] | None = None) -> list[str]:
    data = value or load_hengli_reference()
    errors: list[str] = []
    if data.get("version") != "hengli_reference.v1":
        errors.append("恒立参考轨版本不支持")
    if data.get("track_type") != "vendor_reference_read_only":
        errors.append("恒立六档必须标识为只读参考轨")
    if (data.get("source") or {}).get("integrity_status") == "failed":
        errors.append("恒立参考轨原始DOC哈希或大小不一致")
    rows = list(data.get("scenarios") or [])
    prices = [row.get("purchase_price_wan") for row in rows]
    if prices != EXPECTED_PRICES:
        errors.append("恒立六档收购价不完整或顺序错误")
    if len({row.get("scenario_id") for row in rows}) != 6:
        errors.append("恒立scenario_id不唯一")
    fixed = data.get("fixed_rents_wan") or {}
    fixed_sum = sum(float(item or 0.0) for item in fixed.values())
    for row in rows:
        scenario_id = str(row.get("scenario_id") or "")
        total = float(row.get("room_rent_wan") or 0.0) + fixed_sum
        if abs(total - float(row.get("annual_income_wan") or 0.0)) > 0.011:
            errors.append(f"{scenario_id} 四项租金不勾稽")
        if not 6.0 <= float(row.get("after_tax_irr_pct") or 0.0) <= 6.5:
            errors.append(f"{scenario_id} 税后IRR超出原报告范围")
        for field in ("project_pre_tax_cashflows_wan", "project_after_tax_cashflows_wan"):
            values = row.get(field) or []
            if len(values) != 15 or float(values[0] or 0) >= 0:
                errors.append(f"{scenario_id} {field}期次不完整")
        if row.get("cashflow_source_locator") != f"附表{EXPECTED_PRICES.index(row.get('purchase_price_wan')) + 1}-5":
            errors.append(f"{scenario_id} 现金流来源定位不一致")
    target = next((row for row in rows if row.get("purchase_price_wan") == 3000), {})
    expected = {
        "room_rent_wan": 312.8, "annual_income_wan": 393.11,
        "after_tax_irr_pct": 6.29, "after_tax_npv_wan": 61.78,
        "after_tax_payback_years": 12.39, "bep_pct": 62.06,
        "loan_repayment_years": 14.98,
    }
    for field, expected_value in expected.items():
        if abs(float(target.get(field) or 0.0) - expected_value) > 1e-9:
            errors.append(f"3000万元方案{field}不一致")
    replay = replay_hengli_reference(data)
    if not replay["numerical_passed"]:
        errors.append("恒立六档参考轨独立复算未达到展示精度或正式容差")
    return errors


def scenario_matrix() -> dict[str, Any]:
    data = load_hengli_reference()
    errors = validate_hengli_reference(data)
    replay = replay_hengli_reference(data)
    return {
        **data,
        "valid": not errors,
        "errors": errors,
        "replay": replay,
        "approval_status": "pending" if replay["blocking_issues"] else "reviewable",
        "warning": "本轨仅还原甲方原报告的价格—目标租金谈判边界，不代表市场租金合理性。",
        "independent_corrected_track_required": True,
    }