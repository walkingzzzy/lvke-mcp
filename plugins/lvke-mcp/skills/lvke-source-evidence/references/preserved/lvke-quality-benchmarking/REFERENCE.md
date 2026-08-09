---
name: lvke-quality-benchmarking
description: "Audit Lvke source URLs and compare compatible public or controlled benchmarks. Use when validating URL availability, comparing market, scale, cost, labor, investment, tariff, or operating benchmarks, or deciding whether a benchmark can support planning and finance inputs."
---

# 质量基准比对

要求已固化的 snapshot、EvidencePack 或受控表格。搜索摘要只能用于发现，不能直接进入偏差计算。

## 工作流

1. 对候选 URL 调用数据采集 URL audit，记录状态、时间、重定向和最终来源。
2. 选择同地区层级、期间、币值、税口径、计量单位和业务边界可比的基准。
3. 调用 `analysis_compare_benchmark`，读取兼容性、归一化 ledger、偏差和证据定位。
4. 将结果作为候选判断交给 Codex；采用哪一项须写明选择与舍弃理由。

## 状态处理

- 单位、期间或口径不兼容：保持 `partial`，不得计算伪精确偏差。
- URL 不可用：用 Tavily 改写查询或经 `data_discover` 查找官方替代来源；禁止回退内置 Web Search。
- 上游失败：记录 provider、retryable 和 trace，不归因成本地计算错误。

禁止对冲突值静默平均、把行业区间当项目事实或让 benchmark 覆盖已确认的 ProjectContext。
