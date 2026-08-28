---
name: lvke-report
description: Draft, assemble, revise, validate, and export Lvke feasibility reports and DOCX deliverables. Use for any report chapter, document artifact, report revision, or formal report-generation task.
---

# Lvke Report

## Routing

Open [references/catalog.md](references/catalog.md), select only the rows relevant to the current task, and read each linked source `SKILL.md` completely before acting. Do not preload unrelated references.

## Workflow

1. Classify the request against the catalog.
2. Load the minimum relevant specialist references.
3. Apply their workflow and invariants together with the gates below.
4. Verify the result at the risk level required by the selected references.

## Gates

- Keep proposal, diff, apply, revision, and basis-fingerprint semantics.
- Do not present an unreleased draft or generated DOCX as a formally approved deliverable.
- Require self-contained licensed CJK fonts in formal DOCX output; audit the
  package. soffice PNG conversion is a probe, not page-by-page visual acceptance.

## MCP Tool Mapping

Machine-readable mapping: `src/lvke_mcp/runtime/skill_tool_mapping.json` (`lvke-report` entry).

| Tool | Server | Required inputs | Outcome notes |
|------|--------|-----------------|---------------|
| `report_prepare` / `report_start` | lvke-report-generation | `workspace_id`, finance binding | binds ReportPreparation |
| `report_propose` → `report_apply` | lvke-report-generation | revision lineage | Codex narrative; MCP binds numbers |
| `report_export_docx` | lvke-report-generation | confirmed revision | `FORMAL_ARTIFACT_QUALIFICATION_REQUIRED` = EXPECTED_REJECTION |

Evidence tracks: `technical_fixture`, `controlled_assumption`, `formal_evidence`, `sim_a_formal`. Unpromoted SIM-A cannot clear formal export. Report numbers must bind same FinanceRun hash/lineage.
