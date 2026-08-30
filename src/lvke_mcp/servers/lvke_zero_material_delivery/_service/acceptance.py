"""Graded acceptance: automatic technical, human per-domain internal, formal gate.

三段状态的职责边界是本模块存在的唯一理由，写清楚以免以后被"顺手统一"：

``technical``
    系统自动执行。复用 deliverable-review 的 ``process_acceptance`` 七域链与
    feasibility 的 ``technical`` 校验，再叠加零材料自己的组件齐备/hash/谱系检查。
    全部由确定性规则得出，**不含**任何人工判断。

``internal``
    人工按领域确认，读的是 review 域已有的 ``review_confirm_dimension`` 记录。
    本模块只做聚合与门禁，**绝不**代替责任人提交确认——那正是"把系统自动检查
    伪装成人工签章"。因此这里只 *读* dimension 结果，从不写 Assessment。

``formal``
    资格状态，不是动作。``eligible`` 仅表示门禁已过，真正的 Promotion 由
    ``promotion.confirm_formal_promotion`` 执行；失败时 ``blocked`` 而内部验收
    状态保留——只复制标签放行是这条链最容易出的错。

    **与方案的已知偏差（经确认保留）。** 方案原文要求"内部各领域确认齐全后，
    系统自动执行 Promotion 流程"。此处实现为七域齐全 → ``eligible``，仍需显式
    调用晋升工具。两条理由：

    1. 晋升把受控假设产物转成 ``sim_a_formal`` 正式证据并落盘 SourceFile，是
       难以回退的对外动作，不应由"最后一个领域确认落库"这个副作用触发；
    2. ``responsible_party`` 与 ``confirmation_note`` 是晋升的必填责任声明，
       系统代填就等于替人签署——那与本模块开头声明的边界直接冲突。

    门禁强度不因此降低：``confirm_formal_promotion`` 仍会重读两段验收状态，
    未通过即拒绝（``formal_promotion_acceptance_required``）。

为什么内部验收走 ``review_mode="external"``：``review_prepare`` 在
``review_mode="internal"`` 下会把证据轨强制改写成 ``sim_a_formal`` 并要求
promotion 谱系已存在（``_service/preparation.py:130-149``），而我们的内部验收
**发生在 Promotion 之前**，那条路必然失败关闭。external + process_acceptance
能跑完整的七域 Assessment 与领域确认；代价是 review 域的 release_verdict 恒含
``external_review_release_forbidden``。因此零材料域**不读 review 的
release_verdict**，只读逐维度状态与**确认记录是否存在**（``confirmation_id``），
另加本域门禁。这不是绕过：review 拒绝的是"由 external 套件直接发布"，而我们要
的判据是"七域审查是否通过"，两者是不同的问题。

判据刻意不用 review 的 ``role_confirmed``：它在 quick profile 下退化成"有
Assessment 即为已确认"（``suite_review._dimension_results`` 的 require_semantic
分支），用它就等于把系统自动检查当成人工签字。
"""

from __future__ import annotations

from typing import Any

from lvke_mcp.runtime.quality_severity import split_quality_codes

#: 与 spec 一致的三段状态枚举。schema 与本表必须同源，避免枚举漂移。
TECHNICAL_STATUSES = (
    "not_started",
    "in_progress",
    "passed",
    "passed_with_limitations",
    "failed",
    "blocked",
)
INTERNAL_STATUSES = (
    "not_started",
    "pending",
    "in_progress",
    "passed",
    "passed_with_limitations",
    "blocked",
)
FORMAL_STATUSES = ("blocked", "eligible", "promoted", "project_delivery")

#: 七域顺序与 ``deliverable_review.contracts.REVIEW_DIMENSIONS`` 一致。
REQUIRED_DIMENSIONS: tuple[str, ...] = (
    "compliance",
    "article_quality",
    "data_quality",
    "source_quality",
    "financial_model",
    "financial_tables",
    "feasibility",
)

