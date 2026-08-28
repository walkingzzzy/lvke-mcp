---
name: lvke-mcp-acceptance
description: Run live conversational acceptance for the 14 Lvke MCP services after code freeze and one restart. Use for MCP full testing, compressed-tool migration validation, or post-restart acceptance.
---

# Lvke Mcp Acceptance

## Routing

Open [references/catalog.md](references/catalog.md), select only the rows relevant to the current task, and read each linked source `SKILL.md` completely before acting. Do not preload unrelated references.

## Workflow

1. Classify the request against the catalog.
2. Load the minimum relevant specialist references.
3. Apply their workflow and invariants together with the gates below.
4. Verify the result at the risk level required by the selected references.

## Gates

- Freeze code, restart once, then use real MCP calls rather than direct Python handlers.
- Use restarted live `tools/list` as the only service/tool coverage denominator,
  and verify every listed tool keeps `taskSupport=forbidden`.
- Do not claim formal acceptance until every required live call and gate check passes.
- Classify preview/process formal-artifact rejection as `EXPECTED_REJECTION`,
  then verify the restricted report chain and a separate qualified formal run.
- Inspect DOCX embedded CJK font relationships, license metadata and glyph
  coverage. soffice PDF/PNG conversion is a probe, not page-by-page visual
  acceptance of Chinese text, crop, blank pages, tables, or pagination.

## MCP Tool Mapping

- **Denominator**: live `tools/list` after one restart (173 tools across 14 servers).
- **Classifications**: `PASS`, `EXPECTED_REJECTION`, `UPSTREAM_FAILURE`, `SKIPPED` only.
- **Envelope fields** (every call): `transport_success`, `business_success`, `status`, `trace_id`, `input_hash`, `basis_hash`, `content_hash`, `lineage`, `resource_uris`, `blockers`, `next_actions`.
- **Build gate**: `build_metadata_complete=true` required for G1+ acceptance; dirty checkout → dev build only.
- **Formal candidate (G3)**: EVD-2 分母 = `review_list_requirements` 适用项（generic_feasibility 现为 5，不是历史 24）。未晋升 SIM-A / controlled_assumption / hash-only → preview 正式导出 `EXPECTED_REJECTION`。晋升后的 `sim_a_formal` 可计为正式 EVD-2；拟定模板 ≠ 真实原件。`formal_candidate_eligible` ≠ `release_ready`。仍禁止伪造签章、文号、流水、批复与检测/审计结论。
- Mapping manifest: `src/lvke_mcp/runtime/skill_tool_mapping.json`; validate via `python scripts/validate_skill_tool_mapping.py`.

The linked specialist files are preserved expertise, not optional background. This parent Skill reduces discovery context only; it does not supersede their detailed rules.
