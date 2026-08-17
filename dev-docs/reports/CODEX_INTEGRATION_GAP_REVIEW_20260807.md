# Codex MCP 与 Skills 交付差距复核及整改结果

更新日期：2026-08-08

## 结论

本轮列出的 Codex 发行、Skill 契约和确定性代码缺陷均已修复。`lvke-mcp` 已作为用户级 Codex 插件安装，当前发布面为：

- 14 个 `lvke-*` MCP server，全部完成真实 stdio `initialize`、`tools/list`、`resources/list` 和只读探针。
- 14 个 Codex 父 Skill；80 个专家文档作为普通 `REFERENCE.md` 按需读取，不再重复注册为 Skill。
- 169 个 MCP 工具，与 `SERVER_SPECS` 和服务注册表一致。
- Tavily 是唯一联网 provider；新 Codex 任务实测 `tavily-hikari available=true`。

产品没有前端、语音或协同办公功能，也没有登录、身份、tenant、角色、RBAC、权限管理、安全审查或专业签审功能。业务上的文件格式校验、内容质量复核、环保/消防/生产安全分析不表示产品具备安全审查或权限系统。

## 缺陷整改结果

| 原缺陷 | 状态 | 当前实现与证据 |
| --- | --- | --- |
| Codex 侧没有发布 Lvke MCP/Skills | 已修复 | 新增并安装 `plugins/lvke-mcp`；`codex plugin list` 显示 `installed, enabled`，`codex mcp list` 显示 14 个 `lvke-*` 服务；新增 `dev-docs/config/CODEX_USER_CONFIG.md`。 |
| Skill 引入认证、RBAC、安全审查和签审 | 已修复 | 删除后端 security/RBAC Skill；父 Skill 和验收流程明确禁止这些能力；运行时代码回归检查禁止 identity/permission 层。 |
| 审查编排调用不存在的 `review_attest` / `review_release` | 已修复 | 工作流改为 `review_list_rubrics -> review_score_section -> Codex propose/diff/apply -> 再评分 -> review_compare_assessments`；不补回签审工具。 |
| URL Skill 契约错误 | 已修复 | `safety` 明确为本地非联网检查；`live` 只检查可达性；正文必须另行抓取；`source_import_content` 示例已使用真实参数。 |
| 行业别名误判 | 已修复 | 别名按最长 token 匹配；`warehouse_storage -> 仓储物流`；`光伏`、`photovoltaic`、`solar_power`、`pv_power -> 能源`。 |
| 市场分析刚性输出不完整 | 已修复 | 市场合同扩展为 11 个维度，包含行业业态、目标市场环境、产业链/供应链、产品或服务竞争力、营销策略以及原有规模、供需、饱和度、竞争、份额、价格。 |
| Skills 依赖旧仓库绝对路径 | 已修复 | 51 个外部专家目录已纳入各父 Skill 的 `references/preserved/`；catalog 全部改为包内相对路径。插件构建后只有 14 个 `SKILL.md`，嵌套资料为 80 个 `REFERENCE.md`。 |
| Skill 使用 Claude Code 术语 | 已修复 | 发布包扫描不再出现 `Claude Code` 或 `.claude/`；执行主体统一为 Codex。 |
| “项目用能情况”不是刚性章节 | 已修复 | `gov10`、`gov9`、`ent9`、`ent14` 四类现代报告 outline 均强制包含“项目用能情况”。 |
| 插件安装后 Tavily 不可用 | 已修复 | `lvke-data-acquisition` 配置 Tavily MCP URL，并从用户级凭据文件或环境变量读取 token；修复 `Bearer Bearer` 重复前缀；新任务真实调用返回 `available=true`。 |

## 甲方需求与当前功能对比

状态说明：

- **已实现并发布**：源码、Skill 契约、插件发现和针对性运行验证均已完成。
- **已实现，待真实项目验收**：功能链存在并已通过自动化/协议验证，但尚未使用甲方某个完整真实项目做最终业务验收。

