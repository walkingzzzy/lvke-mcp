# lvke-mcp 模块化重构方案（按当前代码快照）

> 更新时间：2026-08-07  
> 仓库：`/Users/mac/Desktop/mcp_servers`  
> 进度：**Wave 0–4 已完成**。超长文件 37 → 15，剩余 15 个均有保留理由（§3）。  
> 基线绑定 `chore(refactor): 基线快照推进到 Wave 4 之后` 提交。  
> 验证细节见 `REFACTOR_VERIFICATION_PROTOCOL.md`；本文只保留目标、判定标准与波次计划。

## 1. 目标与非目标

### 目标

本次重构只解决代码组织问题：

1. 降低单文件的职责数量和变更耦合。
2. 把可独立测试的纯逻辑从 MCP handler、存储和协议层中抽出。
3. 保留既有 import 路径、工具名、资源 URI、响应 envelope 和幂等语义。
4. 让每个拆分 PR 都可以回滚、定位和验证。

### 非目标

以下事项不与“纯拆分”混在同一个 PR：

- 财务口径、IRR 算法、表格结果、业务规则变更；
- MCP 工具合并、删除或重命名；
- workspace 隔离、权限、外部搜索和存储语义变更；
- 全面修复 domain/adapter/runtime 的历史依赖方向；
- 通过合并重复函数来顺手“清理代码”。

依赖方向治理另立 ADR 和专项 PR。本方案的最低要求是：拆分过程中不得新增反向依赖。

## 2. 当前代码基线

### 2.1 规模

以 `src/lvke_mcp` 为统计范围：

| 项 | Wave 0 起点 | Wave 4 完成后 |
|---|---:|---:|
| Python 文件 | 208 | 433 |
| 代码行数 | 95,084 | 101,282 |
| ≥800 行文件 | 37 | 15 |
| 超长文件合计行数 | 65,928 | 18,190 |

文件数与总行数上升是门面模式的预期代价（每个实现包一个 `__init__.py`，
每个门面一段 re-export）。判定指标是**超长文件数与其合计行数**。

“800 行”只是候选筛选条件，不是必须拆分的条件。是否拆分由职责内聚度、调用图、变更边界和可测性共同决定。

### 2.2 MCP 服务与验证基线

权威服务清单位于 `src/lvke_mcp/testing/server_manifest.py`，当前为 14 个公开 server。

Wave 0 验证结果（验证协议见 `REFACTOR_VERIFICATION_PROTOCOL.md`）：

```text
conda run -n lvke-mcp python -m pytest -q tests/integration
75 passed, 0 skipped, 87 subtests passed

conda run -n lvke-mcp python -m lvke_mcp.testing.smoke_test
smoke: 14/14 passed
```

14 个 server 提供 169 个工具、27 个资源条目。冻结基线位于 `tests/fixtures/baseline/`。

### 2.3 当前分层的真实边界

当前目录结构为：

```text
runtime/     MCP 运行时、stdio、transport、资源注册和共享基础设施
adapters/    repository、spreadsheet、外部存储和文件适配
domains/     财务、报告、研究、项目规划、收购等领域逻辑
servers/     MCP server 注册、handler 和服务编排
testing/     协议测试、验收测试和 server manifest
```

当前存在两个重要事实：

1. `domains` 大量依赖 `adapters`，这是现有实现的一部分，不能在拆分 PR 中顺手改成依赖注入。
2. `runtime/resource_registry.py` 通过懒加载字符串导入多个 server/domain，因此 runtime 不是完全独立的纯底层包。

本轮规则采用“冻结现状、禁止新增”的方式：保留已有边，拆分后不得新增 `runtime → servers/domains`、`adapters → domains` 或 `domains → servers` 的边。历史边另行治理。

依赖门禁按 package/layer 级别比较，而不是简单比较文件级 import 数量。代码从一个文件搬到子模块后，允许产生新的同包文件边，但不得引入新的目标 package、跨层方向或未登记的动态加载路径。Wave 0 需要保存一份“历史反向依赖允许清单”，拆分后逐项比较新增、删除和迁移状态。

## 3. 超长文件清单与处理策略

