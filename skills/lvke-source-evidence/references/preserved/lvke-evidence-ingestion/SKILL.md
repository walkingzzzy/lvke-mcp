---
name: lvke-evidence-ingestion
description: "Use when the user needs to organize public snapshots or controlled project files for a feasibility study: ingest source_snapshot_id or file_id, inspect parsing status, preserve locators, and disclose partial parsing. Not for: web searching/fetching, comparing project metrics, approving facts, calculating finance, or writing a report."
---

# 资料整理与可追溯摄入

目标是形成可查询的 `analysis_task_id`，保留原始 locator、解析状态和资料资格；它不是事实采信或数据计算。

## 工作流

1. 接收已有 `source_snapshot_id` 或工作区 `file_id`；对受控文件先确认其既有安全扫描和解析流程已经完成。
2. 调用 `analysis_ingest`，得到 `analysis_task_id`。
3. 用 `analysis_status` 核对每份资料的摄入和解析状态。
4. 将成功摄入的 task 交给 `lvke-evidence-analysis` 进行查询、口径比较和证据包固化。

## 状态铁律

- `partial` 是有效但不完整的结果，必须列出未完成或不可读来源；不得写成“资料已全部整理”。
- 受控文件的 `formal_use_allowed=false` 不会因摄入成功而改变。
- 不在此处把 OCR 文本、网页摘要或 locator 自动升格为已确认项目参数。

## 不触发

需要找公开网页时使用 `lvke-source-acquisition`；需要比较指标或形成 evidence pack 时使用 `lvke-evidence-analysis`；需要修改解析、安全扫描或资料服务代码时这是开发任务。
