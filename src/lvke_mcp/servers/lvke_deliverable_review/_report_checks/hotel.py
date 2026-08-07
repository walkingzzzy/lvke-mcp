"""酒店租赁专用检查组：金额、租期日期与租约范围。"""

from __future__ import annotations

import re
from calendar import monthrange
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable

from lvke_mcp.runtime.storage import sha256_json
from lvke_mcp.servers.lvke_deliverable_review import rules

from .evidence import (
    _candidate_location,
    _formal_evidence_candidate,
    _formal_evidence_source,
    _parse_datetime,
)

from .patterns import (
    _COMPANY_PATTERN,
)


_MONEY_PATTERN = re.compile(
    r"(?P<number>-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?)\s*"
    r"(?P<unit>亿元|万元|万|元)"
)


_LEASE_DATE_TEXT = r"20\d{2}年\d{1,2}月(?:\d{1,2}日)?"


_LEASE_DATE_PATTERN = re.compile(_LEASE_DATE_TEXT)


_LEASE_ENTITY_PATTERN = re.compile(r"酒吧|清吧|超市|健身房|健身中心")


def _candidate_text(candidate: dict[str, Any]) -> str:
    return str(candidate.get("excerpt") or candidate.get("original_value") or "").strip()


def _candidate_context(candidate: dict[str, Any]) -> str:
    source = candidate.get("source") or {}
    return " ".join(
        str(value or "")
        for value in (
            candidate.get("field"),
            candidate.get("metric"),
            candidate.get("matched_alias"),
            source.get("title"),
            candidate.get("excerpt"),
            candidate.get("original_value"),
        )
    )


def _money_values(text: str) -> list[float]:
    values: list[float] = []
    for match in _MONEY_PATTERN.finditer(str(text or "")):
        raw = float(match.group("number").replace(",", ""))
        unit = match.group("unit")
        if unit == "亿元":
            raw *= 10000.0
        elif unit == "元":
            raw /= 10000.0
        values.append(raw)
    return values


def _distinct_money_values(values: Iterable[float]) -> list[float]:
    distinct: list[float] = []
    for value in sorted(float(item) for item in values):
        if any(abs(value - existing) <= max(0.01, abs(existing) * 1e-6) for existing in distinct):
            continue
        distinct.append(round(value, 8))
    return distinct


def _lease_date(raw: str) -> tuple[int, int, int, str]:
    match = re.fullmatch(r"(20\d{2})年(\d{1,2})月(?:(\d{1,2})日)?", raw)
    if match is None:
        return 0, 0, 0, ""
    year, month = int(match.group(1)), int(match.group(2))
    day = int(match.group(3)) if match.group(3) else monthrange(year, month)[1]
    return year, month, day, "day" if match.group(3) else "month"


def _lease_end_dates(text: str) -> list[dict[str, Any]]:
    raw_dates = _LEASE_DATE_PATTERN.findall(str(text or ""))
    if not raw_dates:
        return []
    selected: list[str] = []
    for pattern in (
        re.compile(rf"(?:至|到|\-|\u2014)\s*(?P<date>{_LEASE_DATE_TEXT})"),
        re.compile(rf"(?:到期(?:日)?|截至|截止(?:至|到)?|租期至|租赁期至)\s*(?P<date>{_LEASE_DATE_TEXT})"),
    ):
        selected.extend(match.group("date") for match in pattern.finditer(text))
    if not selected and len(raw_dates) >= 2 and re.search(r"租赁期|租期|合同期", text):
        selected.append(raw_dates[-1])
    if not selected and len(raw_dates) == 1 and re.search(r"到期|截至|截止|租期|租赁期", text):
        selected.append(raw_dates[0])
    output: list[dict[str, Any]] = []
    for raw in selected:
        year, month, day, precision = _lease_date(raw)
        if not year:
            continue
        output.append({
            "raw": raw,
            "year": year,
            "month": month,
            "day": day,
            "precision": precision,
        })
    return output


def _lease_scoped_texts(text: str, pattern: re.Pattern[str]) -> list[str]:
    output: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        entities = list(_LEASE_ENTITY_PATTERN.finditer(line))
        for entity_index, entity in enumerate(entities):
            if pattern.fullmatch(entity.group(0)) is None:
                continue
            end = entities[entity_index + 1].start() if entity_index + 1 < len(entities) else len(line)
            scoped = line[entity.start():end].strip(" ，,;；。、")
            if scoped:
                output.append(scoped)
    return output


