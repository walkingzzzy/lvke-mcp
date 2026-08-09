---
name: lvke-mcp-conversational-acceptance
description: Run live conversational acceptance for Lvke MCP services. Use whenever the user asks for MCP 全量测试、对话式工具测试、重启后复测、实时 tools/list 覆盖、江夏项目验收、十三表与研究报告生成验收、确认 MCP 是否还有问题，or asks to continue one of those acceptance runs. Enforce real MCP calls in the current Codex conversation and prohibit pytest, test scripts, code scans, or historical results from substituting for acceptance.
---

# Lvke MCP 对话式验收

把用户要求的 MCP 验收作为独立执行模式。目标是让 Codex 在当前对话中真实调用已启用 MCP，并用实际对象、Resource 和导出工件证明业务链是否可用。

## 进入验收模式

用户出现以下意图时立即进入本模式：

- MCP 全量测试、全工具测试或逐工具测试。
- 对话式工具测试、重启后复测或继续验收。
- 用具体项目生成十三表、研究报告并验证结果。
- 确认 MCP/Skills 是否还有问题或是否可以交付。

进入后先声明当前阶段是“实时 MCP 对话式验收”，不要先运行开发回归。

## 硬性禁止

除非用户在当前请求中明确要求，否则不得：

- 运行 pytest、测试 runner、focused/smoke/golden profile 或全仓回归。
- 编写测试脚本、测试代码或测试文件。
- 用 HTTP 路由测试、本地函数调用、代码扫描或 schema 文件阅读冒充 MCP 调用。
- 用历史工具数量、历史 workspace、历史 run/package/revision/review 作为本轮结果。
- 以“测试通过数量”宣称 MCP 已验收。
- 因准备工作持续扩大范围而推迟真实 MCP 主链。
- 在重启后的验收过程中继续修改服务代码并沿用旧结果。

开发测试资产保留在仓库。禁止替代验收不等于删除测试文件。

## 冻结与重启

1. 若仍在开发修复，先完成明确缺陷并停止扩展范围。
2. 终止仍在运行的开发测试进程。
3. 代码冻结后只请求一次 Codex 重启。
4. 重启后直接读取实时 MCP 清单，不再插入 pytest。
5. 若实时调用发现必须修改代码，立即判定当前运行暴露本地缺陷；修复后旧验收证据失效，重新冻结、重启并创建新对象。

不要在代码未冻结时反复要求用户重启。

## 建立实时基线

重启后按以下顺序执行：

1. 读取当前启用 MCP 服务。
2. 对每个服务读取实时 `tools/list`。
3. 以实时时间戳保存服务、工具和 schema 基线。
4. 使用实时工具总数作为唯一覆盖分母；禁止沿用 208、300、305 等历史常量。
5. 确认通用联网搜索只使用 Tavily；不得回退到已注销的内置 Web Search。

## 逐工具真实调用

每个启用工具至少真实调用一次。为每次调用记录：

- 服务、工具和输入摘要。
- 开始时间、结束时间和耗时。
- `success`、`business_success`、`system_success` 与领域状态。
- `trace_id`、对象 ID、Resource URI、content hash 和 lineage。
- 业务结果、blockers、warnings 和 next actions。
- 最终分类。

只使用四种分类：

- `PASS`：真实调用并满足契约和业务预期。
- `EXPECTED_REJECTION`：真实调用后被正确的输入、证据、状态或业务完整性门禁拒绝。
- `UPSTREAM_FAILURE`：Tavily 网络、召回或 provider 调用失败，本地契约正常。
- `SKIPPED`：存在不可伪造且不能安全满足的前置条件，已记录具体原因。

可以安全执行的工具不得直接标记 `SKIPPED`。

## 执行边界

- Git 写工具只操作一次性临时仓库和本地临时 bare remote。
- 禁止修改、reset、clean 或删除当前主仓库。
- Playwright 只访问公开无害页面或本地无害对象。
- 不执行外部 push 或其他不属于 MCP 业务验收的外部副作用。
- 本产品没有登录、身份、tenant、角色、RBAC、权限管理或安全签审；验收不得增加这些步骤。

## 项目主链

每轮创建全新 workspace 和隔离 workspace，不复用历史业务对象。执行：

```text
ProjectContext
→ DiscoverySet
→ SourceSnapshot
→ CandidateSet
→ EvidencePack
→ ResearchPackage
→ MarketSizingCase
→ RevenueDriverSet
→ BuildScaleCase
→ CostDriverSet
→ LaborPlan
→ FinanceSpec
→ FinanceRun
→ FinanceTablesPackage
→ ReportPreparation
→ Codex report proposal/diff/apply
→ ReportRevision
→ DOCX/XLSX/CSV export
→ Review
```

MCP 负责事实固化、计算、版本、表格、校验和门禁。Codex 负责事实选择、冲突解释和报告正文。Codex 不自行计算 IRR、NPV、税费或十三表；MCP 不替 Codex 撰写最终叙事。

## 十三表与报告核验

- FinanceRun 必须有非空 `run_id`、hash 和 lineage。
- `consistency_ok=false` 时业务成功必须为 false，且不得生成可用 package。
- 十三表只消费同一个 run，不得在表服务中重算。
- 最终 XLSX 必须恰好有 13 张正式附表。
- 必须实际读取 13 个 CSV 的 Resource 并核验 run/package/hash/lineage。
- 报告正文必须经过 `propose → diff → apply`。
- 每个重大数字必须绑定同一 FinanceRun 或合格证据对象。
- DOCX 必须实际检查中文字体、可见文本、表格、分页和非空内容。
- MCP 返回的不可变工件原样物化，不手工改写财务数字以取得通过。

## 两轨结论

分开报告：

- 技术金标轨：本地实现、计算、十三表、报告和审查接口是否闭环。
- 真实资料轨：真实公开资料、项目原始资料和标准证据是否充分。

technical fixture 和 controlled assumption 永远不能升级为 formal evidence。真实资料不足时保持 `partial/blocked`，不得用金标结果解除正式门禁。

## 完成条件

只有以下证据齐备才结束：

1. 实时启用工具全部有真实调用记录或不可伪造的跳过原因。
2. 项目主链完成到 combined review，或明确定位本地阻断点。
3. XLSX、13 个 CSV 和 DOCX 已实际导出并核验。
4. 本地问题、上游限制、真实资料缺口和人工门禁分栏统计。
5. 输出目录和全部对象 lineage 可追溯。

不得使用“没有任何问题”。使用以下受限结论：

> 在本轮实时工具、场景和边界覆盖范围内，未发现阻断性的本地 P0/P1。技术金标链、真实资料资格、正式候选和 release 条件分别判定，不相互替代。

任一本地 P0/P1、主链阻断、跨表不一致、lineage 丢失或门禁绕过，都使技术验收不通过。

## 用户指令优先

- 用户说“只回答问题”时，不调用工具，不继续后台测试。
- 用户要求 MCP 对话式验收时，不切换为 pytest 或全仓回归。
- 用户改变范围时立即停止旧方向，以最新请求为准。
- 范围扩张必须先获得明确授权。
