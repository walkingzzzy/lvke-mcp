"""Single source of truth for which quality codes are blocking.

`46a64b2` 把交付链各处的 `blockers` 统一硬编码成 `[]`，真实问题改名塞进
`quality_issues`。这条重构的正确部分是：证据待补、置信度不足这类问题不该
阻断"生成一份带限制说明的过程验收件"。错误的部分是它不加区分地把**所有**
问题都降级——包括规模对账不一致这种"继续算下去只会产出不可信数字"的问题。

于是这里只做一件事：给出唯一的判定入口，区分

- 阻断项（blocking）：基准本身不可信，继续生成会污染下游全部工件。
- 质量项（quality）：结果可用但置信度有限，必须随件披露限制。

判定按"码前缀 + 全码"两级匹配，避免各调用点各写一套 if。
"""

from __future__ import annotations

from typing import Iterable


#: 完整码即阻断。规模对账类问题一旦成立，投资额与行业量级不符，
#: 后续十三表、报告、审查全部建立在错误基准上。
BLOCKING_CODES: frozenset[str] = frozenset({
    # 规模基准不可信
    "project_scale_inconsistent",
    "input_revision_scale_drift",
    # 市场目标量单位与产能单位不相容（"亿元" vs "套/年"）。这不是置信度
    # 不足，而是口径非法：两个量纲不同的数不能相互校验，继续算下去产能与
    # 收入的对应关系整条是假的。此前该码未登记，落到"未知码默认不阻断"。
    "market_capacity_unit_mismatch",
    # 关键环节根本没产出对象，后续引用的是空基准
    "finance_run_persistence_failed",
    "finance_spec_prepare_failed",
    "finance_spec_confirm_failed",
    "finance_run_failed",
    # 证据口径本身不允许：重建来源缺记录、受控假设走正式发布、
    # 未认证项目事实做正式交付。这些不是"置信度低"，是口径非法。
    "reconstruction_records_missing",
    "reconstructed_source_ids_missing",
    "source_reconstructed_cannot_certify_project_fact",
    "controlled_assumption_formal_forbidden",
    "project_fact_evidence_missing",
    "preview_cannot_formal_release",
    "object_chain_not_verifiable_without_workspace",
    # 零材料交付：配置声明的必需组件没产出、manifest 缺失、配置 hash 算不出来。
    # 这三类都不是"置信度不足"——交付包本身不完整，而 manifest/hash 缺失还会让
    # 后续无法校验"这份正文出自哪份配置"。
    #
    # 财务勾稽不通同理：十三表与正文都建立在那份快照上，继续用只会把错误基准
    # 扩散到全部工件。
    "delivery_manifest_missing",
    "report_profile_hash_missing",
    "finance_run_consistency_failed",
    # 审查根本没跑起来 / 没给出可采信结论。缺席的结论不是通过的结论：
    # 判为质量项就等于"审查服务坏了"被读成"交付合格"（fail-open）。
    "review_process_acceptance_target_missing",
    "review_process_acceptance_review_id_missing",
    "review_technical_verdict_missing",
    # 过程验收可以缺阶段进度，但不能缺财务/报告/审查对象本身：
    # 否则 feasibility_release(process_acceptance) 会把
    # finance_run_object_required / report_revision_required /
    # review_run_required 降成"发布质量提示"，run.status 仍叫 released，
    # 调用方无法区分"过程验收留痕"与"正式交付完成"。
    "finance_spec_object_required",
    "finance_run_object_required",
    "finance_tables_package_required",
    "report_revision_required",
    "review_run_required",
})

#: 刻意不列为阻断项的码，连同原因——避免以后有人"顺手补全"又把闸门弄回去。
#:
#: ``project_fact_certification_required``：正式发布路径
#: (``feasibility_release`` scope=formal) 已经用它做专门的拒绝码并给出
#: 针对性 next_actions。若在 validate 层也判为阻断，``review_candidate``
#: 的过程校验会因为"尚未认证项目事实"直接 blocked，而这恰恰是
#: review_candidate 的正常状态——它本来就还没到认证那一步。
NON_BLOCKING_BY_DESIGN: frozenset[str] = frozenset({
    "project_fact_certification_required",
})

