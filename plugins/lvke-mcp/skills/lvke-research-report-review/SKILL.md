---
name: lvke-research-report-review
description: Orchestrate seven-domain review of internal Lvke research packages or imported external report suites. Use for compliance, article, data, source, finance-model, finance-table, and feasibility review; do not use it to claim legal approval, professional certification, identity verification, or electronic signature.
---

# Lvke Research Report Review

## Required Reading

Read [references/assessment-contract.md](references/assessment-contract.md), then read exactly one domain reference in each independent review context:

- [compliance.md](references/compliance.md)
- [article-quality.md](references/article-quality.md)
- [data-quality.md](references/data-quality.md)
- [source-quality.md](references/source-quality.md)
- [financial-model.md](references/financial-model.md)
- [financial-tables.md](references/financial-tables.md)
- [feasibility.md](references/feasibility.md)

## Workflow

1. Import external files through `lvke-source-files`; never execute XLSM macros. For internal review, use immutable object targets only.
2. Call `review_package_prepare`, inspect role confidence, missing roles, parse/OCR status, and confirm every role with `review_package_confirm`.
3. A complete suite requires report, source evidence, base data, finance model, and finance tables. Missing material permits a specialist review only, never a complete-suite pass.
4. Resolve and freeze applicable standards. Confirm every required OCR/low-confidence fragment with `review_confirm_extraction` before relying on it.
5. Run `review_prepare` and `review_start`. Treat deterministic findings and incomplete reasons as immutable input to semantic review.
6. Create seven isolated review contexts. Give each a distinct `reviewer_context_id`, the frozen package, standards snapshot, its domain reference, and no other domain's draft conclusion.
7. Each context calls `review_submit_assessment` with registered semantic `check_id` values, explicit `coverage.checked_check_ids`, target locators, verified evidence or a precise missing-evidence reason, Skill/model/version metadata, and limitations.
8. Call `review_confirm_dimension` for every required domain. Role declaration records responsibility only; it does not authenticate identity or credentials.
9. Call `review_finalize`. The aggregation context may summarize returned `ReviewDimensionResult.v1` values but must not rewrite domain assessments or submit a caller-chosen verdict.
10. Remediate through `propose -> diff -> apply`, import/freeze a new package, and call `review_retest`. A suite retest remains pending until affected domains submit fresh assessments, all required dimensions are confirmed, and the child review is finalized.
11. Export JSON audit, Markdown, DOCX/PDF, XLSX matrix, and locator-oriented annotated DOCX with `review_export`. External packages can export a dossier but can never qualify for Lvke Release.

## Stop Conditions

- Cross-workspace, changed hash, invalid locator, mixed promotion, unsigned internal lineage, unconfirmed OCR, missing required role, missing domain confirmation, P0, unwaived P1, or `incomplete/not_determinable` blocks a complete pass.
- P0 is never waivable. P1 waiver requires scope, impact, compensating controls, responsible party, future expiry, invalidation conditions, and precise evidence.
- `quick` is a hard-gate preview and never a formal seven-domain review. `deep` additionally requires `ARTICLE.VISUAL.LAYOUT` coverage and records any unprocessable page as incomplete.
- Quick dimensions without a semantic Assessment are reported as `incomplete` and unconfirmed. Zero-material TechnicalReport previews may be reviewed through the `zero_material_preview` adapter but never qualify for formal Release.
- MCP deterministic checks do not prove whether a fragment semantically supports a claim. Agent conclusions do not become deterministic merely because they use a fixed schema.

## Tool Surface

`review_package_prepare -> review_package_confirm -> review_confirm_extraction -> review_prepare -> review_start -> review_submit_assessment -> review_get_dimension -> review_confirm_dimension -> review_finalize -> review_disposition_finding -> review_retest -> review_export`
