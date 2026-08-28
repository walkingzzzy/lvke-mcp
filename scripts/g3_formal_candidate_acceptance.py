#!/usr/bin/env python3
"""G3 formal candidate acceptance: EVD-2 gate + seeded formal export probes."""

from __future__ import annotations

import argparse
import json
import os
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
from lvke_mcp.runtime.build_metadata import build_metadata  # noqa: E402
from lvke_mcp.runtime.release_preflight import run_release_preflight  # noqa: E402

REPORTS = ROOT / "dev-docs" / "reports"
P0_ITEMS = [f"P0-{index:02d}" for index in range(1, 25)]


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

    preflight = run_release_preflight(
        calculation_checks=calculation_checks,
        required_artifacts=[],
        evd_distribution={"EVD-0": 20, "EVD-1": 4, "EVD-2": 0},
        sim_a_present=True,
        build_metadata_complete=meta.complete,
        metadata_matches_commit=meta.complete,
        formal_evidence="none — SIM-A controlled_assumption only",
        require_artifact_checks=True,
    )
    pf = preflight.to_dict()
    gates.append(
        GateRecord(
            name="release_preflight",
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

    summary = {
        "p0_total": len(P0_ITEMS),
        "p0_evd2_count": 0,
        "p0_status": {item: "EVD-0/1 (SIM-A)" for item in P0_ITEMS},
        "formal_candidate_eligible": False,
        "release_ready": pf.get("release_ready"),
        "seeded_object_ids": ids,
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
        f"- **P0 EVD-2 计数**：{summary['p0_evd2_count']} / {summary['p0_total']}",
        f"- **formal_candidate_eligible**：{summary['formal_candidate_eligible']}",
        f"- **build_metadata_complete**：{meta.get('build_metadata_complete')}",
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
        f"- [{' ' if summary['p0_evd2_count'] < 24 else 'x'}] 24 项 P0 全部 EVD-2",
        f"- [{'x' if len(business_rejections) == len(probes) and not protocol_errors else ' '}] formal export 使用合法参数且业务拒绝",
        f"- [{' ' if unexpected_passes else 'x'}] 无意外 PASS（process 级导出允许时记为 P1 缺口：{', '.join(p.tool for p in unexpected_passes) or '无'}）",
        f"- [{'x' if not summary['release_ready'] else ' '}] release_preflight 阻断 SIM-A/EVD-0 包",
        "- [ ] DOCX 字体/glyph/逐页 PNG 验收",
        "- [ ] Review → Retest → Export 完整闭环（真实 EVD-2 资料）",
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
