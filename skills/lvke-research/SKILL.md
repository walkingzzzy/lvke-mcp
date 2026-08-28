---
name: lvke-research
description: Run and recover Lvke source discovery and deep research. Use for policy, market, industry, technology, risk, comparable-project, or multi-source research tasks.
---

# Lvke Research

## Routing

Open [references/catalog.md](references/catalog.md), select only the rows relevant to the current task, and read each linked source `SKILL.md` completely before acting. Do not preload unrelated references.

## Workflow

1. Classify the request against the catalog.
2. Load the minimum relevant specialist references.
3. Apply their workflow and invariants together with the gates below.
4. Verify the result at the risk level required by the selected references.

## Gates

- Search only through tavily-hikari; direct HTTP may fetch only a known URL.
- Formal conclusions require at least three query angles, three independent domains, frozen正文, and locators.

## MCP Tool Mapping

Machine-readable mapping: `src/lvke_mcp/runtime/skill_tool_mapping.json` (`lvke-research` entry).

| Tool | Server | Required inputs | Outcome notes |
|------|--------|-----------------|---------------|
| `dr_prepare` → `dr_start` → `dr_submit` | lvke-deep-research | `workspace_id` | ResearchPackage lineage |
| `data_search` / `data_discover` | lvke-data-acquisition | query/URL | network failure = UPSTREAM_FAILURE |

Search summaries and proxy data stay non-formal; freeze正文 with locators before evidence binding.

