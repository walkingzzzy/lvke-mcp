---
name: lvke-api-contract
description: Change or review Lvke MCP tool, Resource, schema, response-envelope, and migration contracts. Use whenever public tool interfaces, compact schemas, handlers, or dependent Skills change. This product has no frontend or HTTP application contract.
---

# Lvke Api Contract

## Routing

Open [references/catalog.md](references/catalog.md), select only the rows relevant to the current task, and read each linked source `SKILL.md` completely before acting. Do not preload unrelated references.

## Workflow

1. Classify the request against the catalog.
2. Load the minimum relevant specialist references.
3. Apply their workflow and invariants together with the gates below.
4. Verify the result at the risk level required by the selected references.

## Gates

- Update producers, consumers, schemas, tests, and migration documentation together.
- Public compression must not weaken server-side validation or business gates.
- Preserve the stable business codes `FORMAL_ARTIFACT_QUALIFICATION_REQUIRED`
  and `EVIDENCE_BINDING_STALE`, including details, blockers, and next actions.
- Treat those codes as business-envelope outcomes with `system_success=true`
  and `business_success=false`; MCP input-schema `-32602` rejections remain
  protocol errors and do not require a business envelope.

## MCP Tool Mapping

Contract Skill — validates against `tests/fixtures/baseline/contracts/` and `lvke://schemas/{server}/{tool}/output` Resources. Run `python scripts/freeze_baseline.py` after intentional contract changes; `python scripts/validate_skill_tool_mapping.py --strict` before plugin release.
