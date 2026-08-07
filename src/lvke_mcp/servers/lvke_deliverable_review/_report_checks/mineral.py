"""选矿专用检查组：合同方、数量、引用与合同值核对。"""

from __future__ import annotations

import re
from typing import Any

from lvke_mcp.servers.lvke_deliverable_review import rules

from .evidence import (
    _candidate_location,
    _claim_evidence,
    _formal_evidence_candidate,
)

from .hotel import (
    _MONEY_PATTERN,
    _candidate_text,
    _evidence_has_term,
    _money_values,
)

from .normalize import (
    _canonical_unit,
    _canonical_value,
    _number,
)

from .patterns import (
    _COMPANY_PATTERN,
)


_CONTRACT_MENTION_PATTERN = re.compile(
    r"购销合同|销售合同|采购合同|供货合同|合作协议"
)


_CONTRACT_QUANTITY_PATTERN = re.compile(
    r"(?P<number>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>万吨|吨)"
)


def _contract_party_role(text: str) -> str:
    normalized = str(text or "").lower()
    if re.search(r"buyer|purchaser|买方|甲方|需方|采购方|发包方", normalized):
        return "buyer"
    if re.search(r"seller|supplier|卖方|乙方|供方|供货方|承包方", normalized):
        return "seller"
    if re.search(r"contractor|承建方|施工方|承建", normalized):
        return "contractor"
    return "party"


def _contract_candidate_field(candidate: dict[str, Any]) -> str:
    source = candidate.get("source") or {}
    metadata = " ".join(
        str(candidate.get(key) or "")
        for key in ("field", "metric", "matched_alias")
    )
    context = f"{metadata} {source.get('title') or ''}".lower()
    if not re.search(
        r"contract|合同|协议|买方|卖方|甲方|乙方|需方|供方|发包方|承包方",
        context,
    ):
        return ""
    if re.search(r"party|buyer|seller|purchaser|supplier|主体|买方|卖方|甲方|乙方|需方|供方|发包方|承包方", metadata.lower()):
        return "party"
    if re.search(r"amount|total|consideration|金额|总额|总价|价款", metadata.lower()):
        return "amount"
    if re.search(r"quantity|volume|数量|采购量|供货量|暂定量", metadata.lower()):
        return "quantity"
    if re.search(r"date|signed|execution|日期|签约|签署|签订", metadata.lower()):
        return "date"
    return ""


def _contract_reference(candidate: dict[str, Any]) -> str:
    explicit = str(
        candidate.get("contract_ref")
        or candidate.get("contract_id")
        or candidate.get("contract_scope")
        or ""
    ).strip()
    if explicit:
        return explicit
    source = candidate.get("source") or {}
    context = " ".join(
        str(value or "")
        for value in (
            source.get("title"),
            candidate.get("field"),
            candidate.get("matched_alias"),
            candidate.get("excerpt"),
        )
    )
    match = re.search(r"\bG\s*(\d{2,5})\b", context, re.I)
    if match:
        return f"G{match.group(1)}"
    return ""


def _contract_evidence_value(
    candidate: dict[str, Any], field: str,
) -> tuple[Any, str]:
    raw_text = _candidate_text(candidate)
    if field == "party":
        companies = _COMPANY_PATTERN.findall(raw_text)
        value = companies[0] if companies else str(
            candidate.get("value") or candidate.get("original_value") or ""
        ).strip()
        return value, _contract_party_role(
            " ".join(
                str(candidate.get(key) or "")
                for key in ("field", "metric", "matched_alias", "excerpt")
            )
        )
    if field in {"amount", "quantity"}:
        numeric = _number(candidate.get("numeric_value"))
        unit = str(candidate.get("expected_unit") or "")
        if numeric is None:
            pattern = _MONEY_PATTERN if field == "amount" else _CONTRACT_QUANTITY_PATTERN
            match = pattern.search(raw_text)
            if match is None:
                return None, ""
            numeric = float(match.group("number").replace(",", ""))
            unit = match.group("unit")
        return _canonical_value(numeric, unit), _canonical_unit(unit)
    if field == "date":
        match = re.search(r"20\d{2}年\d{1,2}月(?:\d{1,2}日)?", raw_text)
        return (match.group(0), "") if match else (None, "")
    return None, ""


