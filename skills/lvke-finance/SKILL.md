---
name: lvke-finance
description: Prepare, validate, calculate, run, inspect, and export deterministic Lvke finance models and thirteen tables. Use for FinanceSpec, assumptions, tax, funding, IRR/NPV, sensitivity, scenarios, or table delivery.
---

# Lvke Finance

## Routing

Open [references/catalog.md](references/catalog.md), select only the rows relevant to the current task, and read each linked source `SKILL.md` completely before acting. Do not preload unrelated references.

## Workflow

1. Classify the request against the catalog.
2. Load the minimum relevant specialist references.
3. Apply their workflow and invariants together with the gates below.
4. Verify the result at the risk level required by the selected references.

## Gates

- Never change formulas, rounding, convergence, warnings, or FinanceSpec validation to make a run pass.
- finance_calculate is a pure calculator and never replaces finance_run_model or formal gates.
- Require complete current formal validation before any formal CSV/XLSX write;
  technical scope may emit only explicitly marked process artifacts.
- Revalidate one canonical promotion across EvidencePack, FinanceFactPack,
  FinanceSpec, BoE, FinanceRun and TablesPackage; copied labels or stored hashes
  are insufficient, and unsigned historical objects fail closed.
- Monthly drivers use `explicit monthly values > seasonality x annual value >
  deterministic legacy annual expansion` for ADR, occupancy, ancillary
  revenue, payroll, utilities, consumables, maintenance and owner OPEX.
  Validate calendar continuity/order/non-negativity and require exact annual
  reconciliation. Monthly P&L, balance sheet, CSV/XLSX and manifests must all
  bind the same run facts and lineage; the existing annual package remains compatible.

## MCP Tool Mapping

Machine-readable mapping: `src/lvke_mcp/runtime/skill_tool_mapping.json` (`lvke-finance` entry).

| Tool | Server | Required inputs | Outcome notes |
|------|--------|-----------------|---------------|
| `finance_prepare_spec` / `finance_confirm_spec` | lvke-finance-model | `workspace_id` | `missing_inputs` = EXPECTED_REJECTION |
| `finance_run_model` | lvke-finance-model | `workspace_id`, confirmed spec | bind `run_id`, `input_hash`, `lineage` |
| `finance_validate_post_generation` | lvke-finance-model | `run_id` | `partial` ≠ formal pass |
| `tables_render` / `tables_export_xlsx` | lvke-finance-tables | same `run_id` | 13 tables must match run lineage |
| `tables_get_package` / `tables_validate_table` | lvke-finance-tables | `finance_tables_package_id` | single-table check never grants whole-package or formal qualification |
| `finance_build_balance_sheet` | lvke-finance-model | `run_id` | derives from a cross-footed run; discloses the equity residual and reconciliation gap instead of plugging it |
| `finance_run_monte_carlo` | lvke-finance-model | `run_id`, `distributions`, `seed` | seeded and deterministic; only IRR/NPV P5/P50/P95 and failure counts are persisted, samples stay in memory |
| `finance_get_analysis` / `finance_list_analyses` / `finance_read_analysis_resource` | lvke-finance-model | `kind` + `target_id` / `workspace_id` / `uri` | read-only; never recomputes and never changes formal qualification |
| `finance_import_vendor_review` | lvke-finance-model | `xlsx_path` | vendor workbook is a read-only formula reference; its values are NEVER an outward-facing number source |

`finance_promote_to_formal` creates an immutable formal revision linked by `parent_run_id`; prior revisions stay readable. `finance_generate_package` is DEPRECATED — call `finance_run_model` then `tables_render` explicitly.

Evidence tracks: `technical_fixture`, `controlled_assumption`, `formal_evidence`. Do not treat `success=true` as formal qualification. Formal blockers include `EVIDENCE_BINDING_STALE`, `FORMAL_ARTIFACT_QUALIFICATION_REQUIRED`, `idempotency_conflict`.
