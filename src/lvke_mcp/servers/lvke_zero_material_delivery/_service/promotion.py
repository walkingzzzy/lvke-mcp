"""Generate deterministic Sim-A template packs and confirm formal promotion."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from lvke_mcp.runtime.evidence_qualification import SIM_A_FORMAL
from lvke_mcp.runtime.formal_promotion import (
    FormalLineageError,
    build_promotion_payload,
    validate_formal_promotion,
    validate_template_pack,
)
from lvke_mcp.runtime.storage import require_safe_id, sha256_json

from .acceptance import REQUIRED_FIELD_UNANSWERED_PREFIX, empty_acceptance
from .assumptions import _field_values
from .explicit_inputs import SOURCE_SENTENCE
from .base import (
    ASSUMPTION_STORE,
    INTENT_STORE,
    PROMOTION_STORE,
    RUN_STORE,
    SERVICE_NAME,
    TEMPLATE_PACK_STORE,
    _blocked,
    _envelope,
    _idempotent_mutation,
    _view,
)
from .finance_align import _scenario_inputs

SIM_A_ORIGIN = "sim_a_template"
TEMPLATE_ROOT = Path(__file__).resolve().parents[3] / "config" / "sim_a_templates"
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

FORBIDDEN_FABRICATION_FIELDS: tuple[dict[str, str], ...] = (
    {
        "name": "official_seal",
        "target_pointer": "/input_revision/official_seal",
        "replacement_condition": "须以盖章原件或行政机关核发材料替换；禁止编造公章或签章",
    },
    {
        "name": "official_document_no",
        "target_pointer": "/input_revision/official_document_no",
        "replacement_condition": "须以批复、备案或登记原文号替换；禁止编造正式文号",
    },
    {
        "name": "approval_no",
        "target_pointer": "/input_revision/approval_no",
        "replacement_condition": "须以主管部门批复号替换；禁止编造批复号",
    },
    {
        "name": "bank_statement_ref",
        "target_pointer": "/input_revision/bank_statement_ref",
        "replacement_condition": "须以银行对账单或资金流水原件替换；禁止编造流水",
    },
    {
        "name": "inspection_conclusion",
        "target_pointer": "/input_revision/inspection_conclusion",
        "replacement_condition": "须以检测/监测机构结论替换；禁止编造检测结论",
    },
    {
        "name": "audit_conclusion",
        "target_pointer": "/input_revision/audit_conclusion",
        "replacement_condition": "须以审计报告结论替换；禁止编造审计结论",
    },
)

ASSUMPTION_POINTERS: dict[str, str] = {
    "total_investment_wan": "/spec/total_investment_wan",
    "annual_revenue_wan": "/spec/annual_revenue_wan",
    "build_period_months": "/spec/build_period_months",
    "loan_ratio": "/spec/loan_ratio",
    "loan_rate": "/spec/loan_rate",
    "operating_period_years": "/spec/operating_period_years",
    "capacity": "/spec/capacity",
    "unit_price": "/spec/unit_price",
    "annual_visitors": "/spec/annual_visitors",
    "spend_per_visitor": "/spec/spend_per_visitor",
}


def _render_template(text: str, values: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        return str(values.get(match.group(1), ""))

    return _PLACEHOLDER_RE.sub(replace, text)


#: 写入每个生成文件**正文开头**的模拟来源披露。
#:
#: 对象层已经带了 ``evidence_origin=sim_a_template`` /
#: ``release_grade=technical_preview``，但文件一旦被导出、转发或打印，读的人只
#: 看得到正文——那时对象层的标记等于不存在。finance 域为此把限制标记写进 CSV
#: 首行、XLSX 的 A1、DOCX 页眉；这里同理，写在 Markdown 正文最前面。
#:
#: 四个可检索标记（拟定模板 / sim_a_template / 技术预估 / 不得作为）刻意同时
#: 出现：中文读者按文案识别，程序按 evidence_origin 字面量检索，两条路都得通。
_SIM_A_DISCLOSURE_LINES: tuple[str, ...] = (
    "> **【拟定模板·不得作为交付物】**",
    ">",
    "> 本文为**拟定模板**（evidence_origin=`sim_a_template`，"
    "release_grade=`technical_preview`），在甲方原始材料缺失条件下"
    "由**受控假设**确定性填空生成，属技术预估性质。",
    ">",
    "> **不得作为对外正式交付物、投资决策依据或合规证明使用。**"
    "全部项目特定数字须以合同、测绘、报价、权属、设计或批复等原始材料"
    "替换并重算后，才可进入正式交付链。",
)

#: 跳过字段披露段的标题。跳过是"用户明确选择不回答"，与"尚未回答"不同，
#: 必须在正文里留痕，否则读者无从知道这些数字是无人确认的受控假设。
_SKIPPED_DISCLOSURE_TITLE = "## 用户跳过字段披露"
_SKIPPED_DISCLOSURE_NOTE = (
    "以下字段由用户显式跳过，未经确认，按受控假设取值并计入交付限制；"
    "正式使用前必须逐项以原始材料替换。"
)
#: 跳过历史披露段。跳过后又回答的字段不再出现在 ``skipped_fields`` 里，
#: 但"曾经选择跳过、后来才补答"这个决策变更本身是审计对象。
_SKIP_HISTORY_TITLE = "## 跳过决策变更记录"
_SKIP_HISTORY_NOTE = (
    "以下字段曾被显式跳过，其后已由用户回答；保留本记录以便审计追溯决策变更。"
)


#: JSON 映射文件的披露文案（同一句话的单行形态，供机器与人同时读）。
_MAPPING_DISCLOSURE = (
    "【拟定模板·不得作为交付物】本文件由受控假设确定性生成，属技术预估性质"
    "（evidence_origin=sim_a_template，release_grade=technical_preview），"
    "不得作为对外正式交付物或投资决策依据。"
)


def _disclosure_block() -> str:
    return "\n".join(_SIM_A_DISCLOSURE_LINES)


def _skipped_disclosure(
    skipped_fields: list[dict[str, Any]],
    skip_history: list[dict[str, Any]],
) -> str:
    """Render the skip disclosure sections appended to every generated markdown."""

    sections: list[str] = []
    if skipped_fields:
        rows = [
            "| 字段 | 跳过原因 |",
            "| --- | --- |",
            *(
                "| {field} | {reason} |".format(
                    field=str(item.get("field") or ""),
                    reason=str(item.get("reason") or "user_skipped"),
                )
                for item in sorted(
                    skipped_fields, key=lambda item: str(item.get("field") or "")
                )
            ),
        ]
        sections.append(
            "\n".join([_SKIPPED_DISCLOSURE_TITLE, "", _SKIPPED_DISCLOSURE_NOTE, "", *rows])
        )
    # 只列"已被回答"的历史项：仍在跳过中的已在上一段逐项披露，重复列出会让读者
    # 以为同一个字段既跳过又已答。
    still_skipped = {
        str(item.get("field") or "")
        for item in skipped_fields
        if isinstance(item, dict)
    }
    resolved_history = [
        item
        for item in skip_history
        if str(item.get("field") or "") not in still_skipped
    ]
    if resolved_history:
        rows = [
            "| 字段 | 原跳过原因 | 当前状态 |",
            "| --- | --- | --- |",
            *(
                "| {field} | {reason} | {state} |".format(
                    field=str(item.get("field") or ""),
                    reason=str(item.get("reason") or "user_skipped"),
                    state=str(item.get("resolution") or "answered"),
                )
                for item in sorted(
                    resolved_history, key=lambda item: str(item.get("field") or "")
                )
            ),
        ]
        sections.append(
            "\n".join([_SKIP_HISTORY_TITLE, "", _SKIP_HISTORY_NOTE, "", *rows])
        )
    return "\n\n".join(sections)


def _template_path(industry_code: str, requirement_id: str) -> Path:
    industry = TEMPLATE_ROOT / industry_code / f"{requirement_id}.md.j2"
    if industry.is_file():
        return industry
    generic = TEMPLATE_ROOT / "generic" / f"{requirement_id}.md.j2"
    if generic.is_file():
        return generic
    return TEMPLATE_ROOT / "generic" / "_default.md.j2"


def _pending_assumption_fields(
    package: dict[str, Any],
    skipped_fields: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return fields still awaiting an answer, treating explicit skips as answered.

    "已处理"有三种：用户确认过、句中已写明、用户显式跳过。前两种此前就被放行，
    第三种没有——于是 ``skip_fields`` 是一条死路：跳过登记进了 DeliveryRun，
    但生成模板包时这些字段仍算 pending，请求被 ``assumptions_not_confirmed``
    拒绝，用户除了回答别无出路。而契约明确宣称"用户可以只回答关键项"。

    放行**不等于**静默：跳过项由 ``questions.summarize_gaps`` 产出
    ``required_field_skipped:*`` 限制码，并在 TemplatePack 正文的跳过披露段
    逐项列出（见 ``_skipped_disclosure``）。这里只解开"不能继续"这一环。
    """

    skipped_names = {
        str(item.get("field") or "")
        for item in (skipped_fields or [])
        if isinstance(item, dict) and str(item.get("field") or "")
    }
    return [
        item
        for item in package.get("fields") or []
        if isinstance(item, dict)
        and not item.get("confirmed")
        and item.get("source_ref") != SOURCE_SENTENCE
        and str(item.get("name") or "") not in skipped_names
    ]