### 3.1 已完成的拆分（27 个文件）

每一项都经 `scripts/split_fidelity.py` 验证为纯搬移（AST 逐定义等价，
无复制、无改写、无丢失）。「定义数」是搬移的顶层定义个数。

| Wave | 文件 | 拆分前 | 门面 | 实现包 | 子模块 | 定义数 |
|---|---|---:|---:|---|---:|---:|
| 1.1 | `servers/lvke_data_analysis/service.py` | 2,147 | 102 | `_service/` | 12 | — |
| 1.2 | `servers/lvke_source_files/service.py` | 1,240 | 156 | `_service/` | 10 | — |
| 1.3 | `servers/lvke_data_acquisition/service.py` | 1,743 | 86 | `_service/` | 7 | — |
| 1.4 | `servers/lvke_deep_research/server.py` | 1,009 | 68 | `_server/` | 4 | — |
| 2.1 | `servers/lvke_project_planning/lifecycle.py` | 964 | 69 | `_lifecycle/` | 6 | — |
| 2.2 | `domains/project_planning/application.py` | 2,374 | — | `_service/` | 6 | — |
| 2.3 | `servers/lvke_project_planning/server.py` | 1,785 | 85 | `_server/` | 7 | — |
| 2.4 | `servers/lvke_zero_material_delivery/service.py` | 1,760 | 98 | `_service/` | 7 | — |
| 2.5 | `domains/research/application.py` | 1,429 | — | `_service/` | 7 | — |
| 2.6 | `domains/reports/application.py` | 1,186 | 78 | `_service/` | 6 | 21 |
| 2.7 | `domains/reports/doc_service.py` | 1,567 | 135 | `_doc_service/` | 8 | 56 |
| 2.8 | `domains/reports/artifacts.py` | 2,177 | 146 | `_artifacts/` | 8 | 66 |
| 2.9 | `servers/lvke_deliverable_review/financial_checks.py` | 1,741 | 43 | `_financial_checks/` | 6 | 15 |
| 2.9 | `servers/lvke_deliverable_review/report_checks.py` | 2,035 | 107 | `_report_checks/` | 8 | 51 |
| 3.1 | `domains/finance/table_render.py` | 3,081 | 80 | `_table_render/` | 8 | 33 |
| 3.1 | `domains/finance/tables_service.py` | 894 | 89 | `_tables_service/` | 5 | 22 |
| 3.2 | `domains/asset_acquisition/model.py` | 1,172 | 66 | `_model/` | 7 | 24 |
| 3.2 | `domains/asset_acquisition/tables.py` | 1,039 | 83 | `_tables/` | 6 | 26 |
| 3.3 | `domains/finance/fact_pack.py` | 1,491 | 85 | `_fact_pack/` | 6 | 30 |
| 3.3 | `domains/finance/vendor_import.py` | 2,302 | 101 | `_vendor_import/` | 8 | 51 |
| 3.3 | `domains/finance/model_application.py` | 1,333 | 72 | `_model_application/` | 4 | 25 |
| 3.3 | `domains/finance/run_service.py` | 1,798 | 75 | `_run_service/` | 6 | 20 |
| 3.4 | `domains/finance/finance_model.py` | 3,684 | 114 | `_finance_model/` | 8 | 32 |
| 3.5 | `servers/lvke_finance_model/server.py` | 2,914 | 154 | `_server/` | 7 | 47 |
| 3.6 | `domains/asset_acquisition/backend.py` | 3,352 | 171 | `_backend/` | 12 | 77 |
| 3.7 | `adapters/spreadsheets/finance_export.py` | 2,005 | 49 | `_finance_export/` | 4 | 11 |
| 4 | `servers/lvke_deliverable_review/service.py` | 5,240 | 218 | `_service/` | 15 | 109 |

Wave 2.2 与 2.5 的门面是 `application.py` 本身（无独立门面文件行数记录）。

### 3.2 剩余的 15 个超长文件（均有保留理由）

