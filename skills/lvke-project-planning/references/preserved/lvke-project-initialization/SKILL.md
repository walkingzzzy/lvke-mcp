---
name: lvke-project-initialization
description: "Create, validate, revise, and resume immutable Lvke ProjectContext and InputApplicability objects. Use before research, planning, finance, report, acquisition, or review work when project type, industry, region, transaction structure, asset type, evidence track, or applicable inputs must be fixed."
---

# 项目初始化

先建立项目对象，再调用任何下游业务工具。Skill 不保存状态；以 MCP 返回的对象、hash 和 Resource 为准。

## 工作流

1. 收集项目名称、行业、建设性质、地区、目标、报告类型、交易结构、目标/资产类型和证据轨。
2. 调用 `project_context_create` 创建不可变草稿；缺失信息不得用行业默认值冒充事实。
3. 调用 `project_context_validate`，读取逐字段 `required/optional/not_applicable/prohibited` 结果。
4. 把 `project_context_id`、`input_applicability_id` 和 `basis_hash` 交给研究、规划、财务与审查阶段。
5. 目标或项目事实变化时调用 `project_context_revise`，使用 `expected_basis_hash` 并处理返回的 stale 下游对象。

## 状态与门禁

- `missing_inputs`：只补指定字段并重新校验。
- `blocked/conflict`：不得创建下游正式对象；读取 blocker 或最新 basis 后恢复。
- `controlled_assumption` 只能进入技术估算，不能升级为真实证据。
- 不把 acquisition、operation lease 或 greenfield 隐式互换，不让 Codex 自造业务对象 ID。

完成条件是 ProjectContext 已验证、InputApplicability 已固化且下游使用同一 basis，不是“工具返回了 JSON”。
