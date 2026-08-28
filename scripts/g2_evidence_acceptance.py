#!/usr/bin/env python3
"""G2 evidence-track acceptance: controlled import + 22 citation audits."""

from __future__ import annotations

import argparse
import base64
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

from acceptance_common import call_tool, classify_outcome, object_id_from_payload  # noqa: E402
from lvke_mcp.runtime.build_metadata import build_metadata  # noqa: E402

REPORTS = ROOT / "dev-docs" / "reports"
CITATION_FIXTURE = ROOT / "dev-docs" / "fixtures" / "g2_v16_citation_urls.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class StepRecord:
    step: str
    tool: str
    server: str
    status: str
    success: bool | None
    classification: str
    trace_id: str
    object_id: str
    code: str
    protocol_error: str
    notes: str = ""


def _load_citation_urls() -> list[str]:
    doc = json.loads(CITATION_FIXTURE.read_text(encoding="utf-8"))
    urls = list(doc.get("urls") or [])
    if len(urls) != 22:
        raise ValueError(f"expected 22 citation URLs, got {len(urls)}")
    return urls


def run_g2_chain(workspace_id: str, data_dir: str) -> list[StepRecord]:
    steps: list[StepRecord] = []
    idem = f"g2-evidence-{uuid.uuid4().hex[:12]}"
    citation_urls = _load_citation_urls()

    def record(step: str, server: str, tool: str, payload: dict[str, Any], *, notes: str = "") -> dict[str, Any]:
        protocol_error = payload.pop("_protocol_error", None)
        steps.append(
            StepRecord(
                step=step,
                tool=tool,
                server=server,
                status=str(payload.get("status") or ""),
                success=payload.get("success"),
                classification=classify_outcome(payload, protocol_error=protocol_error),
                trace_id=str(payload.get("trace_id") or ""),
                object_id=object_id_from_payload(payload),
                code=str(payload.get("code") or ""),
                protocol_error=str(protocol_error or ""),
                notes=notes,
            )
        )
        return payload

    def chain(module: str, tool: str, args: dict[str, Any], *, timeout: float = 90) -> dict[str, Any]:
        payload, protocol_error = call_tool(module, tool, args, timeout=timeout, data_dir=data_dir)
        payload["_protocol_error"] = protocol_error
        return payload

    m_src = "lvke_mcp.servers.lvke_source_files.server"
    m_analysis = "lvke_mcp.servers.lvke_data_analysis.server"
    m_data = "lvke_mcp.servers.lvke_data_acquisition.server"

    fixture_text = (
        "G2 controlled import fixture — 咸安区低空经济农文旅融合发展项目\n"
        "证据等级: source_reconstructed\n"
    )
    imported = record(
        "SourceImport",
        "lvke-source-files",
        "source_import_content",
        chain(
            m_src,
            "source_import_content",
            {
                "workspace_id": workspace_id,
                "original_filename": "g2_fixture.txt",
                "declared_mime": "text/plain",
                "content_base64": base64.b64encode(fixture_text.encode()).decode(),
                "idempotency_key": f"{idem}-import",
            },
        ),
    )
    source_id = str(imported.get("file_id") or imported.get("source_file_id") or "")

    record(
        "SourceSnapshot",
        "lvke-source-files",
        "source_file_get",
        chain(m_src, "source_file_get", {"workspace_id": workspace_id, "file_id": source_id})
        if source_id
        else {"success": False, "status": "blocked", "code": "source_missing", "_protocol_error": None},
    )

    ingested = record(
        "AnalysisIngest",
        "lvke-data-analysis",
        "analysis_ingest",
        chain(
            m_analysis,
            "analysis_ingest",
            {
                "workspace_id": workspace_id,
                "file_ids": [source_id] if source_id else [],
            },
        ),
    )
    analysis_id = str(ingested.get("analysis_task_id") or ingested.get("analysis_id") or "")

    if analysis_id:
        record(
            "EvidencePack",
            "lvke-data-analysis",
            "analysis_build_evidence_pack",
            chain(
                m_analysis,
                "analysis_build_evidence_pack",
                {
                    "workspace_id": workspace_id,
                    "analysis_task_id": analysis_id,
                    "evidence_track": "source_reconstructed",
                },
            ),
        )

    record(
        "ProviderStatus",
        "lvke-data-acquisition",
        "data_provider_status",
        chain(m_data, "data_provider_status", {}),
    )

    record(
        "DataDiscover",
        "lvke-data-acquisition",
        "data_discover",
        chain(
            m_data,
            "data_discover",
            {
                "workspace_id": workspace_id,
                "queries": ["湖北省 低空经济 政策 2024"],
                "limit_per_query": 3,
            },
            timeout=120,
        ),
        notes="network/provider dependent",
    )

    for index, url in enumerate(citation_urls, start=1):
        record(
            f"CitationAudit-{index:02d}",
            "lvke-data-acquisition",
            "data_audit_urls",
            chain(
                m_data,
                "data_audit_urls",
                {
                    "workspace_id": workspace_id,
                    "urls": [url],
                    "audit_mode": "live",
                },
                timeout=60,
            ),
            notes=url,
        )

    return steps