| 文件 | 行数 | 保留理由 |
|---|---:|---|
| `testing/source_reconstructed_acceptance.py` | 1,810 | 测试代码，不纳入生产模块化波次 |
| `domains/finance/_finance_model/engine.py` | 1,681 | `compute_financials`(1,450) 与自定义目标/缩放重算/情景互相递归，同一事务边界（§4） |
| `adapters/spreadsheets/_finance_export/delivery_tables.py` | 1,506 | `_write_delivery_tables` 单函数逐表写入，单一事务边界 |
| `domains/research/extractor.py` | 1,485 | Wave 5 观察项：单一 extractor，优先抽 parser/locator 纯函数 |
| `domains/finance/reference_schema.py` | 1,459 | Wave 5 观察项：schema 高内聚 |
| `domains/finance/spec.py` | 1,433 | Wave 5 观察项：FinanceSpec 契约高内聚，不按行数硬拆 |
| `servers/lvke_feasibility_delivery/service.py` | 1,322 | Wave 5 观察项：主 handler |
| `domains/finance/evidence_binding.py` | 1,182 | Wave 5 观察项 |
| `servers/lvke_archive/storage.py` | 1,043 | Wave 5 观察项：先补存储测试 |
| `runtime/transport.py` | 1,041 | Wave 5 观察项：协议层高风险，需先有协议回归 |
| `domains/finance/_finance_model/annual.py` | 898 | `_build_annual`(685) 年度投影，单一事务边界 |
| `servers/lvke_deliverable_review/rules.py` | 890 | 两个 checks 的共同下层，拆出只产生无法复用的薄 wrapper（§4） |
| `domains/finance/_vendor_import/finance_input.py` | 826 | 甲方表到财务输入的构造链，职责单一 |
| `domains/finance/_table_render/normalize.py` | 813 | 行归一化与渲染行契约，`_normalize_rows`(266) + `_renderer_row_contract`(308) |
| `domains/finance/industry_scenario_factory.py` | 801 | Wave 5 观察项：场景工厂保持领域内聚 |

### 3.3 原始候选清单（Wave 0 快照，留档）

下表是 Wave 0 时的判断依据。`拆分`表示进入波次；`观察`表示暂不为降低行数而拆；`测试代码`表示不纳入生产模块化波次。

