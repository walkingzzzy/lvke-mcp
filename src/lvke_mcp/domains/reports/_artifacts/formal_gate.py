"""Report basis capture and quality diagnostics."""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any


from lvke_mcp.domains.reports import doc_service
from lvke_mcp.runtime.formal_promotion import FormalLineageError

from .base import (
    BASIS_SCHEMA_VERSION,
    DRAFT_MARKER,
    DeliverableArtifactError,
    _GOVERNED_SNAPSHOTS,
    _canonical_hash,
    _without_volatile_timestamps,
)

from .snapshots import (
    _document_snapshot,
    _fresh_readiness,
    _json_snapshot,
    _load_finance_run,
    _source_basis_snapshot,
)

from .support_files import (
    _appendix_files_snapshot,
)


def _finance_quality_snapshot(
    workspace_id: str,
    *,
    expected_run_id: str = "",
) -> dict[str, Any]:
    """Collect finance quality diagnostics without granting or denying output."""

    try:
        from lvke_mcp.domains.finance import gate as finance_gate

        value = finance_gate.assert_publish_finance_binding(
            workspace_id,
            strict=True,
            expected_run_id=expected_run_id,
        )
    except Exception as exc:  # noqa: BLE001 - normalized as a blocking result
        return {
            "ok": True,
            "quality_issues": [{
                "code": "finance_publish_gate_failed",
                "message": f"财务发布门禁执行失败: {type(exc).__name__}",
            }],
            "error": type(exc).__name__,
        }
    if not isinstance(value, dict):
        return {
            "ok": True,
            "quality_issues": [{
                "code": "finance_publish_gate_invalid",
                "message": "财务发布门禁返回格式错误",
            }],
            "error": "invalid_result",
        }
    return value


