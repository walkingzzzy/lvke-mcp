---
name: lvke-deliverable-review
description: 审查财务运行、十三表、研报和联合交付包，执行 finding、整改、复测、签审与正式发布门禁。
---

# 交付物审查

审查服务只消费同一 workspace/tenant 下的不可变目标对象及其 Resource。审查结论必须区分 `generated`、`validated`、`reviewed`、`approved` 和 `formally_deliverable`。

## 标准流程

`review_resolve_standards → review_prepare → review_start → review_get → review_list_findings → review_disposition_finding → review_retest → review_attest → review_export → review_release`

`review_release` 只有在所有 P0/P1 finding 关闭、复测通过、所需财务/业务/法务/报告/最终审批角色独立签审且 `publish_eligibility=true` 时才允许成功。

## 证据边界

- 技术 fixture、controlled assumption、搜索摘要和不可回读 URL 不能升级为 formal evidence。
- partial 或 incomplete 结果必须原样保留并阻断正式发布。
- 同一 actor 不能同时编制、复核和最终批准。
- 任何目标、上游 hash、规则包或证据变化都会使旧审查失效，必须创建新 review/retest 对象。

## 验收记录

逐调用记录输入 hash、开始/结束时间、耗时、成功状态、status、finding、blocker、basis hash、content hash、lineage 和 Resource URI。正式 release、签名和批准不在自动验收中伪造。