def _formal_contract_evidence(
    candidates: list[dict[str, Any]], *, target_id: str,
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        if not _formal_evidence_candidate(candidate):
            continue
        field = _contract_candidate_field(candidate)
        reference = _contract_reference(candidate)
        if not field or not reference:
            continue
        value, unit_or_role = _contract_evidence_value(candidate, field)
        if value in (None, ""):
            continue
        grouped.setdefault(reference, []).append({
            "field": field,
            "role": unit_or_role if field == "party" else "",
            "unit": unit_or_role if field != "party" else "",
            "value": value,
            "location": _candidate_location(candidate, target_id=target_id),
        })
    return grouped


def _normalized_company(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(value or "").lower())


def _report_contract_values(rows: list[dict[str, Any]]) -> dict[str, Any]:
    parties: dict[str, set[str]] = {"buyer": set(), "seller": set(), "party": set()}
    amounts: set[float] = set()
    quantities: set[float] = set()
    dates: set[str] = set()
    for row in rows:
        text = str(row.get("text") or "")
        for match in _COMPANY_PATTERN.finditer(text):
            before = text[max(0, match.start() - 20):match.start()]
            after = text[match.end():min(len(text), match.end() + 12)]
            role = _contract_party_role(f"{before} {after}")
            if role == "contractor":
                continue
            parties.setdefault(role, set()).add(match.group(1))
        for match in _MONEY_PATTERN.finditer(text):
            before = text[max(0, match.start() - 36):match.start()]
            if not re.search(
                r"(?:合同(?:含税)?(?:总额|金额|价款|总价)|含税总额|价税合计|签约金额)\D{0,16}$",
                before,
            ):
                continue
            amounts.update(_money_values(match.group(0)))
        for pattern in (
            re.compile(rf"(?:合同|采购|供货|暂定)(?:数量|量)\D{{0,12}}(?P<measure>{_CONTRACT_QUANTITY_PATTERN.pattern})"),
            re.compile(rf"(?:数量|采购量|供货量|暂定量)\D{{0,12}}(?P<measure>{_CONTRACT_QUANTITY_PATTERN.pattern})"),
        ):
            for match in pattern.finditer(text):
                measure = _CONTRACT_QUANTITY_PATTERN.search(match.group("measure"))
                if measure is None:
                    continue
                value = float(measure.group("number").replace(",", ""))
                quantities.add(_canonical_value(value, measure.group("unit")))
        for pattern in (
            re.compile(r"(?:合同日期|签约日期|签署日期|签订日期|签订于)\D{0,12}(?P<date>20\d{2}年\d{1,2}月(?:\d{1,2}日)?)"),
            re.compile(r"(?P<date>20\d{2}年\d{1,2}月(?:\d{1,2}日)?)\D{0,12}(?:签约|签署|签订合同)"),
        ):
            dates.update(match.group("date") for match in pattern.finditer(text))
    return {
        "parties": {key: sorted(values) for key, values in parties.items()},
        "amounts_wan": sorted(amounts),
        "quantities_ton": sorted(quantities),
        "dates": sorted(dates),
    }


def _contract_value_matches(field: str, expected: Any, actual: Any) -> bool:
    if field == "party":
        return _normalized_company(str(expected)) == _normalized_company(str(actual))
    if field == "amount":
        return abs(float(expected) - float(actual)) <= max(0.01, abs(float(expected)) * 1e-6)
    if field == "quantity":
        return abs(float(expected) - float(actual)) <= max(0.01, abs(float(expected)) * 1e-6)
    return str(expected) == str(actual)


def _mineral_findings(
    content: str,
    claims: list[dict[str, Any]],
    evidence_claims: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    target_id: str,
    standard_basis: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    findings: list[dict[str, Any]] = []
    incomplete: list[str] = []
    for label, terms, severity, role in (
        ("采矿许可证", ("采矿许可证", "采矿权证"), "P0", "legal"),
        ("土地权属/出让依据", ("土地出让合同", "土地权证", "不动产权证"), "P0", "legal"),
        ("规划许可", ("建设工程规划许可证", "规划许可"), "P1", "legal"),
        ("环评批复", ("环评批复", "环境影响评价"), "P1", "legal"),
        ("能评依据", ("节能审查", "能评批复", "能源评价"), "P1", "business"),
    ):
        if _evidence_has_term(candidates, sources, terms):
            continue
        findings.append(rules.finding(
            "MINERAL.PERMITS",
            severity,
            f"黄鹰岩项目缺少可回读的{label}原始证据",
            category="mineral_permits",
            expected={"document": label, "original_snapshot": True},
            actual="未在绑定 evidence pack 中找到",
            target_location={"target_id": target_id, "required_document": label},
            standard_basis=standard_basis,
            review_area=role,
            remediation="补充证载主体、项目名称、地址、范围、规模和有效期完整原件及 SHA-256",
        ))
    radius_claims = [
        row for row in [*claims, *evidence_claims]
        if row.get("metric") == "market_radius" or row.get("unit") == "公里"
    ]
    radius_150 = [
        row for row in radius_claims
        if abs(float(row["value"]) - 150.0) <= 0.01
    ]
    if radius_150:
        has_method = bool(re.search(r"(?:150\s*(?:公里|千米|km)).{0,120}(?:样本|调研|统计|测算|来源|口径|独立佐证)", content, re.S | re.I))
        has_evidence = any(
            _claim_evidence(
                row,
                candidates,
                exclude_candidate_id=str(row.get("candidate_id") or ""),
                exclude_source_id=str(row.get("source_id") or ""),
            )
            for row in radius_150
        )
        if not has_method or not has_evidence:
            findings.append(rules.finding(
                "MINERAL.MARKET.RADIUS",
                "P1",
                "150公里市场需求缺少方法披露或独立原始证据",
                category="market_evidence",
                expected="披露区域、年份、样本、计算方法和独立佐证",
                actual={
                    "method_disclosed": has_method,
                    "independent_exact_evidence_bound": has_evidence,
                    "claims": [
                        {
                            "value": row["value"],
                            "source_kind": row.get("source_kind") or "report",
                            "location": row["location"],
                        }
                        for row in radius_150
                    ],
                },
                target_location={
                    "target_id": target_id,
                    "text_anchor": "150公里市场",
                    "locations": [row["location"] for row in radius_150],
                },
                standard_basis=standard_basis,
                review_area="business",
                remediation="补充市场半径方法、样本和独立证据，避免以销售意向替代市场容量",
            ))
    for label, pattern in (
        ("能源与水耗", r"电耗|用电量|水耗|用水量|燃料消耗|能源成本"),
        ("生产成本驱动", r"单位成本|原料成本|人工成本|制造费用|运输成本"),
    ):
        if re.search(pattern, content):
            continue
        findings.append(rules.finding(
            "MINERAL.OPERATING.DRIVERS",
            "P1",
            f"黄鹰岩项目缺少{label}披露",
            category="operating_assumption",
            expected=label,
            actual=None,
            target_location={"target_id": target_id, "required_disclosure": label},
            standard_basis=standard_basis,
            review_area="business",
            remediation="补充建筑、设备、能源、水、燃料和成本驱动的量价明细及来源",
        ))
    contract_rows = [
        {"line": line_number, "text": line.strip()}
        for line_number, line in enumerate(content.splitlines(), start=1)
        if _CONTRACT_MENTION_PATTERN.search(line)
    ]
    for index, row in enumerate(contract_rows, start=1):
        line = row["text"]
        values = _report_contract_values([row])
        missing: list[str] = []
        if not any(values["parties"].values()):
            missing.append("主体")
        if not values["amounts_wan"]:
            missing.append("金额")
        if not values["quantities_ton"]:
            missing.append("数量")
        if not values["dates"]:
            missing.append("日期")
        if missing:
            findings.append(rules.finding(
                "MINERAL.CONTRACT.FIELDS",
                "P1",
                "合同描述缺少主体、金额、数量或日期，无法与原件精确核对",
                category="contract_consistency",
                expected=["主体", "金额", "数量", "日期"],
                actual={"missing": missing, "text": line[:300]},
                target_location={
                    "target_id": target_id,
                    "contract_mention": index,
                    "line": row["line"],
                    "text_anchor": line[:120],
                },
                standard_basis=standard_basis,
                review_area="legal",
                remediation="按合同原件补齐关键字段并核对与研报、销量、价格和收入假设的一致性",
            ))

    evidence_contracts = _formal_contract_evidence(candidates, target_id=target_id)
    if contract_rows and not evidence_contracts:
        incomplete.append("mineral_contract_formal_evidence_unavailable")
    for reference, evidence_fields in sorted(evidence_contracts.items()):
        report_rows = [
            row for row in (
                {
                    "line": line_number,
                    "text": line.strip(),
                }
                for line_number, line in enumerate(content.splitlines(), start=1)
            )
            if reference.lower() in row["text"].lower()
        ]
        if not report_rows:
            continue
        evidence_kinds = {row["field"] for row in evidence_fields}
        evidence_party_roles = {
            str(row.get("role") or "")
            for row in evidence_fields
            if row["field"] == "party"
        }
        if (
            evidence_kinds != {"party", "amount", "quantity", "date"}
            or not {"buyer", "seller"}.issubset(evidence_party_roles)
        ):
            incomplete.append("mineral_contract_formal_evidence_fields_incomplete")
        report_values = _report_contract_values(report_rows)
        missing: list[str] = []
        mismatches: list[dict[str, Any]] = []
        field_labels = {
            "party": "主体",
            "amount": "金额",
            "quantity": "数量",
            "date": "日期",
        }
        party_role_labels = {"buyer": "买方", "seller": "卖方", "party": "未标明角色"}
        for field in ("party", "amount", "quantity", "date"):
            expected_rows = [row for row in evidence_fields if row["field"] == field]
            if not expected_rows:
                continue
            if field == "party":
                actual_by_role = report_values["parties"]
                for expected_row in expected_rows:
                    role = str(expected_row.get("role") or "party")
                    actual = actual_by_role.get(role) or []
                    if not actual:
                        label = (
                            f"{field_labels[field]}({party_role_labels.get(role, role)})"
                            if role != "party" else field_labels[field]
                        )
                        if label not in missing:
                            missing.append(label)
                        continue
                    if not any(
                        _contract_value_matches(field, expected_row["value"], value)
                        for value in actual
                    ):
                        mismatches.append({
                            "field": field_labels[field],
                            "role": role,
                            "expected": expected_row["value"],
                            "actual": actual,
                            "evidence_location": expected_row["location"],
                        })
                continue
            actual_key = {
                "amount": "amounts_wan",
                "quantity": "quantities_ton",
                "date": "dates",
            }[field]
            actual = report_values[actual_key]
            if not actual:
                missing.append(field_labels[field])
                continue
            expected_values = [row["value"] for row in expected_rows]
            if not any(
                _contract_value_matches(field, expected, value)
                for expected in expected_values
                for value in actual
            ):
                mismatches.append({
                    "field": field_labels[field],
                    "expected": expected_values,
                    "actual": actual,
                    "evidence_locations": [row["location"] for row in expected_rows],
                })
        if not missing and not mismatches:
            continue
        findings.append(rules.finding(
            "MINERAL.CONTRACT.FIELDS",
            "P1",
            f"{reference}合同关键字段在研报中缺失或与正式原件不一致",
            category="contract_consistency",
            expected={
                "contract_reference": reference,
                "fields": ["主体", "金额", "数量", "日期"],
                "evidence_fields": evidence_fields,
            },
            actual={
                "missing": missing,
                "mismatches": mismatches,
                "report_values": report_values,
                "report_context": [row["text"][:300] for row in report_rows[:12]],
            },
            target_location={
                "target_id": target_id,
                "contract_reference": reference,
                "locations": [
                    {
                        "line": row["line"],
                        "text_anchor": row["text"][:160],
                    }
                    for row in report_rows[:12]
                ],
            },
            evidence=[row["location"] for row in evidence_fields],
            standard_basis=standard_basis,
            review_area="legal",
            remediation="按合同原件补齐或修正主体、含税总额、暂定数量和签约日期，不得以承建方或项目总投资替代合同字段",
        ))
    return findings, sorted(set(incomplete))