| 文件 | 行数 | 当前判断 | 建议边界 |
|---|---:|---|---|
| `servers/lvke_deliverable_review/service.py` | 5,240 | 拆分，最高风险 | 规则执行器、workspace 指标、retest/状态、门面 |
| `domains/finance/finance_model.py` | 3,684 | 拆分，高风险 | 计算核心、年度/月份投影、校验、表格投影 |
| `domains/asset_acquisition/backend.py` | 3,352 | 拆分，高风险 | spec/run 存取、hotel/solar 分支、价格求解、产物 |
| `domains/finance/table_render.py` | 3,081 | 拆分，高收益 | specs、行列原语、13 表 builder、markdown、编排 |
| `servers/lvke_finance_model/server.py` | 2,914 | 拆分，先拆注册层 | schema、tool handlers、server builder、兼容路由 |
| `domains/project_planning/application.py` | 2,374 | 拆分，高耦合 | case 用例、validation、envelope、状态/门面 |
| `domains/finance/vendor_import.py` | 2,302 | 拆分 | xlsx 读取、字段映射、重算、双轨对照 |
| `domains/reports/artifacts.py` | 2,177 | 拆分 | docx 样式、字体、引用、artifact IO |
| `servers/lvke_data_analysis/service.py` | 2,147 | 试点拆分 | 数值门、候选提取、benchmark、evidence pack、资源表面 |
| `servers/lvke_deliverable_review/report_checks.py` | 2,035 | 观察后拆 | 章节检查器与通用报告规则 |
| `adapters/spreadsheets/finance_export.py` | 2,005 | 拆分 | workbook IO、13 表写入、样式、重算/导出 |
| `testing/source_reconstructed_acceptance.py` | 1,810 | 测试代码，暂不拆 | 按领域测试场景拆文件，而非生产门面 |
| `domains/finance/run_service.py` | 1,798 | 拆分，高耦合 | spec 准备、计算事务、package、查询/审计 |
| `servers/lvke_project_planning/server.py` | 1,785 | 拆分注册层 | schema、dispatch、server builder |
| `servers/lvke_zero_material_delivery/service.py` | 1,760 | 拆分，高收益 | 行业 profile、假设、跨域编排、artifact/resource |
| `servers/lvke_data_acquisition/service.py` | 1,743 | 拆分 | provider、search/discovery、snapshot import、安全门 |
| `servers/lvke_deliverable_review/financial_checks.py` | 1,741 | 观察后拆 | 财务检查器按规则组拆，不改变门禁顺序 |
| `domains/reports/doc_service.py` | 1,567 | 拆分 | 九章 outline、编号、docx adapter、校验 |
| `domains/finance/fact_pack.py` | 1,491 | 拆分 | pack 构造、domain depth、source binding |
| `domains/research/extractor.py` | 1,485 | 观察 | 单一 extractor；优先抽 parser 与 locator 纯函数 |
| `domains/finance/reference_schema.py` | 1,459 | 观察 | schema 高内聚，先抽纯校验器 |
| `domains/finance/spec.py` | 1,433 | 观察 | FinanceSpec 契约高内聚，不按行数硬拆 |
| `domains/research/application.py` | 1,429 | 拆分 | task lifecycle、provider、quality/checkpoint |
| `domains/finance/model_application.py` | 1,333 | 拆分 | 7 个用例按事务边界拆，保留 envelope |
| `servers/lvke_feasibility_delivery/service.py` | 1,322 | 观察后决定 | 先抽 `_resolve_object` 和 `_formal_object_validation`，handler 仍保留 |
| `servers/lvke_source_files/service.py` | 1,240 | 拆分 | chunk upload、workbook inspect、external corpus |
| `domains/reports/application.py` | 1,186 | 拆分 | report lifecycle、readiness、resource facade |
| `domains/finance/evidence_binding.py` | 1,182 | 观察后拆 | local chain、source locator、finance binding |
| `domains/asset_acquisition/model.py` | 1,172 | 拆分 | monthly、solar、legacy 年度路径 |
| `servers/lvke_archive/storage.py` | 1,043 | 观察 | storage 类职责较集中，先补存储测试 |
| `runtime/transport.py` | 1,041 | 观察 | 协议层高风险，只有在有协议回归后再拆 |
| `domains/asset_acquisition/tables.py` | 1,039 | 拆分 | 13 表构造、integrity、xlsx/markdown adapter |
| `servers/lvke_deep_research/server.py` | 1,009 | 拆分注册层 | tool schema、dispatch、server builder |
| `servers/lvke_project_planning/lifecycle.py` | 964 | 观察 | 与 application 的私有 API 一并处理 |
| `servers/lvke_deliverable_review/rules.py` | 890 | 观察 | 规则组合器与扫描器分离 |
| `domains/finance/tables_service.py` | 894 | 拆分 | resource/list、render、csv validation |
| `domains/finance/industry_scenario_factory.py` | 801 | 观察 | 场景工厂保持领域内聚 |

## 4. 拆分判定标准

满足下列任意两项，才进入拆分波次：

- 一个文件包含两个以上独立变更单元；
- 纯函数可脱离 handler/store 单测；
- 生产代码存在明显的职责边界或循环依赖风险；
- 文件有多个稳定外部消费者，需要门面兼容；
- 静态目录、协议 schema、IO 代码与业务逻辑变更频率明显不同。

以下情况优先保留：

- 单一事务边界，拆分会破坏幂等、审计或 hash 顺序；
- 高内聚的 schema/contract 定义；
- 只是测试场景数量多，而非生产职责多；
- 拆出后只产生一个无法独立复用的薄 wrapper。

400 行是建议线，不是验收硬上限。超过 600 行需要在 PR 说明中写出保留理由；目标是清晰的职责边界，而不是制造微型文件。

## 5. 目标结构与兼容策略

### 5.1 门面模式

目标模块采用“原模块作为门面”的迁移方式：

```text
table_render.py                 # 第一阶段保留为兼容门面
_table_render/
  specs.py
  primitives.py
  build_investment.py
  build_funding.py
  build_working_capital.py
  orchestrator.py
  markdown.py
```

