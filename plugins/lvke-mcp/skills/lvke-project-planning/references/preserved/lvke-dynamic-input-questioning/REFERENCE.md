---
name: lvke-dynamic-input-questioning
description: After project_context_validate, read the InputApplicability object and have Codex collect only the missing required fields. Use for conversation-first project intake without a front end.
---

# Dynamic Input Questioning Contract

Codex uses `InputApplicability` to determine which fields to collect before proceeding to planning or finance.

## Workflow

```
1. project_context_create(workspace_id, context={…partial…}, idempotency_key)
   → project_context_id

2. project_context_validate(workspace_id, project_context_id, idempotency_key)
   → status: "ok" | "missing_inputs"
   → input_applicability.missing_fields: ["field_a", "field_b", …]
   → input_applicability.field_states: {field: "required_missing"|"required_present"|"optional"|"not_applicable"}
   → input_applicability.next_actions: ["补充 ProjectContext 字段: field_a", …]

3. If status == "missing_inputs":
   → Codex reads input_applicability.missing_fields
   → Ask the user ONLY for those fields (do not enumerate all optional fields)
   → project_context_revise(workspace_id, project_context_id, expected_basis_hash, patch={…answers…}, idempotency_key)
   → Repeat project_context_validate until status == "ok"

4. If status == "ok":
   → Continue to market sizing, build scale, finance, etc.
```

## Reading the InputApplicability Object Later

If you have an `input_applicability_id` but not the inline value, read it back:

```
planning_get_object(workspace_id, object_type="InputApplicability", object_id=input_applicability_id)
```

The returned object has the same `missing_fields`, `field_states`, `required_fields`, `optional_fields`, and `not_applicable_fields` keys.

## Field State Reference

| State | Meaning |
|---|---|
| `required_missing` | Must collect before any planning object can be created |
| `required_present` | Required and already provided |
| `optional_present` | Provided but not required |
| `optional` | Not provided; Codex may ask if the task benefits from it |
| `not_applicable` | Not relevant given project type or transaction structure |

## Collecting Policy Candidates and Attachments

When the project type calls for policy basis:
- Use `planning_prepare(object_kind="policy_basis", …)` after context is valid.
- Attachments (Excel, PDF, Word) go through `source_import_content` or `source_upload_begin/chunk/commit` before being referenced in any planning object.
- Do not prompt for attachments on every project—only when `planning_get_industry_constraints` or the industry Skill indicates they are needed.

## Constraints

- Do not invent field names or combine multiple questions into a single free-text prompt when they map to distinct structured fields.
- `project_context_revise` requires `expected_basis_hash`—read it from the most recent `project_context_validate` or `project_context_create` response.
- If the user declines to provide a required field, record it as a `blocker` and state what downstream objects cannot be created until it is supplied.
