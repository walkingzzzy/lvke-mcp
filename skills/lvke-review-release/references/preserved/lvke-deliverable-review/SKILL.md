---
name: lvke-deliverable-review
description: 审查财务运行、十三表、研报和联合交付包，执行 finding、整改、复测与审查报告导出。用于内容质量和证据完整性检查，不用于身份、权限或安全签审。
---

# 交付物审查

审查服务只消费同一 `workspace_id` 数据命名空间下的不可变目标对象及其 Resource。审查结论区分 `generated`、`validated` 和 `reviewed`；它不授予外部批准或法律效力。

## 标准流程

`review_resolve_standards → review_prepare → review_start → review_get → review_list_findings → review_disposition_finding → review_retest → review_export`

本链（无 package 步）**仅适用于单一不可变目标**——`finance_run`、`finance_tables_package`、`report_revision`、`research_package`、`evidence_pack` 等。这类目标走确定性规则包路径，不进七域聚合。**五材料套件与七域审查必须走 package-first 链**：`review_package_prepare → review_package_confirm → review_prepare(target={target_type:"review_package", target_id:<已确认 package_id>}) → review_start → review_submit_assessment → review_confirm_dimension → review_finalize`（详见 `lvke-research-report-review`）。把套件塞进本链会拿不到七域结论，且 `review_finalize` 会以 `review_package_target_required` 拒绝。

所有 P0/P1 finding 必须处置并按需带证据复测；`review_export` 仅导出审查结果，不执行发布、认证、角色检查或安全签审。

## 证据边界

- 技术 fixture、controlled assumption、搜索摘要和不可回读 URL 不能升级为 formal evidence。
- partial 或 incomplete 结果必须原样保留并阻断正式发布。
- 任何目标、上游 hash、规则包或证据变化都会使旧审查失效，必须创建新 review/retest 对象。
- SIM-A 在 prepare、start、retest、export 四个边界重验同一 promotion；Retest
  创建不可变 child 并绑定精确 remediation evidence，Export manifest 保留 origin、
  promotion 和文件 hash。历史无签名审查失败关闭。

## 验收记录

逐调用记录输入 hash、开始/结束时间、耗时、成功状态、status、finding、blocker、basis hash、content hash、lineage 和 Resource URI。质量审查不等于客户验收。