实现包必须使用 `_table_render/` 这类不同名称，不能让 `foo.py` 与 `foo/` 同时存在并依赖解释器的模块解析优先级。只有在所有消费者迁移并完成 API 快照后，才考虑把 `foo.py` 原子地替换为 `foo/__init__.py`。同一个 PR 不同时进行逻辑搬移、路径删除和业务修复。

门面必须显式 re-export 稳定符号，并覆盖当前已知的私有跨模块访问。对会被 monkeypatch 或重新赋值的模块级状态，不依赖普通 re-export，改用明确的 state owner 或兼容代理。

### 5.2 命名和依赖

- 文件和包名使用 snake_case；
- 禁止 `utils.py`，按职责命名 `envelope.py`、`parsing.py`、`concurrency.py` 等；
- 子模块只能依赖同层或已存在的下层边；
- 不把懒 import 提到模块顶层，除非单独验证启动图和循环依赖；
- 领域函数不得从 server 反向调用；
- 纯搬移 PR 不合并重复函数，不改变 `None`、`0.0`、空列表等语义。

### 5.3 需要特别保护的契约

- MCP server module path 和 14 个 manifest 名称；
- tools/list、resources/list 的数量、名称、schema 和 URI；
- `mcp-envelope.v2` 字段及 blocker/next_actions 形状；
- workspace 隔离和 idempotency key；
- 财务模型版本、13 张表、双轨对照和 IRR 数值；
- 报告九章、正式发布和 review finding 生命周期；
- `source_reconstructed` 与恒力六档验收场景。

## 6. 波次计划

### Wave 0：冻结基线和护栏（已完成）

**完成标志：**

- [x] 修正并脚本化超长文件统计（`scripts/module_metrics.py`）；
- [x] 生成模块消费者清单和导入图，包含 AST import 与字符串懒加载；
- [x] 保存 75/0/87 的测试结果、14/14 smoke 结果、Python/依赖版本；
- [x] 增加 API 快照：导入路径、符号、签名、工具/资源清单（`scripts/api_snapshot.py`）；
- [x] 增加三个自动化门禁测试（`tests/integration/test_refactor_guardrails.py`）；
- [x] 处理当前两个 baseline skip（已修复，零 skip）；
- [x] 建立 `REFACTOR_VERIFICATION_PROTOCOL.md`，本文档只保留规则和链接。

**交付产出：**

- `scripts/module_metrics.py` — 行数、消费者、导入图、分层边、循环扫描；
- `scripts/api_snapshot.py` — 208 模块、2,804 符号的签名与实现归属；
- `quality/module_metrics.json` — 基线边界快照（3 循环、3 组禁止边）；
- `quality/api_snapshot.json` — 基线 API 快照；
- `tests/integration/test_refactor_guardrails.py` — MCP 契约 + Python API + 依赖边界门禁；
- `tests/fixtures/baseline/` — 刷新 14 server 的 tools/resources/contracts（169 工具 / 27 资源）；
- `REFACTOR_VERIFICATION_PROTOCOL.md` — 验证命令、失败处理、冻结债清单。

**发现：**

- 历史循环 3 个：`asset_acquisition ↔ finance ↔ reports`、`finance.run_service ↔ table_pack`、`runtime.resource_registry ↔ feasibility_delivery.service`。
- 历史禁止边 3 组：`adapters → domains`（1 条）、`runtime → domains`（2 条）、`runtime → servers`（11 条懒加载）。
- 动态模块加载检测需要识别一层间接（`def _module(name): return import_module(name)`），
  否则会漏掉 `resource_registry` 的 24 条懒加载边。

### Wave 1：试点和低风险 server（已完成）

四项全部完成，明细见 §3.1。

**试点结论（Wave 1.1 + 1.4，commit 见 git log）：**

门面模板已验证可用：实现包 `_service/` 或 `_server/`，原文件保留为门面并
显式 re-export 全部符号（含 `re`、`datetime` 这类附带 import——消费者用
`from ... import service` 后按 `service.X` 取属性，属性访问必须继续可用）。

