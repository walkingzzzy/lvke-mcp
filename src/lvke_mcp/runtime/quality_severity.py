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
