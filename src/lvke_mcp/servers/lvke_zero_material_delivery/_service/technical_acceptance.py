"""Run the automatic technical acceptance over one zero-material delivery run.

技术验收 = 零材料自己的确定性组件/槽位/谱系检查 **加上** 两条既有链路：

1. deliverable-review 的 ``process_acceptance``：先用本运行的不可变对象组建
   ReviewPackage（``review_package_prepare`` → ``review_package_confirm``），
   再 ``review_prepare`` → ``review_start``。审查目标必须是 ``review_package``
   ——七域 Assessment、领域确认与 finalize 都只接受这个 target_type；直接以
   ``report_artifact`` 为目标只能跑确定性检查，拿不到七域链。预览报告本身作为
   ``report`` 角色组件进套件，走 review 域为零材料预留的
   ``artifact_domain="zero_material_preview"`` 解析路径。
2. feasibility 的 ``technical`` 校验，**仅当**本运行已绑定 ``fdr_*`` 可研交付运行。

两条链的结果只作为**补充证据**并入技术验收，不覆盖零材料自己的判据：review 的
``release_verdict`` 在 external 模式下恒含 ``external_review_release_forbidden``，
把它当阻断项会让技术验收永远不可能通过——那是"禁止 external 套件直接发布"，
与"技术链是否自洽"是两个问题。

``feasibility_validation_id`` 在预览阶段恒为空 —— 这是**已确认的设计**，不是漏项
=======================================================================

零材料预览链不创建 ``fdr_*``：可研交付运行由晋升之后的
``project_context_create`` → ``feasibility_start`` 建立（见
``promotion.confirm_formal_promotion`` 的 next_actions）。因此预览阶段没有可校验
的可研交付对象，本字段留空而**不**伪造一个。

刻意不为了让这条校验"看起来跑过"而在预览阶段建 ``fdr_*``：那个对象永远不会走到
release，等于在谱系里留一个永久半成品，并让"这条链到哪一步了"变得不可读。

预览阶段的技术验收覆盖面由另外两者保证：review 的七域 ``process_acceptance``，
以及本域五个确定性域（正文结构、研究证据、财务模型、十三表、交付谱系）。
feasibility 的 ``technical`` 校验在晋升后的正式链上生效——那时 ``fdr_*`` 已存在，
``_feasibility_technical`` 会真正调用它。

读到本字段为空时，正确结论是"当前处于预览阶段"，不是"校验被跳过了"。
"""

from __future__ import annotations

import hashlib
from typing import Any

from .acceptance import build_technical_domain_results, fold_technical


def _lineage_inputs(
    domain: dict[str, Any],
    delivery_artifacts: dict[str, Any],
) -> dict[str, Any]:
    refs = {
        str(key): bool(value)
        for key, value in dict(domain.get("object_refs") or {}).items()
    }
    refs.update(
        {
            str(key): bool(value)
            for key, value in dict(delivery_artifacts.get("object_refs") or {}).items()
        }
    )
    return {
        "object_refs": refs,
        "manifest_uri_present": bool(delivery_artifacts.get("manifest_uri")),
    }


#: 零材料 external 过程验收下**必然**出现、且不代表交付缺陷的 incomplete 原因。
#:
#: 三条都是路线的结构性事实而非可修复问题：客户零资料所以套件缺 base_data；
#: external 模式恒禁止直接发布（正是我们刻意选它的原因）；内部验收尚未开始所以
#: 套件本该未 finalize。
#:
#: 刻意按**精确码**列举而不用前缀匹配：放宽成前缀就会把"审查真的没跑完"一起
#: 放过，而那必须阻断。新增条目前先确认它真的是"零材料必然如此"，不是"这次恰好
#: 出现"。
_STRUCTURAL_REVIEW_INCOMPLETE_REASONS: frozenset[str] = frozenset({
    "review_package_role_missing:base_data",
    "external_review_release_forbidden",
    "review_suite_not_finalized",
})


#: 内部目标 → 套件角色。与 ``_service/suite_package.internal_component`` 的映射
#: 一致；``base_data`` 在该映射里无对应内部对象类型，因此零材料套件必然
#: ``full_suite=False``。这不影响七域 Assessment 与确认（它们只要求套件存在），
#: 只意味着不能声称"完整研报套件"——如实标注为限制项，不伪装成完整套件。
_TARGET_ROLES: dict[str, str] = {
    "report_artifact": "report",
    "evidence_pack": "source_evidence",
    "research_package": "source_evidence",
    "finance_run": "finance_model",
    "acquisition_run": "finance_model",
    "finance_tables_package": "finance_tables",
    "acquisition_tables_package": "finance_tables",
}