**试点暴露的两类「测试全绿的拆分事故」，已加门禁 `scripts/split_fidelity.py`：**

1. **语义等价改写**：`query()` 里 `r"[\w一-鿿]+"` 被写成 `r"[\w一-鿿]+"`。
   对 `re` 完全等价，75 个测试全过，三道 Wave 0 门禁也全过——但这是重写而非
   搬移，diff 从此不可复核。
2. **helper 复制而非搬移**：`_locator_text` 同时出现在两个子模块。两份都能用，
   没有任何门禁失败，但从此存在两份会各自漂移的实现。

Wave 0 的三道门禁只覆盖**接口**层，查不出上面两类。`split_fidelity.py` 按 AST
比较搬移前后的定义，把「纯搬移」变成可自动验证的条件。

**另修正 `scripts/api_snapshot.py`**：公开符号改取 `__all__` 与 `dir()` 的**并集**。
只取 `__all__` 会造成保护空洞——门面新增 `__all__` 后未列入的符号会从快照消失，
此后真被删掉也不会报警。

**路径耦合**：`scripts/independence_scan.py` 的语义豁免按**路径**登记，被豁免的
行搬到新文件后豁免会失效。这不是放宽规则，搬移时需同步改路径。

### Wave 2：编排与报告（已完成）

九项全部完成，明细见 §3.1。`rules.py`(890) 按 §4 保留：它是两个 checks 的
共同下层，拆出只产生无法独立复用的薄 wrapper。

**新增通用驱动器 `scripts/module_split.py`**：配置只声明符号归属，
组间 import 清单、未用 import 剪枝、成环检测、注释迁移全由脚本推导。
**手写组间 import 清单是 Wave 2.4/2.5 两次 `NameError` 事故的根因。**

**两处门禁缺陷（都是本波撞出来的）：**

1. `module_split.referenced()` 原先把**所有**字符串常量按整词计入符号引用，
   于是 `{"status": ...}` 的 dict 键、`data.get("start")` 的键名被当成对同名
   函数的引用，造出假的组间依赖边和假环。Wave 2.6/2.7 的分组都曾被挡住。
   已改为只解析注解位置的字符串。
2. `module_metrics` 的循环比较原先按节点序列精确匹配。实现搬进 `_impl/` 后，
   同一历史环的路径必然多出子模块节点，会被同时报成「新增环」和「已解决环」
   ——环总数 3→3 不变却门禁失败。已改为按参与环的门面模块集合归一化，
   并验证真新环仍被抓到、不同门面不混淆。

### Wave 3：财务和收购核心（已完成）

七组全部完成，明细见 §3.1。每一步都跑了 13 表、IRR、hash 与 xlsx 输出验证，
不只验证 import 成功。

**三处有意保留的大函数**（按 §4，不为降低行数切开函数体）：
`_finance_model/engine.py`(1,681)、`_finance_model/annual.py`(898)、
`_finance_export/delivery_tables.py`(1,506)。理由见 §3.2。

**Wave 3.1 的 monkeypatch 兼容事故**：`tables_service` 的三个薄委托被测试
`patch.object(门面, ...)` 替换，搬进实现包后 patch 静默失效，`render()` 返回
`success=False`。正解是实现函数加仅关键字注入点 + 门面包装函数传入门面自身属性
（实现包零反向依赖）。三条错解与完整分析见 `REFACTOR_VERIFICATION_PROTOCOL.md` §7.1。

**Wave 3.4 的两处修正**：`module_split.bound_names` 不递归 `Try`/`If`/`With`，
导致可选依赖兜底块里定义的名字查不到归属（跨组引用直接 `NameError`）；
`api_snapshot --check` 抓到门面漏 re-export `InvestmentBreakdown`，
修法是照抄原模块的条件性而非无条件 import。

### Wave 4：交付评审核心（已完成）

`servers/lvke_deliverable_review/service.py` 5,240 → 门面 220，
实现包 15 个子模块，109/109 纯搬移。分组按上面的推荐顺序而非行数。

**三处分组决定由审查状态机的真实环迫使**（改分组消不掉，只能下沉或合并）：

