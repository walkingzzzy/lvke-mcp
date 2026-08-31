---
name: lvke-finance-tables
description: Validate, render, inspect, and export the fixed fourteen feasibility-study finance delivery tables (附表1-附表10 plus 附表11 财务计划现金流量表) from one immutable FinanceRun. Use when a valid run_id must produce a FinanceTablesPackage, fifteen CSV resources (fourteen tables plus the data-lineage sheet), or XLSX with technical and formal lineage checks.
---

# Thirteen Finance Tables

## Workflow

1. Require one successful `run_id`; never combine runs or accept loose input fields.
2. Call `tables_render` and retain `finance_tables_package_id`.
3. Call `tables_validate(validation_scope=technical)`, then `tables_validate(validation_scope=formal)` for release work.
4. Call `tables_list_tables` and verify all registered annual/monthly table IDs belong to the same package and run.
5. Read representative tables with `tables_get_table`; check investment, financing, working capital, depreciation, debt service, cost, profit, cash flow, and balance-sheet links. 交付集共 **14 张**：附表1-附表10（附表6-1/6-2/6-3 是附表6 的子表，故 13 张旧交付集只排到附表10）加 **附表11 财务计划现金流量表**（table_id `financial-plan`，2023 大纲 financial_sustainability 要求；逐年给出经营/投资/融资三活动、还本付息、累计盈余资金与资金缺口标记，附表9/10 覆盖不到这一层）。它在甲方参考底稿中不存在，结构由引擎冻结并声明 `reference_provenance=engine_defined_no_reference_sheet`——因此 `reference_source_sheet_count` 仍是 15、`review_workbook_sheet_count` 仍是 16，不随交付表增加。
6. Export with `tables_export_csv` and `tables_export_xlsx` only after validation passes. Formal export must finish its current validation preflight before creating any directory or file; a blocked response exposes no path or Resource URI. Confirm the compatible annual package plus monthly P&L/balance-sheet resources and one readable XLSX. Manifests must retain monthly driver/calendar/reconciliation and canonical promotion lineage.

If validation or export fails, keep the blocker and do not treat a partial Resource as a formal artifact. Report generation must bind the same `run_id` and package ID.
