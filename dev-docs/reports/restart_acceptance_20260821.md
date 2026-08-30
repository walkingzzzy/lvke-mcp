# Restarted MCP Acceptance 2026-08-21

> Historical snapshot: this acceptance recorded the 2026-08-21 runtime; its 171-tool denominator is not the current topology. The current runtime snapshot (2026-08-30) is 14 servers / 180 tools / 242 resources, with `outputSchema` on 180/180 tools.

Workspace `restart-acceptance-20260821-a` was created after the MCP restart. The **historical** live denominator was 171 tools. The synthetic chain reached ProjectContext, source import, analysis, EvidencePack, research, FinanceSpec, FinanceRun, 13-table rendering, report propose/diff/apply, and Review.

The runtime correctly kept the fixture on the `controlled_assumption` track. Finance tables were generated as a draft package with semantic blockers, the research package was `partial`, and formal DOCX/XLSX/report release remained ineligible. A retest against the same report revision was rejected with `retest_target_not_newer`, proving stale-target protection.

Two fail-open defects were fixed:

- `report_prepare` no longer advertises `formal_ready` when any quality or qualification blocker exists. Draft readiness and formal readiness are now separate.
- `review_export` now rejects DOCX/XLSX when review quality issues, release limitations, or non-pass verdicts remain. JSON/Markdown remain process records.

The current live processes still report `build_metadata_incomplete` because the tracked worktree is dirty. This is an intentional release gate, not a successful build. A clean checkout must run `scripts/write_build_metadata.py --release`, rebuild the plugin, and restart once before formal-candidate evaluation.

Evidence IDs and detailed machine-readable findings are in [restart_acceptance_20260821.json](/Users/mac/Desktop/mcp_servers/dev-docs/reports/restart_acceptance_20260821.json).
