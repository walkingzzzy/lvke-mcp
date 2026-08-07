# Errors

Command failures and integration errors.

---

## [ERR-20260807-008] MCP BoE rejects draft ProjectContext

**Logged**: 2026-08-07T01:25:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: integration

### Summary
BoE construction correctly rejected a planning basis list containing the current draft ProjectContext.

### Error
```
confirmed_planning_object_required: BoE planning basis 必须是同作用域 confirmed 对象: pctx_ba6bb53e27f48b4f2f7aadc9
```

### Context
- The ProjectContext has a valid InputApplicability object but its own status remains `draft`.
- MarketSizingCase, RevenueDriverSet, BuildScaleCase, CostDriverSet, and LaborPlan are confirmed in the same workspace.

### Suggested Fix
Do not claim a draft ProjectContext is confirmed; pass only confirmed planning objects to the BoE basis list and preserve the context through object lineage.

### Metadata
- Reproducible: yes
- Related Files: src/lvke_mcp/servers/lvke_finance_model/server.py

### Resolution
- **Resolved**: 2026-08-07T01:25:00+08:00
- **Notes**: Retry removes only the draft context from `planning_object_ids`; no service code was changed.

---

## [ERR-20260807-007] MCP BoE selection reason length

**Logged**: 2026-08-07T01:23:00+08:00
**Priority**: low
**Status**: resolved
**Area**: integration

### Summary
After correcting BoE pointer roots, one `selection_reason` remained shorter than the live contract minimum.

### Error
```
Schema validation failed at 'entries.1.selection_reason': '复用已确认收入候选' is too short
```

### Context
- Operation: live `finance_build_basis_of_estimate`.
- The value and source hash were valid; only the audit explanation length failed schema validation.

### Suggested Fix
Use a complete selection explanation for every material BoE entry, and do not add pointers for fields absent from the public FinanceSpec candidate.

### Metadata
- Reproducible: yes
- Related Files: src/lvke_mcp/servers/lvke_finance_model/server.py

### Resolution
- **Resolved**: 2026-08-07T01:23:00+08:00
- **Notes**: Retry expands the reason and keeps the BoE limited to accepted v2 spec/input fields; no service code was changed.

---

## [ERR-20260807-006] MCP BoE target pointer prefix

**Logged**: 2026-08-07T01:22:00+08:00
**Priority**: low
**Status**: resolved
**Area**: integration

### Summary
The first live `finance_build_basis_of_estimate` request used business paths without the required `spec` or `input_revision` root.

### Error
```
Schema validation failed at 'entries.0.target_pointer': '/revenue/products/0/price_per_unit' does not match '^/(?:spec|input_revision)/'
```

### Context
- Operation: technical fixture BoE construction for the confirmed FinanceSpec candidate.
- The public contract requires every entry to identify whether it binds a spec field or a deterministic input revision.

### Suggested Fix
Use `/spec/revenue/...` for FinanceSpec fields and `/input_revision/...` for engine input fields.

### Metadata
- Reproducible: yes
- Related Files: src/lvke_mcp/servers/lvke_finance_model/server.py

### Resolution
- **Resolved**: 2026-08-07T01:22:00+08:00
- **Notes**: Retry changes only target pointer prefixes; no service code was changed.

---

## [ERR-20260807-005] MCP FinanceSpec v3 public-input boundary

**Logged**: 2026-08-07T01:20:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: integration

### Summary
Submitting FinanceSpec v3 extension fields directly to `finance_prepare_spec.spec` was rejected by the live public schema.

### Error
```
Schema validation failed at 'spec': Additional properties are not allowed ('asset_type', 'decision_thresholds', 'evidence_links', 'financing', 'historical_statements', 'hotel_operation', 'lease_portfolio', 'project_parties', 'scenario_dimensions', 'solar_operation', 'transaction' were unexpected)
```

### Context
- Operation: live `lvke-finance-model.finance_prepare_spec` during the technical fixture chain.
- The running public contract exposes the v2 FinanceSpec candidate shape; the server is expected to migrate accepted v1/v2 input internally before confirmation.

### Suggested Fix
Submit only the public v2 candidate fields to `finance_prepare_spec`; verify the returned confirmed object and formal validator handle the v3 migration separately.

### Metadata
- Reproducible: yes
- Related Files: src/lvke_mcp/domains/finance/parameter_resolver.py

