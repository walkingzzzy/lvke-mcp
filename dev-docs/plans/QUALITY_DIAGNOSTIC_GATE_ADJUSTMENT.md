# 技术验收阶段数据质量诊断门禁调整

## 目标

当前阶段只做技术验收和内部验收，不做正式研报生成和正式交付资格判断。AI 产物固定为
内部诊断草稿，所有业务质量问题继续诊断而不阻断下游计算。

## 变更摘要

### 统一诊断信封（§1）

所有工具在响应边界补齐三态字段：

| 字段 | 说明 |
|------|------|
| `operation_status` | `completed` / `failed`（failed 仅系统异常） |
| `diagnostic_available` | 是否产生了可供分析的结果 |
| `quality_status` | `pass` / `warn` / `fail` / `unclassified` |
| `uncertainties` | 结构化不确定性项 |
| `quality_issues` | 文本质量码列表 |
| `diagnostic_only` | 当前产物仅限技术/内部验收 |
| `human_confirmation_required` | 需要人工确认 |
| `formal_report_allowed` | 当前阶段恒 `false` |

已有 `success` / `business_success` / `ready` 语义重新定义为工具执行与业务操作完成
状态，**不再隐含"数据质量通过"或"正式研报可发布"**。

### structured uncertainty 模型（§3）

四类：`fact` / `assumption` / `unverified` / `conflict`。

- `conflict` 必须保留冲突双方值，禁止只保留选中值
- `assumption` 必须写明采用原因
- `unverified` 必须写明缺少什么证据
- `fact` 必须绑定来源引用

### QualityDiagnostic 对象（§4）

新 `lvke://quality-diagnostics/workspaces/{ws}/diagnostics/{qd_id}` 不可变对象，
仅对"影响数值可信度的冲突"固化。内容寻址 ID，支持幂等重写。`target_type` 覆盖
`finance_run` / `finance_tables` / `report_revision` / `acquisition_run`。

### 表包流程（§5）

- `tables_render` 对不一致 run 产出诊断表包，`diagnostic_only=true`、
  `bindable_to_report=false`、`formal_report_allowed=false`
- 响应不再出现"可直接绑定到报告"
- 对 material conflict 固化为 `QualityDiagnostic`

### 报告生成边界（§6）

- `artifact_kind` = `internal_diagnostic_draft`
- `confirmation_status` = `pending_external`
- `formal_report_allowed` = `false`
- `report_validate` 的 next_actions 不再声称"可导出正式件"
- AI 不提供自动正式确认（MCP 无"正式确认"工具）

### FDR 编排（§7）

- 默认 `release_scope` 改为 `process_acceptance`
- `feasibility_validate` 技术范围输出结构化 `uncertainties` 和上游
  `QualityDiagnostic` 引用
- `release` 对 `process_acceptance` 保持结构性阻断，对口径非法/证据不足等
  质量项不再拦截 release
- `project_delivery` 正式门禁保留但不在当前主流程中默认触发

### `classify_quality`（§8）

新增 `quality_severity.classify_quality(code)` 替代 `is_blocking()` 作为技术验收
阶段的统一判定入口。返回 `(quality_status, diagnostic_required, material_conflict,
formal_report_allowed)`。

## 重字段释义调整

| 字段 | 旧语义 | 新语义（当前阶段） |
|------|--------|-------------------|
| `success=true` | 数据质量通过 | 只表示工具执行成功 |
| `business_success=true` | 正式交付可发布 | 只表示业务操作完成 |
| `ready=true` | 正式就绪 | 只表示有诊断结果可继续处理 |
| `blockers` | 下游停止信号 | 诊断发现（不再阻止下游计算） |

## 文件清单

| 文件 | 变更 |
|------|------|
| `runtime/quality_severity.py` | 新增 `classify_quality`、`aggregate_quality_status` |
| `runtime/schemas.py` | 新增 7 个 envelope 字段到 `envelope_properties` |
| `runtime/outcomes.py` | 新增 `apply_diagnostic_envelope` |
| `runtime/transport.py` | 在 `_attach_runtime_metadata` 中调用 `apply_diagnostic_envelope` |
| `runtime/errors.py` | 在 `sanitized_error_payload` 中加入新字段 |
| `adapters/quality_diagnostic_repository.py` | 新建：QualityDiagnostic 固化 |
| `domains/finance/_tables_service/base.py` | 更新 `_package_result` |
| `domains/finance/_tables_service/render.py` | 新增 QualityDiagnostic 固化 |
| `domains/finance/_model_application/run_cases.py` | 新增 `calculation_status`、QualityDiagnostic 固化 |
| `domains/reports/validation.py` | 新增 `artifact_kind`、`confirmation_status` |
| `domains/reports/_service/generation.py` | 新增 `artifact_kind`、`confirmation_status` |
| `servers/lvke_feasibility_delivery/service.py` | 默认 `release_scope=process_acceptance`、新增 uncertainties/diagnostics |
| `tests/integration/test_quality_diagnostic_gates.py` | 新建：17 条验收测试 |

## 验收标准（§9）

19 条标准已通过 17 条集成测试覆盖 + 2 条回归（F-9/F-15）由现有测试覆盖。