#: 技术验收按领域给结果，覆盖 spec 要求的五个面。
TECHNICAL_DOMAINS: tuple[str, ...] = (
    "report_structure",
    "research_evidence",
    "finance_model",
    "finance_tables",
    "delivery_lineage",
)


def _empty_technical() -> dict[str, Any]:
    return {
        "status": "not_started",
        "review_preparation_id": "",
        "review_id": "",
        "review_package_id": "",
        "feasibility_validation_id": "",
        "domain_results": [],
        "blockers": [],
        "limitations": [],
    }


def _empty_internal() -> dict[str, Any]:
    return {
        "status": "not_started",
        "review_id": "",
        "domain_confirmations": [],
        # 字段名刻意不叫 ``confirmed_by``：那读起来像身份归属，而本产品没有身份、
        # 资质或签名层（``scripts/independence_scan.py`` 的 forbidden_semantics
        # 会据此判 non_conforming）。这里存的是责任声明**文本**，不是已验证身份。
        "role_declarations": [],
        "latest_confirmation_at": "",
        "missing_dimensions": list(REQUIRED_DIMENSIONS),
        "blockers": [],
        "limitations": [],
    }


def _empty_formal() -> dict[str, Any]:
    return {"status": "blocked", "promotion_id": "", "blockers": [], "limitations": []}


def empty_acceptance() -> dict[str, Any]:
    """Return the acceptance object for a run that has not been verified yet."""

    return {
        "technical": _empty_technical(),
        "internal": _empty_internal(),
        "formal": _empty_formal(),
    }


def _domain_result(
    domain: str,
    *,
    codes: list[str],
    checked: list[str],
) -> dict[str, Any]:
    blocking, quality = split_quality_codes(codes)
    if blocking:
        status = "failed"
    elif quality:
        status = "passed_with_limitations"
    else:
        status = "passed"
    return {
        "domain": domain,
        "status": status,
        "blockers": blocking,
        "limitations": [item for item in quality if item not in set(blocking)],
        "checked": sorted(set(checked)),
    }


def build_technical_domain_results(
    *,
    component_status: dict[str, Any],
    unresolved_slots: list[str],
    research: dict[str, Any],
    finance: dict[str, Any],
    tables: dict[str, Any],
    lineage: dict[str, Any],
    profile_selection: dict[str, Any],
) -> list[dict[str, Any]]:
    """Derive per-domain technical results from deterministic evidence only.

    每个域的判据都指向一个可复算的事实：组件在不在、hash 对不对、槽位有没有解析到、
    勾稽通不通。没有"看起来还行"这类判断。
    """

    missing_components = [
        f"required_component_missing:{name}"
        for name, present in sorted(component_status.items())
        if not present
    ]
    slot_codes = [f"report_slot_unresolved:{name}" for name in sorted(set(unresolved_slots))]
    profile_codes = (
        []
        if str(profile_selection.get("profile_content_hash") or "")
        else ["report_profile_hash_missing"]
    )
    research_codes = (
        []
        if str(research.get("research_package_id") or "")
        else ["research_evidence_pending"]
    )
    if research.get("fallback_used"):
        research_codes.append("zero_material_public_search_fallback")
    finance_codes: list[str] = []
    if not finance.get("run_id"):
        finance_codes.append("finance_run_failed")
    elif finance.get("consistency_ok") is not True:
        # 勾稽不通不是"置信度低"：十三表与正文都建立在这份快照上。
        finance_codes.append("finance_run_consistency_failed")
    tables_codes = [
        *([] if tables.get("finance_tables_package_id") else ["finance_tables_render_failed"]),
        *([] if tables.get("csv_ok") else ["finance_tables_csv_export_failed"]),
        *([] if tables.get("xlsx_ok") else ["finance_tables_xlsx_export_failed"]),
    ]
    # 证据类引用为空**不是**谱系断裂：公开检索没抓到可固化来源时
    # research_package_id / evidence_pack_id 本来就是空的，那属于"证据待补"，
    # 已由 research_evidence_pending 覆盖。把它也算成谱系缺失会双重计数，
    # 并把一个正常的零材料运行判成失败。
    # 谱系检查只管**本该存在**的结构性引用。
    evidence_refs = {
        "research_package_id",
        "evidence_pack_id",
        "research_task_id",
        "csv_manifest_id",
    }
    lineage_codes = [
        f"delivery_lineage_missing:{name}"
        for name, present in sorted(dict(lineage.get("object_refs") or {}).items())
        if not present and name not in evidence_refs
    ]
    if lineage.get("manifest_uri_present") is False:
        lineage_codes.append("delivery_manifest_missing")

    return [
        _domain_result(
            "report_structure",
            codes=[*missing_components, *slot_codes, *profile_codes],
            checked=["required_components", "configured_slots", "profile_hash"],
        ),
        _domain_result(
            "research_evidence",
            codes=research_codes,
            checked=["research_package", "public_source_snapshots"],
        ),
        _domain_result(
            "finance_model",
            codes=finance_codes,
            checked=["finance_run", "consistency_ok"],
        ),
        _domain_result(
            "finance_tables",
            codes=tables_codes,
            checked=["thirteen_tables", "csv_export", "xlsx_export"],
        ),
        _domain_result(
            "delivery_lineage",
            codes=lineage_codes,
            checked=["object_refs", "run_manifest"],
        ),
    ]


