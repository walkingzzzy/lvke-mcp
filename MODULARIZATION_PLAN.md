# lvke-mcp 模块化重构方案（按当前代码快照）

> 更新时间：2026-08-07  
> 仓库：`/Users/mac/Desktop/mcp_servers`  
> 基线：Wave 0 已完成，基线绑定在 `e4f3385` 之后的 Wave 0 提交。  
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

以 `src/lvke_mcp` 为统计范围，当前观测到：

- 208 个 Python 文件；
- 约 95,084 行代码；
- 37 个文件达到 800 行；
- 这些超长文件合计约 65,928 行。

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

下表按当前工作区行数记录。`拆分`表示进入波次；`观察`表示暂不为降低行数而拆；`测试代码`表示不纳入生产模块化波次。

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

### Wave 1：试点和低风险 server（进行中）

| # | 文件 | 拆分前 | 门面 | 状态 |
|---|---|---:|---:|---|
| 1.1 | `servers/lvke_data_analysis/service.py` | 2,147 | 102 | ✅ 已提交 |
| 1.2 | `servers/lvke_source_files/service.py` | 1,240 | 157 | ✅ 已提交 |
| 1.3 | `servers/lvke_data_acquisition/service.py` | 1,743 | — | 进行中 |
| 1.4 | `servers/lvke_deep_research/server.py`（注册层） | 1,009 | 68 | ✅ 已提交 |

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

### Wave 2：编排与报告（预计 6–9 人日）

- `lvke_zero_material_delivery/service.py`：行业 profile、财务口径计算、跨域编排和 artifact/resource 分开；`start` 与 `confirm_assumptions` 的调用关系必须保留；
- `domains/project_planning/application.py`、`servers/lvke_project_planning/lifecycle.py` 与 `servers/lvke_project_planning/server.py`：先定义公开 application facade，再搬移 case/validation，最后拆 server schema、dispatch 和 builder；保留 lifecycle 所需私有兼容符号；
- `domains/research/application.py`、`domains/reports/application.py`、`domains/reports/doc_service.py`：按 lifecycle、quality、IO 边界拆；
- `domains/reports/artifacts.py`：先拆纯 docx 样式/引用，再拆文件 IO；
- deliverable review 的 `report_checks.py`、`financial_checks.py`、`rules.py`：按规则组拆，保留执行顺序和 finding ID。

### Wave 3：财务和收购核心（预计 12–18 人日）

此波只能在 Wave 0 的数值和 golden fixture 完成后执行，财务包内不并行修改同一依赖链。

推荐顺序：

1. `table_render.py` 与 `tables_service.py`；
2. `asset_acquisition/model.py`、`tables.py`；
3. `fact_pack.py`、`vendor_import.py`、`model_application.py`、`run_service.py`；
4. `finance_model.py`；
5. `servers/lvke_finance_model/server.py`，只拆 schema、handler、兼容路由和 builder；
6. `asset_acquisition/backend.py`；
7. `finance_export.py`。

每一步都必须验证 13 表、IRR、双轨、hash、审计链和 xlsx 输出，而不能只验证 import 成功。

### Wave 4：交付评审核心（预计 5–8 人日）

目标是拆分 `servers/lvke_deliverable_review/service.py`，这是当前最大且风险最高的生产文件。必须在 Wave 2 已稳定拆出 report、financial 和 rule checks 后执行。

推荐顺序：

1. 固化 review finding、disposition、retest、release 的状态机测试；
2. 抽取 workspace metrics 和跨域对象读取投影；
3. 抽取规则执行器，保持 finding ID、顺序、severity 和 blocker 聚合不变；
4. 抽取 retest 与 project events；
5. 保留原 `service.py` 作为 handler、状态机编排和兼容门面；
6. 对九章报告、财务门禁、假材料阻断和正式发布链执行完整验收。

该 Wave 不与财务核心 Wave 3 并行，因为 review service 直接消费财务、收购、报告和规划结果。

### Wave 5：谨慎观察项（按收益单独立项）

暂不以行数为目的拆分：

- `runtime/transport.py`；
- `runtime/resource_registry.py` 的架构迁移；
- `servers/lvke_feasibility_delivery/service.py` 主 handler；
- `servers/lvke_archive/storage.py`；
- `domains/finance/spec.py`、`reference_schema.py`；
- `domains/finance/industry_scenario_factory.py`；
- `testing/source_reconstructed_acceptance.py`。

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
conda run -n lvke-mcp python scripts/module_metrics.py --check quality/module_metrics.json
conda run -n lvke-mcp python scripts/api_snapshot.py --check quality/api_snapshot.json
conda run -n lvke-mcp python scripts/independence_scan.py --strict
```

集成测试包含三个自动化护栏门禁（MCP 契约、Python API、依赖边界），
覆盖方案 §8 原先人工检查的工具名、schema、import 路径、签名、跨层边与循环。

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

## 9. 交付物和估算

Wave 0–4 预计约 28–43 人日，区间取决于财务核心和 deliverable review 状态机的测试补齐程度；Wave 5 观察项不计入本轮承诺，也不承诺把全部 37 个超长文件一次性完成。每波结束必须交付：

- 变更文件和兼容门面；
- 测试及 golden/API 快照；
- import/依赖扫描结果；
- 实际耗时与剩余风险；
- 本文档的完成清单。

建议先完成 Wave 1 试点，再用真实耗时重新估算 Wave 2–4，而不是沿用静态经验工期。

## 10. 完成定义

本方案完成的标志不是所有文件都低于 800 行，而是：

1. 高变更、高耦合模块已经按真实职责拆开；
2. 稳定的单一职责大文件有明确的保留理由；
3. 公开 MCP 和 Python 兼容契约有自动化门禁；
4. 每个拆分可独立回滚，且没有把业务修复隐藏在重构中；
5. 架构清债、依赖注入和业务重写都有独立的后续计划。

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
