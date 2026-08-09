---
name: lvke-backend
description: Implement and review Lvke MCP stdio servers, domain services, jobs, idempotency, persistence, schemas, and Resources. Use for MCP handlers, runtime behavior, storage, or tool-contract changes. This product has no authentication, tenant, role, RBAC, or permission-management layer.
---

# Lvke Backend

## Routing

Open [references/catalog.md](references/catalog.md), select only the rows relevant to the current task, and read each linked source `REFERENCE.md` completely before acting. Do not preload unrelated references.

## Workflow

1. Classify the request against the catalog.
2. Load the minimum relevant specialist references.
3. Apply their workflow and invariants together with the gates below.
4. Verify the result at the risk level required by the selected references.

## Gates

- Treat `workspace_id` only as a data namespace, never as an authorization boundary.
- Keep deterministic validation, immutable lineage, idempotency, and stable error contracts.
- Do not add login, identity, role, tenant, RBAC, permission, or security-signoff workflows.

The linked specialist files are preserved expertise, not optional background. This parent Skill reduces discovery context only; it does not supersede their detailed rules.
