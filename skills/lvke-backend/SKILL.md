---
name: lvke-backend
description: Implement and review the Lvke FastAPI backend, jobs, idempotency, security, RBAC, tenant isolation, and professional signoff. Use for backend routes, services, workers, persistence, or authorization changes.
---

# Lvke Backend

## Routing

Open [references/catalog.md](references/catalog.md), select only the rows relevant to the current task, and read each linked source `SKILL.md` completely before acting. Do not preload unrelated references.

## Workflow

1. Classify the request against the catalog.
2. Load the minimum relevant specialist references.
3. Apply their workflow and invariants together with the gates below.
4. Verify the result at the risk level required by the selected references.

## Gates

- Bind actors server-side and preserve tenant/workspace isolation.
- Keep idempotency central and professional signoff separation-of-duty fail-closed.

The linked specialist files are preserved expertise, not optional background. This parent Skill reduces discovery context only; it does not supersede their detailed rules.

