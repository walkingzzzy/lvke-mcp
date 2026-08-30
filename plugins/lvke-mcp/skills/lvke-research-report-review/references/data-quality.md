# Data Quality Domain

Use `DATA.ANOMALY.RECONCILIATION` after reading deterministic CSV/schema findings.

Check types, required fields, duplicates, missing values, units, currencies, entities, periods, continuity, outliers, totals, conversion rules, and version dates. Trace material inputs into the model, tables, and report. Distinguish a real zero from missing or not applicable.

Do not silently repair values. Record source row/cell, expected basis, actual value, transformation, downstream uses, and reconciliation difference. Missing lineage for a decision-driving value is at least P1.