#: 此处曾有 ``_assumption_confirmed(package) -> bool``。它已被删除而不是保留：
#: 布尔结果答不了"还差哪几个字段"，调用方只能回一句笼统的"须先确认"。现在
#: 唯一入口是 ``_pending_assumption_fields``，它返回待办字段本身，拒绝信息因此
#: 能逐项指名（见 ``generate_template_pack`` 的 pending_fields）。
#:
#: 顺带修掉的另一件事：原实现有一个 ``if confirmation_status == "confirmed"``
#: 分支，其 then 与 else 返回完全相同的表达式——读的人会以为
#: ``confirmation_status`` 参与了判定，实际不参与。


def _assumption_table(fields: list[dict[str, Any]]) -> str:
    rows = ["| 字段 | 取值 | 单位 | 来源 | 已确认 |", "| --- | --- | --- | --- | --- |"]
    for item in fields:
        if not isinstance(item, dict):
            continue
        rows.append(
            "| {name} | {value} | {unit} | {source} | {confirmed} |".format(
                name=str(item.get("name") or ""),
                value=str(item.get("value") if item.get("value") is not None else ""),
                unit=str(item.get("unit") or ""),
                source=str(item.get("source_ref") or ""),
                confirmed="是" if item.get("confirmed") else "否",
            )
        )
    return "\n".join(rows)