def _capture_basis(
    workspace_id: str,
    *,
    template_version: str,
    report_revision_id: str = "",
    document_snapshot: dict[str, Any] | None = None,
    expected_run_id: str = "",
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    formal_lineage: dict[str, Any] = {}
    if report_revision_id:
        from lvke_mcp.adapters.report_repository import REVISION_STORE
        from lvke_mcp.domains.reports.formal_lineage import (
            validate_report_revision_lineage,
        )
        from lvke_mcp.runtime.formal_promotion import SIM_A_FORMAL

        revision = REVISION_STORE.get(workspace_id, report_revision_id)
        revision_payload = (revision or {}).get("payload") or {}
        upstream = revision_payload.get("upstream") or {}
        if str(upstream.get("evidence_policy") or "") == SIM_A_FORMAL:
            try:
                formal_lineage = validate_report_revision_lineage(
                    workspace_id,
                    revision or {},
                )
            except FormalLineageError as exc:
                # Provenance remains in the diagnostics snapshot. It is not a
                # prerequisite for creating a report artifact.
                formal_lineage = {"lineage_warning": exc.code}
    document, content, meta = _document_snapshot(
        workspace_id,
        supplied_snapshot=document_snapshot,
    )
    readiness = _without_volatile_timestamps(
        _fresh_readiness(
            workspace_id,
            document_snapshot=(
                {**document_snapshot, "workspace_id": workspace_id}
                if isinstance(document_snapshot, dict)
                else None
            ),
        )
    )
    sources = _source_basis_snapshot(workspace_id)

    artifacts: dict[str, dict[str, Any]] = {}
    artifact_values: dict[str, Any] = {}
    for name in _GOVERNED_SNAPSHOTS:
        summary, value = _json_snapshot(
            workspace_id,
            name,
        )
        artifacts[name] = summary
        artifact_values[name] = value
    appendix_files = _appendix_files_snapshot(
        workspace_id, artifact_values.get("appendix_manifest"),
    )

    binding_summary, binding_value = _json_snapshot(
        workspace_id,
        "finance_binding",
    )
    binding = binding_value if isinstance(binding_value, dict) else {}
    run_id = str(expected_run_id or binding.get("finance_run_id") or "")
    if not run_id:
        # MCP 边界无持久化 finance_binding：绑定退化为最新 run（MCP gate 语义）。
        try:
            from lvke_mcp.domains.finance import run_store

            run_id = str((run_store.latest_run(workspace_id) or {}).get("run_id") or "")
        except Exception:  # noqa: BLE001 - 无 run 时按未绑定处理
            run_id = ""
    run = _load_finance_run(workspace_id, run_id)
    finance = {
        "binding": binding_summary,
        "binding_snapshot": copy.deepcopy(binding),
        "run_id": run_id,
        "run_kind": (
            "asset_acquisition" if run_id.startswith("acqrun_")
            else "feasibility_finance" if run_id else "none"
        ),
        "run_present": isinstance(run, dict) and not run.get("_load_error"),
        "run_hash": _canonical_hash(
            run if isinstance(run, dict) else {"run_id": run_id, "present": False}
        ),
        "run_snapshot": copy.deepcopy(run) if isinstance(run, dict) else None,
        "publish_gate": _without_volatile_timestamps(
            _finance_quality_snapshot(workspace_id, expected_run_id=run_id)
        ),
    }
    # Capture narrative verification in the immutable basis used by both draft
    # and formal artifacts. Previously validation computed this separately,
    # while draft export only saw the publish gate, so detected mismatches were
    # omitted from blocker_summary and the DOCX cover page.
    try:
        from lvke_mcp.domains.finance import gate as finance_gate

        finance["narrative"] = _without_volatile_timestamps(
            finance_gate.verify_narrative_numbers(
                workspace_id,
                content,
                run_id=run_id,
            )
        )
    except Exception as exc:  # noqa: BLE001 - basis remains fail-closed
        finance["narrative"] = {
            "ok": False,
            "run_id": run_id or None,
            "matches": [],
            "mismatches": [],
            "unmapped": [],
            "error": type(exc).__name__,
        }
    meta_doc_kind = str(meta.get("doc_kind") or "")
    material = {
        "schema_version": BASIS_SCHEMA_VERSION,
        "workspace_id": workspace_id,
        "workspace_version": meta.get("workspace_version"),
        "report_type": str(meta.get("report_type") or ""),
        "doc_kind": meta_doc_kind or doc_service.DEFAULT_DOC_KIND,
        "template_version": template_version,
        "report_revision_id": str(report_revision_id or ""),
        "evidence_policy": formal_lineage.get("evidence_policy"),
        "evidence_origin": formal_lineage.get("evidence_origin"),
        "project_fact_certified": formal_lineage.get("project_fact_certified"),
        "formal_promotion": copy.deepcopy(formal_lineage.get("formal_promotion")),
        "document": document,
        "sources": sources,
        "readiness": {
            "hash": _canonical_hash(readiness),
            "snapshot": readiness,
        },
        "finance": finance,
        "artifacts": artifacts,
        "appendix_files": appendix_files,
    }
    basis = {**material, "fingerprint": _canonical_hash(material)}
    context = {
        "meta": meta,
        "artifact_values": artifact_values,
    }
    return basis, content, context


def _readiness_blockers(readiness: dict[str, Any]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for raw in readiness.get("blockers") or []:
        if isinstance(raw, dict):
            blockers.append({
                "code": str(raw.get("code") or "readiness_blocker"),
                "message": str(raw.get("message") or "发布就绪度存在阻断项"),
                "details": copy.deepcopy(raw.get("details") or {}),
            })
        else:
            blockers.append({
                "code": "readiness_blocker",
                "message": str(raw),
                "details": {},
            })
    known = {item["code"] for item in blockers}
    for code in readiness.get("blocking_issues") or []:
        value = str(code or "readiness_blocker")
        if value not in known:
            blockers.append({"code": value, "message": value, "details": {}})
            known.add(value)
    if readiness.get("error") and "readiness_error" not in known:
        blockers.append({
            "code": "readiness_error",
            "message": "发布就绪度计算存在错误",
            "details": {"error": readiness.get("error")},
        })
    return blockers


def _marker_markdown(
    content: str,
    readiness: dict[str, Any],
    *,
    additional_blockers: Sequence[dict[str, Any]] = (),
) -> tuple[str, dict[str, Any]]:
    quality_issues = [
        *_readiness_blockers(readiness),
        *(copy.deepcopy(item) for item in additional_blockers),
    ]
    deduped_quality_issues: list[dict[str, Any]] = []
    seen_quality_issues: set[tuple[str, str]] = set()
    for item in quality_issues:
        key = (str(item.get("code") or ""), str(item.get("message") or ""))
        if key not in seen_quality_issues:
            seen_quality_issues.add(key)
            deduped_quality_issues.append(item)
    quality_issues = deduped_quality_issues
    warnings: list[dict[str, Any]] = []
    for raw in readiness.get("warnings") or []:
        if isinstance(raw, dict):
            warnings.append({
                "code": str(raw.get("code") or "warning"),
                "message": str(raw.get("message") or "需人工复核"),
                "details": copy.deepcopy(raw.get("details") or {}),
            })
        else:
            warnings.append({"code": "warning", "message": str(raw), "details": {}})
    lines = [
        f"# {DRAFT_MARKER}",
        "",
        "> 本文件为**验证草稿**，供输入核对与修订使用。内容受当前输入快照、来源绑定和完整性状态约束。",
        "",
        "## 阻断项与警告摘要",
        "",
    ]
    if quality_issues:
        lines.extend(
            f"- 质量提示 [{item['code']}]：{item['message']}" for item in quality_issues
        )
    else:
        lines.append("- 当前自动检查未发现质量提示。")
    lines.extend(
        f"- 警告 [{item['code']}]：{item['message']}" for item in warnings
    )
    lines.extend(["", "---", "", content])
    summary = {
        "blockers": [],
        "quality_issues": quality_issues,
        "warnings": warnings,
        "blocker_count": 0,
        "quality_issue_count": len(quality_issues),
        "warning_count": len(warnings),
    }
    return "\n".join(lines), summary


def _draft_basis_quality_issues(
    basis: dict[str, Any],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Summarize basis quality issues for the output manifest."""

    blockers: list[dict[str, Any]] = []
    for name in _GOVERNED_SNAPSHOTS:
        snapshot = (basis.get("artifacts") or {}).get(name) or {}
        if snapshot.get("present") is not True or snapshot.get("error"):
            blockers.append({
                "code": "governed_snapshot_incomplete",
                "message": f"{name} 快照缺失或损坏",
                "details": {"artifact": name, "error": snapshot.get("error")},
            })
    finance = basis.get("finance") or {}
    run = finance.get("run_snapshot")
    if not finance.get("run_id"):
        blockers.append({
            "code": "finance_binding_required",
            "message": "尚未绑定财务 run",
            "details": {},
        })
    elif not isinstance(run, dict) or run.get("_load_error"):
        blockers.append({
            "code": "finance_run_unavailable",
            "message": "绑定的财务 run 不存在或不可读",
            "details": {"run_id": finance.get("run_id")},
        })
    finance_gate = finance.get("publish_gate") or {}
    run_snapshot = finance.get("run_snapshot") or {}
    if str(run_snapshot.get("evidence_policy") or "") not in {"formal_evidence", "sim_a_formal"}:
        blockers.append({
            "code": "FORMAL_ARTIFACT_QUALIFICATION_REQUIRED",
            "message": "财务运行仍处于非正式证据轨，禁止正式报告工件",
            "details": {"evidence_policy": run_snapshot.get("evidence_policy")},
        })
    run_mode = str(
        run_snapshot.get("mode")
        or run_snapshot.get("delivery_mode")
        or run_snapshot.get("assurance_level")
        or ""
    )
    if run_mode == "estimate_preview":
        blockers.append({
            "code": "FORMAL_ARTIFACT_QUALIFICATION_REQUIRED",
            "message": "预览运行不得生成正式报告工件",
            "details": {"mode": run_snapshot.get("mode"), "delivery_mode": run_snapshot.get("delivery_mode")},
        })
    narrative = (
        finance_gate.get("finance_narrative")
        or finance.get("narrative")
        or {}
    )
    if isinstance(narrative, dict) and (
        narrative.get("mismatches")
        or narrative.get("unmapped")
        or narrative.get("error")
    ):
        mismatches = list(narrative.get("mismatches") or [])
        unmapped = list(narrative.get("unmapped") or [])
        blockers.append({
            "code": "finance_narrative_mismatch",
            "message": str(
                narrative.get("message") or "正文数字与财务运行不一致"
            ),
            "details": {
                "mismatch_count": len(mismatches),
                "mismatches": mismatches[:20],
                "unmapped_count": len(unmapped),
                "unmapped": unmapped[:20],
            },
        })
    for raw in finance_gate.get("blockers") or []:
        if isinstance(raw, dict):
            blockers.append({
                "code": str(raw.get("code") or "finance_publish_gate_blocked"),
                "message": str(raw.get("message") or "财务正式发布门禁未通过"),
                "details": copy.deepcopy(raw.get("details") or {}),
            })
    for appendix_file in basis.get("appendix_files") or []:
        if appendix_file.get("ok") is not True:
            blockers.append({
                "code": "appendix_file_inconsistent",
                "message": "已就绪附表文件不存在或哈希不一致",
                "details": copy.deepcopy(appendix_file),
            })
    return blockers

# 门面模块的公开面。显式声明而不是靠"碰巧 import 了"——API 快照门禁
# (tests/integration/test_refactor_guardrails.py) 要求这些 re-export 保持
# 可达,而 ruff F401 会把它们判成未使用。写成 __all__ 让两个门禁同时成立,
# 也让"哪些名字是刻意对外的"可读。
__all__ = [
    "Any",
    "BASIS_SCHEMA_VERSION",
    "DRAFT_MARKER",
    "DeliverableArtifactError",
    "Sequence",
    "_GOVERNED_SNAPSHOTS",
    "_appendix_files_snapshot",
    "_canonical_hash",
    "_capture_basis",
    "_document_snapshot",
    "_draft_basis_quality_issues",
    "_fresh_readiness",
    "_json_snapshot",
    "_load_finance_run",
    "_marker_markdown",
    "_readiness_blockers",
    "_source_basis_snapshot",
    "_finance_quality_snapshot",
    "_without_volatile_timestamps",
    "copy",
    "doc_service",
]
