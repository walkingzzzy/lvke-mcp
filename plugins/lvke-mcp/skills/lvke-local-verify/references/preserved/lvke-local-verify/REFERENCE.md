---
name: lvke-local-verify
description: Verify the local Lvke MCP and Codex Skills repository before claiming completion. Use for focused tests, full regression, schema checks, stdio smoke tests, Skill validation, plugin validation, and Codex discovery. This project has no frontend test track.
---

# Lvke Local Verification

Use the smallest applicable level first, then broaden according to risk.

## Levels

| Level | Scope | Required checks |
|---|---|---|
| V0 | Any edit | `git diff --check`, YAML/JSON parse |
| V1 | Domain behavior | Focused pytest files |
| V2 | MCP contract | Server manifest, schemas, tool registration, stdio initialize |
| V3 | Skills | `quick_validate.py`, catalog link resolution, tool-name checks |
| V4 | Codex plugin | `validate_plugin.py`, `codex plugin list`, `codex mcp list` |
| V5 | Release candidate | Full pytest suite plus representative live MCP calls in a new Codex task |

## Commands

```bash
python -m pytest -q tests/integration/test_codex_skill_delivery.py
python -m pytest -q
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/plugin-creator/scripts/validate_plugin.py" plugins/lvke-mcp
codex plugin list
codex mcp list
```

For every published parent Skill, run skill-creator's `quick_validate.py`. For every MCP in `SERVER_SPECS`, verify `initialize`, `tools/list`, and one bounded representative call.

Run all commands in the existing `lvke-mcp` Conda environment. Do not create a
project-local environment and do not invoke `uv`. For formal DOCX, audit the
embedded CJK font relationships, decodability, PostScript names, OFL metadata,
and glyph coverage, then render every page to PNG and visually inspect it.

## Completion Rules

- Unit and integration tests prove source behavior, not Codex discovery.
- Plugin validation proves package shape, not that a running task loaded it.
- Only a new Codex task can prove newly installed Skills and MCP tools are discoverable.
- Tavily provider unavailability is an upstream limitation; do not substitute another provider.
- This verification matrix has no frontend, voice, collaboration, authentication, role, tenant, RBAC, permission, or security-signoff track.