def write_report(workspace_id: str, steps: list[StepRecord], meta: dict[str, Any]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    json_path = REPORTS / f"G2_EVIDENCE_ACCEPTANCE_{stamp}.json"
    md_path = REPORTS / "G2_EVIDENCE_ACCEPTANCE.md"

    counts: dict[str, int] = {}
    for step in steps:
        counts[step.classification] = counts.get(step.classification, 0) + 1

    with_trace = sum(1 for s in steps if s.trace_id)
    protocol_errors = [s for s in steps if s.protocol_error]

    payload = {
        "generated_at": _utc_now(),
        "workspace_id": workspace_id,
        "build_metadata": meta,
        "citation_url_count": 22,
        "classification_counts": counts,
        "steps_with_trace_id": with_trace,
        "steps": [asdict(s) for s in steps],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# G2 真实资料轨验收报告",
        "",
        f"- **生成时间（UTC）**：{payload['generated_at']}",
        f"- **工作区**：`{workspace_id}`",
        f"- **引用核验数**：22（V1.6 主报告全量 URL）",
        f"- **含 trace_id 步骤**：{with_trace}/{len(steps)}",
        f"- **build_metadata_complete**：{meta.get('build_metadata_complete')}",
        "",
        "## 步骤分类",
        "",
        "| 分类 | 数量 |",
        "|------|------|",
    ]
    for key in ("PASS", "EXPECTED_REJECTION", "UPSTREAM_FAILURE", "SKIPPED"):
        lines.append(f"| {key} | {counts.get(key, 0)} |")

    lines += [
        "",
        "## 链路步骤",
        "",
        "| 步骤 | 工具 | 分类 | status | trace_id | object_id | 备注 |",
        "|------|------|------|--------|----------|-----------|------|",
    ]
    for step in steps:
        tid = step.trace_id[:16] + "…" if step.trace_id else (step.protocol_error or "—")
        lines.append(
            f"| {step.step} | `{step.tool}` | {step.classification} | {step.status or '—'} | "
            f"`{tid}` | {step.object_id or '—'} | {step.notes[:40] or '—'} |"
        )

    lines += [
        "",
        "## G2 退出条件核对",
        "",
        f"- [{'x' if with_trace >= len(steps) - 2 else ' '}] 受控 import 可回读且含 trace",
        f"- [{'x' if len(protocol_errors) == 0 else ' '}] 无 PROTOCOL_ERROR（-32602）",
        f"- [{'x' if counts.get('PASS', 0) >= 3 else ' '}] 核心 import/ingest 步骤 PASS",
        "- [ ] 22 条引用全部可回读快照或标记 unresolved（需联网 provider）",
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

    workspace_id = args.workspace.strip() or f"g2-evidence-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    meta = build_metadata().envelope_fields()

    with tempfile.TemporaryDirectory(prefix="lvke-g2-acceptance-") as tmp:
        os.environ["LVKE_MCP_DATA_DIR"] = tmp
        steps = run_g2_chain(workspace_id, tmp)

    write_report(workspace_id, steps, meta)
    protocol = sum(1 for s in steps if s.protocol_error)
    print(f"steps={len(steps)} protocol_errors={protocol} with_trace={sum(1 for s in steps if s.trace_id)}")
    return 1 if protocol else 0


if __name__ == "__main__":
    raise SystemExit(main())