def _flatten_pointers(value: Any, prefix: str) -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}/{key}"
            if isinstance(item, (dict, list)):
                rows.extend(_flatten_pointers(item, path))
            elif isinstance(item, (str, int, float, bool)) or item is None:
                rows.append((path, item))
    elif isinstance(value, list):
        for index, item in enumerate(value[:20]):
            path = f"{prefix}/{index}"
            if isinstance(item, (dict, list)):
                rows.extend(_flatten_pointers(item, path))
            elif isinstance(item, (str, int, float, bool)) or item is None:
                rows.append((path, item))
    return rows


def _mapping_rows(
    package: dict[str, Any],
    spec: dict[str, Any],
    finance_inputs: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    fields = [item for item in package.get("fields") or [] if isinstance(item, dict)]
    for item in fields:
        name = str(item.get("name") or "")
        if not name:
            continue
        pointer = ASSUMPTION_POINTERS.get(name, f"/input_revision/{name}")
        value = item.get("value")
        filled = value not in (None, "")
        rows.append(
            {
                "name": name,
                "target_pointer": pointer,
                "value": value if filled else None,
                "unit": str(item.get("unit") or ""),
                "basis": "controlled_assumption",
                "replacement_condition": str(
                    item.get("validation_condition")
                    or "须以合同、测绘、报价或权属等原始材料替换"
                ),
                "status": "mapped" if filled else "interface_only",
            }
        )
        seen.add(pointer)
    for prefix, source in (("/spec", spec), ("/input_revision", finance_inputs)):
        for pointer, value in _flatten_pointers(source, prefix):
            if pointer in seen:
                continue
            if any(token in pointer for token in ("assumptions", "field_sources", "source_hint")):
                continue
            filled = value not in (None, "")
            rows.append(
                {
                    "name": pointer.rsplit("/", 1)[-1],
                    "target_pointer": pointer,
                    "value": value if filled else None,
                    "unit": "",
                    "basis": "controlled_assumption",
                    "replacement_condition": "须以原始材料或已认证证据替换后才能作为项目事实",
                    "status": "mapped" if filled else "interface_only",
                }
            )
            seen.add(pointer)
    for item in FORBIDDEN_FABRICATION_FIELDS:
        if item["target_pointer"] in seen:
            continue
        rows.append(
            {
                "name": item["name"],
                "target_pointer": item["target_pointer"],
                "value": None,
                "unit": "",
                "basis": "controlled_assumption",
                "replacement_condition": item["replacement_condition"],
                "status": "interface_only",
            }
        )
    return rows


def _resolve_applicable_requirements(
    workspace_id: str,
    *,
    project_type: str,
    industry_code: str,
    idempotency_key: str,
) -> tuple[list[dict[str, Any]], str]:
    from lvke_mcp.runtime.service_gateway import (
        list_standard_requirements,
        resolve_standards,
    )

    resolved = resolve_standards(
        {
            "workspace_id": workspace_id,
            "project_context": {
                "project_type": project_type,
                "industry_code": industry_code,
                "target_type": "report_revision",
            },
            "facilities": [],
            "idempotency_key": f"zmd-std-{idempotency_key}",
        }
    )
    if not resolved.get("success"):
        return [], str(resolved.get("code") or "standards_resolve_failed")
    applicability_id = str(resolved.get("standard_applicability_id") or "")
    listed = list_standard_requirements(
        {
            "workspace_id": workspace_id,
            "standard_applicability_id": applicability_id,
        }
    )
    if not listed.get("success"):
        return [], str(listed.get("code") or "standards_list_failed")
    requirements = [
        dict(item)
        for item in listed.get("requirements") or []
        if isinstance(item, dict) and str(item.get("requirement_id") or "")
    ]
    return requirements, ""


def _scale_summary(values: dict[str, Any]) -> str:
    parts: list[str] = []
    if values.get("total_investment_wan") is not None:
        parts.append(f"总投资 {values['total_investment_wan']} 万元")
    if values.get("annual_revenue_wan") is not None:
        parts.append(f"年收入 {values['annual_revenue_wan']} 万元")
    if values.get("build_period_months") is not None:
        parts.append(f"建设期 {values['build_period_months']} 个月")
    return "；".join(parts) if parts else "规模参数见已确认假设表"


def generate_template_pack(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    run_id = require_safe_id(args.get("delivery_run_id"), "delivery_run_id")
    idempotency_key = str(args.get("idempotency_key") or "")
    project_type = str(args.get("project_type") or "generic_feasibility").strip() or "generic_feasibility"
    request_payload = {
        "delivery_run_id": run_id,
        "project_type": project_type,
        "report_profile_id": str(args.get("report_profile_id") or "").strip(),
        "template_set_id": str(args.get("template_set_id") or "").strip(),
        # 调用方声明"我看到的是哪份已确认答案"。留空表示不断言，沿用运行绑定的那份。
        "confirmed_assumption_package_id": str(
            args.get("confirmed_assumption_package_id") or ""
        ).strip(),
    }

    def mutation() -> dict[str, Any]:
        run_record = RUN_STORE.get(workspace_id, run_id)
        if run_record is None:
            return _blocked("delivery_run_not_found", "未找到指定 DeliveryRun")
        run = _view(run_record, "delivery_run_id")
        package_id = str(run.get("assumption_package_id") or "")
        if not package_id:
            return _blocked("assumption_package_missing", "DeliveryRun 未绑定 AssumptionPackage")
        # 显式答案引用是乐观并发断言：确认动作会**新建** AssumptionPackage，
        # 所以"我基于 zma_A 生成模板包"与运行当前挂着 zma_B 冲突时必须阻断，
        # 而不是按 B 生成一份调用方没看过的模板包。
        declared_package = str(request_payload["confirmed_assumption_package_id"])
        if declared_package and declared_package != package_id:
            return _blocked(
                "confirmed_assumption_package_stale",
                "声明的已确认答案快照与当前 DeliveryRun 绑定的不一致",
                declared_assumption_package_id=declared_package,
                current_assumption_package_id=package_id,
                next_actions=[
                    "用 delivery_status 读取当前 assumption_package_id 后重试",
                    "或省略 confirmed_assumption_package_id 以沿用运行绑定的快照",
                ],
            )
        package_record = ASSUMPTION_STORE.get(workspace_id, package_id)
        if package_record is None:
            return _blocked("assumption_package_not_found", "未找到指定 AssumptionPackage")
        package = _view(package_record, "assumption_package_id")
        # 跳过登记在 DeliveryRun 上（假设包只记确认），因此判"还差哪些答案"必须
        # 两者一起看。跳过史（含被后续回答覆盖的）也一并取出，供正文披露。
        skipped_fields = [
            dict(item)
            for item in run.get("skipped_fields") or []
            if isinstance(item, dict) and str(item.get("field") or "")
        ]
        skip_history = [
            dict(item)
            for item in run.get("skip_history") or []
            if isinstance(item, dict) and str(item.get("field") or "")
        ]
        pending_fields = _pending_assumption_fields(package, skipped_fields)
        if pending_fields:
            return _blocked(
                "assumptions_not_confirmed",
                "须先确认 AssumptionPackage 再生成拟定模板包；未确认字段亦可显式跳过",
                pending_fields=sorted(
                    str(item.get("name") or "") for item in pending_fields
                ),
                next_actions=[
                    "调用 delivery_list_assumptions 与 delivery_confirm_assumptions",
                    "或在 delivery_confirm_assumptions 的 skip_fields 中显式跳过后重试"
                    "（跳过项会计入交付限制并在模板正文披露）",
                ],
            )
        intent_id = str(run.get("intent_id") or "")
        intent_record = INTENT_STORE.get(workspace_id, intent_id) if intent_id else None
        intent = _view(intent_record, "delivery_intent_id") if intent_record else {}
        industry = dict(intent.get("industry") or package.get("industry_profile") or {})
        industry_code = str(
            industry.get("industry_code") or package.get("industry_code") or ""
        )
        # 报告配置：默认沿用运行冻结的那份。
        report_profile = dict(run.get("report_profile") or {})
        requested_profile_id = str(request_payload["report_profile_id"])
        requested_template_set = str(request_payload["template_set_id"])
        if requested_profile_id or requested_template_set:
            # 运行已冻结配置时**拒绝覆盖**。
            #
            # 技术验收与内部七域验收都是针对"按配置 A 生成的那份交付"做的。允许在
            # 生成模板包时换成配置 B，就等于让配置 B 的 pack 继承配置 A 的验收结论
            # 并据此晋升——验收对象与晋升对象不是同一件东西。
            #
            # 在源头拒绝而不是到 Promotion 再比对：越早失败，调用方越容易看懂
            # "该改的是创建阶段的配置选择，不是这里"。
            frozen_set = str(report_profile.get("template_set_id") or "")
            frozen_id = str(report_profile.get("profile_id") or "")
            if frozen_set or frozen_id:
                requested_matches = (
                    not requested_profile_id or requested_profile_id == frozen_id
                ) and (
                    not requested_template_set or requested_template_set == frozen_set
                )
                if not requested_matches:
                    return _blocked(
                        "report_profile_override_conflicts_with_run",
                        "DeliveryRun 已冻结报告配置，生成模板包时不得改用其它配置",
                        frozen_profile_id=frozen_id,
                        frozen_template_set_id=frozen_set,
                        requested_profile_id=requested_profile_id,
                        requested_template_set_id=requested_template_set,
                        next_actions=[
                            "省略 report_profile_id / template_set_id 以沿用运行冻结的配置",
                            "或用 delivery_create_from_sentence 以目标配置新建一条交付链",
                        ],
                    )
            from .report_profiles import ReportProfileError, resolve_profile

            try:
                overridden = resolve_profile(
                    industry_code=industry_code,
                    project_type=project_type,
                    transaction_structure=(
                        "asset_acquisition"
                        if project_type == "asset_acquisition"
                        else "new_build"
                    ),
                    asset_type=str(industry.get("asset_type") or "general"),
                    report_type=str(intent.get("report_type") or ""),
                    requested_profile_id=requested_profile_id,
                    requested_template_set_id=requested_template_set,
                )
            except ReportProfileError as exc:
                return _blocked(
                    exc.code,
                    f"指定的报告配置不可用：{exc.message}",
                    report_profile_detail=exc.detail,
                )
            report_profile = dict(overridden["selection"])
        requirements, standards_error = _resolve_applicable_requirements(
            workspace_id,
            project_type=project_type,
            industry_code=industry_code,
            idempotency_key=idempotency_key,
        )
        if standards_error:
            return _blocked(standards_error, "未能解析本项目适用标准需求")
        if not requirements:
            return _blocked("applicable_requirements_empty", "适用标准需求为空，无法生成拟定模板包")
        spec, finance_inputs, _context = _scenario_inputs(package)
        from lvke_mcp.domains.finance import model_application as finance

        validated = finance.validate_spec({"spec": spec, "for_formal": False})
        if validated.get("structural_valid") is False or (
            validated.get("quality_valid") is False and validated.get("structural_valid") is not True
        ):
            return _blocked(
                "candidate_spec_invalid",
                "候选 FinanceSpec 未通过结构校验，拒绝生成拟定模板包",
                quality_issues=list(validated.get("quality_issues") or []),
                next_actions=["修正 AssumptionPackage 后重新确认并再生成模板包"],
            )
        mapping_rows = _mapping_rows(package, spec, finance_inputs)
        fields = [item for item in package.get("fields") or [] if isinstance(item, dict)]
        values = {
            "project_name": str(
                intent.get("project_name")
                or _project_name(str(intent.get("sentence") or ""), str(intent.get("project_name") or ""))
                or "未命名项目"
            ),
            "region": str(intent.get("region") or ""),
            "industry_code": industry_code,
            "industry_label": str(
                industry.get("industry_label") or package.get("industry_label") or industry_code
            ),
            "scale_summary": _scale_summary(_field_values(package)),
            "assumption_table": _assumption_table(fields),
        }
        files: list[dict[str, Any]] = []
        for requirement in requirements:
            requirement_id = str(requirement.get("requirement_id") or "")
            render_values = {
                **values,
                "requirement_id": requirement_id,
                "requirement_title": str(requirement.get("title") or requirement_id),
                "requirement_description": str(requirement.get("description") or ""),
            }
            template_path = _template_path(industry_code, requirement_id)
            rendered = _render_template(
                template_path.read_text(encoding="utf-8"),
                render_values,
            )
            # 披露注入在**渲染之后**、算 hash 之前：hash 必须覆盖披露段，否则
            # 有人删掉披露仍能通过 ``expected_promoted_files`` 的正文/hash 相等
            # 校验，而那正是这套 hash 要防的事。
            #
            # 注入而不是写进 15 份 .md.j2 模板：模板可被新增（每个行业目录一份），
            # 落在模板里的披露会随"新加一个行业忘了抄披露"静默消失。生成器是
            # 唯一出口，放这里才是不可绕过的。
            body = "\n\n".join(
                part
                for part in (
                    _disclosure_block(),
                    rendered.strip(),
                    _skipped_disclosure(skipped_fields, skip_history),
                )
                if part
            )
            digest = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
            files.append(
                {
                    "requirement_id": requirement_id,
                    "kind": "markdown",
                    "filename": f"{requirement_id}.md",
                    "sha256": digest,
                    "text": body,
                    "template_path": str(template_path.relative_to(TEMPLATE_ROOT)),
                }
            )
            mapping_document = {
                "schema_version": "sim-a-requirement-mapping.v1",
                # 映射文件与 .md 同为可导出工件，同样必须自带披露：只有 .md 带
                # 标记的话，单独拿到一份 .json 的人看不出它是拟定模板产物。
                "disclosure": _MAPPING_DISCLOSURE,
                "requirement_id": requirement_id,
                "requirement_title": str(requirement.get("title") or requirement_id),
                "evidence_policy": SIM_A_FORMAL,
                "evidence_origin": SIM_A_ORIGIN,
                "release_grade": "technical_preview",
                "assumption_package_id": package_id,
                "delivery_run_id": run_id,
                # 跳过项与跳过史进映射文件：它决定了哪些 row 的取值无人确认。
                "skipped_fields": sorted(
                    skipped_fields, key=lambda item: str(item.get("field") or "")
                ),
                "skip_history": sorted(
                    skip_history, key=lambda item: str(item.get("field") or "")
                ),
                "rows": mapping_rows,
            }
            mapping_text = json.dumps(mapping_document, ensure_ascii=False, indent=2)
            files.append(
                {
                    "requirement_id": requirement_id,
                    "kind": "json",
                    "filename": f"{requirement_id}.json",
                    "sha256": "sha256:" + hashlib.sha256(mapping_text.encode("utf-8")).hexdigest(),
                    "text": mapping_text,
                    "template_path": "generated",
                }
            )
        payload = {
            "object_type": "TemplatePack",
            "delivery_run_id": run_id,
            "assumption_package_id": package_id,
            "intent_id": intent_id,
            "project_type": project_type,
            "industry_code": industry_code,
            "evidence_policy": SIM_A_FORMAL,
            "evidence_origin": SIM_A_ORIGIN,
            # 生成模板包本身不授予任何验收资格：两段验收都从 pending 起步，
            # 由 delivery_confirm_formal_promotion 的门禁读真实状态。
            "assurance_level": "estimate_preview",
            "release_grade": "technical_preview",
            "technical_acceptance": "pending",
            "internal_acceptance": "pending",
            # 披露已注入每份文件正文（不只是对象层标记）。留此字段是为了让审查侧
            # 能直接判"这份 pack 的文件是否带正文披露"，不必反解 15 份正文。
            "body_disclosure_applied": True,
            "report_profile": report_profile,
            # 跳过项随 pack 冻结：晋升后的 SourceFile 只带正文，pack 是"哪些字段
            # 未经确认"唯一的结构化记录。
            "skipped_fields": sorted(
                skipped_fields, key=lambda item: str(item.get("field") or "")
            ),
            "skip_history": sorted(
                skip_history, key=lambda item: str(item.get("field") or "")
            ),
            "requirement_ids": [str(item.get("requirement_id") or "") for item in requirements],
            "files": [
                {
                    "requirement_id": item["requirement_id"],
                    "kind": item["kind"],
                    "filename": item["filename"],
                    "sha256": item["sha256"],
                    "text": item["text"],
                    "template_path": item["template_path"],
                }
                for item in files
            ],
            "mapping_row_count": len(mapping_rows),
            "interface_only_count": sum(
                1 for item in mapping_rows if item.get("status") == "interface_only"
            ),
            "fabrication_forbidden": [item["name"] for item in FORBIDDEN_FABRICATION_FIELDS],
        }
        record = TEMPLATE_PACK_STORE.put(
            workspace_id,
            payload,
            producer=f"{SERVICE_NAME}.delivery_generate_template_pack",
            status="ok",
            source_ids=[run_id, package_id],
            basis=request_payload,
        )
        view = _view(record, "template_pack_id")
        return _envelope(
            True,
            "ok",
            resource_uris=[record["resource_uri"]],
            template_pack_id=record["object_id"],
            template_pack=view,
            requirement_ids=payload["requirement_ids"],
            mapping_row_count=payload["mapping_row_count"],
            interface_only_count=payload["interface_only_count"],
            files=[
                {
                    "filename": item["filename"],
                    "sha256": item["sha256"],
                    "kind": item["kind"],
                    "requirement_id": item["requirement_id"],
                }
                for item in files
            ],
            warnings=[
                "拟定模板包按受控假设生成，正文已写入不得作为交付物的披露标记",
                *(
                    [
                        f"{len(skipped_fields)} 个字段由用户显式跳过，未经确认，"
                        "已在正文与映射文件中逐项披露"
                    ]
                    if skipped_fields
                    else []
                ),
            ],
            # 跳过项随响应回传并进限制清单：调用方不必再去读 DeliveryRun 才知道
            # 这份 pack 里有多少字段无人确认。
            skipped_fields=payload["skipped_fields"],
            skip_history=payload["skip_history"],
            release_limitations=sorted(
                {
                    "evidence_origin_sim_a_template",
                    *(
                        f"required_field_skipped:{str(item.get('field') or '')}"
                        for item in skipped_fields
                    ),
                }
            ),
            next_actions=[
                "核对拟定模板包后调用 delivery_confirm_formal_promotion",
                "确认时必须填写 responsible_party 与 confirmation_note",
                *(
                    ["如需消除跳过限制项，先用 delivery_confirm_assumptions 回答被跳过字段"]
                    if skipped_fields
                    else []
                ),
            ],
            validation_complete=False,
            input_evidence_complete=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="delivery_generate_template_pack",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutation,
    )


def _project_name(sentence: str, supplied: str = "") -> str:
    from .routing import _project_name as resolve_name

    return str(resolve_name(sentence, supplied) or supplied or "")


#: 晋升前必须与运行逐项相等的配置身份字段。
_PROFILE_IDENTITY_FIELDS = (
    "profile_id",
    "template_set_id",
    "profile_version",
    "profile_content_hash",
)


def _profile_identity_mismatch(
    workspace_id: str,
    pack: dict[str, Any],
) -> list[dict[str, str]]:
    """Return per-field mismatches between the pack's profile and the run's.

    运行没有冻结配置（历史 run）时返回空列表：那种情况下没有可比对的基准，
    不能凭空判不一致。运行**有**配置而 pack 没有则算不一致——晋升对象必须能
    指名它用的配置。
    """

    run_record = RUN_STORE.get(workspace_id, str(pack.get("delivery_run_id") or ""))
    if run_record is None:
        return []
    run_profile = dict(_view(run_record, "delivery_run_id").get("report_profile") or {})
    if not any(str(run_profile.get(field) or "") for field in _PROFILE_IDENTITY_FIELDS):
        return []
    pack_profile = dict(pack.get("report_profile") or {})
    return [
        {
            "field": field,
            "run_value": str(run_profile.get(field) or ""),
            "template_pack_value": str(pack_profile.get(field) or ""),
        }
        for field in _PROFILE_IDENTITY_FIELDS
        if str(run_profile.get(field) or "") != str(pack_profile.get(field) or "")
    ]


def _acceptance_gate(workspace_id: str, delivery_run_id: str) -> dict[str, Any]:
    """Read the run's graded acceptance and decide whether promotion may proceed.

    刻意在这里重新读一遍 review 域的领域确认，而不是相信 DeliveryRun 上写死的
    ``internal.status``：责任人通常在 ``delivery_start`` 之后才逐个确认，缓存值
    会长期停在 pending。

    三段验收的阻断项**都要读**，``formal`` 那段尤其不能漏
    ==================================================

    此前只读 technical 与 internal 两段。``fold_formal`` 算出的
    ``required_field_unanswered:*``（关键必填字段未回答，见
    ``acceptance.REQUIRED_FIELD_UNANSWERED_PREFIX``）因此对晋升完全无效——
    ``acceptance.formal.status`` 报 blocked，晋升却照样放行，同一个响应里
    两个答案互相矛盾。

    这个漏洞过去被另一道门挡着：关键字段未回答意味着假设包未确认，生成模板包
    在 ``assumptions_not_confirmed`` 就被拒了，走不到这里。允许显式跳过之后
    那道门不再兜底（跳过是被明确允许的），本处必须自己成立。
    """

    from .lifecycle import _refresh_acceptance

    if not delivery_run_id:
        return {
            "acceptance": empty_acceptance(),
            "blockers": ["formal_promotion_delivery_run_missing"],
        }
    record = RUN_STORE.get(workspace_id, delivery_run_id)
    if record is None:
        return {
            "acceptance": empty_acceptance(),
            "blockers": ["formal_promotion_delivery_run_not_found"],
        }
    acceptance = _refresh_acceptance(workspace_id, _view(record, "delivery_run_id"))
    technical = dict(acceptance.get("technical") or {})
    internal = dict(acceptance.get("internal") or {})
    formal = dict(acceptance.get("formal") or {})
    blockers: list[str] = []
    technical_status = str(technical.get("status") or "not_started")
    internal_status = str(internal.get("status") or "not_started")
    if technical_status not in {"passed", "passed_with_limitations"}:
        blockers.append(f"technical_acceptance_not_passed:{technical_status}")
        blockers.extend(str(item) for item in technical.get("blockers") or [])
    if internal_status not in {"passed", "passed_with_limitations"}:
        blockers.append(f"internal_acceptance_not_passed:{internal_status}")
        blockers.extend(str(item) for item in internal.get("blockers") or [])
    # formal 段自己算出的阻断项（关键必填字段未回答等）。
    #
    # 只取 ``fold_formal`` 独立判据产出的那些，**不**照搬整份 formal.blockers：
    # 后者还含 ``technical_acceptance_not_passed:*`` /
    # ``internal_acceptance_not_passed:*`` 的回声，而那两段的判据本函数上面已经
    # 各自算过一次。整份并入只会让同一件事出现两条码，且在两段本已通过时把
    # 陈旧的回声当成新阻断——那正是"新增阻断误杀正常链"的形状。
    blockers.extend(
        item
        for item in (str(value) for value in formal.get("blockers") or [])
        if item.startswith(REQUIRED_FIELD_UNANSWERED_PREFIX)
    )
    return {"acceptance": acceptance, "blockers": sorted(set(blockers))}


def confirm_formal_promotion(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_safe_id(args.get("workspace_id"), "workspace_id")
    pack_id = require_safe_id(args.get("template_pack_id"), "template_pack_id")
    responsible_party = str(args.get("responsible_party") or "").strip()
    confirmation_note = str(args.get("confirmation_note") or "").strip()
    idempotency_key = str(args.get("idempotency_key") or "")
    if not responsible_party or not confirmation_note:
        return _blocked(
            "missing_inputs",
            "responsible_party 与 confirmation_note 为确认晋升必填项",
            next_actions=["补全责任方与确认说明后重试"],
        )
    request_payload = {
        "template_pack_id": pack_id,
        "responsible_party": responsible_party,
        "confirmation_note": confirmation_note,
    }

    def mutation() -> dict[str, Any]:
        try:
            validated_pack = validate_template_pack(workspace_id, pack_id)
        except FormalLineageError as exc:
            return _blocked(exc.code, exc.message)
        pack = dict(validated_pack["payload"])
        # 分级验收门禁：技术验收与内部分领域验收都必须已通过，才允许晋升。
        # 这里读的是 DeliveryRun 上固化的 acceptance，并按 review 域最新确认刷新
        # ——不接受调用方自报状态。
        # 第二道：pack 的配置身份必须与已验收 DeliveryRun 的完全一致。
        #
        # 生成侧已在源头拒绝覆盖，这里独立复核而不依赖它——验收结论与晋升对象
        # 必须是同一份配置，这个不变量值得两处各守一次。
        profile_mismatch = _profile_identity_mismatch(workspace_id, pack)
        if profile_mismatch:
            return _blocked(
                "formal_promotion_report_profile_mismatch",
                "TemplatePack 的报告配置与已验收 DeliveryRun 不一致",
                profile_mismatch=profile_mismatch,
                next_actions=[
                    "以运行冻结的配置重新生成模板包后再晋升",
                ],
            )
        gate = _acceptance_gate(workspace_id, str(pack.get("delivery_run_id") or ""))
        if gate["blockers"]:
            return _blocked(
                "formal_promotion_acceptance_required",
                "技术验收或内部分领域验收未通过，不得晋升为正式候选",
                # ``_blocked`` 自己会把 code 放进 blockers，这里只能另起字段名，
                # 否则 _envelope 收到两个 blockers 直接 TypeError。
                acceptance_blockers=gate["blockers"],
                acceptance=gate["acceptance"],
                next_actions=[
                    "按 acceptance.technical.blockers 修复交付链后重算",
                    "七域责任人调用 review_submit_assessment 与 review_confirm_dimension",
                    # 关键必填字段未答的唯一出路是回答它：跳过只换来技术预览，
                    # 不换正式资格。说清替代路径，免得调用方反复重试同一个调用。
                    *(
                        [
                            "回答关键必填字段后重算："
                            + "、".join(
                                item.removeprefix(REQUIRED_FIELD_UNANSWERED_PREFIX)
                                for item in gate["blockers"]
                                if item.startswith(REQUIRED_FIELD_UNANSWERED_PREFIX)
                            )
                            + "（这些字段跳过只能取得技术预览，不能取得正式候选资格）"
                        ]
                        if any(
                            item.startswith(REQUIRED_FIELD_UNANSWERED_PREFIX)
                            for item in gate["blockers"]
                        )
                        else []
                    ),
                ],
            )
        promoted_files = list(validated_pack["promoted_files"])
        payload = build_promotion_payload(
            pack,
            template_pack_id=pack_id,
            responsible_party=responsible_party,
            confirmation_note=confirmation_note,
            promoted_files=promoted_files,
        )
        preview = PROMOTION_STORE.preview_identity(workspace_id, payload)
        promotion_id = preview["object_id"]
        from lvke_mcp.runtime import service_gateway

        file_ids: list[str] = []
        imported: list[dict[str, Any]] = []
        descriptor_by_id = {item["file_id"]: item for item in promoted_files}
        for index, item in enumerate(pack.get("files") or []):
            if not isinstance(item, dict):
                continue
            filename = str(item.get("filename") or f"sim-a-{index}.txt")
            text = str(item.get("text") or "")
            mime = "application/json" if item.get("kind") == "json" else "text/markdown"
            digest = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
            expected_file_id = f"src_{digest.removeprefix('sha256:')[:24]}"
            descriptor = descriptor_by_id.get(expected_file_id)
            if descriptor is None:
                return _blocked(
                    "formal_promotion_preview_mismatch",
                    "TemplatePack 文件未能映射到预演 promotion payload",
                )
            imported_file = service_gateway.import_promoted_content(
                workspace_id,
                original_filename=filename,
                declared_mime=mime,
                content_base64=base64.b64encode(text.encode("utf-8")).decode("ascii"),
                idempotency_key=f"{idempotency_key}:{filename}",
                expected_sha256=digest,
                expected_file_id=expected_file_id,
                promotion_id=promotion_id,
                template_pack_id=pack_id,
                requirement_id=descriptor["requirement_id"],
                kind=descriptor["kind"],
            )
            file_id = str(imported_file.get("file_id") or "")
            source_record = imported_file.get("source_file") or {}
            actual_hash = "sha256:" + str(source_record.get("sha256") or "").removeprefix("sha256:")
            if not file_id or file_id != expected_file_id or actual_hash != digest:
                return _blocked(
                    str(imported_file.get("code") or "source_import_failed"),
                    "拟定模板导入结果与 promotion 预演不一致",
                    import_result=imported_file,
                )
            file_ids.append(file_id)
            imported.append(
                {
                    "file_id": file_id,
                    "filename": filename,
                    "sha256": digest,
                    "requirement_id": descriptor["requirement_id"],
                    "kind": descriptor["kind"],
                    "evidence_policy": SIM_A_FORMAL,
                    "evidence_origin": SIM_A_ORIGIN,
                }
            )
        if set(file_ids) != {item["file_id"] for item in promoted_files}:
            return _blocked(
                "formal_promotion_file_set_mismatch",
                "实际导入的 SourceFile 集合与 promotion 预演不精确相等",
            )
        record = PROMOTION_STORE.put(
            workspace_id,
            payload,
            producer=f"{SERVICE_NAME}.delivery_confirm_formal_promotion",
            status="ok",
            source_ids=[pack_id, str(pack.get("assumption_package_id") or "")],
            basis=request_payload,
            object_id=promotion_id,
        )
        if record["object_id"] != promotion_id or record.get("content_hash") != sha256_json(payload):
            return _blocked("formal_promotion_persist_mismatch", "FormalPromotion 持久化结果与预演不一致")
        try:
            canonical = validate_formal_promotion(
                workspace_id,
                promotion_id,
                expected_file_ids=file_ids,
            )
        except FormalLineageError as exc:
            return _blocked(exc.code, exc.message)
        from .acceptance import fold_formal

        promoted_acceptance = {
            **gate["acceptance"],
            "formal": fold_formal(
                technical=dict(gate["acceptance"].get("technical") or {}),
                internal=dict(gate["acceptance"].get("internal") or {}),
                promotion_id=promotion_id,
            ),
        }
        return _envelope(
            True,
            "ok",
            resource_uris=[record["resource_uri"]],
            promotion_id=promotion_id,
            file_ids=sorted(file_ids),
            imported_files=imported,
            evidence_policy=SIM_A_FORMAL,
            evidence_origin=SIM_A_ORIGIN,
            formal_promotion=canonical["formal_promotion"],
            acceptance=promoted_acceptance,
            # 晋升后证据政策转为 sim_a_formal，但模拟来源必须在 lineage 与限制里保留。
            release_limitations=sorted(
                {
                    *(str(item) for item in promoted_acceptance["formal"].get("limitations") or []),
                    "evidence_origin_sim_a_template",
                }
            ),
            next_actions=[
                "project_context_create",
                "feasibility_start",
            ],
            next_action_details=[
                {
                    "tool": "project_context_create",
                    "evidence_policy": SIM_A_FORMAL,
                    "promotion_id": promotion_id,
                    "file_ids": sorted(file_ids),
                },
                {
                    "tool": "feasibility_start",
                    "evidence_policy": SIM_A_FORMAL,
                    "note": "必须新建可研链对象，禁止复用 zmr_* 作为 delivery_run_id",
                },
            ],
            validation_complete=False,
            input_evidence_complete=False,
        )

    return _idempotent_mutation(
        workspace_id,
        operation="delivery_confirm_formal_promotion",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        mutation=mutation,
    )