def fold_technical(
    domain_results: list[dict[str, Any]],
    *,
    extra_blockers: list[str] | None = None,
    extra_limitations: list[str] | None = None,
    review_preparation_id: str = "",
    review_id: str = "",
    review_package_id: str = "",
    feasibility_validation_id: str = "",
) -> dict[str, Any]:
    """Fold per-domain results into one honest technical status."""

    codes = [
        *(extra_blockers or []),
        *(extra_limitations or []),
    ]
    for row in domain_results:
        codes.extend(str(item) for item in row.get("blockers") or [])
        codes.extend(str(item) for item in row.get("limitations") or [])
    blocking, quality = split_quality_codes(codes)
    limitations = [item for item in quality if item not in set(blocking)]
    if blocking:
        status = "blocked" if any(
            str(row.get("status") or "") == "failed" for row in domain_results
        ) else "failed"
    elif limitations:
        status = "passed_with_limitations"
    else:
        status = "passed"
    return {
        "status": status,
        "review_preparation_id": review_preparation_id,
        "review_id": review_id,
        "review_package_id": review_package_id,
        "feasibility_validation_id": feasibility_validation_id,
        "domain_results": domain_results,
        "blockers": blocking,
        "limitations": limitations,
    }


#: 零材料结构性缺项：客户方零资料，套件里**必然**没有 base_data 角色
#: （``internal_component`` 的角色映射也没有任何内部对象类型落到 base_data）。
#: review 因此把 compliance 恒判 ``incomplete``——那个判断是诚实的：没有基础
#: 资料就不能声称完整套件合规。
#:
#: 内部验收对这一项按"置信度不足"处理而不是"口径非法"：它不是可修复的缺陷，
#: 而是零材料路线的定义本身，且必须**始终**留在 limitations 里随件披露。
#: 刻意只列这一条并按精确码匹配——放宽成前缀或整类 incomplete 就等于把
#: "审查没跑完"也一起放行了。
STRUCTURAL_INCOMPLETE_REASONS: frozenset[str] = frozenset({
    "review_package_role_missing:base_data",
})