| 甲方需求 | MCP / Skills 当前能力 | 状态 | 尚需验收的内容 |
| --- | --- | --- | --- |
| N1 市场分析 | Tavily 多查询和不同发布主体来源；快照、locator、EvidencePack；11 维市场输出合同 | 已实现并发布 | 用甲方真实行业资料验证各维度的来源充分性和数值口径。 |
| N2 财务测算与 13 表 | FinanceSpec/FinanceRun、融资还款、指标、敏感性/Monte Carlo；固定 13 张基础附表 | 已实现，待真实项目验收 | 以甲方真实资料完成一次可复算的 13 表与报告交付，fixture 不能替代业务验收。 |
| N3 数据质量与溯源 | URL safety/live 审计、正文快照、locator、冲突/缺失、15%/30% 基准分级、截图绑定合同 | 已实现，待真实项目验收 | 在真实来源上核验链接可打开、正文可回指、可选截图可读且引用进入报告。 |
| N4 少量输入推导建设规模 | 土地、产能、容积率、建筑密度、绿化率、建筑面积约束；行业别名与行业参数 | 已实现并发布 | 对甲方实际涉及的行业逐一验证参数适用性。 |
| N5 原料、燃料动力、环保及用能联动 | 成本驱动对象包含原料、燃料动力、环保 CAPEX/OPEX；进入 FinanceSpec；报告强制“项目用能情况” | 已实现并发布 | 真实行业单位消耗、价格、环保方案和能源口径需来源验证。 |
| N6 定员与人工成本 | 劳动计划推导、创建、确认、校验及工资福利成本联动 | 已实现并发布 | 真实行业定员合理性需样本验收。 |
| N7 评分、修正和知识闭环 | rubric 评分、finding/retest、Codex 修订再评分、知识候选审核发布 | 已实现并发布 | 用真实章节跑一次完整闭环；该流程不包含身份、权限或签审。 |
| N8 动态输入、附件和政策 | `input_applicability`、缺失字段、动态追问 Skill、文件导入、政策候选和证据绑定 | 已实现并发布 | 用真实 Codex 对话验证只追问适用字段及附件/政策联动。 |
| N9 技术经济比选 | equipment、building、process、site、operating_model 五类比选，含评分、选择和淘汰理由 | 已实现并发布 | 至少用一个真实项目方案完成比选验收。 |
| N10 Deep Research | 计划修订、事件、来源增删、checkpoint/resume/cancel、提交、质量确认；Tavily-only 采集 | 已实现并发布 | 用真实研究主题完成计划、干预、恢复、引用和质量确认全流程。 |

## Tavily-only 边界

- 不要求或配置 Exa、Firecrawl、ddgs 等其他 provider。
- `data_search`、`data_discover` 和外部 `tavily-hikari` 都属于 Tavily，不得宣称为多 provider 验证。
- 质量门要求的是多个查询角度和不同发布主体来源，而不是多个 provider。
- token 不写入仓库、插件清单、Skill、日志或测试报告；当前用户级凭据文件为 `~/.lvke/config/tavily_mcp_bearer_token`。

## 发布与验证证据

| 验证项 | 结果 |
| --- | --- |
| 全量 pytest | `85 passed, 646 subtests passed` |
| Codex 交付回归 | `10 passed, 559 subtests passed` |
| 14 个源码父 Skill quick validation | 全部通过 |
| 14 个插件父 Skill quick validation | 全部通过 |
| 插件结构验证 | 通过 |
| `git diff --check` | 通过 |
| 真实 stdio smoke | `14/14 passed`，合计 169 个工具 |
| 新 Codex 任务 Skill 发现 | 恰好 14 个 `lvke-*` Skill |
| 新 Codex 任务 MCP 调用 | `lvke-data-acquisition.data_provider_status` 调用完成 |
| Tavily 连通性 | `tavily-hikari available=true`，`streamable_http` |

关键文件：

- `plugins/lvke-mcp/.codex-plugin/plugin.json`
- `plugins/lvke-mcp/.mcp.json`
- `scripts/build_codex_plugin.py`
- `tests/integration/test_codex_skill_delivery.py`
- `dev-docs/config/CODEX_USER_CONFIG.md`
- `src/lvke_mcp/testing/server_manifest.py`

## 仍未完成的业务验收

本轮完成的是列出的代码、契约和 Codex 发布缺陷。以下内容不能用自动化测试代替，仍应单独安排甲方真实项目验收：

1. 使用一套完整真实项目资料跑通研究、规划、财务、13 表、报告、评分修订和导出。
2. 对甲方实际覆盖的行业逐一确认建设规模、定员、原料/能源/环保参数。
3. 对真实公开来源确认链接、正文 locator、冲突处理、报告引用和可选截图的可读性。
4. 由甲方确认最终报告内容和数值满足业务预期；这属于内容验收，不是安全审查、权限审批或专业签审功能。