- retest 的分类原语（`_classify_retest_operations`、`_shadow_comparison`、
  `_finding_match_key`、`_finding_coverage_rule_id`、`_gate_difference`）放
  `base`：`events._project_events` 与 `retest` 都要用它们，留在 `retest`
  会造成 `events → retest → events` 环；
- `get_review` 归 `lifecycle` 而非 `events`：读取时会触发
  `_resume_async_review_if_needed`，是生命周期操作而非纯投影，
  留在 `events` 会造成 `events → lifecycle → events` 环；
- `_project` 留在 `events`：它编排 `_project_events` 与 `_freshness_reasons`。

规则执行顺序、finding ID、severity 与 blocker 聚合全部未变；
`_ASYNC_THREADS` / `_ASYNC_LOCK` 只在 `base` 有一份，异步复审记账仍是单实例。

同步了 `independence_scan.py` 的 5 条语义豁免路径（Wave 1 记录的路径耦合陷阱）。

### Wave 5：谨慎观察项（未启动，按收益单独立项）

以下仍不以行数为目的拆分，逐项现状见 §3.2：

- `runtime/transport.py`（1,041）；
- `runtime/resource_registry.py` 的架构迁移；
- `servers/lvke_feasibility_delivery/service.py` 主 handler（1,322）；
- `servers/lvke_archive/storage.py`（1,043）；
- `domains/finance/spec.py`（1,433）、`reference_schema.py`（1,459）、
  `evidence_binding.py`（1,182）；
- `domains/research/extractor.py`（1,485）；
- `domains/finance/industry_scenario_factory.py`（801）；
- `testing/source_reconstructed_acceptance.py`（1,810）。

这些模块先通过纯函数抽取、测试补齐或独立 ADR 解决具体痛点。

## 7. 每个拆分 PR 的执行流程

1. 记录 commit SHA、Python 版本、依赖 lock 信息和 worktree 状态；
2. 生成目标模块的消费者清单，包含 `from`、`import`、`importlib`、字符串路径和测试 monkeypatch；
3. 保存 import API、函数签名、工具/资源 schema 和关键 golden 输出；
4. 只做代码搬移、局部 import 调整和门面 re-export；
5. 先运行目标单测，再运行完整 integration 和 server smoke；
6. 检查导入图、循环依赖、启动时间和 stdout 协议；
7. 更新“已完成拆分”记录，并在 PR 中列出未迁移消费者；
8. 逻辑修复、依赖方向改造和重复代码合并另开 PR。

## 8. 验证协议

详见 `REFACTOR_VERIFICATION_PROTOCOL.md`。本节仅列出核心要点。

### 必跑命令

```bash
conda run -n lvke-mcp python -m pytest -q tests/integration
conda run -n lvke-mcp python -m lvke_mcp.testing.smoke_test
conda run -n lvke-mcp python -m compileall -q src/lvke_mcp
conda run -n lvke-mcp python scripts/module_metrics.py --check tests/fixtures/baseline/refactor/module_metrics.json
conda run -n lvke-mcp python scripts/api_snapshot.py --check tests/fixtures/baseline/refactor/api_snapshot.json
conda run -n lvke-mcp python scripts/independence_scan.py --strict
```

**基线在 `tests/fixtures/baseline/refactor/`，不是 `quality/`**——后者在
`.gitignore` 里，把重构基线放那里会让门禁在干净 clone 上静默跳过。
`api_snapshot.py` 不带 `--check` 会**覆写**基线，比较用途必须带上。

每个拆分还要跑 `scripts/split_fidelity.py <搬移前 ref> <门面> <实现包>`
验证是纯搬移（AST 逐定义等价）。

集成测试包含三个自动化护栏门禁（MCP 契约、Python API、依赖边界），
覆盖方案 §8 原先人工检查的工具名、schema、import 路径、签名、跨层边与循环。
这三道只覆盖**接口**层；`split_fidelity.py` 补上「语义等价改写」与
「helper 复制而非搬移」两类测试全绿的事故。

### 兼容性门禁

拆分 PR 合并前必须满足：