def fold_internal(
    *,
    technical_status: str,
    dimension_results: list[dict[str, Any]],
    review_id: str = "",
    dossier_verdict: str = "",
    inherited_limitations: list[str] | None = None,
) -> dict[str, Any]:
    """Aggregate human per-domain confirmations; never synthesize one.

    ``dimension_results`` 来自 review 域的 ``review_finalize`` / ``review_get_dimension``。
    只有 ``role_confirmed=True`` 才算责任人确认过——``status=passed`` 但未确认的维度
    仍然记为缺失，因为那是系统检查通过而不是人工验收通过。
    """

    result = _empty_internal()
    result["review_id"] = review_id
    inherited = sorted({str(item) for item in (inherited_limitations or []) if str(item)})
    if technical_status not in {"passed", "passed_with_limitations"}:
        # 技术验收未通过时内部验收不能通过，且不得因为"领域都确认了"而放行。
        result["status"] = "blocked"
        result["blockers"] = [f"technical_acceptance_not_passed:{technical_status}"]
        result["limitations"] = inherited
        return result

    confirmed: list[dict[str, Any]] = []
    missing: list[str] = []
    blockers: list[str] = []
    accepted_limitations: set[str] = set()
    by_dimension = {
        str(row.get("dimension") or ""): row
        for row in dimension_results
        if isinstance(row, dict)
    }
    for dimension in REQUIRED_DIMENSIONS:
        row = by_dimension.get(dimension)
        # 判据是**确认记录真的存在**（confirmation_id 非空），不是 role_confirmed。
        # review 域的 role_confirmed 在 quick profile 下退化成"有 Assessment 即为
        # 已确认"（suite_review.py:1083 的 require_semantic 分支）——那是系统自动
        # 检查通过，不是责任人签过字。用它做内部验收判据就等于把自动检查伪装成
        # 人工验收，正是本模块开头声明要避免的那件事。
        if row is None or not str(row.get("confirmation_id") or ""):
            missing.append(dimension)
            continue
        status = str(row.get("status") or "")
        reasons = {str(item) for item in row.get("incomplete_reasons") or [] if str(item)}
        # 必须是"**全部**原因都是结构性缺项"才降级。取交集非空就降级会让
        # ["review_package_role_missing:base_data", "standards_snapshot_unavailable"]
        # 这种组合把真实问题一起放过——一条真原因足以让整个维度继续阻断。
        structurally_only = bool(reasons) and reasons <= STRUCTURAL_INCOMPLETE_REASONS
        if status in {"failed"}:
            blockers.append(f"review_dimension_failed:{dimension}")
        elif status in {"incomplete", "not_determinable"}:
            if structurally_only and status == "incomplete":
                accepted_limitations.update(
                    f"review_dimension_structurally_incomplete:{dimension}:{item}"
                    for item in sorted(reasons)
                )
            else:
                blockers.append(f"review_dimension_{status}:{dimension}")
        confirmed.append(
            {
                "dimension": dimension,
                "status": status,
                "role_declaration": str(row.get("role_declaration") or ""),
                "review_statement": str(row.get("review_statement") or ""),
                "limitations_accepted": [
                    str(item) for item in row.get("limitations_accepted") or []
                ],
                "dimension_confirmation_id": str(
                    row.get("dimension_confirmation_id") or row.get("confirmation_id") or ""
                ),
                "confirmed_at": str(row.get("confirmed_at") or ""),
                # 确认是审查责任声明，不是身份/资质/电子签名认证。
                "identity_or_credential_verified": False,
            }
        )
        accepted_limitations.update(
            str(item) for item in row.get("limitations_accepted") or [] if str(item)
        )

    result["domain_confirmations"] = confirmed
    result["missing_dimensions"] = missing
    result["role_declarations"] = sorted(
        {str(row.get("role_declaration") or "") for row in confirmed if row.get("role_declaration")}
    )
    result["latest_confirmation_at"] = max(
        (str(row.get("confirmed_at") or "") for row in confirmed),
        default="",
    )
    if dossier_verdict and dossier_verdict != "pass":
        blockers.append(f"review_suite_verdict_not_pass:{dossier_verdict}")
    if missing:
        blockers.append("internal_acceptance_dimensions_incomplete")
    result["blockers"] = sorted(set(blockers))
    # 限制项由技术验收继承，内部确认只能"接受"限制，不能清除它。
    result["limitations"] = sorted(set(inherited) | accepted_limitations)
    if result["blockers"]:
        result["status"] = "pending" if missing and not any(
            item.startswith("review_dimension_") for item in result["blockers"]
        ) else "blocked"
    elif result["limitations"]:
        result["status"] = "passed_with_limitations"
    else:
        result["status"] = "passed"
    return result