def _suite_targets(
    *,
    technical_report_id: str,
    domain_refs: dict[str, Any],
    public_research: dict[str, Any],
) -> list[dict[str, Any]]:
    """Collect the immutable objects that make up this run's review suite."""

    targets: list[dict[str, Any]] = []
    if technical_report_id:
        targets.append(
            {
                "target_type": "report_artifact",
                "target_id": technical_report_id,
                "artifact_domain": "zero_material_preview",
            }
        )
    for key, target_type in (
        ("finance_run_id", "finance_run"),
        ("acquisition_run_id", "acquisition_run"),
        ("finance_tables_package_id", "finance_tables_package"),
        ("acquisition_tables_package_id", "acquisition_tables_package"),
        ("evidence_pack_id", "evidence_pack"),
    ):
        target_id = str(domain_refs.get(key) or "")
        if target_id:
            targets.append({"target_type": target_type, "target_id": target_id})
    research_package_id = str(public_research.get("research_package_id") or "")
    if research_package_id:
        targets.append(
            {"target_type": "research_package", "target_id": research_package_id}
        )
    return targets


def _review_process_acceptance(
    workspace_id: str,
    *,
    technical_report_id: str,
    domain_refs: dict[str, Any],
    public_research: dict[str, Any],
    industry_code: str,
    project_type: str,
    region: str,
    lineage_key: str,
) -> dict[str, Any]:
    """Build the review suite and run review's ``process_acceptance`` over it.

    走 ``review_mode="external"``：``internal`` 会把证据轨强制改写成 sim_a_formal
    并要求 promotion 谱系已存在，而内部验收发生在 Promotion **之前**。
    profile 固定 ``standard``：``quick`` 下 review 的 ``role_confirmed`` 会退化成
    "有 Assessment 即已确认"，那会让内部验收失去人工确认这一层。

    失败不阻断技术验收：review 域不可用或目标暂不可解析时，如实记录诊断码作为
    限制项。把"审查服务本身没跑起来"当成"交付不合格"会掩盖真正的交付问题。
    """

    from lvke_mcp.runtime import service_gateway

    result: dict[str, Any] = {
        "review_preparation_id": "",
        "review_id": "",
        "review_package_id": "",
        "codes": [],
    }
    targets = _suite_targets(
        technical_report_id=technical_report_id,
        domain_refs=domain_refs,
        public_research=public_research,
    )
    if not technical_report_id:
        result["codes"].append("review_process_acceptance_target_missing")
        return result
    try:
        draft = service_gateway.review_prepare_package(
            {
                "workspace_id": workspace_id,
                "idempotency_key": f"zmd-suite-draft-{lineage_key}",
                "review_mode": "external",
                "review_profile": "standard",
                # region 与 project_type 都必须给全：缺任一项，套件的
                # COMP.REQUIREMENT.COVERAGE 会判"无法冻结适用要求范围"并留下
                # 一条 P1，那是调用方漏传参数，不是交付缺陷。
                "project_scope": {
                    "industry_code": industry_code or "general",
                    "project_type": project_type,
                    "region": region or "待确认",
                },
                "internal_targets": targets,
            }
        )
        draft_id = str(draft.get("review_package_draft_id") or "")
        if not draft_id:
            result["codes"].append(
                f"review_suite_draft_failed:{draft.get('code') or 'unknown'}"
            )
            return result
        components = [
            item for item in draft.get("components") or [] if isinstance(item, dict)
        ]
        confirmed = service_gateway.review_confirm_package(
            {
                "workspace_id": workspace_id,
                "idempotency_key": f"zmd-suite-confirm-{lineage_key}",
                "review_package_draft_id": draft_id,
                "expected_draft_hash": str(draft.get("draft_hash") or ""),
                "component_roles": [
                    {
                        "component_id": str(item.get("component_id") or ""),
                        "role": _TARGET_ROLES.get(
                            str(item.get("component_type") or ""),
                            str(item.get("suggested_role") or "attachment"),
                        ),
                    }
                    for item in components
                ],
                "confirmation_statement": (
                    "零材料技术预览套件；组件角色按不可变对象类型确定性映射，"
                    "缺 base_data 角色（内部对象无对应类型），不构成完整研报套件"
                ),
            }
        )
        package_id = str(confirmed.get("review_package_id") or "")
        if not package_id:
            result["codes"].append(
                f"review_suite_confirm_failed:{confirmed.get('code') or 'unknown'}"
            )
            return result
        result["review_package_id"] = package_id
        result["codes"].extend(
            f"review_suite_role_missing:{item}"
            for item in confirmed.get("missing_required_roles") or []
        )
        prepared = service_gateway.review_prepare(
            {
                "workspace_id": workspace_id,
                "idempotency_key": f"zmd-tech-review-prep-{lineage_key}",
                "target": {
                    "target_type": "review_package",
                    "target_id": package_id,
                },
                "review_profile": "standard",
                "review_mode": "external",
                "project_context": {
                    "industry_code": industry_code or "general",
                    "project_type": project_type,
                    "evidence_track": "controlled_assumption",
                    # 过程验收：只判技术链，不叠加正式发布资格。
                    "review_purpose": "process_acceptance",
                    "release_scope": "process_acceptance",
                },
            }
        )
        preparation_id = str(prepared.get("review_preparation_id") or "")
        result["review_preparation_id"] = preparation_id
        if not preparation_id:
            result["codes"].append(
                f"review_process_acceptance_prepare_failed:{prepared.get('code') or 'unknown'}"
            )
            return result
        started = service_gateway.review_start(
            {
                "workspace_id": workspace_id,
                "idempotency_key": f"zmd-tech-review-start-{lineage_key}",
                "review_preparation_id": preparation_id,
                "mode": "standard",
                "execution": "sync",
            }
        )
    except (ValueError, RuntimeError, OSError) as exc:
        result["codes"].append(f"review_process_acceptance_unavailable:{type(exc).__name__}")
        return result
    result["review_id"] = str(started.get("review_id") or "")
    # fail-closed：审查没真正跑起来，就不能当成"没发现问题"。
    #
    # 此前只在 technical_verdict 非空且不为 pass 时才记问题，于是三种情形都被
    # 当成通过：失败信封、review_id 为空、verdict 缺失。缺席的结论不是通过的
    # 结论——这正是"审查服务没跑起来"被读成"交付合格"的 fail-open。
    #
    # 注意与 except 分支的分工：那里处理"审查域不可用"（进程级异常），这里处理
    # "审查域答复了但没给出可采信结论"。两者都必须留痕，且都不得静默放过。
    if not started.get("success"):
        result["codes"].append(
            f"review_process_acceptance_start_failed:{started.get('code') or 'unknown'}"
        )
    if not result["review_id"]:
        result["codes"].append("review_process_acceptance_review_id_missing")
    technical_verdict = str(started.get("technical_verdict") or "")
    if not technical_verdict:
        result["codes"].append("review_technical_verdict_missing")
    elif technical_verdict != "pass":
        # verdict 非 pass 要区分根因，不能整体判死。
        #
        # review 的 technical_verdict 由 incomplete_reasons 推出，而零材料 external
        # 过程验收下这三条**必然**出现：
        #   review_package_role_missing:base_data —— 客户零资料，套件必然缺该角色
        #   external_review_release_forbidden    —— external 模式恒含，正是本路线
        #   review_suite_not_finalized           —— 内部验收尚未开始，此刻本该未 finalize
        # 它们是零材料路线的结构性事实，本模块别处已按结构性披露处理；在 verdict
        # 层面又整体判死，就会让每一条正常的零材料链都进 failed。
        #
        # 因此：**全部**原因都是结构性的 → 记结构性披露；出现任何其它原因 →
        # 阻断。判据与 acceptance.STRUCTURAL_* 同一思路：一条真原因足以阻断。
        reasons = {
            str(item)
            for item in started.get("quality_issues") or []
            if str(item)
        }
        residual = sorted(reasons - _STRUCTURAL_REVIEW_INCOMPLETE_REASONS)
        if reasons and not residual:
            result["codes"].append(
                f"review_technical_verdict_structurally_incomplete:{technical_verdict}"
            )
        else:
            result["codes"].append(
                f"review_technical_verdict_not_pass:{technical_verdict}"
            )
            result["codes"].extend(
                f"review_incomplete_reason:{item}" for item in residual
            )
    result["codes"].extend(
        f"review_quality_issue:{item}"
        for item in started.get("quality_issues") or []
        if str(item)
    )
    return result