#: 阶段链结构未完成 —— 明确按质量项处理，不阻断。
#:
#: 产品决定：交付流程"还没走完"是置信度不足，不是基准不可信。允许在
#: 阶段链未完整时产出一份把全部缺口写进 release_limitations 的过程验收件，
#: 这正是过程验收存在的意义。与之相对，本模块 BLOCKING_* 里那些码是
#: "口径非法"——继续算下去只会产出不可信数字，必须停。
#:
#: 这些后缀覆盖 ``<stage>_pending`` / ``_output_refs_missing`` /
#: ``_basis_hash_missing`` / ``_input_refs_missing`` /
#: ``_output_kind_missing:<Kind>`` / ``_object_required`` 等结构完整性码。
NON_BLOCKING_SUFFIXES: tuple[str, ...] = (
    "_pending",
    "_output_refs_missing",
    "_input_refs_missing",
    "_basis_hash_missing",
    "_object_required",
    "_package_required",
    "_revision_required",
    "_run_required",
    "_blockers_present",
)

#: 结构完整性码里带 ``:`` 后缀的形态（``finance_spec_output_kind_missing:FinanceSpec``）。
NON_BLOCKING_INFIXES: tuple[str, ...] = (
    "_output_kind_missing:",
    "_parent_stage_binding_missing:",
    "stage_order_invalid:",
)

#: 前缀即阻断。这些码带字段/对象后缀（如
#: ``project_scale_inconsistent:route_length_km``、
#: ``reconstruction_record_invalid:0:missing_locator``）。
BLOCKING_PREFIXES: tuple[str, ...] = (
    "project_scale_inconsistent",
    "input_revision_scale_drift",
    "scale_reconciliation_failed",
    "reconstruction_record_invalid",
    "reconstruction_source_not_found",
    "reconstruction_source_hash_mismatch",
    "formal_evidence_policy_forbidden",
    "evidence_policy_mismatch",
    "source_reconstructed_fact_certification_forbidden",
    "reconstruction_records_not_propagated",
    "controlled_assumption_release_forbidden",
    "source_reconstructed_release_forbidden",
    "technical_fixture_release_forbidden",
    "formal_lineage",
    "formal_project_context",
    "formal_source",
    "formal_finance",
    "formal_tables",
    "formal_report",
    "formal_review",
    # 带字段/对象后缀的零材料交付完整性码：
    # ``required_component_missing:report_docx``、
    # ``delivery_lineage_missing:research_package_id``。
    #
    # 刻意判为阻断而不是限制项：方案要求"存在口径、hash、谱系或组件缺失"时
    # 进 failed/blocked。此前它们落到"未知码默认不阻断"，于是 DOCX 与 XLSX
    # 都没产出的运行照样报 passed_with_limitations 并走到 formal=eligible。
    "required_component_missing",
    "delivery_lineage_missing",
    # 带原因后缀的审查启动失败码：
    # ``review_process_acceptance_unavailable:OSError``、
    # ``review_process_acceptance_start_failed:<code>``、
    # ``review_suite_draft_failed:<code>``、``review_suite_confirm_failed:<code>``、
    # ``review_process_acceptance_prepare_failed:<code>``、
    # ``review_technical_verdict_not_pass:<verdict>``。
    #
    # 全部 fail-closed。刻意**不**含 ``review_quality_issue:``——那是审查真的跑完
    # 之后报出的质量提示，按质量项处理才对；也不含
    # ``review_suite_role_missing:`` —— 零材料必然缺 base_data，属结构性披露。
    "review_process_acceptance_unavailable",
    "review_process_acceptance_start_failed",
    "review_process_acceptance_prepare_failed",
    "review_suite_draft_failed",
    "review_suite_confirm_failed",
    "review_technical_verdict_not_pass",
    # 使 verdict 非 pass 的**残余**原因（已排除零材料结构性那三条）。
    # 与上面 ``review_technical_verdict_not_pass`` 成对出现，指名根因。
    "review_incomplete_reason",
)


def is_blocking(code: str) -> bool:
    """Return True when ``code`` must stop the chain instead of annotating it."""

    text = str(code or "").strip()
    if not text:
        return False
    if text in NON_BLOCKING_BY_DESIGN:
        return False
    # 显式命中的阻断码优先于结构后缀判定：例如
    # ``finance_run_object_required`` 按结构处理，但 ``finance_run_failed``
    # 是阻断——两者都以 finance_run 开头，顺序不能反。
    if text in BLOCKING_CODES:
        return True
    if text.startswith(BLOCKING_PREFIXES):
        return True
    # 默认不阻断：未知码按质量项处理，让交付继续但如实披露。把默认设成
    # 阻断会让任何新增诊断码意外掐断整条链。NON_BLOCKING_SUFFIXES /
    # NON_BLOCKING_INFIXES 保留为文档化的意图声明，说明结构完整性码
    # 是刻意走这条默认分支的，不是漏判。
    return False


