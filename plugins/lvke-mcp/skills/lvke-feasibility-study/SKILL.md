---
name: lvke-feasibility-study
description: Orchestrate complete Lvke feasibility-study work across evidence, planning, finance, report, review, and release. Use for end-to-end feasibility delivery or when deciding the next governed domain stage.
---

# Lvke Feasibility Study

## Routing

Open [references/catalog.md](references/catalog.md), select only the rows relevant to the current task, and read each linked source `SKILL.md` completely before acting. Do not preload unrelated references.

## Workflow

1. Classify the request against the catalog.
2. Load the minimum relevant specialist references.
3. Apply their workflow and invariants together with the gates below.
4. Verify the result at the risk level required by the selected references.

## Gates

- Keep every immutable object boundary and explicit confirmation step.
- Do not claim formal delivery until review and release gates pass.
- Asset-acquisition preview reports may be technically ready while
  `formal_release_eligible=false`; formal artifacts require a qualified
  `formal_candidate` run.
- Build preview reports through `report_prepare` with
  `finance_binding.kind=asset_acquisition`; classify direct preview/process
  formal-artifact calls as `EXPECTED_REJECTION`.

The linked specialist files are preserved expertise, not optional background. This parent Skill reduces discovery context only; it does not supersede their detailed rules.
