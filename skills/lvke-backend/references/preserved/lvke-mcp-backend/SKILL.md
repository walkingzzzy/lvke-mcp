---
name: lvke-mcp-backend
description: Implement and review the local Lvke MCP stdio runtime, tool schemas, handlers, Resources, storage, and tests. Use for changes under src/lvke_mcp or MCP server behavior. Do not add authentication, roles, tenant isolation, RBAC, permission management, or professional-signoff workflows.
---

# Lvke MCP Backend

## Architecture

```text
OfficialStdioServer
  -> registered tool schema and annotations
  -> deterministic domain handler
  -> immutable JSONArtifactStore or exported artifact
  -> response envelope with status, blockers, next_actions, hashes, and lineage
```

The public product is local MCP plus Codex Skills. It has no HTTP application or front end. `workspace_id` partitions local business objects; it is not an identity or authorization primitive.

## Workflow

1. Locate the registered tool and its internal full schema.
2. Preserve compact `tools/list` projection and full server-side validation.
3. Keep calculations deterministic and keep report prose in Codex.
4. For mutations, preserve idempotency, immutable revisions, hashes, and stale lineage.
5. Return stable business states such as `ok`, `partial`, `missing_inputs`, `blocked`, or `upstream_failure`.
6. Update contract and integration tests for every public behavior change.

## Scope

- Use Tavily as the only external web provider.
- Do not add authentication, sessions, identities, roles, tenants, RBAC, permissions, or security signoff.
- Do not add a frontend, voice workflow, or collaborative-office workflow.
- Keep URL public-target checks and file type validation as data-ingestion correctness checks, not user authorization features.

## Verification

Run focused tests first, then the full test suite and a real stdio initialize/tools-list smoke check.
