# ReviewAssessment.v1 Contract

Freeze `review_package_id`, package hash, standards snapshot, profile, and domain before reviewing. Do not inspect or edit another domain's draft assessment.

Submit one domain assessment with:

- a unique `reviewer_context_id` used by no other domain in the Review;
- `skill`, `skill_version`, `model`, `model_version`, and execution environment;
- status: `passed`, `failed`, `incomplete`, `not_determinable`, or `not_applicable`;
- `coverage.checked_check_ids` containing every applicable semantic check for the profile;
- findings using only the registered semantic `check_id` for that domain;
- exact target location plus verified evidence fragment, or an explicit missing-evidence reason;
- confidence limits and processing limitations.

`passed` cannot carry findings. `failed` must carry at least one finding. Do not convert missing material into a pass. A locator proves where evidence came from, not whether it supports the claim.

Severity:

- P0: decisive contradiction, tampering, fatal formula error, or unlawful/unsafe reliance; never waivable.
- P1: material decision impact or required evidence gap; blocks unless fully conditioned waiver is accepted.
- P2: substantive quality defect without immediate decision invalidation.
- P3: editorial or low-impact improvement.

The aggregator reads immutable Assessment and DimensionResult objects. It cannot change their status, finding, evidence, or limitation.