- baseline 已绑定干净 commit；
- 既有测试无新增失败、跳过或 xfail；批准的 skip 必须按测试 ID 比较，不能只比较数量；
- 14 个 server smoke 全部通过；
- 原 import 路径和稳定符号仍可用；
- 工具/资源名称、数量、schema 和 URI 无意外变化；
- 关键 golden fixture 的 JSON、财务数值、13 表和 docx manifest 无变化；
- 无新增跨层边、循环 import 或顶层懒 import 提升；
- workspace 隔离、幂等、错误 envelope 和 stdout 纯 JSON-RPC 保持不变；
- 变更说明包含拆分前后模块归属和未覆盖风险。

Wave 0 已修复原有的两个 skip：`test_skill_p1_012_locator_normalization_documented`
与 `test_skill_p2_013_cost_quantity_semantics_documented` 引用的 SKILL.md 已定位到
`skills/lvke-project-planning/references/preserved/`，断言从 `skipTest` 改为
路径存在性硬断言。**当前基线不存在批准的 skip，新增 skip/xfail 一律视为失败。**

### 失败处理

如果 baseline 本身失败，不得把“拆分前后失败数相同”视为通过。应先记录已知失败清单，或先修复 baseline，再开始拆分。遇到 import cycle、状态 identity 变化、数值差异或协议差异时，停止当前 PR，不在同一 PR 中修复业务逻辑。

## 9. 交付物

Wave 0–4 已交付：27 个巨型文件拆为 27 个实现包、198 个子模块，
实现包内共 995 个顶层函数/类定义。每个拆分都经 `split_fidelity.py`
按 AST 逐定义验证为纯搬移。Wave 5 观察项未启动。

每波的交付物（变更文件、兼容门面、测试与 API 快照、依赖扫描结果、
剩余风险）都在对应 commit message 里，`git log --oneline` 可按 wave 前缀检索。

## 10. 完成定义与当前状态

本方案完成的标志不是所有文件都低于 800 行，而是：

| # | 判据 | 状态 |
|---|---|---|
| 1 | 高变更、高耦合模块已按真实职责拆开 | ✅ 22 个文件，含全部 5,000/3,000 行级文件 |
| 2 | 稳定的单一职责大文件有明确保留理由 | ✅ 剩余 15 个逐项列明（§3.2） |
| 3 | 公开 MCP 和 Python 兼容契约有自动化门禁 | ✅ 5 组必跑 + `split_fidelity` |
| 4 | 每个拆分可独立回滚，未把业务修复藏进重构 | ✅ 每波独立 commit；一处既有行为明确记录为「照原样保留」 |
| 5 | 架构清债、依赖注入和业务重写有独立后续计划 | ⏳ Wave 5 与依赖方向 ADR 未启动 |

未变的契约：14 个 server、169 个工具、27 个资源条目、`mcp-envelope.v2`、
财务模型版本 `finance_model.v2.4` / 模板 `finance_tables.v3`、13 张表、
报告九章、review finding 生命周期。

未偿的债（不在本轮范围）：3 个历史循环 import 与 3 组禁止方向跨层边保持冻结，
无新增（明细见 `REFACTOR_VERIFICATION_PROTOCOL.md` §4）。

---

## 附录：当前重点模块的外部消费者观测

以下是 Wave 0 前应复核的当前消费者数量（包含 `src`、`tests`、`scripts` 的静态 import；动态字符串路径另行扫描）：

| 模块 | 消费者文件数 |
|---|---:|
| `finance/run_service.py` | 16 |
| `finance/spec.py` | 11 |
| `finance/finance_model.py` | 9 |
| `finance/table_render.py` | 7 |
| `project_planning/application.py` | 7 |
| `asset_acquisition/backend.py` | 7 |
| `finance/model_application.py` | 6 |
| `reports/application.py` | 6 |
| `asset_acquisition/tables.py` | 6 |
| `feasibility_delivery/service.py` | 5 |
| `data_analysis/service.py` | 4 |
| `finance_export.py` | 4 |
| `zero_material_delivery/service.py` | 2 |

消费者数量只用于风险排序，不能替代调用图分析；动态 import、`*` import、monkeypatch 和模块级状态必须在 Wave 0 单独确认。