def _feasibility_technical(
    workspace_id: str,
    feasibility_run_id: str,
) -> list[str]:
    """Fold feasibility's technical validation into acceptance codes.

    空 ``feasibility_run_id`` 返回空列表而不是阻断码：预览阶段本来就没有 ``fdr_*``
    （见模块文档）。这里刻意**不**补一个 ``feasibility_validation_pending`` 之类的
    限制项——那会让每一条正常预览都带一条无从消除的提示，噪声盖掉真问题。

    晋升后的正式链上 ``fdr_*`` 存在，这条校验才真正执行。
    """

    if not feasibility_run_id:
        return []
    from lvke_mcp.runtime import service_gateway

    try:
        validated = service_gateway.feasibility_validate(
            {
                "workspace_id": workspace_id,
                "delivery_run_id": feasibility_run_id,
                "scope": "technical",
            }
        )
    except (ValueError, RuntimeError, OSError) as exc:
        return [f"feasibility_validate_unavailable:{type(exc).__name__}"]
    validation = dict(validated.get("validation") or {})
    return [
        *[str(item) for item in validation.get("blockers") or []],
        *[str(item) for item in validation.get("quality_issues") or []],
    ]


def run_technical_acceptance(
    workspace_id: str,
    *,
    intent: dict[str, Any],
    domain: dict[str, Any],
    delivery_artifacts: dict[str, Any],
    finance_summary: dict[str, Any] | None = None,
    extra_blockers: list[str] | None = None,
    extra_limitations: list[str] | None = None,
) -> dict[str, Any]:
    """Compute the ``acceptance.technical`` object for one delivery run.

    Args:
        finance_summary: ``artifact_delivery`` 已从不可变 FinanceRun 读出的摘要。
            必须用它，**不要**从 ``domain["finance_run"]`` 取 ``consistency_ok``：
            那是 ``run_model`` 的响应信封，本来就不含该字段，``.get`` 会静默返回
            None 并被读成"勾稽不通"——把一个正常运行判成失败。
    """

    refs = dict(delivery_artifacts.get("object_refs") or {})
    domain_refs = dict(domain.get("object_refs") or {})
    research = dict(domain.get("research") or {})
    public_research = dict(research.get("public_research") or {})
    finance_run = dict(domain.get("finance_run") or {})
    tables = dict(domain.get("tables") or {})
    csv_export = dict(domain.get("csv_export") or {})
    xlsx_export = dict(domain.get("xlsx_export") or {})
    profile_selection = dict(delivery_artifacts.get("report_profile") or {})
    technical_report_id = str(refs.get("technical_report_id") or "")
    lineage_key = hashlib.sha256(
        f"{workspace_id}|{technical_report_id}|{profile_selection.get('profile_content_hash')}".encode(
            "utf-8"
        )
    ).hexdigest()[:24]

    domain_results = build_technical_domain_results(
        component_status=dict(delivery_artifacts.get("component_status") or {}),
        unresolved_slots=[
            str(item) for item in delivery_artifacts.get("unresolved_slots") or []
        ],
        research={
            "research_package_id": str(public_research.get("research_package_id") or ""),
            "fallback_used": bool(public_research.get("fallback_used")),
        },
        finance={
            "run_id": str(
                domain_refs.get("finance_run_id")
                or domain_refs.get("acquisition_run_id")
                or finance_run.get("run_id")
                or ""
            ),
            "consistency_ok": dict(finance_summary or {}).get("consistency_ok"),
        },
        tables={
            "finance_tables_package_id": str(
                domain_refs.get("finance_tables_package_id")
                or domain_refs.get("acquisition_tables_package_id")
                or tables.get("finance_tables_package_id")
                or ""
            ),
            "csv_ok": bool(csv_export.get("csv_resource_uris") or csv_export.get("resource_uris")),
            "xlsx_ok": bool(xlsx_export.get("xlsx_resource") or xlsx_export.get("resource_uris")),
        },
        lineage=_lineage_inputs(domain, delivery_artifacts),
        profile_selection=profile_selection,
    )
    review = _review_process_acceptance(
        workspace_id,
        technical_report_id=technical_report_id,
        domain_refs=domain_refs,
        public_research=public_research,
        industry_code=str(dict(intent.get("industry") or {}).get("industry_code") or ""),
        project_type=(
            "asset_acquisition"
            if str(dict(domain.get("route") or {}).get("finance_kind") or "")
            == "asset_acquisition"
            else "generic_feasibility"
        ),
        region=str(intent.get("region") or ""),
        lineage_key=lineage_key,
    )
    feasibility_codes = _feasibility_technical(
        workspace_id,
        str(domain_refs.get("feasibility_delivery_run_id") or ""),
    )
    return fold_technical(
        domain_results,
        extra_blockers=list(extra_blockers or []),
        extra_limitations=[
            *(extra_limitations or []),
            *review["codes"],
            *feasibility_codes,
        ],
        review_preparation_id=review["review_preparation_id"],
        review_id=review["review_id"],
        review_package_id=review["review_package_id"],
        feasibility_validation_id=str(domain_refs.get("feasibility_delivery_run_id") or ""),
    )


__all__ = ["run_technical_acceptance"]