#: 关键必填字段未回答的限制码前缀（由 ``questions.summarize_gaps`` 产出）。
#: 它必须在正式资格上**阻断**，而不是只作为限制项披露：方案明确要求"关键字段
#: 未回答时允许生成技术预览，但不得因此直接获得正式资格"。
#: 只登记不阻断会让五个关键字段全空的运行照样走到 eligible。
REQUIRED_FIELD_UNANSWERED_PREFIX = "required_field_unanswered:"


def fold_formal(
    *,
    technical: dict[str, Any],
    internal: dict[str, Any],
    promotion_id: str = "",
    promotion_blockers: list[str] | None = None,
    project_delivery_released: bool = False,
) -> dict[str, Any]:
    """Compute formal eligibility; a label is never a substitute for the gate."""

    result = _empty_formal()
    result["limitations"] = sorted(
        {
            *(str(item) for item in technical.get("limitations") or []),
            *(str(item) for item in internal.get("limitations") or []),
        }
    )
    blockers: list[str] = [str(item) for item in (promotion_blockers or []) if str(item)]
    # 关键字段未答：技术预览可用，正式资格必须阻断。逐条列出字段名，
    # 让调用方直接知道要补哪几个，而不是只收到一个笼统的"资格不足"。
    blockers.extend(
        sorted(
            item
            for item in result["limitations"]
            if item.startswith(REQUIRED_FIELD_UNANSWERED_PREFIX)
        )
    )
    technical_status = str(technical.get("status") or "not_started")
    internal_status = str(internal.get("status") or "not_started")
    if technical_status not in {"passed", "passed_with_limitations"}:
        blockers.append(f"technical_acceptance_not_passed:{technical_status}")
    if internal_status not in {"passed", "passed_with_limitations"}:
        blockers.append(f"internal_acceptance_not_passed:{internal_status}")
    result["blockers"] = sorted(set(blockers))
    result["promotion_id"] = promotion_id
    if result["blockers"]:
        # Promotion 失败或门禁未过时，内部验收状态保留但正式资格阻断。
        result["status"] = "blocked"
    elif project_delivery_released:
        result["status"] = "project_delivery"
    elif promotion_id:
        result["status"] = "promoted"
    else:
        result["status"] = "eligible"
    return result


def dimension_rows_from_review(
    dimension_results: list[dict[str, Any]],
    confirmations: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Join review dimension results with their confirmation payloads."""

    rows: list[dict[str, Any]] = []
    for row in dimension_results:
        if not isinstance(row, dict):
            continue
        dimension = str(row.get("dimension") or "")
        confirmation = dict(confirmations.get(dimension) or {})
        rows.append(
            {
                **row,
                "role_declaration": str(confirmation.get("role_declaration") or ""),
                "review_statement": str(confirmation.get("review_statement") or ""),
                "limitations_accepted": [
                    str(item) for item in confirmation.get("limitations_accepted") or []
                ],
                "dimension_confirmation_id": str(
                    confirmation.get("dimension_confirmation_id") or ""
                ),
                "confirmed_at": str(confirmation.get("confirmed_at") or ""),
            }
        )
    return rows


__all__ = [
    "FORMAL_STATUSES",
    "INTERNAL_STATUSES",
    "REQUIRED_DIMENSIONS",
    "TECHNICAL_DOMAINS",
    "TECHNICAL_STATUSES",
    "build_technical_domain_results",
    "dimension_rows_from_review",
    "empty_acceptance",
    "fold_formal",
    "fold_internal",
    "fold_technical",
]
