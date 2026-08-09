---
name: lvke-api-contract
description: Change or review Lvke MCP tool, Resource, schema, response-envelope, and migration contracts. Use whenever public tool interfaces, compact schemas, handlers, or dependent Skills change. This product has no frontend or HTTP application contract.
---

# Lvke Api Contract

## Routing

Open [references/catalog.md](references/catalog.md), select only the rows relevant to the current task, and read each linked source `REFERENCE.md` completely before acting. Do not preload unrelated references.

## Workflow

1. Classify the request against the catalog.
2. Load the minimum relevant specialist references.
3. Apply their workflow and invariants together with the gates below.
4. Verify the result at the risk level required by the selected references.

## Gates

- Update producers, consumers, schemas, tests, and migration documentation together.
- Public compression must not weaken server-side validation or business gates.

The linked specialist files are preserved expertise, not optional background. This parent Skill reduces discovery context only; it does not supersede their detailed rules.