def _lease_term_flags(text: str) -> set[str]:
    flags: set[str] = set()
    if re.search(r"递增|上调|调增|每.{0,12}(?:增加|上浮|调整)", text):
        flags.add("escalation")
    if re.search(r"付款|支付|缴纳|预付|付租", text):
        flags.add("payment")
    if re.search(r"终止|解除|违约退出", text):
        flags.add("termination")
    return flags


def _hotel_findings(
    content: str,
    claims: list[dict[str, Any]],
    evidence_claims: list[dict[str, Any]],
    evidence_candidates: list[dict[str, Any]],
    target_id: str,
    standard_basis: list[dict[str, Any]],
    review_as_of: str = "",
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    source_lines = str(content or "").splitlines()

    def source_line(row: dict[str, Any]) -> str:
        line_number = int((row.get("location") or {}).get("line") or 0)
        return source_lines[line_number - 1] if 0 < line_number <= len(source_lines) else ""

    def is_total_room_claim(row: dict[str, Any]) -> bool:
        if row.get("unit") != "间":
            return False
        if row.get("source_kind") == "evidence":
            return row.get("evidence_scope") == "total"
        line = source_line(row)
        location = row.get("location") or {}
        start = int(location.get("char_offset_start") or 0)
        end = int(location.get("char_offset_end") or start)
        before = line[max(0, start - 24):start]
        after = line[end:min(len(line), end + 12)]
        # Totals are written as "66间客房" or "客房共66间".  Room-type
        # breakdowns such as "大床房6间" must not become conflicting totals.
        return bool(re.match(r"\s*(?:客房|房间)", after)) or bool(
            re.search(r"(?:客房|房间)\s*(?:共|合计|总计)\s*$", before)
        )

    room_rows = [
        row for row in [*claims, *evidence_claims]
        if row.get("metric") == "room_count" and is_total_room_claim(row)
    ]
    room_values = sorted({round(float(row["value"]), 8) for row in room_rows})
    if len(room_values) > 1:
        findings.append(rules.finding(
            "HOTEL.ROOM_COUNT.CONFLICT",
            "P0",
            "酒店客房数存在未解释冲突",
            category="operating_assumption",
            expected="确认唯一口径，或逐项说明不同范围、主体和证据",
            actual={
                "values": room_values,
                "claims": [
                    {"value": row["value"], "location": row["location"]}
                    for row in room_rows
                ],
            },
            target_location={"target_id": target_id, "metric": "room_count", "scope": "total"},
            standard_basis=standard_basis,
            review_area="business",
            remediation="以经核验经营资料确认总客房口径，并同步修订全文及财务模型",
        ))

    area_anchors: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("building", re.compile(r"(?:总)?建筑面积|房屋建筑面积|产权面积|证载面积")),
        ("land", re.compile(r"(?:总)?占地(?:面积)?|用地面积|土地面积")),
        ("ancillary", re.compile(r"建设面积|经营面积|营业面积|使用面积")),
    )

    def area_scope(row: dict[str, Any]) -> str:
        if row.get("unit") != "㎡":
            return ""
        if row.get("source_kind") == "evidence":
            return str(row.get("evidence_scope") or "")
        line = source_line(row)
        start = int((row.get("location") or {}).get("char_offset_start") or 0)
        anchors: list[tuple[int, int, str]] = []
        for scope, pattern in area_anchors:
            anchors.extend(
                (match.start(), match.end(), scope) for match in pattern.finditer(line)
            )
        preceding = [anchor for anchor in anchors if anchor[1] <= start]
        if not preceding:
            return ""
        _anchor_start, anchor_end, scope = max(preceding, key=lambda item: item[0])
        if start - anchor_end > 96:
            return ""
        return "" if scope == "ancillary" else scope

    area_groups: dict[str, list[dict[str, Any]]] = {}
    for row in [*claims, *evidence_claims]:
        if row.get("metric") != "area":
            continue
        scope = area_scope(row)
        if scope:
            area_groups.setdefault(scope, []).append(row)
    for scope, area_rows in sorted(area_groups.items()):
        area_values = sorted({round(float(row["value"]), 8) for row in area_rows})
        if len(area_values) <= 1:
            continue
        findings.append(rules.finding(
            "HOTEL.AREA.CONFLICT",
            "P0",
            "酒店同一面积口径存在未解释冲突",
            category="rights_and_area",
            expected="同一面积口径应有唯一数值，或逐项说明范围、时点和证据",
            actual={
                "scope": scope,
                "values": area_values,
                "claims": [
                    {"value": row["value"], "location": row["location"]}
                    for row in area_rows
                ],
            },
            target_location={"target_id": target_id, "metric": "area", "scope": scope},
            standard_basis=standard_basis,
            review_area="legal",
            remediation="以权证或经核验测绘资料确认口径，并同步修订全文及财务模型",
        ))
    mode_patterns = {
        "lease": re.compile(r"纯出租|整体出租|全部出租|租赁经营"),
        "self_operated": re.compile(r"自营|自主经营|全经营"),
        "entrusted": re.compile(r"委托经营|委托管理"),
        "mixed": re.compile(r"混合经营|自营\s*[+＋与和]\s*出租|部分自营.*部分出租"),
    }
    modes = {name: [match.group(0) for match in pattern.finditer(content)] for name, pattern in mode_patterns.items()}
    active_modes = {name: values for name, values in modes.items() if values}
    if len(active_modes) > 1:
        findings.append(rules.finding(
            "HOTEL.OPERATING_MODEL",
            "P0",
            "酒店经营模式在正文中存在未裁决冲突",
            category="operating_assumption",
            expected="纯出租、自营、委托或混合经营采用唯一一致口径",
            actual=active_modes,
            target_location={"target_id": target_id, "text_anchor": "经营模式"},
            standard_basis=standard_basis,
            review_area="business",
            remediation="由业务负责人确认经营模式，并重算对应收入、成本、税费和现金流",
        ))
    owners = set()
    operators = set()
    for line in content.splitlines():
        companies = _COMPANY_PATTERN.findall(line)
        if re.search(r"权利人|产权人|所有权人|不动产权", line):
            owners.update(companies)
        if re.search(r"经营主体|许可主体|被许可人|酒店管理", line):
            operators.update(companies)
    if owners and operators and owners.isdisjoint(operators):
        findings.append(rules.finding(
            "HOTEL.RIGHTS.LICENSES",
            "P0",
            "资产权利人与经营许可主体不一致且未见合法衔接说明",
            category="rights_and_licenses",
            expected={"rights_owner_matches_or_authorizes_operator": True},
            actual={"rights_owners": sorted(owners), "licensed_operators": sorted(operators)},
            target_location={"target_id": target_id, "text_anchor": "权利人与许可主体"},
            standard_basis=standard_basis,
            review_area="legal",
            remediation="核验权证、经营许可、委托/租赁关系及主体授权链",
        ))
    if re.search(r"体育场馆用地|体育用地|运动员教练员之家", content) and re.search(r"酒店经营|住宿经营|客房", content):
        findings.append(rules.finding(
            "HOTEL.LAND_USE.COMPLIANCE",
            "P0",
            "体育用途土地或建筑用于酒店经营，缺少用途转换合规结论",
            category="land_use_compliance",
            expected="用途与酒店经营活动一致，或具备有效用途转换文件",
            actual="报告同时出现体育用途与酒店经营表述",
            target_location={"target_id": target_id, "text_anchor": "体育用途/酒店经营"},
            standard_basis=standard_basis,
            review_area="legal",
            remediation="补充规划、土地、消防及用途转换原件并由法务核验",
        ))
    parsed_as_of = _parse_datetime(review_as_of)
    for lease_name, pattern, candidate_owner_pattern in (
        ("酒吧/清吧", re.compile(r"酒吧|清吧"), re.compile(r"酒吧|清吧|\bbar\b|\bpub\b", re.I)),
        ("超市", re.compile(r"超市"), re.compile(r"超市|supermarket", re.I)),
        ("健身房", re.compile(r"健身房|健身中心"), re.compile(r"健身房|健身中心|fitness|\bgym\b", re.I)),
    ):
        mentions: list[dict[str, Any]] = []
        for line_number, line in enumerate(content.splitlines(), start=1):
            for scoped in _lease_scoped_texts(line, pattern):
                mentions.append({
                    "source_kind": "report",
                    "text": scoped,
                    "location": {
                        "target_id": target_id,
                        "line": line_number,
                        "text_anchor": scoped[:160],
                    },
                })
        for candidate in evidence_candidates:
            if not _formal_evidence_candidate(candidate):
                continue
            candidate_text = _candidate_text(candidate)
            if not candidate_text or not candidate_owner_pattern.search(
                _candidate_context(candidate)
            ):
                continue
            scoped_texts = _lease_scoped_texts(candidate_text, pattern)
            # OCR candidates are frequently one semantic field per block.  The
            # field/alias or source title owns the block even when the block text
            # itself does not repeat the lease name.
            if not scoped_texts:
                scoped_texts = [candidate_text]
            for scoped in scoped_texts:
                mentions.append({
                    "source_kind": "evidence",
                    "text": scoped,
                    "location": _candidate_location(candidate, target_id=target_id),
                })
        deduplicated: dict[str, dict[str, Any]] = {}
        for mention in mentions:
            key = sha256_json({
                "text": mention["text"],
                "location": mention["location"],
            })
            deduplicated[key] = mention
        mentions = list(deduplicated.values())
        if not mentions:
            continue
        amount_values = _distinct_money_values(
            value for mention in mentions for value in _money_values(mention["text"])
        )
        end_dates = [
            date for mention in mentions for date in _lease_end_dates(mention["text"])
        ]
        end_months = {(row["year"], row["month"]) for row in end_dates}
        report_flags = set().union(*(
            _lease_term_flags(row["text"])
            for row in mentions if row["source_kind"] == "report"
        )) if any(row["source_kind"] == "report" for row in mentions) else set()
        evidence_flags = set().union(*(
            _lease_term_flags(row["text"])
            for row in mentions if row["source_kind"] == "evidence"
        )) if any(row["source_kind"] == "evidence" for row in mentions) else set()
        missing_contract_terms = sorted(evidence_flags - report_flags)
        positive_renewal = any(
            re.search(r"已续租|续租至|已续约|续约至|签订续租|完成续约", row["text"])
            for row in mentions
        )
        expired = bool(
            parsed_as_of
            and end_dates
            and max(
                datetime(row["year"], row["month"], row["day"], tzinfo=timezone.utc)
                for row in end_dates
            ) < parsed_as_of
            and not positive_renewal
        )
        reasons: list[str] = []
        if len(amount_values) > 1:
            reasons.append("amount_conflict")
        if len(end_months) > 1:
            reasons.append("end_date_conflict")
        if expired:
            reasons.append("lease_expired_without_renewal")
        if missing_contract_terms:
            reasons.append("contract_terms_not_reflected_in_report")
        if not reasons:
            continue
        locations = [deepcopy(row["location"]) for row in mentions]
        findings.append(rules.finding(
            "HOTEL.LEASE.TERMS.CONFLICT",
            "P0",
            f"{lease_name}租约金额、期限或合同条款与报告假设不一致",
            category="contract_consistency",
            expected="合同主体、租赁物、金额、期限、递增和付款条款与全文及财务假设一致",
            actual={
                "reasons": reasons,
                "amounts_wan": amount_values,
                "end_dates": sorted({row["raw"] for row in end_dates}),
                "missing_contract_terms": missing_contract_terms,
                "claims": [
                    {
                        "source_kind": row["source_kind"],
                        "text": row["text"][:300],
                        "location": row["location"],
                    }
                    for row in mentions[:24]
                ],
            },
            target_location={
                "target_id": target_id,
                "lease": lease_name,
                "locations": locations[:24],
            },
            standard_basis=standard_basis,
            review_area="legal",
            remediation="以合同原件逐项核对租金、递增、付款、到期和终止条件，并同步重算现金流",
        ))
    return findings


def _evidence_has_term(candidates: list[dict[str, Any]], sources: list[dict[str, Any]], terms: tuple[str, ...]) -> bool:
    haystacks = [
        " ".join(str(row.get(key) or "") for key in ("field", "metric", "excerpt", "matched_alias"))
        for row in candidates if _formal_evidence_candidate(row)
    ]
    haystacks.extend(
        " ".join(str(row.get(key) or "") for key in ("title", "url"))
        for row in sources if _formal_evidence_source(row)
    )
    return any(any(term in text for term in terms) for text in haystacks)
