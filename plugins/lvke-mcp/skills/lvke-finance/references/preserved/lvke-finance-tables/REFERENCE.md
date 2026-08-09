---
name: lvke-finance-tables
description: Validate, render, inspect, and export the fixed thirteen feasibility-study finance tables from one immutable FinanceRun. Use when a valid run_id must produce a FinanceTablesPackage, thirteen CSV resources, or XLSX with technical and formal lineage checks.
---

# Thirteen Finance Tables

## Workflow

1. Require one successful `run_id`; never combine runs or accept loose input fields.
2. Call `tables_render` and retain `finance_tables_package_id`.
3. Call `tables_validate(validation_scope=technical)`, then `tables_validate(validation_scope=formal)` for release work.
4. Call `tables_list_tables` and verify all 13 registered table IDs belong to the same package and run.
5. Read representative tables with `tables_get_table`; check investment, financing, working capital, depreciation, debt service, cost, profit, cash flow, and balance-sheet links.
6. Export with `tables_export_csv` and `tables_export_xlsx` only after validation passes. Confirm 13 CSV resources and one readable XLSX.

If validation or export fails, keep the blocker and do not treat a partial Resource as a formal artifact. Report generation must bind the same `run_id` and package ID.