### Resolution
- **Resolved**: 2026-08-07T01:20:00+08:00
- **Notes**: Retry uses a v2 migration input and keeps v3-only technical details in controlled input limitations; no service code was changed.

---

## [ERR-20260807-004] MCP cost-driver investment closure

**Logged**: 2026-08-07T01:17:00+08:00
**Priority**: low
**Status**: resolved
**Area**: integration

### Summary
The corrected cost-driver request passed schema and calculation, but validation correctly rejected a construction investment amount that did not equal its component sum.

### Error
```
cost_driver_validation_failed: /invest_breakdown/construction_wan
investment_breakdown_inconsistent
```

### Context
- Operation: live `planning_prepare_cost_drivers` → `planning_calculate_cost_drivers` → `planning_validate_cost_drivers`.
- The request used `construction_wan=1000`, while civil, equipment, installation, other, and reserve summed to `2800`.

### Suggested Fix
Set `construction_wan` to the exact sum of `civil_wan + equipment_wan + installation_wan + other_wan + reserve_wan` before confirmation.

### Metadata
- Reproducible: yes
- Related Files: src/lvke_mcp/servers/lvke_project_planning/lifecycle.py

### Resolution
- **Resolved**: 2026-08-07T01:17:00+08:00
- **Notes**: The next candidate uses the closed construction breakdown; no service code was changed.

---

## [ERR-20260807-003] MCP cost-driver evidence hash validation

**Logged**: 2026-08-07T01:16:00+08:00
**Priority**: low
**Status**: resolved
**Area**: integration

### Summary
The first live `planning_prepare_cost_drivers` call used a non-hex placeholder instead of a valid SHA-256 content hash.

### Error
```
Schema validation failed at 'operating_cost_items.0.evidence_bindings.0.content_hash': 'sha256:technical-fixture-r3-cost' does not match '^(?:sha256:)?[0-9a-fA-F]{64}$'
```

### Context
- Operation: compressed MCP acceptance for `lvke-project-planning.planning_prepare_cost_drivers`.
- The business input was otherwise unchanged; the failure occurred in the public MCP schema before service execution.

### Suggested Fix
Use a 64-character hexadecimal SHA-256 value, including the optional `sha256:` prefix, for every evidence binding content hash.

### Metadata
- Reproducible: yes
- Related Files: src/lvke_mcp/servers/lvke_project_planning/server.py

### Resolution
- **Resolved**: 2026-08-07T01:16:00+08:00
- **Notes**: The retry uses a valid deterministic technical-fixture hash; no service code was changed.

---

## [ERR-20260807-002] source-layout Python diagnostics

**Logged**: 2026-08-07T00:40:47+08:00
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
Ad hoc system-Python schema introspection failed because the repository uses a `src` layout and project-local dependencies.

### Error
```
ModuleNotFoundError: No module named 'lvke_mcp'
ModuleNotFoundError: No module named 'mcp'
```

### Context
- Command: inline Python import of `lvke_mcp.servers.lvke_deep_research.server`.
- Environment: system Python outside the repository virtual environment.

### Suggested Fix
Use `.venv/bin/python` with `PYTHONPATH=src` for repository module diagnostics.

### Metadata
- Reproducible: yes
- Related Files: src/lvke_mcp/servers/lvke_deep_research/server.py

### Resolution
- **Resolved**: 2026-08-07T00:40:47+08:00
- **Notes**: Repository virtual environment and source path were identified before retrying.

---

## [ERR-20260807-001] py_compile cache permission

**Logged**: 2026-08-07T00:28:00+08:00
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
System Python could not create its default bytecode cache while compiling workspace files.

### Error
```
PermissionError: [Errno 1] Operation not permitted: /Users/mac/Library/Caches/com.apple.python/...
```

### Context
- Command: `python3 -m py_compile ...`
- Environment: managed Codex workspace on macOS.

### Suggested Fix
Set `PYTHONPYCACHEPREFIX` to a writable temporary directory for syntax checks.

### Metadata
- Reproducible: yes
- Related Files: src/lvke_mcp/servers/lvke_deep_research/server.py

### Resolution
- **Resolved**: 2026-08-07T00:28:00+08:00
- **Notes**: Re-run with `PYTHONPYCACHEPREFIX=/tmp/lvke-pycache`.

---
