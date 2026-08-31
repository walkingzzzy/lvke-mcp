---
name: lvke-evidence-analysis
description: "Use when the user needs to query organized feasibility-study sources; extract explicit field candidates with locators; profile controlled XLSX/CSV cells; compare metric candidates across units, dates and scopes using declared conversion rules; expose conflicts or missing evidence; or create an evidence_pack_id. Not for: source fetching, automatic approval of facts, automatic anomaly mining, FinanceSpec confirmation, financial calculation, or report assembly."
---

# 证据分析与证据包

目标是把已摄入资料的候选事实、定位、冲突和缺口固化为 `evidence_pack_id`。

## 工作流

1. 接收 `analysis_task_id`，先用 `analysis_query` 找到相关原文和 locator。需要字段候选时，调用 `analysis_extract_candidates(field_specs)`；其输出是原值/摘录/locator，不是已确认输入。
2. 需要检查受控表格结构时调用 `analysis_profile_tabular`；它只统计已解析的 cell locator、表头和公式/数值计数，不重算工作簿。它接受**两种 locator 形状**（取并集，不是只认一种）：CSV 解析器发 `kind="cell"` 且**无 `sheet`**，XLSX 解析器发 `kind="spreadsheet_cell"` 且**有 `sheet`**。CSV 按 `table_kind` 归到 `csv` 表名下（缺省 `table`），不伪造工作表名——所以 CSV 画像里出现的 `csv` 是占位表名而非真实 sheet。既非这两种 kind、或 cell 坐标解析不出行列的资料，会以 `no_cell_locators` / `invalid_cell_locators` 进 `skipped` 而不是静默通过。
3. 只从可回指来源整理 observations，保留 `source_id`、指标、单位、时点、范围和 locator。单位相同时用 `analysis_compare`；需要换算时只用 `analysis_normalize_compare`，并提供每个指标明确的 source/target unit、factor 与 conversion_basis。
4. 调用 `analysis_build_evidence_pack`，明确 `expected_fields`、候选事实和冲突，取得 `evidence_pack_id`。

## 当前能力边界

当前 MCP 只提供确定性字段候选抽取、受控 cell-locator 表格画像和显式规则单位换算；尚未提供自动异常检测、语义补全、自动选择“正确值”、自动单位推断或自动确认 FinanceSpec。不得把这些能力描述成已经执行。

## 停止条件

- conflict、missing_fields 或 `partial` 必须随 evidence pack 保留。
- 无 conversion rule、非数值 observation 或未匹配单位必须保留为 `unprocessed`，不能静默换算或平均。
- 公开网页候选、未获正式使用资格的受控文件和 OCR 未复核值，均不得自动进入 FinanceSpec/input revision。

## 不触发

寻找或抓取网页使用 `lvke-source-acquisition`；资料摄入使用 `lvke-evidence-ingestion`；用户已确认输入后的确定性财务计算使用财务 Skills。
