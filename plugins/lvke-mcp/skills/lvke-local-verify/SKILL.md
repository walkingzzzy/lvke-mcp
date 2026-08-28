---
name: lvke-local-verify
description: Run the Lvke local verification and golden-sample matrix when remote CI is unavailable. Use before claiming code, MCP, report, finance, or desktop changes are complete.
---

# Lvke Local Verify

## Routing

Open [references/catalog.md](references/catalog.md), select only the rows relevant to the current task, and read each linked source `SKILL.md` completely before acting. Do not preload unrelated references.

## Workflow

1. Classify the request against the catalog.
2. Load the minimum relevant specialist references.
3. Apply their workflow and invariants together with the gates below.
4. Verify the result at the risk level required by the selected references.

## Gates

- Record exact commands and distinguish unrelated baseline defects from regressions.
- A passing local test suite does not replace required live MCP conversational acceptance.
- Use only the existing `lvke-mcp` Conda environment; do not create or invoke a
  project-local virtual environment or `uv` runtime.
- Verify P0A with `scripts/golden_samples_manifest.py`; P0B remains
  `pending_business_approval` until real dual-track approval material is supplied.
- Audit exported DOCX packages for decodable embedded OFL CJK fonts and glyph
  coverage, then render every page to PNG and inspect visible Chinese text,
  tables, pagination, clipping, and blank pages.

## MCP Tool Mapping

Local verify complements — does not replace — live MCP acceptance (`lvke-mcp-acceptance`). Scripts: `scripts/g1_live_acceptance.py`, `scripts/g2_evidence_acceptance.py`, `scripts/g3_formal_candidate_acceptance.py`, `scripts/release_preflight.py`.
