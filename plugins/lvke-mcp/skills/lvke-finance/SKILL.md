---
name: lvke-finance
description: Prepare, validate, calculate, run, inspect, and export deterministic Lvke finance models and thirteen tables. Use for FinanceSpec, assumptions, tax, funding, IRR/NPV, sensitivity, scenarios, or table delivery.
---

# Lvke Finance

## Routing

Open [references/catalog.md](references/catalog.md), select only the rows relevant to the current task, and read each linked source `SKILL.md` completely before acting. Do not preload unrelated references.

## Workflow

1. Classify the request against the catalog.
2. Load the minimum relevant specialist references.
3. Apply their workflow and invariants together with the gates below.
4. Verify the result at the risk level required by the selected references.

## Gates

- Never change formulas, rounding, convergence, warnings, or FinanceSpec validation to make a run pass.
- finance_calculate is a pure calculator and never replaces finance_run_model or formal gates.

The linked specialist files are preserved expertise, not optional background. This parent Skill reduces discovery context only; it does not supersede their detailed rules.

