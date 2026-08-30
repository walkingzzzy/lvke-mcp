#!/usr/bin/env python3
"""G3 formal candidate acceptance: EVD-2 gate + seeded formal export probes."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(SCRIPTS))

from acceptance_common import call_tool, classify_outcome  # noqa: E402
from g1_golden_chain import run_golden_chain  # noqa: E402
from lvke_mcp.domains.reports.docx_fonts import audit_docx_fonts  # noqa: E402
from lvke_mcp.runtime.build_metadata import build_metadata  # noqa: E402
from lvke_mcp.runtime.release_preflight import run_release_preflight  # noqa: E402
from lvke_mcp.runtime.soffice import resolve_soffice_binary, run_soffice_convert  # noqa: E402

REPORTS = ROOT / "dev-docs" / "reports"
def _call(module: str, tool: str, args: dict[str, Any], data_dir: str, timeout: int = 120) -> dict[str, Any]:
    payload, error = call_tool(module, tool, args, data_dir=data_dir, timeout=timeout)
    if error:
        return {"success": False, "code": "protocol_error", "message": error}
    return payload if isinstance(payload, dict) else {"success": False, "code": "empty_payload"}


INDUSTRY_CASES = (
    {
        "profile": "tourism_catering",
        "sentence": "在湖北建设一座儿童游乐园并编制可行性研究报告",
        "project_name": "G3晋升游乐园",
        "full_chain": True,
    },
    {
        "profile": "real_estate",
        "sentence": "在武汉开发一个住宅房地产项目并编制可行性研究报告",
        "project_name": "G3晋升房地产",
        "full_chain": True,
    },
    {
        "profile": "manufacturing",
        "sentence": "在湖北建设一座装备制造厂房并编制可行性研究报告",
        "project_name": "G3晋升制造",
        "full_chain": False,
    },
    {
        "profile": "environment_utilities",
        "sentence": "在湖北建设一座污水处理厂并编制可行性研究报告",
        "project_name": "G3晋升环保",
        "full_chain": False,
    },
    {
        "profile": "park_infrastructure",
        "sentence": "在湖北建设一个产业园并编制可行性研究报告",
        "project_name": "G3晋升园区",
        "full_chain": False,
    },
    {
        "profile": "urban_rail_transit",
        "sentence": "在武汉建设一条地铁延伸线并编制可行性研究报告",
        "project_name": "G3晋升城轨",
        "full_chain": False,
    },
    {
        "profile": "cemetery_funeral",
        "sentence": "在湖北建设一座经营性公墓并编制可行性研究报告",
        "project_name": "G3晋升墓地",
        "full_chain": False,
    },
)


def _run_promoted_evidence_chain(
    workspace_id: str,
    data_dir: str,
    *,
    sentence: str = "在湖北建设一座儿童游乐园并编制可行性研究报告",
    project_name: str = "G3晋升游乐园",
    industry_code: str = "tourism_catering",
    full_chain: bool = False,
) -> dict[str, Any]:
    """Confirm Sim-A assumptions, promote, and count EVD-2 on the new chain."""

    zmd = "lvke_mcp.servers.lvke_zero_material_delivery.server"
    review = "lvke_mcp.servers.lvke_deliverable_review.server"
    sources = "lvke_mcp.servers.lvke_source_files.server"
    idem = f"g3-promo-{uuid.uuid4().hex[:8]}"
    created = _call(
        zmd,
        "delivery_create_from_sentence",
        {
            "workspace_id": workspace_id,
            "sentence": sentence,
            "project_name": project_name,
            "region": "湖北省",
            "idempotency_key": f"{idem}-create",
        },
        data_dir,
    )
    run_id = str((created.get("delivery_run") or {}).get("delivery_run_id") or "")
    if not created.get("success") or not run_id:
        return {"ok": False, "code": created.get("code") or "create_failed", "evd2": 0, "status": {}, "requirement_ids": []}
    started = _call(
        zmd,
        "delivery_start",
        {"workspace_id": workspace_id, "delivery_run_id": run_id, "idempotency_key": f"{idem}-start"},
        data_dir,
    )
    package_id = str(
        (started.get("assumption_package") or {}).get("assumption_package_id")
        or (started.get("delivery_run") or {}).get("assumption_package_id")
        or ""
    )
    if not package_id:
        return {"ok": False, "code": "assumption_package_missing", "evd2": 0, "status": {}, "requirement_ids": []}
    for index in range(8):
        listed = _call(
            zmd,
            "delivery_list_assumptions",
            {"workspace_id": workspace_id, "assumption_package_id": package_id, "limit": 10},
            data_dir,
        )
        items = [item for item in listed.get("confirmation_items") or [] if isinstance(item, dict)]
        if not items:
            break
        confirmed = _call(
            zmd,
            "delivery_confirm_assumptions",
            {
                "workspace_id": workspace_id,
                "assumption_package_id": package_id,
                "confirmations": [
                    {"name": item["name"], "value": item.get("value"), "note": "G3 全量确认"}
                    for item in items
                ],
                "idempotency_key": f"{idem}-confirm-{index}",
            },
            data_dir,
        )
        package_id = str(
            (confirmed.get("assumption_package") or {}).get("assumption_package_id") or package_id
        )
        run_id = str((confirmed.get("delivery_run") or {}).get("delivery_run_id") or run_id)
        if not confirmed.get("success"):
            return {"ok": False, "code": confirmed.get("code") or "confirm_failed", "evd2": 0, "status": {}, "requirement_ids": []}
    packed = _call(
        zmd,
        "delivery_generate_template_pack",
        {"workspace_id": workspace_id, "delivery_run_id": run_id, "idempotency_key": f"{idem}-pack"},
        data_dir,
        timeout=180,
    )
    if not packed.get("success"):
        return {"ok": False, "code": packed.get("code") or "pack_failed", "evd2": 0, "status": {}, "requirement_ids": []}
    promoted = _call(
        zmd,
        "delivery_confirm_formal_promotion",
        {
            "workspace_id": workspace_id,
            "template_pack_id": packed.get("template_pack_id"),
            "responsible_party": "G3 验收责任方",
            "confirmation_note": "确认将拟定模板包导入新可研链并计数 EVD-2",
            "idempotency_key": f"{idem}-promo",
        },
        data_dir,
        timeout=180,
    )
    if not promoted.get("success"):
        return {"ok": False, "code": promoted.get("code") or "promo_failed", "evd2": 0, "status": {}, "requirement_ids": []}
    resolved = _call(
        review,
        "review_resolve_standards",
        {
            "workspace_id": workspace_id,
            "project_context": {
                "project_type": "generic_feasibility",
                "target_type": "report_revision",
                "evidence_track": "sim_a_formal",
            },
            "facilities": [],
            "idempotency_key": f"{idem}-std",
        },
        data_dir,
    )
    applicability_id = str(resolved.get("standard_applicability_id") or "")
    listed = _call(
        review,
        "review_list_requirements",
        {"workspace_id": workspace_id, "standard_applicability_id": applicability_id},
        data_dir,
    )
    requirement_ids = [
        str(item.get("requirement_id") or "")
        for item in listed.get("requirements") or resolved.get("applicable_requirements") or []
        if str(item.get("requirement_id") or "")
    ]
    if not requirement_ids:
        requirement_ids = [
            str(item)
            for item in packed.get("requirement_ids") or []
            if str(item)
        ]
    imported = [
        item for item in promoted.get("imported_files") or []
        if isinstance(item, dict) and str(item.get("filename") or "").endswith(".md")
    ]
    status: dict[str, str] = {}
    evd2 = 0
    for requirement_id in requirement_ids:
        match = next(
            (
                item for item in imported
                if str(item.get("filename") or "") == f"{requirement_id}.md"
            ),
            None,
        )
        if match is None:
            status[requirement_id] = "pending_evidence"
            continue
        file_id = str(match.get("file_id") or "")
        fetched = _call(
            sources,
            "source_file_get",
            {"workspace_id": workspace_id, "file_id": file_id},
            data_dir,
        )
        record = fetched.get("source_file") if isinstance(fetched.get("source_file"), dict) else fetched
        digest = str((record or {}).get("sha256") or match.get("sha256") or "")
        if digest and not str(digest).startswith("sha256:"):
            digest = f"sha256:{digest}"
        attached = _call(
            review,
            "review_attach_requirement_evidence",
            {
                "workspace_id": workspace_id,
                "standard_applicability_id": applicability_id,
                "requirement_id": requirement_id,
                "resource_uri": f"lvke://source-files/workspaces/{workspace_id}/files/{file_id}",
                "locator": f"{requirement_id}.md",
                "content_hash": digest,
                "evidence_track": "sim_a_formal",
                "idempotency_key": f"{idem}-attach-{requirement_id}",
            },
            data_dir,
        )
        if attached.get("success"):
            evd2 += 1
            status[requirement_id] = "evd2_sim_a_formal"
        else:
            status[requirement_id] = str(attached.get("code") or "attach_failed")
    from lvke_mcp.testing.sim_a_formal_acceptance import (
        run_sim_a_formal_finance,
        run_sim_a_formal_full_chain,
    )

    os.environ["LVKE_MCP_DATA_DIR"] = data_dir
    finance_chain = run_sim_a_formal_finance(
        workspace_id=workspace_id,
        file_ids=list(promoted.get("file_ids") or []),
        project_name=project_name,
        industry_code=industry_code,
        case_key=idem,
    )
    formal = dict(finance_chain)
    if full_chain and finance_chain.get("ok"):
        formal = run_sim_a_formal_full_chain(
            finance_chain,
            case_key=f"{idem}-full",
            industry_code=industry_code,
        )
    visual = _audit_docx_visual(
        formal.get("report_export") or {},
        persist_dir=REPORTS / "g3-visual" / industry_code,
    ) if full_chain else {
        "ok": False,
        "skipped": True,
        "kind": "soffice_conversion_probe",
        "visual_page_inspection": False,
    }
    evd2_ok = evd2 == len(requirement_ids) and evd2 > 0
    finance_ok = bool(formal.get("finance_run_id"))
    return {
        "ok": evd2_ok and finance_ok and (not full_chain or bool(formal.get("ok"))),
        "code": "" if evd2_ok else "evd2_incomplete",
        "evd2": evd2,
        "status": status,
        "requirement_ids": requirement_ids,
        "promotion_id": str(promoted.get("promotion_id") or ""),
        "file_ids": list(promoted.get("file_ids") or []),
        "context_ok": bool(formal.get("project_context_id")),
        "ingest_ok": bool(formal.get("evidence_pack_id")),
        "evidence_pack_ok": bool(formal.get("evidence_pack_id")),
        "finance_run_ok": finance_ok,
        "tables_ok": bool(formal.get("tables_ok")),
        "report_export_ok": bool(formal.get("report_export_ok")),
        "review_retest_export": bool(formal.get("review_retest_export")),
        "release_ok": bool(formal.get("release_ok")),
        "release_code": str((formal.get("release") or {}).get("code") or formal.get("step") or ""),
        "run_id": str(formal.get("finance_run_id") or ""),
        "report_revision_id": str(formal.get("report_revision_id") or ""),
        "docx_visual": visual,
        "docx_visual_acceptance": bool(visual.get("ok")),
        "chain_step": str(formal.get("step") or ""),
        "chain_payload": formal.get("payload") if not formal.get("ok") else {},
    }


def _applicable_requirement_ids(workspace_id: str, data_dir: str) -> list[str]:
    resolved, error = call_tool(
        "lvke_mcp.servers.lvke_deliverable_review.server",
        "review_resolve_standards",
        {
            "workspace_id": workspace_id,
            "project_context": {
                "project_type": "generic_feasibility",
                "target_type": "report_revision",
            },
            "facilities": [],
            "idempotency_key": f"g3-std-{workspace_id}",
        },
        data_dir=data_dir,
        timeout=60,
    )
    if error or not resolved.get("success"):
        return []
    listed, list_error = call_tool(
        "lvke_mcp.servers.lvke_deliverable_review.server",
        "review_list_requirements",
        {
            "workspace_id": workspace_id,
            "standard_applicability_id": str(resolved.get("standard_applicability_id") or ""),
        },
        data_dir=data_dir,
        timeout=60,
    )
    if list_error or not listed.get("success"):
        return [
            str(item.get("requirement_id") or "")
            for item in resolved.get("applicable_requirements") or []
            if str(item.get("requirement_id") or "")
        ]
    return [
        str(item.get("requirement_id") or "")
        for item in listed.get("requirements") or []
        if str(item.get("requirement_id") or "")
    ]


def _png_conversion_probe(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return {"ok": False, "error": "invalid_png", "bytes": len(data)}
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    size = path.stat().st_size
    return {
        "ok": width >= 400 and height >= 400 and size >= 4096,
        "width": width,
        "height": height,
        "bytes": size,
    }


def _audit_docx_visual(
    export_payload: dict[str, Any],
    *,
    persist_dir: Path | None = None,
) -> dict[str, Any]:
    """Font/glyph audit plus soffice PDF/PNG conversion probe.

    This is not a page-by-page visual acceptance: it does not inspect Chinese
    visibility, cropping, blank pages, tables, or pagination.
    """

    root = Path(str(export_payload.get("deliverable_path") or ""))
    docx_path = None
    if root.is_dir():
        matches = sorted(root.glob("*.docx"))
        docx_path = matches[0] if matches else None
    elif root.is_file() and root.suffix.lower() == ".docx":
        docx_path = root
    if docx_path is None or not docx_path.is_file():
        return {
            "ok": False,
            "kind": "soffice_conversion_probe",
            "visual_page_inspection": False,
            "fonts_ok": False,
            "soffice_unavailable": False,
            "error": "docx_not_found",
            "page_png_count": 0,
        }
    audit = audit_docx_fonts(docx_path.read_bytes())
    fonts_ok = bool(
        audit.get("portable_cjk_fonts")
        and int(audit.get("embedded_font_count") or 0) > 0
        and int(audit.get("invalid_locale_font_count") or 0) == 0
        and all(int(item.get("missing_cjk_glyph_count") or 0) == 0 for item in audit.get("embedded_fonts") or [])
    )
    soffice = resolve_soffice_binary()
    if not soffice:
        return {
            "ok": False,
            "kind": "soffice_conversion_probe",
            "visual_page_inspection": False,
            "fonts_ok": fonts_ok,
            "font_audit": audit,
            "soffice_unavailable": True,
            "page_png_count": 0,
            "note": "soffice unavailable; conversion probe failed",
        }
    with tempfile.TemporaryDirectory(prefix="lvke-g3-docx-") as tmp:
        work = Path(tmp)
        converted = run_soffice_convert(
            source=docx_path,
            convert_to="pdf",
            outdir=work,
            binary=soffice,
            timeout=180,
            check=False,
        )
        pdfs = list(work.glob("*.pdf"))
        if converted.returncode != 0 or not pdfs:
            return {
                "ok": False,
                "kind": "soffice_conversion_probe",
                "visual_page_inspection": False,
                "fonts_ok": fonts_ok,
                "font_audit": audit,
                "soffice_unavailable": False,
                "error": "soffice_pdf_failed",
                "stderr": converted.stderr[-500:],
                "page_png_count": 0,
            }
        png_dir = work / "pages"
        png_dir.mkdir()
        png_cmd = run_soffice_convert(
            source=pdfs[0],
            convert_to="png",
            outdir=png_dir,
            binary=soffice,
            timeout=180,
            check=False,
        )
        pages = sorted(path for path in png_dir.glob("*.png") if path.stat().st_size > 0)
        if png_cmd.returncode != 0 or not pages:
            return {
                "ok": False,
                "kind": "soffice_conversion_probe",
                "visual_page_inspection": False,
                "fonts_ok": fonts_ok,
                "font_audit": audit,
                "soffice_unavailable": False,
                "error": "soffice_png_failed",
                "stderr": png_cmd.stderr[-500:],
                "page_png_count": 0,
            }
        probes = [_png_conversion_probe(path) for path in pages]
        saved: list[str] = []
        if persist_dir is not None:
            persist_dir.mkdir(parents=True, exist_ok=True)
            for path in pages:
                target = persist_dir / path.name
                shutil.copy2(path, target)
                saved.append(str(target.relative_to(ROOT)) if target.is_relative_to(ROOT) else str(target))
        pages_ok = bool(probes) and all(item.get("ok") for item in probes)
        return {
            "ok": fonts_ok and pages_ok,
            "kind": "soffice_conversion_probe",
            "visual_page_inspection": False,
            "fonts_ok": fonts_ok,
            "font_audit": audit,
            "soffice_unavailable": False,
            "page_png_count": len(pages),
            "page_png_paths": saved,
            "page_probes": probes,
            "note": (
                "soffice PDF/PNG conversion probe only; no CJK visibility, "
                "crop, blank-page, table or pagination inspection"
            ),
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class GateRecord:
    name: str
    status: str
    passed: list[str]
    failed: list[str]
    blockers: list[str]


@dataclass
class ProbeRecord:
    tool: str
    server: str
    classification: str
    status: str
    code: str
    trace_id: str
    protocol_error: str


def _chain_ids(steps: list) -> dict[str, str]:
    ids: dict[str, str] = {}
    for step in steps:
        if step.object_id:
            ids[step.step] = step.object_id
        if step.step == "FinanceRun":
            ids["run_id"] = step.object_id
        if step.step == "DeliveryRun":
            ids["delivery_run_id"] = step.object_id
        if step.step == "Review":
            ids["review_id"] = step.object_id
        if step.step == "ReportRevision":
            ids["report_revision_id"] = step.object_id
    return ids


def run_g3_checks(workspace_id: str, data_dir: Path) -> tuple[list[GateRecord], list[ProbeRecord], dict[str, Any], list]:
    meta = build_metadata()
    gates: list[GateRecord] = []
    probes: list[ProbeRecord] = []
    shared = str(data_dir)

    chain_steps = run_golden_chain(workspace_id, data_dir)
    ids = _chain_ids(chain_steps)

    def calculation_checks() -> tuple[list[str], list[str]]:
        passed: list[str] = []
        failed: list[str] = []
        chain_results = {step.step: step for step in chain_steps}
        finance = chain_results.get("FinanceRun")
        tables = chain_results.get("FinanceTablesPackage")
        if finance and tables and finance.classification == "PASS" and tables.classification == "PASS":
            passed.append("synthetic finance calculation and tables completed")
        else:
            failed.append("independent calculation chain incomplete")
        return passed, failed

    preview_preflight = run_release_preflight(
        calculation_checks=calculation_checks,
        required_artifacts=[],
        evd_distribution={"EVD-0": 20, "EVD-1": 4, "EVD-2": 0},
        sim_a_present=True,
        sim_a_formal=False,
        build_metadata_complete=meta.complete,
        metadata_matches_commit=meta.complete,
        formal_evidence="none — unpromoted SIM-A / controlled_assumption only",
        require_artifact_checks=True,
    )
    pf = preview_preflight.to_dict()
    gates.append(
        GateRecord(
            name="preview_release_preflight",
            status="pass" if pf.get("release_ready") else "blocked",
            passed=[g["name"] + ": " + g["status"] for g in pf.get("gates", []) if g.get("status") == "pass"],
            failed=[g["name"] + ": " + g["status"] for g in pf.get("gates", []) if g.get("status") != "pass"],
            blockers=list(pf.get("blockers") or []),
        )
    )

    idem = f"g3-{uuid.uuid4().hex[:8]}"
    review_id = ids.get("review_id") or "missing-review"
    revision_id = ids.get("report_revision_id") or "missing-revision"
    delivery_run_id = ids.get("delivery_run_id") or "missing-fdr"

    probe_specs = (
        (
            "review_export",
            "lvke_mcp.servers.lvke_deliverable_review.server",
            "lvke-deliverable-review",
            {
                "workspace_id": workspace_id,
                "review_id": review_id,
                "formats": ["docx", "xlsx"],
                "idempotency_key": f"{idem}-review-export",
            },
        ),
        (
            "report_export_docx",
            "lvke_mcp.servers.lvke_report_generation.server",
            "lvke-report-generation",
            {
                "workspace_id": workspace_id,
                "report_revision_id": revision_id,
                "kind": "formal_candidate",
            },
        ),
        (
            "feasibility_release",
            "lvke_mcp.servers.lvke_feasibility_delivery.server",
            "lvke-feasibility-delivery",
            {
                "workspace_id": workspace_id,
                "delivery_run_id": delivery_run_id,
                "release_scope": "project_delivery",
                "idempotency_key": f"{idem}-fdr-release",
            },
        ),
    )

    for tool, module, server, args in probe_specs:
        payload, protocol_error = call_tool(module, tool, args, data_dir=shared, timeout=120)
        classification = classify_outcome(payload, protocol_error=protocol_error)
        probes.append(
            ProbeRecord(
                tool=tool,
                server=server,
                classification=classification,
                status=str(payload.get("status") or ""),
                code=str(payload.get("code") or ""),
                trace_id=str(payload.get("trace_id") or ""),
                protocol_error=str(protocol_error or ""),
            )
        )

    preview_requirement_ids = _applicable_requirement_ids(workspace_id, shared)
    industry_results = []
    promotion = {}
    for case in INDUSTRY_CASES:
        row = _run_promoted_evidence_chain(
            workspace_id,
            shared,
            sentence=str(case["sentence"]),
            project_name=str(case["project_name"]),
            industry_code=str(case["profile"]),
            full_chain=bool(case["full_chain"]),
        )
        row["profile"] = case["profile"]
        row["full_chain"] = bool(case["full_chain"])
        industry_results.append(row)
        if case["profile"] == "tourism_catering":
            promotion = row
    requirement_ids = list(promotion.get("requirement_ids") or preview_requirement_ids)
    evd2 = int(promotion.get("evd2") or 0)
    status = dict(promotion.get("status") or {})
    for item in requirement_ids:
        status.setdefault(item, "pending_evidence")

    def formal_calculation_checks() -> tuple[list[str], list[str]]:
        passed: list[str] = []
        failed: list[str] = []
        finance_ok = all(bool(item.get("finance_run_ok")) for item in industry_results)
        if finance_ok and industry_results:
            passed.append("promoted finance runs completed")
        else:
            failed.append("promoted finance chain incomplete")
        return passed, failed

    formal_preflight = run_release_preflight(
        calculation_checks=formal_calculation_checks,
        required_artifacts=[],
        evd_distribution={"EVD-0": 0, "EVD-1": 0, "EVD-2": evd2},
        required_evd2_count=len(requirement_ids),
        sim_a_present=True,
        sim_a_formal=True,
        build_metadata_complete=meta.complete,
        metadata_matches_commit=meta.complete,
        formal_evidence="sim_a_formal template pack attached as EVD-2 (not authentic originals)",
        require_artifact_checks=True,
    )
    formal_pf = formal_preflight.to_dict()
    gates.append(
        GateRecord(
            name="formal_release_preflight",
            status="pass" if formal_pf.get("release_ready") else "blocked",
            passed=[
                g["name"] + ": " + g["status"]
                for g in formal_pf.get("gates", [])
                if g.get("status") == "pass"
            ],
            failed=[
                g["name"] + ": " + g["status"]
                for g in formal_pf.get("gates", [])
                if g.get("status") != "pass"
            ],
            blockers=list(formal_pf.get("blockers") or []),
        )
    )
    formal_evidence_ok = (formal_pf.get("evidence_gate") or {}).get("status") == "pass"
    summary = {
        "p0_total": len(requirement_ids),
        "p0_evd2_count": evd2,
        "p0_status": status,
        "requirement_ids": requirement_ids,
        "formal_candidate_eligible": bool(
            evd2 == len(requirement_ids) and evd2 > 0 and promotion.get("ok")
        ),
        "preview_release_ready": pf.get("release_ready"),
        "formal_evidence_ready": formal_evidence_ok,
        "formal_release_ready": formal_pf.get("release_ready"),
        "release_ready": formal_pf.get("release_ready"),
        "preview_preflight": pf,
        "formal_preflight": formal_pf,
        "seeded_object_ids": ids,
        "denominator": "review_list_requirements",
        "preview_requirement_ids": preview_requirement_ids,
        "industry_results": [
            {
                "profile": item.get("profile"),
                "ok": item.get("ok"),
                "finance_run_ok": item.get("finance_run_ok"),
                "tables_ok": item.get("tables_ok"),
                "report_export_ok": item.get("report_export_ok"),
                "review_retest_export": item.get("review_retest_export"),
                "release_ok": item.get("release_ok"),
                "release_code": item.get("release_code"),
                "docx_visual_acceptance": item.get("docx_visual_acceptance"),
                "chain_step": item.get("chain_step"),
                "full_chain": item.get("full_chain"),
            }
            for item in industry_results
        ],
        "promotion": {
            "ok": bool(promotion.get("ok")),
            "code": promotion.get("code") or "",
            "promotion_id": promotion.get("promotion_id") or "",
            "file_ids": list(promotion.get("file_ids") or []),
            "finance_run_ok": bool(promotion.get("finance_run_ok")),
            "docx_visual_acceptance": bool(promotion.get("docx_visual_acceptance")),
            "docx_visual": promotion.get("docx_visual") or {},
            "review_retest_export": bool(promotion.get("review_retest_export")),
            "report_export_ok": bool(promotion.get("report_export_ok")),
            "release_ok": bool(promotion.get("release_ok")),
        },
    }
    return gates, probes, summary, chain_steps


def write_report(
    workspace_id: str,
    gates: list[GateRecord],
    probes: list[ProbeRecord],
    summary: dict[str, Any],
    meta: dict[str, Any],
    chain_steps: list,
) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    json_path = REPORTS / f"G3_FORMAL_CANDIDATE_{stamp}.json"
    md_path = REPORTS / "G3_FORMAL_CANDIDATE_ACCEPTANCE.md"

    payload = {
        "generated_at": _utc_now(),
        "workspace_id": workspace_id,
        "build_metadata": meta,
        "summary": summary,
        "gates": [asdict(g) for g in gates],
        "formal_export_probes": [asdict(p) for p in probes],
        "seed_chain_steps": len(chain_steps),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    business_rejections = [
        p for p in probes
        if p.classification == "EXPECTED_REJECTION" and not p.protocol_error
    ]
    unexpected_passes = [p for p in probes if p.classification == "PASS"]
    protocol_errors = [p for p in probes if p.protocol_error]

    lines = [
        "# G3 正式候选验收报告",
        "",
        f"- **生成时间（UTC）**：{payload['generated_at']}",
        f"- **工作区**：`{workspace_id}`",
        f"- **P0 EVD-2 计数**：{summary['p0_evd2_count']} / {summary['p0_total']}（当前目录 generic_feasibility 适用项，不是历史 24）",
        f"- **formal_candidate_eligible**：{summary['formal_candidate_eligible']}（仅表示拟定模板已按 `sim_a_formal` 附着，不是真实原件 EVD-2，也不等于 `release_ready`）",
        f"- **preview_release_ready**：{summary.get('preview_release_ready')}（未晋升 preview 预检，预期 false）",
        f"- **formal_evidence_ready**：{summary.get('formal_evidence_ready')}（晋升链证据关口，5/5 `sim_a_formal` 可过）",
        f"- **formal_release_ready / release_ready**：{summary.get('release_ready')}（同一套预检函数；脏树、缺 build_time、未配置正式工件时仍为 false）",
        f"- **build_metadata_complete**：{meta.get('build_metadata_complete')}",
        f"- **preview 分母**：{len(summary.get('preview_requirement_ids') or [])}",
        "- **正式导出探测对象**：未晋升 preview 金标链（须继续拒绝）",
        "- **完整 Review→Export→release**：仅 `tourism_catering` 与 `real_estate`；其余五档停在 FinanceRun",
        "",
        "## Release Preflight 四关口",
        "",
    ]
    for gate in gates:
        lines.append(f"### {gate.name}")
        lines.append(f"- status: **{gate.status}**")
        if gate.blockers:
            lines.append(f"- blockers: {', '.join(gate.blockers)}")
        lines.append("")

    lines += [
        "## 正式导出探测（须业务层 EXPECTED_REJECTION，非 -32602）",
        "",
        "| 工具 | 分类 | status | code | trace_id | protocol |",
        "|------|------|--------|------|----------|----------|",
    ]
    for probe in probes:
        lines.append(
            f"| `{probe.tool}` | {probe.classification} | {probe.status or '—'} | "
            f"{probe.code or '—'} | `{probe.trace_id[:16]}…` | {probe.protocol_error or '—'} |"
        )

    lines += [
        "",
        "## G3 退出条件核对",
        "",
        f"- [{' ' if summary['p0_evd2_count'] < summary['p0_total'] else 'x'}] 适用标准需求全部按 `sim_a_formal` 附着（分母={summary['p0_total']}，拟定模板 ≠ 真实原件）",
        f"- [{'x' if len(business_rejections) == len(probes) and not protocol_errors else ' '}] preview formal export 使用合法参数且业务拒绝",
        f"- [{' ' if unexpected_passes else 'x'}] 无意外 PASS（process 级导出允许时记为 P1 缺口：{', '.join(p.tool for p in unexpected_passes) or '无'}）",
        f"- [{'x' if not summary.get('preview_release_ready') else ' '}] preview 预检阻断未晋升 SIM-A",
        f"- [{'x' if summary.get('formal_evidence_ready') else ' '}] 晋升链证据关口承认 `sim_a_formal`（与 preview 共用同一预检函数）",
        f"- [{'x' if not summary['release_ready'] else ' '}] 正式 `release_ready` 仍为 false（构建元数据/工件关口，不只是证据）",
        f"- [{'x' if (summary.get('promotion') or {}).get('docx_visual_acceptance') else ' '}] DOCX 字体/glyph + soffice 转换探测（不是逐页视觉验收）",
        "- [ ] 逐页视觉验收（中文可见性/裁切/空白页/表格/分页：本轮未做）",
        f"- [{'x' if (summary.get('promotion') or {}).get('review_retest_export') else ' '}] Review → Retest → Export 完整闭环（仅游乐园/房地产两档，拟定 `sim_a_formal`）",
        "",
        "## 七档晋升链",
        "",
        "| 档 | 完整链 | FinanceRun | 报告导出 | 审查复测导出 | release | 视觉 |",
        "|----|--------|------------|----------|--------------|---------|------|",
    ]
    for item in summary.get("industry_results") or []:
        lines.append(
            f"| {item.get('profile')} | {item.get('full_chain')} | {item.get('finance_run_ok')} | "
            f"{item.get('report_export_ok')} | {item.get('review_retest_export')} | "
            f"{item.get('release_ok')} | {item.get('docx_visual_acceptance')} |"
        )
    lines += [
        "",
        f"详细 trace：`{json_path.relative_to(ROOT)}`",
        "",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default="", help="workspace_id")
    args = parser.parse_args()

    workspace_id = args.workspace.strip() or f"g3-formal-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    meta = build_metadata().envelope_fields()

    with tempfile.TemporaryDirectory(prefix="lvke-g3-acceptance-") as tmp:
        os.environ["LVKE_MCP_DATA_DIR"] = tmp
        gates, probes, summary, chain_steps = run_g3_checks(workspace_id, Path(tmp))

    write_report(workspace_id, gates, probes, summary, meta, chain_steps)
    protocol = sum(1 for p in probes if p.protocol_error)
    business = sum(1 for p in probes if p.classification == "EXPECTED_REJECTION" and not p.protocol_error)
    blocked = not summary.get("release_ready")
    print(f"probes_business_reject={business} protocol_errors={protocol} release_blocked={blocked}")
    return 0 if business == len(probes) and blocked and not protocol else 1


if __name__ == "__main__":
    raise SystemExit(main())
