# MCP 与 Skills 缺陷修复方案

生成时间：2026-08-05　适用仓库：`/Users/mac/Desktop/mcp_servers`（独立发行版 `lvke-mcp`）

## 一、背景

本轮验收对 24 个 MCP server、262 个工具执行了 30,550 次真实 `tools/call`，暴露 20 个缺陷
（2 个 P0、14 个 P1、4 个 P2），阻塞正式验收。

值得说明的是：**仓库现有 7 个集成测试全部通过**，却没能拦住这 20 个缺陷。原因是这些测试
直接 import service 模块调用函数，绕过了 stdio / transport 层——而
[transport.py:517](src/lvke_mcp/runtime/transport.py#L517) 先执行 handler（写入落盘），
再到 [566-580](src/lvke_mcp/runtime/transport.py#L566-L580) 校验 outputSchema。
写入先于校验，这正是 P0-009 这类"报错了但状态已改变"的非原子缺陷的结构性来源。
`runtime/stdio.py`（handler 在 155 行，校验在 172-190 行）有同样的顺序问题。

## 二、已确认的边界

用户已明确的四项决定与四条禁区，方案全程遵守。

| 项 | 决定 |
|---|---|
| 甲方资料根 | **仅用仓库 `docs/`**；`/Users/mac/Desktop/工程/hubei-lvke` 不作为资料源，无任何调用与代码协调 |
| 档案索引 | 用 `scripts/build_archive_index.py` 自建到 `~/.lvke`，不进 Git |
| 用户级配置 | `~/.claude.json` 与 `~/.codex/config.toml` **两边都改** |
| 节奏 | 先出详细方案，再按域实施 |

禁区：不新增权限/认证/授权/安全审查/安全门禁；不删除或覆盖已有不可变业务对象；
不新增联网搜索 MCP；凭据不写入仓库、日志或测试报告；保留现有错误对象与 partial 工件
作为历史记录，通过版本与资格校验使其失效，不做破坏性清理；修复后仅创建本地 commit，不 push。

skills 只在本项目 `.claude/skills/` 下，没有外部引用。

## 三、关键前提：工作树有 2683 行未提交改动

当前工作树相对 HEAD（commit `76eb673`）有 **46 文件 2683 行未提交改动**，其中包含：

- 整套 source-reconstructed 正式验收机制（`research/application.py` +118 行，
  `testing/source_reconstructed_acceptance.py` 全新文件）
- P2-010（DR 分页）与 P2-019（零资料 `resource_uris`）已在工作树修好（未提交）
- P0-009（证据策略误升级）bug **存在于未提交代码中**，HEAD 版不存在该机制
- 4 个打包配置文件与 1 个索引构建脚本，全部**未跟踪**

**方案适用起点**：当前工作树（含未提交改动），而非 HEAD 基线。

**20 项缺陷实施工作量分布**：

| 类别 | 数量 | 缺陷 ID |
|---|---|---|
| 需改代码 | 13 | P0-002, P1-003, P1-006, P1-008, P0-009, P1-011, P1-014, P1-015, P1-016, P1-017, P1-018, P1-012, P2-013 |
| 只需测试回归 | 2 | P2-010, P2-019 |
| 需跟踪 + 打包 | 5 | P1-001, P1-004, P1-011, P1-016（后两者与代码改动重叠） |
| 根因待定 | 1 | P1-007（需用真实 XLSX 复跑定位） |
| 纯文档或契约说明 | 2 | P1-012（locator 规范）, P2-013（cost 口径） |
| 最低优先级契约缺口 | 1 | P2-005（URL 审计与抓取不一致，只补文档） |

修复实施前需先决定如何处理这 2683 行：

1. **提交现有改动**（推荐），在新 commit 基础上实施 20 项修复，最后统一 commit 一次
2. **stash 现有改动**，从 HEAD 开始实施，完成后再决定如何合并 stash
3. **创建 worktree**，在干净分支上修复，完成后合并回来

**推荐方案 1**：未提交改动包含正式验收核心机制，是测试覆盖的必要前提；
已有 30,550 条真实 MCP 调用在此基础上执行，回退到 HEAD 会失去全部覆盖。
建议先提交为 `feat: source-reconstructed formal acceptance (WIP)`，再在其上修复 20 项缺陷，
最后统一 commit 为 `fix: close mcp acceptance blockers and contract gaps`。

---

## 四、七个缺陷簇

### 簇 1　数据获取与资料源（5 项）

**MCP-P0-002　Tavily 未连通**——两个独立故障：

1. `data_provider_status`（[service.py:1652](src/lvke_mcp/servers/lvke_data_acquisition/service.py#L1652)）
   在已经运行的 async handler 里调 `asyncio.run(tavily_provider.provider_status())`，
   抛 `RuntimeError: asyncio.run() cannot be called from a running event loop`，
   被 transport 包成 `internal_error`。
2. search / discover / fetch 得到 `providerRequested=none`，因为
   `TAVILY_MCP_URL` 与 `LVKE_MCP_TAVILY_SERVER` 均未设置，`configured_transport()` 返回 None。

关键结论：**Streamable HTTP 传输已经实现好了**
（`domains/research/providers/tavily.py` 正确使用 mcp 2.0.0 的
`create_mcp_http_client` + `streamable_http_client`），所以这一项的范围比原方案小得多——
只需修 `asyncio.run` 这一个 bug 加上配好环境变量，不需要写新的传输层。

**MCP-P1-003 / P1-008　资源清单 internal error**：
`lvke_data_acquisition/service.py:1579,1586` 与 `lvke_data_analysis/service.py:2084,2088`
引用 `_RESOURCE_STORES`（带下划线前缀），而 adapter 导出的是 `RESOURCE_STORES`（不带），
两个 service 都没 import → `NameError`，在到达分页 helper 之前就炸了。
`*_read_resource` 之所以正常，是因为它走 adapter 内部的 `resolve_repository_resource()`，
在 adapter 模块作用域里访问 `RESOURCE_STORES`，没碰到这个名字。

**MCP-P1-004　外部语料 / 本地导入**：`external_corpora.v1.json` 存在但**未被 Git 跟踪**
→ 不在 wheel 里。`LVKE_EXTERNAL_CORPUS_ROOT`（必需，无默认值）未设置。

**MCP-P1-001　档案索引不可用**：`scripts/build_archive_index.py` 存在但**未跟踪**；
`metadata.sqlite`、`bm25/` 在 `LVKE_ARCHIVE_DATA_DIR` 与仓库默认 `data/archive_index/`
都不存在。`extract_structure` / `get_template_paragraph` 返回 `index_unavailable` 是正确行为。

**MCP-P2-005　URL 审计与抓取不一致**：`data_audit_urls(live)` 与 `data_fetch(direct_http)`
走不同的网络解析路径。这不是安全检查，作为契约缺口记录，优先级最低。

**打包配置（同时解决 P1-011 / P1-016）**：`src/lvke_mcp/config/` 下 4 个文件全部**未跟踪**
→ 8 月 4 日构建的 wheel 里 `lvke_mcp/config/*` 条目为零（已核验）。
`pyproject.toml:60` 声明了 `config/**/*`，但 setuptools 默认只打包被跟踪的文件。
代码里的 `parents[2]` 路径推导在开发态和安装态**都是正确的**，不需要改成 `importlib.resources`。

### 簇 2　财务与十三张表（3 项）

**MCP-P1-014　FinanceSpec 版本不一致**：`domains/finance/spec.py:332-333`——
遇到不受支持的版本（例如连字符写法 `finance-spec.v3`）时 `migrate_spec_to_v3` 原样返回，
不迁移也不报错。`SUPPORTED_SPEC_VERSIONS` 只含下划线三种写法，连字符别名没有映射。
已复现：`finance_spec.v1/v2/v3` 与 `None` 都能迁到 v3，唯独 `finance-spec.v3` 停在原值。

**MCP-P1-015　三张表语义检查被阻断**：这三项检查**不是空壳**，是真实的数据完备性闸门
（[finance_export.py:1259-1292, 1351, 1368, 1387](src/lvke_mcp/adapters/spreadsheets/finance_export.py#L1259)）：

- `investment_quantity_indicator`：要求附表1明细行（编号以 `1.` 开头）的
  `quantity` 与 `indicator` 两列都非空，且 `quantity × indicator ÷ 10000` 与 `total`
  的偏差在 0.01 以内（line 1318-1325 的独立复算）。
- `working_capital_reconciled`：仅当 `method == "turnover_days"`、未被
  `scaled_to_stated_total` 强制缩放、且与附表1声明值偏差在容差内时通过；
  其他计算方法一律 `False`（line 1291-1292）。
- `supporting_schedules_formula_driven`：附表 2/3/6-1/6-3/8 每张都要有表内非依赖公式，
  或该表被标记 `not_applicable`。

**这改变了修复方向**：不能放宽这些校验（它们守的是"金额不能只填总数"这类实质诚信规则），
而应让 blocker 明确指出缺哪个输入、该调哪个工具补。

导出原子性：XLSX 先落盘再校验，与 transport 同样的非原子模式，需改为临时目录构建 →
校验 → `os.replace` 原子发布。

**MCP-P1-016　评审标准目录无效**：`review_standard_requirements.json` 未跟踪，与 P1-011 同源。

### 簇 3　Deep Research（2 项，含 1 个 P0）

**MCP-P0-009　证据策略误升级 + 质量确认非原子**（缺陷在未提交代码中）：

- 误升级：`domains/research/application.py:388` 只从 `evidence_pack_ids` 聚合
  `evidence_policy`，当没传 evidence pack、或 pack 里没有该字段时默认
  `"formal_evidence"`；line 398 随即把 `project_fact_certified` 置为 `True`。
  但 `citations[]` 里每条引用自带的 `evidence_policy` 从未被读取——于是**每条引用都是
  `source_reconstructed` 的包，会被认证成正式证据**。
- 非原子：`dr_confirm_quality` 在 512-516 行写 QualityReview、526-530 行写 completed 包，
  535-543 行才构建并校验响应。校验失败时两次写入都已落盘，调用方看到
  `invalid_tool_output`，而 `dr_list_resources` 里包已经是 `completed`。

已核实：HEAD 版该文件中 `evidence_policy` / `project_fact_certified` / `reconstruction`
计数**全为 0**，整套机制是工作树里 +118 行未提交新增。所以这是**新代码的首次实现缺陷**，
不是既有基线的退化，修的是尚未提交的实现。

**MCP-P2-010　`dr_list_resources` 无分页**（报告称 6720 条）：工作树**已修好**——
`lvke_deep_research/server.py:1015` 已调用 `paginate_resource_entries`，
且 HEAD 版该文件无此调用。本项只需补回归测试固化，防止再退化。

### 簇 4　项目规划（3 项）

**MCP-P1-011　行业约束 / 技能解析**：`lifecycle.py:298` 读
`parents[2]/config/industry_params.yaml`，`application.py:57` 读
`parents[2]/config/industry_skill_routes.json`——两者都指向未跟踪、未打包的
`src/lvke_mcp/config/`。注意仓库里另有一份被跟踪的
`src/lvke_mcp/domains/finance/config/industry_params.yaml`，不是同一个文件，别混淆。

**SKILL-P1-012　MarketSizing locator 规范形式未说明**：
`application.py:641-651` 的 `normalized_locator()` 用
`canonical_json`（`separators=(",", ":")`，紧凑无空格）归一化；
调用方若用 Python `json.dumps` 的默认 `separators=(", ", ": ")`（带空格）就匹配不上
（line 696 的 `locator not in known_locators`）。代码本身已同时接受结构化对象与字符串，
缺的是文档和一次宽容的重归一化。

**SKILL-P2-013　成本口径与 flat 收入资格**：
`lifecycle.py:547` 只读 `annual_quantity`；`design_capacity` 从不参与金额计算
（line 576-577 的语义声明写得很清楚：`design_capacity_semantics =
"engineering_capacity_only_not_used_in_amount"`）。只传 `design_capacity` 会因
`quantity is None` 触发 `calculation_inputs_required`。这是**契约表达问题，不是算法缺陷**。

flat 收入：`application.py:1168-1186` 对 `model=="flat" and mode=="review_candidate"`
要求绑定 `source_id`/`content_hash`/`locator`，并对 `evidence_track ==
"source_reconstructed"` 追加 5 个重建字段。**它不区分 `process_acceptance` 与
`project_delivery`**——这里的 `mode` 是收入驱动自身的模式，不是全局交付范围。

### 簇 5　编排与跨服务（2 项）

**MCP-P1-017　跨服务解析器不一致**：
`feasibility_delivery/service.py:588-756` **已实现** 8 类 `lvke://` URI 解析
（source-files、data-acquisition、data-analysis、asset-acquisition、finance-model、
deliverable-review、deep-research、source-reconstructed）。正式门禁把真实 URI 报成
not_found，是因为分支先做 workspace 校验再解析 URI：URI 合法但对象属于另一个 workspace 时，
先撞上 workspace 检查，错误信息却是 `*_ref_not_found`（line 756），指向了错误的原因。

**MCP-P1-018　恒力缺 `process_acceptance` 确认路径**：分支**已经存在**——
`asset_acquisition/service.py:277` 接收 `confirmation_scope`，line 290 判定
`process_acceptance`，line 297-298 据此把 `project_fact_certified=False`、
`business_decision_status=not_selected`。测试失败是因为**调用方没传这个参数**，
需要的是输入 schema 的枚举与文档，以及六档独立运行的用例。

### 簇 6　零资料交付（1 项）

**MCP-P2-019　`delivery_list_resources` 返回空**：工作树**已修好**——
HEAD 版 line 1685 硬编码 `resource_uris=[]`，工作树已改为
`[str(item["uri"]) for item in page.get("resources") or []]`（未提交）。
`_RESOURCE_STORES` 在 HEAD 与工作树都已定义并被 `delivery_list_resources` 正确引用。
本项只需补回归测试。

## 五、修复清单

### 5.1 代码改动

| # | 文件 | 改动 |
|---|---|---|
| 1 | `servers/lvke_data_acquisition/service.py:1647` | `provider_status` 改 `async def`，`asyncio.run(...)` → `await ...` |
| 2 | 同上 `:19,1579,1586` | import `RESOURCE_STORES`，去掉下划线前缀 |
| 3 | `servers/lvke_data_analysis/service.py:10,2084,2088` | 同上（从 `data_analysis_repository` 导入） |
| 4 | `domains/research/application.py:386-402` | 同时从 `evidence_pack_ids` **和** `citations[].evidence_policy` 聚合；任一为 `source_reconstructed` 则整包降级且 `project_fact_certified=False` |
| 5 | `domains/research/application.py:512-543` | 先构建并校验完整响应，再写 QualityReview 与 completed 包 |
| 6 | `servers/lvke_data_analysis/server.py:93-97` | `_MISSING_FIELD` 去掉 `additionalProperties: false`，或补全 `aliases_tried`/`expected_unit`/`source_ids`/`next_action` 四个属性 |
| 7 | `domains/finance/spec.py:332-333` | 连字符版本别名归一化到下划线；仍不受支持的版本显式报错而非静默放行 |
| 8 | `adapters/spreadsheets/finance_export.py:1351,1368,1387` | 保留判定逻辑，扩充 blocker 文案：指明缺失字段与补数据的工具 |
| 9 | 财务导出路径 | 临时目录构建 → 校验 → `os.replace` 原子发布 |
| 10 | `servers/lvke_feasibility_delivery/service.py:588-756` | workspace 校验与 URI 解析分离，错误码区分 `ref_not_found` 与 `ref_wrong_workspace` |
| 11 | `servers/lvke_asset_acquisition/server.py` | 输入 schema 暴露 `confirmation_scope` 枚举（含 `process_acceptance`）并补描述 |
| 12 | `domains/project_planning/application.py:690-701` | locator 比较时两侧都重归一化，兼容带空格的 `json.dumps` 默认写法 |

不需要改代码、只需补回归测试的两项：**P2-010**（DR 分页）与 **P2-019**（零资料
`resource_uris`）在工作树里已修好。第 6 项要连带覆盖 `analysis_build_evidence_pack`，
因为 `_PACK_OUTPUT` 与 `_EXTRACT_OUTPUT` 共用同一个 `_MISSING_FIELD`。

**P1-007（`analysis_profile_tabular` XLSX → `invalid_tool_output`）根因未定**：
已排除 schema 冲突——`_OUTPUT` 基础 schema 是 `additionalProperties: True`
（`lvke_data_analysis/server.py:18`），额外字段不会被拒。需要实施时用那份 103KB
真实 XLSX 复跑一次，从 stderr 抓 `logger.exception` 打出的 ValidationError JSON path
定位到具体字段，再决定改法。

复用的既有工具，不新写：`runtime/storage.py:45-89` 的 `paginate_resource_entries`、
`runtime/storage.py:33-34` 的 `canonical_json`、`runtime/storage.py:150-164` 的
FileLock + 临时文件 + `os.replace` 原子写、`runtime/responses.py:24-113` 的 `ok`/`err`、
`runtime/schemas.py:88-124` 的 `make_tool_output_schema`。

### 5.2 打包与索引

1. `git add src/lvke_mcp/config/*.json src/lvke_mcp/config/*.yaml scripts/build_archive_index.py`
2. 重建 wheel，核验 `lvke_mcp/config/*` 四个条目出现
3. 运行一次 `scripts/build_archive_index.py`，输出到 `~/.lvke/archive_index`

### 5.3 文档改动

- `.claude/skills/` 中 3 个文案仍提到 hubei-lvke 的 skill，改为仓库内 `docs/`（纯文案）
- 补 MarketSizing locator 规范形式说明（紧凑 JSON，`separators=(",", ":")`）
- 补 `annual_quantity` 与 `design_capacity` 的口径说明
- 原测试计划里的 `quick_validate.py` 不存在，替换为 skills 与 `agents/openai.yaml` 一致性检查

### 5.4 用户级配置（两边都改）

`~/.claude.json` 与 `~/.codex/config.toml` 中给对应条目补环境变量：

| server | 追加的环境变量 |
|---|---|
| `lvke-data-acquisition` | `TAVILY_MCP_URL`、`TAVILY_MCP_BEARER_TOKEN`、`LVKE_EXTERNAL_CORPUS_ROOT` |
| `lvke-source-files` | `LVKE_EXTERNAL_CORPUS_ROOT=/Users/mac/Desktop/mcp_servers/docs` |
| `lvke-archive` | `LVKE_ARCHIVE_DATA_DIR=/Users/mac/.lvke/archive_index` |

Tavily 的 URL 与 bearer 取自现有 tavily-hikari 配置，**不写入仓库、日志或测试报告**。
两个文件都先备份。改完需重启会话，MCP server 只在会话启动时加载。

## 六、实施顺序

先修 P0 原子性，避免错误的 `evidence_policy` 继续扩散；再自下而上打通依赖链。

1. P0-009 证据策略与写入原子性
2. Tavily + 资源清单（打通 获取 → 分析 → 研究 链）
3. 打包配置解析（行业参数、评审标准、外部语料）
4. 跨服务解析器（统一正式血缘）
5. FinanceSpec 版本 + 三张表 blocker 文案（打通十三张表）
6. 报告与评审标准（九章 + finding 复测）
7. 恒力 `process_acceptance`（六档独立运行）
8. 收尾：分页回归、locator 文档、零资料清单

## 七、验证

### 回归测试

新增 `tests/integration/test_mcp_acceptance_20_defects.py`，每个缺陷 ID 一个测试方法。
覆盖：Tavily provider_status 在事件循环内可调、空 CandidateSet 的 missing_fields 形状、
XLSX 画像、4 个服务的资源清单 + cursor 往返、DR 提交保留
`source_reconstructed`、质量确认失败时零写入、连字符版本迁移、三张表 blocker 文案含缺失字段名、
标准目录可加载、跨服务解析 8 类对象、恒力六档 `process_acceptance`、
结构化 locator 两种 JSON 写法都能匹配。

注意本仓库**没装 pytest**，用 `unittest`；`unittest discover` 会因
`tests/` 不可 import 而失败，直接跑文件：

```bash
PYTHONPATH=src .venv/bin/python tests/integration/test_mcp_acceptance_20_defects.py
```

现有 7 个测试必须继续全绿。

### 真实 MCP 冒烟

配置改完并重启会话后，用全新 workspace：

1. 24 个 server 各完成一次 `initialize` 握手（用 Python subprocess harness，
   本机没有 `timeout` 命令，误用会静默返回空输出且退出码 0）
2. 每个受影响工具 ≥10 次真实调用；联网、XLSX、财务、导出不并行
3. Tavily 搜索 → DiscoverySet → SourceSnapshot → 往返读取
4. 崇阳 + 潜山：本地导入 → EvidencePack → 质量确认 → Market/Option/Scale/Drivers →
   FinanceSpec/BoE/Run → 技术与正式表 → 九章 propose/diff/apply/validate →
   评审 finding/disposition/retest → `process_acceptance` 发布
5. 恒力六档：独立 spec → run → 表包 → 报告数据 → 血缘，全程保持
   `business_decision_status=not_selected`
6. 验证 `project_delivery + source_reconstructed` → `project_fact_evidence_missing`

通过标准：20 个缺陷全部关闭；两份报告与恒力六档过程验收链路完整；
未新增权限/安全门禁/联网搜索 MCP。

## 八、假设与遗留

- 档案索引继续引用外部 123MB 文件，不进 Git
- 现有错误对象与 partial 工件保留为历史记录，靠版本与资格校验使其失效
- `MCP-P2-005`（URL 审计与抓取不一致）作为契约缺口记录，本轮只补文档不改行为
- 全部检查通过后创建一次本地 commit：`fix: close mcp acceptance blockers and contract gaps`，不 push