def split_quality_codes(codes: Iterable[object]) -> tuple[list[str], list[str]]:
    """Split codes into ``(blockers, quality_issues)``, both sorted and deduped.

    调用点只需把手上全部问题码交给它，不要自己再判一遍严重性——那正是
    严重性判定在六处各写一套、最后集体退化成 ``[]`` 的成因。
    """

    blocking: set[str] = set()
    quality: set[str] = set()
    for item in codes:
        text = str(item or "").strip()
        if not text:
            continue
        quality.add(text)
        if is_blocking(text):
            blocking.add(text)
    return sorted(blocking), sorted(quality)


#: quality_status 的等级。fail > unclassified > warn > pass：
#: 一旦出现 material_conflict，即使还有未知码也不得显示为"通过"；
#: 未知码也不得显示为 pass（会漏报），只能停在 unclassified。
_QUALITY_RANK = {
    "pass": 0,
    "warn": 1,
    "unclassified": 2,
    "fail": 3,
}


def classify_quality(code: object) -> dict[str, bool | str]:
    """Classify one diagnostic code into the diagnostic-only envelope model.

    技术验收阶段的统一判定入口：不再用 `is_blocking()` 决定"整条链停不停"，
    而是把每个问题码归入四档 `quality_status` 并告诉调用方是否需要
    (a) 生成诊断对象、是否 (b) 构成数值可信度冲突。任何业务质量码都不触发
    下游计算停止；是否可用于正式研报由 `formal_report_allowed` 表达，
    与 `success` / `ready` 完全解耦。

    - ``fail``：影响数值可信度的口径/勾稽冲突（material conflict），必须
      固化 QualityDiagnostic 并保留冲突双方。
    - ``warn``：结果可用但置信度有限（证据待补、阶段未完成、可行性较差）。
    - ``unclassified``：未登记的规则码。按方案"不会因新规则未登记而停止
      技术诊断"，自动标记人工确认，不得显示为质量通过。
    - ``pass``：无问题（调用方在无问题时直接给 pass，本函数只分类问题码）。
    """

    text = str(code or "").strip()
    if not text:
        return {
            "quality_status": "unclassified",
            "diagnostic_required": True,
            "material_conflict": False,
            "formal_report_allowed": False,
        }
    if text in NON_BLOCKING_BY_DESIGN:
        return {
            "quality_status": "warn",
            "diagnostic_required": True,
            "material_conflict": False,
            "formal_report_allowed": False,
        }
    # 显式命中的阻断码优先于结构后缀判定（与 is_blocking 同序）。
    if text in BLOCKING_CODES or text.startswith(BLOCKING_PREFIXES):
        return {
            "quality_status": "fail",
            "diagnostic_required": True,
            "material_conflict": True,
            "formal_report_allowed": False,
        }
    # 结构完整性码（阶段未完成、对象缺失等）：置信度不足而非口径冲突。
    if text.startswith(NON_BLOCKING_INFIXES) or text.endswith(NON_BLOCKING_SUFFIXES):
        return {
            "quality_status": "warn",
            "diagnostic_required": True,
            "material_conflict": False,
            "formal_report_allowed": False,
        }
    return {
        "quality_status": "unclassified",
        "diagnostic_required": True,
        "material_conflict": False,
        "formal_report_allowed": False,
    }


def aggregate_quality_status(codes: Iterable[object]) -> str:
    """Return the worst ``quality_status`` across a set of diagnostic codes.

    等级：fail > unclassified > warn > pass。未知码会把整体停在
    ``unclassified``，避免"混着未知码却显示质量通过"。
    """

    worst = "pass"
    for item in codes:
        text = str(item or "").strip()
        if not text:
            continue
        status = str(classify_quality(text)["quality_status"] or "unclassified")
        if _QUALITY_RANK.get(status, 0) > _QUALITY_RANK.get(worst, 0):
            worst = status
    return worst
