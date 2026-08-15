"""正式资格门禁：财务硬门、basis 捕获与 readiness/正式 blocker 聚合。"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any


from lvke_mcp.domains.reports import doc_service

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


def _strict_finance_gate(
    workspace_id: str,
    *,
    expected_run_id: str = "",
) -> dict[str, Any]:
    """Run the authoritative finance publish gate even without finance evidence."""

    try:
        from lvke_mcp.domains.finance import gate as finance_gate

        value = finance_gate.assert_publish_finance_binding(
            workspace_id,
            strict=True,
            expected_run_id=expected_run_id,
        )
    except Exception as exc:  # noqa: BLE001 - normalized as a blocking result
        return {
            "ok": False,
            "blockers": [{
                "code": "finance_publish_gate_failed",
                "message": f"财务发布门禁执行失败: {type(exc).__name__}",
            }],
            "error": type(exc).__name__,
        }
    if not isinstance(value, dict):
        return {
            "ok": False,
            "blockers": [{
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
            _strict_finance_gate(workspace_id, expected_run_id=run_id)
        ),
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


def _basis_problem(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details}


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


def _assert_formal_basis(basis: dict[str, Any], context: dict[str, Any]) -> None:
    """Validate immutable inputs and readiness evidence for a formal artifact."""
    finance = basis.get("finance") or {}
    if (
        basis.get("doc_kind") != "feasibility"
        or basis.get("report_type") == "asset_acquisition"
        or finance.get("run_kind") == "asset_acquisition"
    ):
        raise DeliverableArtifactError(
            "FORMAL_ARTIFACT_TYPE_UNSUPPORTED",
            "通用正式工件仅支持非资产收购可行性研究报告",
            details={
                "doc_kind": basis.get("doc_kind"),
                "report_type": basis.get("report_type"),
                "finance_run_kind": finance.get("run_kind"),
            },
        )

    readiness = (basis.get("readiness") or {}).get("snapshot") or {}
    blockers = _readiness_blockers(readiness)
    if readiness.get("publishable") is not True or blockers:
        raise DeliverableArtifactError(
            "FORMAL_READINESS_BLOCKED",
            "当前发布就绪度存在阻断项，不能生成正式工件",
            details={"blockers": blockers, "readiness": copy.deepcopy(readiness)},
        )

    problems: list[dict[str, Any]] = []

    current_sources = basis.get("sources") or {}
    if current_sources.get("error"):
        problems.append(_basis_problem(
            "SOURCE_BASIS_UNAVAILABLE",
            "原始资料快照不可用",
            error=current_sources.get("error"),
        ))

    for name in _GOVERNED_SNAPSHOTS:
        current = (basis.get("artifacts") or {}).get(name) or {}
        if current.get("present") is not True or current.get("error"):
            problems.append(_basis_problem(
                "GOVERNED_SNAPSHOT_INCOMPLETE",
                f"{name} 快照缺失或损坏",
                artifact=name,
                error=current.get("error"),
            ))
    for appendix_file in basis.get("appendix_files") or []:
        if appendix_file.get("ok") is not True:
            problems.append(_basis_problem(
                "GOVERNED_APPENDIX_FILE_INCONSISTENT",
                "附表清单声明的已就绪文件不可用或哈希不一致",
                appendix_id=appendix_file.get("appendix_id"),
                source=appendix_file.get("source"),
                error=appendix_file.get("error"),
            ))

    run = finance.get("run_snapshot")
    binding = finance.get("binding_snapshot") or {}
    run_id = str(finance.get("run_id") or "")
    if str(binding.get("binding_kind") or "") == "asset_acquisition":
        problems.append(_basis_problem(
            "FINANCE_BINDING_KIND_UNSUPPORTED",
            "通用可研正式工件不能使用资产收购财务绑定",
            binding_kind=binding.get("binding_kind"),
        ))
    if finance.get("run_kind") != "feasibility_finance" or not run_id:
        problems.append(_basis_problem(
            "FINANCE_BINDING_REQUIRED",
            "正式工件必须绑定非资产收购财务 run",
            run_id=run_id,
            run_kind=finance.get("run_kind"),
        ))
    if not isinstance(run, dict) or run.get("_load_error"):
        problems.append(_basis_problem(
            "FINANCE_RUN_UNAVAILABLE",
            "finance_binding 指向的财务 run 不存在或不可读",
            run_id=run_id,
            error=(run or {}).get("_load_error") if isinstance(run, dict) else "not_found",
        ))
    elif str(run.get("workspace_id") or basis.get("workspace_id") or "") != basis.get("workspace_id"):
        problems.append(_basis_problem(
            "FINANCE_RUN_WORKSPACE_MISMATCH",
            "财务 run 与工作区不匹配",
            run_id=run_id,
        ))
    else:
        for field in (
            "input_hash",
            "spec_hash",
            "table_bundle_hash",
            "manifest_hash",
            "template_version",
        ):
            bound = binding.get(field)
            current = run.get(field)
            if bound not in (None, "") and bound != current:
                problems.append(_basis_problem(
                    "FINANCE_BINDING_HASH_MISMATCH",
                    "财务绑定字段与 FinanceRun 不一致",
                    field=field,
                    expected=bound,
                    actual=current,
                ))

    finance_gate = finance.get("publish_gate") or {}
    if finance_gate.get("ok") is not True or finance_gate.get("blockers"):
        problems.append(_basis_problem(
            "FINANCE_PUBLISH_GATE_BLOCKED",
            "财务正式发布门禁未通过",
            blockers=copy.deepcopy(finance_gate.get("blockers") or []),
            error=finance_gate.get("error"),
        ))
    if str(finance_gate.get("bound_run_id") or "") != run_id:
        problems.append(_basis_problem(
            "FINANCE_GATE_BOUND_RUN_MISMATCH",
            "财务门禁返回的绑定 run 与工件依据不一致",
            expected=run_id,
            actual=finance_gate.get("bound_run_id"),
        ))
    if problems:
        raise DeliverableArtifactError(
            "FORMAL_BASIS_INCONSISTENT",
            "正式工件的文档、财务或治理快照不一致",
            details={"problems": problems},
        )


def _marker_markdown(
    content: str,
    readiness: dict[str, Any],
    *,
    additional_blockers: Sequence[dict[str, Any]] = (),
) -> tuple[str, dict[str, Any]]:
    blockers = [
        *_readiness_blockers(readiness),
        *(copy.deepcopy(item) for item in additional_blockers),
    ]
    deduped_blockers: list[dict[str, Any]] = []
    seen_blockers: set[tuple[str, str]] = set()
    for item in blockers:
        key = (str(item.get("code") or ""), str(item.get("message") or ""))
        if key not in seen_blockers:
            seen_blockers.add(key)
            deduped_blockers.append(item)
    blockers = deduped_blockers
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
    if blockers:
        lines.extend(
            f"- 阻断项 [{item['code']}]：{item['message']}" for item in blockers
        )
    else:
        lines.append("- 当前自动检查未发现阻断项；本文件仍仅供内部复核。")
    lines.extend(
        f"- 警告 [{item['code']}]：{item['message']}" for item in warnings
    )
    lines.extend(["", "---", "", content])
    summary = {
        "blockers": blockers,
        "warnings": warnings,
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
    }
    return "\n".join(lines), summary


def _draft_basis_blockers(
    basis: dict[str, Any],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Summarize non-readiness gates that still prevent a formal artifact."""

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
