---
name: lvke-source-evidence
description: Govern Lvke source import, security scan, parsing, evidence extraction, benchmarking, and source qualification. Use for project files, public snapshots, locators, evidence packs, or source quality work.
---

# Lvke Source Evidence

## Routing

Open [references/catalog.md](references/catalog.md), select only the rows relevant to the current task, and read each linked source `SKILL.md` completely before acting. Do not preload unrelated references.

## Workflow

1. Classify the request against the catalog.
2. Load the minimum relevant specialist references.
3. Apply their workflow and invariants together with the gates below.
4. Verify the result at the risk level required by the selected references.

## Gates

- Never promote search summaries or uncontrolled files to formal evidence.
- Keep scanning, workspace isolation, locator, hash, and lineage checks fail-closed.
- A changed acquisition evidence binding returns `EVIDENCE_BINDING_STALE`; save a
  new Spec revision and rerun before formal artifact generation.
- Preserve current evidence hash/version against the immutable run snapshot;
  technical validation alone never repairs a stale formal binding.

## MCP Tool Mapping

Machine-readable mapping: `src/lvke_mcp/runtime/skill_tool_mapping.json` (`lvke-source-evidence` entry).

| Tool | Server | Required inputs | Outcome notes |
|------|--------|-----------------|---------------|
| `source_import_local_path` / `source_import_content` | lvke-source-files | `workspace_id` | hash-only → EXPECTED_REJECTION for formal |
| `source_upload_begin` → `source_upload_chunk` → `source_upload_commit` | lvke-source-files | `upload_id`, byte `offset_bytes` | chunked path for files over the inline limit; commit verifies chunk continuity, total size and full SHA-256 before parsing. `source_upload_abort` discards an uncommitted session |
| `source_task_status` | lvke-source-files | `task_kind` + matching id | `task_kind="upload"` takes `upload_id` (`ups_`), `task_kind="parse"` takes `job_id` (`job_`); the kind must match the id namespace |
| `analysis_ingest` → `analysis_build_evidence_pack` | lvke-data-analysis | source snapshots | binds EvidencePack |
| `analysis_financial_trends` | lvke-data-analysis | observations with strict periods | YoY/QoQ/CAGR/common-size; missing or zero base period returns a structured issue instead of a number |
| `analysis_list_unit_rules` | lvke-data-analysis | — | reads the controlled unit dictionary; rules apply only when the caller enables them explicitly |
| `data_fetch` | lvke-data-acquisition | qualified URL | unreachable URL → partial/blocked |
| `data_get_url_audit` / `data_get_visual_capture` | lvke-data-acquisition | `url_audit_id` / `visual_capture_id` | read back an immutable audit or screenshot binding with its lineage; a capture never upgrades a source to formal evidence |

Evidence tracks: `source_reconstructed`, `controlled_assumption`, `formal_evidence`. Hash-only registration must not enter EvidencePack or formal candidate.
