---
name: lvke-section-quality-improvement
description: Score report sections, let Codex propose revisions, rescore, compare, and optionally submit high-quality sections as knowledge candidates. Use when iteratively improving report section quality.
---

# Section Quality Improvement Loop

This workflow orchestrates deterministic scoring with Codex-driven revision.

## Workflow: Score → Revise → Rescore → Knowledge

```
1. review_list_rubrics(workspace_id, project_context)
   → identify applicable rubrics for this project

2. review_score_section(workspace_id, report_revision_id, section_id, rubric_id?)
   → deterministic assessment with dimension scores and structured suggestions

3. Codex reads the assessment and proposes improvements
   → report_propose_section(workspace_id, report_revision_id, section_id, summary, proposed_content, basis)
   → report_diff(workspace_id, proposal_id)
   → review proposal, then report_apply(workspace_id, proposal_id)

4. review_score_section(workspace_id, NEW_report_revision_id, section_id, rubric_id?)
   → score the revised section (new revision ID from step 3 apply)

5. review_compare_assessments(workspace_id, before_assessment_id, after_assessment_id)
   → structured diff showing dimension changes and unresolved blockers

6. If quality improved and evidence is complete:
   knowledge_submit_candidate(workspace_id, candidate={type:"high_quality_section",...}, idempotency_key)
   → candidate status: validated
   → Manual or workflow-driven review: knowledge_review_candidate(..., decision="accepted")
   → knowledge_publish_release(workspace_id, candidate_id, review_id, idempotency_key)
```

## Key Constraints

- **review_score_section produces suggestions only**. It does not modify report content.
- **Codex performs the revision**: it reads the assessment, drafts new content, and calls report_propose_section / report_apply.
- **New revision ID after apply**: report_apply returns a new report_revision_id. Use that ID in the rescore step.
- **knowledge_review_candidate records a content-quality decision**. It does not authenticate a person or enforce a role; the caller must provide the decision, reason, and idempotency key required by the tool schema.

## Anti-patterns

- Calling review_score_section and expecting it to rewrite the section inline
- Scoring the same revision_id before and after—apply returns a new revision
- Submitting knowledge candidates from `partial` research or `source_reconstructed` evidence without stating limitations
- Treating a knowledge-quality decision as authentication, authorization, or professional signoff
