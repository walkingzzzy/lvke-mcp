# Lvke MCP 第二轮压缩方案

> 制定日期: 2026-08-07 | 审查修订: 2026-08-07 | 基线: 14 server / 193 工具（commit `745f7b1` 后）
> 目标: **下线 32 个旧名、增加 8 个聚合入口，193 → 169 工具（净减 24，-12%）**，**零功能缺失**
> 度量口径: 以 Conda 环境中完整序列化的 `tools/list` 字符数为准；token 数不再用“字符数 ÷ 4”代替真实 tokenizer 结果
> 实施结果: **14 server / 169 工具 / 完整 `tools/list` 141,035 字符**；v2 manifest 为 32 条唯一路由

## 结论摘要

**本轮净减 24 个工具，但迁移对象是 32 个旧公开名。** 机械聚类给出 36 个候选，逐一对抗验证后否决了 12 个：`analysis_compare` 簇存在 `readOnlyHint` 冲突，`report_validate` 本身就是发布门禁，导出簇因 openpyxl 输出非字节确定会破坏 hash 不变量，跨 server 的同名动词（19 个）是架构必然而非冗余。

**本轮最重要的发现是一个技术陷阱**：胖分支使用 `oneOf` 会让分支参数从公开投影消失。安全模式是**扁平 schema + enum 判别字段 + `allOf/if-then` 绑定**，这也正是仓库既有三个成功聚合先例的形状。样本中的 schema 投影下降 42%~93%，且 8 项服务端对抗校验全过；该样本不外推为完整 `tools/list` 的承诺降幅。

**功能不缺失由四道机制保证**：① `allOf/if-then` 让每个 `object_kind` 的完整约束在服务端仍然强制；② 过渡期内旧工具保留原 schema 与原 handler，新聚合入口使用独立 dispatcher；③ parity 测试通过旧、新 `ToolSpec.handler` 逐工具断言响应等价；④ v2 manifest 必须含 32 条唯一旧名路由，`removed_tool_count == len(entries) == 32`。

---

## 一、事实基线与方法

### 1.1 当前公开面成本（Conda 运行时重测）

本表使用 `tests/integration/test_mcp_compression.py::test_topology_tool_count_and_public_metadata_budget`
相同的序列化口径：完整构造 `types.Tool`，再紧凑序列化 `{"tools":[...]}`。`inputSchema` 列仅用于定位成本来源，不能冒充完整公开面。

| server | 工具 | 完整 `tools/list` 字符 | `inputSchema` 字符 |
|---|---:|---:|---:|
| lvke-project-planning | 36 | 40,141 | 30,855 |
| lvke-finance-model | 19 | 16,489 | 11,204 |
| lvke-deliverable-review | 15 | 16,142 | 12,358 |
| lvke-data-analysis | 11 | 13,090 | 10,205 |
| lvke-deep-research | 18 | 11,855 | 7,289 |
| lvke-source-files | 14 | 9,786 | 6,278 |
| lvke-report-generation | 13 | 9,159 | 5,858 |
| lvke-asset-acquisition | 12 | 8,614 | 5,528 |
| lvke-data-acquisition | 10 | 7,336 | 4,738 |
| lvke-feasibility-delivery | 10 | 6,824 | 4,424 |
| lvke-zero-material-delivery | 9 | 5,892 | 3,606 |
| lvke-reference | 12 | 5,719 | 3,015 |
| lvke-finance-tables | 8 | 4,986 | 2,975 |
| lvke-knowledge-governance | 6 | 4,597 | 3,089 |
| **合计** | **193** | **160,630** | **111,422** |

`lvke-project-planning` 占完整公开面的 25%，仍是唯一值得动大手术的目标。最终收益必须用同一命令重测；未接入真实 tokenizer 前不声明 token 降幅。

### 1.2 判定方法

对每个候选簇问四个问题，任一为"是"即**不可合并**：

1. **注解冲突**：组内 `readOnlyHint` 或 `destructiveHint` 不一致？（权限语义冲突，客户端会误判）
2. **幂等冲突**：组内 `idempotency_key` 必填性不一致，或一个固化对象另一个不固化？
3. **门禁不对称**：组内成员的 fail-closed 校验条数/强度不同，合并后弱者会拉低强者？
4. **跨进程**：成员分属不同 server？（MCP 下不同进程、不同 store，合并根本不可能）

只有四问全否，才进入"差异是参数取值还是业务语义"的判断。

### 1.3 继承第一轮的三条不变量

本轮完全继承 `dev-docs/config/mcp-compression-migration.json` 的不变量，不新增例外：

1. 只改公开路由，原业务实现保留为可 import 的内部库。
2. 既有 object ID / `lvke://` URI / workspace 校验 / hash / lineage / 公式 / 发布门禁**一律不变**。
3. 每个下线的公开名在 manifest 里有且仅有一条替换路由。

---

## 二、关键技术发现：判别式的选型规则

这是本轮方案的技术前提，**选错会让合并后的工具不可用**。

### 2.0 精确规则：每个 `oneOf` 分支各自受 2,048 字符约束

一句话规则：**`oneOf` 不是禁用，而是每个分支按 `_schema_size()` 计算的紧凑 JSON 字符串长度必须 <= 2,048**（`_PUBLIC_SCHEMA_INLINE_LIMIT`）。实现使用 `len(str)`，不是 UTF-8 字节数；判断条件是 `> 2048`，因此 2,048 本身仍会内联。超限分支会被压成只含类型、schema URI 与 pointer 的存根，分支参数从 `tools/list` 消失。

实测扫描单分支规模（两分支等大）：

| 单分支原始大小 | 判别式 | 顶层参数 |
|---:|---|---|
| 1,432 字符 | 存活 ✓ | 完整 |
| 2,692 字符 | 被压 ✗ | 0 个 |
| 3,952 字符 | 被压 ✗ | 0 个 |

且**逐分支独立判定**——故意构造"分支 0 小(431)/分支 1 大(3,952)"，结果分支 0 完整存活、分支 1 被压成 0 个参数。这解释了 planning 样本：4 个分支里只有第 0 个（1,662 字符）存活，其余（2,547 / 2,350 / 4,912）全被压掉。

**由此得到选型判据**：

| 场景 | 单分支规模 | 推荐模式 |
|---|---|---|
| 参数少、分支瘦（如 cancel/resume） | 每分支 <= 2,048 字符 | 可用 `oneOf`，但仍须测试完整公开投影 |
| 参数多、分支胖（如 planning 的 create/prepare/confirm） | 任一分支 > 2,048 字符 | **扁平 + enum 判别 + `allOf/if-then`**（§2.2） |

两种模式都能做到公开面下降且校验不降级，区别只是分支胖瘦。**下一节记录的是胖分支场景下 `oneOf` 的失败实测**，作为选型依据的反面证据保留。

### 2.1 胖分支下 `oneOf` 会被压缩器摧毁（实测）

`transport.py` 的"保留全部顶层参数"保证只在 **root 且 `key == "properties"`** 时生效：

```python
# transport.py:861-864
if root and key == "properties" and isinstance(item, dict):
    # The public contract must retain every top-level argument name.
    # Large schemas are compacted one property at a time so the
    # properties map itself is never replaced by an opaque stub.
```

当 root 是 `oneOf` 时**没有 `properties` 键**，各分支走通用路径，超过 2,048 字符即被压成存根。实测把 4 个 `planning_create_*` 合成 `oneOf`：

```
分支 0: object_kind = {"type":"string","const":"build_scale"}   顶层参数 10 个 ✓
分支 1: object_kind = *** 丢失 ***    required = None    顶层参数 = []
分支 2: object_kind = *** 丢失 ***    required = None    顶层参数 = []
分支 3: object_kind = *** 丢失 ***    required = None    顶层参数 = []
```

公开面看似 -73%，但那是**销毁了 3 个分支换来的**——调用方无法知道 `cost_drivers` / `labor_plan` / `revenue_drivers` 需要什么参数。**故 planning 这类胖分支场景必须否决 `oneOf`**，改用 §2.2 的扁平模式；瘦分支场景（§2.0）不受此限。

### 2.2 胖分支的安全模式：扁平 + enum 判别 + `allOf/if-then`

仓库里三个成功的聚合先例全是**扁平结构**（它们的参数少，本可以用 `oneOf`，但扁平更省）：

| 先例 | 判别字段 | 顶层参数 | 原始大小 |
|---|---|---|---:|
| `source_inspect_workbook` | `operation`（5 值 enum） | file_id, operation, options, range, sheet, workspace_id | 900 字符 |
| `reference_observe` | `dataset`（3 值 enum） | dataset, filters, period, subject | 272 字符 |
| `finance_calculate` | `operation`（7 值 enum） | operation, inputs | 231 字符 |

模式是：**共同必填提到顶层 + enum 判别字段 + 专属字段收进通用容器**（`options` / `filters` / `inputs`）。

本方案照此推广，并额外用 `allOf/if-then` 把容器绑定到各 kind 的真实 schema。`payload` 中只能放**移除公共顶层字段后的分支专属 schema**，不能直接嵌入原工具的完整 root schema，否则会在 payload 内重复要求 `workspace_id`、`project_context_id` 和 `idempotency_key`：

```python
{
  "type": "object", "additionalProperties": False,
  "properties": {
    "workspace_id":       {"type":"string","minLength":1,"pattern":"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
    "object_kind":        {"type":"string","enum":["build_scale","cost_drivers","labor_plan","revenue_drivers"]},
    "project_context_id": {"type":"string","minLength":1},
    "idempotency_key":    {"type":"string","minLength":1,"maxLength":200},
    "payload":            {"type":"object"},          # 容器，公开面只露一行
  },
  "required": ["workspace_id","object_kind","project_context_id","idempotency_key","payload"],
  "allOf": [                                          # 服务端强制逐类完整约束
    {"if":   {"properties":{"object_kind":{"const":"build_scale"}},"required":["object_kind"]},
     "then": {"properties":{"payload": <仅含 build_scale 专属字段的严格 schema>}}},
    # … 每个 kind 一条
  ],
}
```

统一公开契约如下。读类聚合入口统一使用 `target_id`；旧名 shim 继续接收原 ID 字段并映射到 `target_id`。写类 planning 入口把选择或候选结构放进严格 `payload`。

| 新工具 | 固定顶层字段 | 分支字段 |
|---|---|---|
| `planning_validate` | `workspace_id`, `object_kind`, `target_id` | 无 |
| `finance_get_analysis` | `workspace_id`, `kind`, `target_id` | 无 |
| `planning_compare` | `workspace_id`, `object_kind`, `target_id` | 无 |
| `source_task_status` | `workspace_id`, `task_kind`, `target_id` | 无；dispatcher 映射回 `upload_id` / `job_id` |
| `delivery_transition` | `workspace_id`, `operation`, `delivery_run_id`, `idempotency_key` | cancel 分支额外要求 `reason` |
| `planning_confirm` | `workspace_id`, `object_kind`, `target_id`, `idempotency_key`, `payload` | 选择字段或确认理由 |
| `planning_prepare` | `workspace_id`, `object_kind`, `project_context_id`, `idempotency_key`, `payload` | 各候选结构及上游对象 ID |
| `planning_create` | `workspace_id`, `object_kind`, `project_context_id`, `idempotency_key`, `payload` | 各类直接固化输入 |

复杂 planning 聚合入口必须显式注册稳定 schema Resource：

```text
lvke://schemas/project-planning-validate
lvke://schemas/project-planning-confirm
lvke://schemas/project-planning-prepare
lvke://schemas/project-planning-create
```

仅把 URI 写进 migration manifest 不会创建 Resource；必须调用 `register_schema_resource()`，并测试 `resources/list` 与 `resources/read`。

### 2.3 样本实测：schema 投影下降 42%~93%，服务端校验零降级

以 `build_scale` + `labor_plan` 两类为样本（注册态含完整 `allOf`）：

```
公开面:   3,764 → 2,174 字符 (-42%)
注册态:   4,196 字符（含完整约束，不进 tools/list）
判别式:   ['build_scale','labor_plan']  存活 ✓
顶层参数: idempotency_key, object_kind, payload, project_context_id, workspace_id  全存活 ✓
```

八项对抗校验**全部达到预期**：

| 测试 | 预期 | 实际 |
|---|---|---|
| build_scale 合法入参 | 通过 | 通过 ✓ |
| labor_plan 合法入参 | 通过 | 通过 ✓ |
| **跨类串用**（kind=labor_plan 给 build_scale payload） | 拒绝 | `Additional properties are not allowed` ✓ |
| build_scale 缺 constraints | 拒绝 | `'constraints' is a required property` ✓ |
| welfare_rate 越界 1.5>1 | 拒绝 | `1.5 is greater than the maximum of 1` ✓ |
| headcount=0（minimum 1） | 拒绝 | `0 is less than the minimum of 1` ✓ |
| 未知 object_kind | 拒绝 | `'bogus' is not one of [...]` ✓ |
| evidence_bindings 缺 evidence_track | 拒绝 | `'evidence_track' is a required property` ✓ |

**跨类串用被拒**是最关键的一条——它证明判别式不是装饰，`allOf/if-then` 真的在按 kind 强制约束。

### 2.4 过渡期：旧 handler 与新 dispatcher 双轨注册

实测 `register_tool` 只拒重名，不拒同一 handler 使用不同名字，但这只证明注册能力，**不证明旧参数能直接调用新 dispatcher**。旧 schema 没有 `object_kind`、`target_id` 或 `payload`，不能直接复用新 handler。

```
同 handler 注册两个名字: 允许 ✓
重名: 被拒 ✓  tool already registered: new_name
```

过渡期采用以下明确策略：

1. 32 个旧工具保留原 input schema、原 handler 和原注解，仅在描述中标 `[DEPRECATED]`。
2. 新增 8 个聚合工具，绑定独立 dispatcher；dispatcher 只负责参数映射与调用原业务函数。
3. 每个旧工具与对应新分支做响应 parity；写工具还要验证旧幂等记录可由新入口重放。
4. 过渡期工具数为 **201（193 + 8）**，公开面会暂时增加；只有摘除 32 个旧名后才达到 169。
5. 摘除旧名之前完成 Skills、`_NEXT_TOOLS`、schema Resource、manifest 和持久化 `next_actions` 的迁移。

---

## 三、压缩提案（按风险分批）

### 批次一：零风险清理（不减工具数，减维护面）

这批不动任何公开工具。**先做这批**，因为它降低后续批次的验证噪音。

> **重要修正**：本节初稿把 5 项列为"死代码可删"，经 monkeypatch 探针实测后，**其中 4 项被推翻**——它们经跨域转发器对外可达或是真实功能缺口，删了会打断公开工具或掩盖 bug。修正后只有 2 项可删。这是本轮分析中最危险的一处误判，记录在此以免重犯。

| 项 | 证据 | 处置 |
|---|---|---|
| `_common/` 全部兼容垫片 | 文件形如 `from lvke_mcp.runtime.transport import *`，注释写明"切完即删"；全仓已无 import | **删除** ✓ |
| `transport.py` 的 `_public_output_schema` | 死代码，无调用点 | **删除** ✓ |
| `domains/review/` | 只有 1 行 `__init__.py` 空壳，注释说是"审查兼容元数据" | 删除（低价值，可选） |
| `runtime/jobs.py` 的 `JobRepository` | 141 行，全仓零调用方 | **保留**：可能是尚未接线的规划能力而非废弃能力，与"193 工具 `task_support` 全为 forbidden"吻合。标注"预留未接"，随异步能力一并决策 |
| ~~`zero_material_delivery/service.py:1663-1727` 的 `list_resources` / `read_resource`~~ | **推翻**：探针实测经 `rr.list_resources(ws,"zero-material-delivery")` 与 `rr.read_resource(...)` **真实调进**这两个函数 | **不可删**——它们是 `lvke_list_resources` / `lvke_read_resource` 的实际后端 |
| ~~`deliverable_review` 的 `list_resources` / `read_resource`~~ | **推翻**：同上，探针实测 `rev.list_resources` / `rev.read_resource` 均被调进；`deliverable-review` 在 `DOMAINS` 里 | **不可删**——已通过跨域转发器对外可达，既不需再注册也不能删 |
| ~~`report_prepare` 的 `project_metadata` 死字段~~ | **推翻**：它确实不在 schema 里（`additionalProperties:False` 故恒为 `{}`），但这是功能缺口不是死代码 | **不可删**——应补进 schema，见下 |
| ~~`report_start` 的 `chapters` 参数~~ | **推翻**：`chapters` **在** schema 里（`server.py:143-146`，`array of integer, minimum 1`）且被读入 `requested_chapters`（`application.py:413`） | 保留 schema；仅"无下游消费者"这一点成立 |

**新核实的可删项**（`domains/finance/tables_service.py`，用 `inspect` 实测 `__module__` 判定别名是否被后续 `def` 遮蔽）：

| 行 | 项 | 判定 |
|---|---|---|
| :25 | `_delivery_assessment = tables_application.delivery_assessment` | 被 `:805` 的 `def` 遮蔽 → **死赋值，可删** |
| :26 | `_delivery_keys = ...` | 被 `:817` 遮蔽 → **死赋值** |
| :27 | `_structured_delivery_tables = ...` | 被 `:821` 遮蔽 → **死赋值** |
| :30 | `_validate_render = ...` | 被 `:813` 遮蔽 → **死赋值** |
| :798-803 | `def _formal_delivery_gate(...)` | 全仓零调用点（真实 gate 走 `tables_application.formal_delivery_gate`）→ **可整段删** |
| :24 / :28 / :29 | `_load_run` / `_structured_table_manifest` / `_structured_table_quality` | 实测 `__module__` 指向 `tables_application`，**未被遮蔽，必须保留**——不可连带删除 |

**`table_registry().alias_tool` 不纳入零风险清理。** 它虽然会经 `tables_list_tables` 返回已下线的 13 个旧工具名，但 `tests/integration/test_mcp_compression.py::test_removed_table_aliases_are_exact_registry_routes` 明确把该字段锁定为第一轮迁移元数据。是否改成 `canonical_call` 属公开响应字段变更，必须单独决策并同步测试、基线和迁移文档，不能作为死代码顺手删除。

**探针方法**（本轮用来推翻误判的手段，建议后续沿用）：

```python
# 判定"某函数是否真的对外可达"——静态 grep 查不到动态分派，必须实测
import lvke_mcp.servers.lvke_deliverable_review.service as rev
from lvke_mcp.runtime import resource_registry as rr
hits = []
orig = rev.list_resources
rev.list_resources = lambda *a, **k: (hits.append("rev.list_resources"), orig(*a, **k))[1]
rr.list_resources("__probe__", "deliverable-review", limit=5)
assert hits, "未被调进 → 才是死代码"
```

**`project_metadata` 是真实功能缺口，不是死字段**：

`application.py:323` 读 `args.get("project_metadata")`，但该字段不在 `report_prepare` 的 schema 内且 `additionalProperties: False`，所以恒为 `{}`。下游后果是审查侧的 `PROJECT.METADATA.COMPLETE` 规则（属 `generic-feasibility` 与 `amusement-feasibility` 两个 rule_pack，适用于 `report_revision` / `combined_deliverable` 等 4 种 target）会从 `upstream.project_metadata` 读元数据（`deliverable_review/service.py:1447`），而这条链的源头恒空。

**正确处置是补 schema 而非删代码**——把 `project_metadata` 加进 `report_prepare` 的 `properties`，让调用方能真正传入 `project_type` / `industry` / `valuation_date` / `currency` / `amount_unit` / `tax_basis` / `forecast_period` 等字段（别名表见 `service.py:1453-1460`）。这是独立缺陷修复，不属压缩范畴，但应记入待办。

### 批次二：低风险合并（-11 工具）

组内注解一致、门禁同构、调用方引用少。

| 新工具 | 吸收 | 判别字段 | 净减 | skills 引用 |
|---|---|---|---:|---:|
| `planning_validate(object_kind=)` | 6 个 `planning_validate_*` | `object_kind`（6 值） | **-5** | 9 |
| `finance_get_analysis(kind=)` | `finance_get_balance_sheet` / `_monte_carlo` / `_basis_of_estimate` / `_fact_pack` | `kind`（4 值） | **-3** | 0 |
| `planning_compare(object_kind=)` | `planning_compare_market_cases` / `_revenue_candidates` | `object_kind`（2 值） | **-1** | 0 |
| `source_task_status(task_kind=)` | `source_parse_status` / `source_upload_status` | `task_kind`（parse/upload） | **-1** | 0 |
| `delivery_transition(operation=)` | `delivery_cancel` / `delivery_resume` | `operation`（cancel/resume） | **-1** | 0 |

> **`report_validate` + `report_validate_section` 已从本批移除**（初稿曾列为 -1）。原因：`report_validate` **本身就是发布门禁**——`report_export_docx(kind="formal_candidate")` 调它并以 `not validation.get("valid")` 硬拒（`domains/reports/application.py:640-649`）。而 `report_validate_section` 恒返回 `validation_complete=False` + `scope="section_only"`（`read_model.py:353,361`）。合并后若调用方传了 `section_id`，正式导出门禁就会拿到一个"永不完整"的校验结果——这正是本方案 §1.2 判据 3 要防的"门禁降级"。**不可合并。**

**`delivery_transition` 是本轮最干净的一条合并**——两个公开入口本就是同一函数：

```python
# zero_material_delivery/service.py:1578,1582
def cancel(args):  return _transition_control(args, operation="cancel")
def resume(args):  return _transition_control(args, operation="resume")
```

`_transition_control` 内三处 `operation ==` 分叉已把全部语义差异表达完毕（`:1599` 已取消拦截、`:1601` 非取消拦截、`:1603` next_stage 选择），不存在第二个隐藏判别维度。分支瘦（约 914 字节），按 §2.0 可直接用 `oneOf`——`cancel` 分支 `required` 含 `reason`，`resume` 分支不含，必填性差异天然表达。

三条 blocker（`delivery_run_not_found` 共有、`delivery_run_already_cancelled` 仅 cancel、`delivery_run_not_cancelled` 仅 resume）因分派器一字未改而全部可达。

> **唯一破坏点**：`service.py:1222` 的 blocker 文案硬编码"该运行已取消，须先调用 delivery_resume 创建恢复快照"，合并后指向已下线公开名，需同步改文案。

**`source_task_status` 风险已降为低**：两者 `outputSchema.status` 的 enum 完全相同（均为 `ok / partial / missing_inputs / blocked / failed / upstream_failure`）。新聚合入口统一接收 `target_id`，dispatcher 按 `task_kind` 映射为 `upload_id` 或 `job_id`；旧名继续保留原字段名和原错误语义。两者 ID 命名空间物理不相交（`ups_` 前缀 vs `job_` 前缀），还应增加“kind 与 ID 前缀不匹配时拒绝”的测试。

**为什么低风险**：

- `planning_validate` 六个成员全部 `readOnlyHint=True`、全不要 `idempotency_key`、公共必填只有 `workspace_id`、差异只是对象 ID 字段名。`lifecycle.py` 各函数 16–36 行且都调同一组 `_candidate` / `_envelope` / `_payload` 辅助——差异是校验规则不是流程语义。
- `finance_get_*` 四个都是纯读不可变对象、零副作用。**注意不要吸收 `finance_get_run`**（它有 `view` 参数与四种投影，语义更重）。
- `report_validate` 与 `report_validate_section` 已明确否决合并；本轮不得新增任何统一入口或可选 `section_id` 分支。

### 批次三：中风险合并（-13 工具）

需要逐条核对门禁后再动。

| 新工具 | 吸收 | 判别字段 | 净减 | 前置条件 |
|---|---|---|---:|---|
| `planning_confirm(object_kind=)` | 7 个 `planning_confirm_*` | `object_kind`（7 值） | **-6** | 三类选择语义必须用 `allOf/if-then` 分别绑定，见下 |
| `planning_prepare(object_kind=)` | 5 个 `planning_prepare_*` | `object_kind`（5 值） | **-4** | 各类候选结构差异大，容器需绑定完整 schema |
| `planning_create(object_kind=)` | 4 个 `planning_create_*` | `object_kind`（4 值） | **-3** | `minItems` 差异（cost_drivers 是 3，prepare 版是 1）须按 kind 保留 |

**`planning_confirm` 的三类选择语义**（这是合并的难点，必须精确表达）：

| 类别 | 必填选择字段 | 语义 |
|---|---|---|
| market_case / revenue_drivers / build_scale | `selected_candidate_id` + `rejected_candidate_ids` + `selection_reason` | 单选 + **必须列全舍弃项**（防隐式合并） |
| option_comparison | `selected_option_id` + `rejected_option_ids` + `selection_reason` | 同上但字段名不同 |
| cost_drivers / labor_plan | `confirmation_reason`（≥10 字） | 无候选集，只需确认理由 |
| policy_basis | `selected_candidate_ids`（**复数**） + `selection_reason` | 多选 |

这四种形态用 `allOf/if-then` 按 `object_kind` 绑定即可精确表达，且 `_selection()` 的"rejected 必须等于全集减选中"校验对相应类别继续生效。**验收标准**：至少逐类触发 `market_rejected_candidates_incomplete`、`planning_rejected_candidates_incomplete`、`option_rejected_list_incomplete` 及各类 reason-insufficient blocker，不能只测通用 lifecycle code。

**幂等命名空间必须逐 kind 保留**（这是批次三的隐藏陷阱，实测后确认有干净解法）：

`_idempotent_mutation` 的重放匹配条件是 `operation == 记录里的 operation` **且** key_hash 相同（`domains/project_planning/application.py:258-261`），而各工具传入的 `operation` 就是自己的工具名字面量（如 `operation="planning_prepare_cost_drivers"`，`lifecycle.py:535`）。

若合并后统一传 `operation="planning_confirm"`，**7 类对象的幂等键会塌进同一命名空间**——调用方用同一个 `idempotency_key` 先 confirm market_case 再 confirm cost_drivers，第二次会因 `request_hash` 不同而直接返回 `idempotency_conflict`。这是合并**制造**出来的新失败路径。

`operation` 是纯内部字段，既不在 input schema 也不在 outputSchema 中，但**不能按 `object_kind` 机械拼接**。`option_comparison` 的公开旧名是 `planning_confirm_option_comparison`，历史 operation 与 producer 却是 `planning_confirm_option_selection`。必须维护显式映射：

```python
CONFIRM_OPERATION_BY_KIND = {
    "market_case": "planning_confirm_market_case",
    "revenue_drivers": "planning_confirm_revenue_drivers",
    "build_scale": "planning_confirm_build_scale",
    "cost_drivers": "planning_confirm_cost_drivers",
    "labor_plan": "planning_confirm_labor_plan",
    "policy_basis": "planning_confirm_policy_basis",
    "option_comparison": "planning_confirm_option_selection",
}
```

prepare/create 也使用显式 `OPERATION_BY_KIND`，即使当前字面量可由 kind 拼出也不依赖这个偶然关系。验收必须覆盖两类序列：

- 同一 `idempotency_key` 跨 kind 调用不得产生压缩后新增的 `idempotency_conflict`；
- 先经旧入口写入幂等记录，再经新聚合入口提交等价请求，必须返回原重放结果而不是创建新对象。

#### 批次二、三的三处强制配套改动

对抗验证找出三处"改工具名会连带出事"的隐藏耦合，**每一处都实测确认，落地时必须一并处理**：

**① `_NEXT_TOOLS` 是运行时发射器，不是文档**（`lvke_feasibility_delivery/service.py:424-426`）

```python
"market": ["planning_prepare_market_case", "planning_validate_market_case", "planning_confirm_market_case"],
"option": ["planning_prepare_option_comparison", "planning_score_option_comparison", ...],
"scale":  ["planning_solve_build_scale", "planning_validate_build_scale", ...],
```

`feasibility_next_actions` 把这些名字以机器可读的 `{tool, arguments, reason}` 形式**返回给编排 Agent**。不同步改，编排器会持续让 Agent 去调不存在的工具。它是模块级代码常量（非持久化数据），改动成本低，但**必须在同一 commit 内改**。

**② 工具名字符串在内容寻址的 payload 里**（`domains/project_planning/application.py` 共 7 处）

`planning_prepare_market_case` 落库的 payload 含：

```python
"next_actions": ["调用 planning_compare_market_cases 比较路径偏差",
                 "调用 planning_validate_market_case 检查证据与口径", ...]
```

这个 dict 直接传给 `MARKET_CASE_STORE.put()`，而 `object_id = sha256(payload)[:24]`。实测同步改字符串的后果：

```
改前 object_id: mkt_5f525150ca65d28833ae1c19
改后 object_id: mkt_8fa88c5b848b3786af1f94f8   ← 同一业务输入落成不同对象
```

**违反不变量②。** 处置分两步：

- **批次三期间：payload 里的字符串保持不动**（零 hash 影响）。因为过渡期内旧名仍可调用，这些指引依然有效。
- **结束过渡期之前：先把 `next_actions` 从持久化 payload 中移出**。实测它唯一的消费者是 `_envelope(next_actions=payload["next_actions"])`——即那份数据只是响应信封的取值来源，不是业务事实。移出后工具名永不再影响 hash。这是一次性重构，**是摘除旧名的前置条件**。

> 顺带说明：`basis` 是与 payload 分开传的，不含 `next_actions`，所以 `basis_hash` 全程不受影响（已实测）。

**③ A-critic 的"扁平化削弱门禁"针对的是另一种写法**

有一条反对意见指出：把各 kind 的 `candidates` **上提到 root properties** 会让 root 的 `candidates` 变成三种形态的并集，于是 PolicyBasis 形态的候选能在 `object_kind="market_case"` 下通过校验。

**这条对 §2.2 方案不成立**——我的方案不上提，而是保留 `payload` 容器并用 `allOf/if-then` 逐 kind 绑定。实测双向拦截：

```
PolicyBasis 候选伪装成 market_case → 被拒 ✓
  Additional properties are not allowed ('classification', 'content_hash', 'locator', 'reason', ...)
market 候选伪装成 policy_basis     → 被拒 ✓
```

**落地时必须用 payload 容器写法，不得为了让顶层参数名出现在 `tools/list` 而上提深层结构**——那样做会打开一条今天由 JSON Schema 独家守住的 fail-closed 门。代价是调用方需回读 `lvke://schemas/project-planning-*` 才知道每个 kind 要传什么；这正是 §2.0 记录的既有权衡，也是全仓三个聚合先例（`operation=` / `dataset=`）已在用的模式。

### 批次四：导出合并 —— **已整体撤回**（净减归零）

> 以下保留分析过程，因为它记录了"看起来最对称、最该合并"的一组为何不能动。结论见本节末尾的撤回说明。

`review_export(formats=[json|markdown|docx|xlsx])` 已证明"一个工具多格式"在本仓库可行，是这两条合并的先例。但两组的门禁对称性**截然不同**，必须分开处理。

| 新工具 | 吸收 | 门禁对称性 | 风险 |
|---|---|---|---|
| `acquisition_export_tables(formats=[csv,xlsx])` | `acquisition_export_tables_csv` / `_xlsx` | **对称** ✓ | 低，可直接合并 |
| `tables_export(formats=[csv,xlsx])` | `tables_export_csv` / `tables_export_xlsx` | **不对称** ✗ | 中，需先修 |

**收购侧对称（可直接合并）**：两个导出函数在同一位置做同一道门禁——`_package` 取不到即 `TABLE_PACKAGE_NOT_FOUND`，随后都调 `_ensure_exportable(payload)`（要求 `integrity.status == "passed"`，否则 `TABLE_PACKAGE_INCOMPLETE`），连 `_table_contract` 的调用都一致。合并后两格式共用同一门禁，无强弱之分。

**通用十三表侧不对称（先修再合）**：`export_xlsx` 有"两条件合取"，`export_csv` 只有一条：

```python
# tables_service.py:173  xlsx —— 两条件合取
formal_ready = bool(validation.get("validation_complete")) and bool(export_quality.get("validation_complete"))
if validation.get("validation_complete") and not export_quality.get("validation_complete"):
    blockers.append("xlsx_delivery_quality_not_formal")

# tables_service.py:320  csv —— 只看 package 门禁，无 csv_delivery_quality 深度审查
"delivery_mode": "formal" if rendered.get("validation_complete") else "draft",
```

源码注释写明 xlsx 的设计意图是"XLSX 成功写出绝不单独抬升正式资格"。**若直接合并，两种结果都不可接受**：CSV 被动继承严格门禁（行为变更，虽是收紧但未经评审），或 XLSX 被 CSV 拉松（正式资格门禁松动 = 事故）。

> 顺带修正一处文档错误：CSV 实际导出 **14 个文件**（13 表 + `00_数据血缘.csv`，见 `tables_service.py:258,318`），`MCP_SERVICES.md` 与工具描述写的"13 张"不准。

#### ⚠ 批次四已撤回（对抗验证后否决，含"对称"的收购侧）

初稿把收购侧判为"低风险可直接合并"。对抗验证提出五条反对，**逐条实测后全部成立**，故整个批次四撤回：

**① openpyxl 输出非字节确定，破坏不变量②**（最致命）

提案的安全性论证是"重跑写出字节等价文件，故历史幂等记录不命中也无害"。实测该前提为假：

```
同一 Workbook 两次 save 的 sha256：
  9151a574abe6a1e95022d9ba966b17e0
  c991fe624ca6ebd8a9d6c78c2d776585      ← 不同
差异文件：docProps/core.xml
  dcterms:created:  2026-08-07T00:54:11Z vs ...:12Z
```

openpyxl 每次 `save()` 都把当前时钟写进 `docProps/core.xml`。于是同一个 `acquisition_tables_package_id` 在迁移后返回的 `xlsx_hash` 与调用方此前持久化的值不一致——**直接违反不变量②"既有 hash 一律不变"**。

**② 幂等命名空间塌陷制造新失败路径**

两个导出工具各自传 `operation="acquisition_export_tables_xlsx"` / `..._csv`（`service.py:499,510`）。合并后若统一 operation 并把 `sorted(formats)` 并入 payload，今天合法的"同一 key 先导 CSV 再导 XLSX"序列会在第二次调用直接返回 `IDEMPOTENCY_CONFLICT`。

> 注意这与批次三的同名陷阱**性质不同**：批次三可以通过 `object_kind → 历史 operation` 显式映射恢复唯一命名空间；而导出侧的 `formats` 是**数组**，同一次调用可能含两个格式，无法映射到唯一的原 operation。

**③ 顶层信封语义未定义**：`export_csv` 的入口门禁是**丢弃整个 rendered 信封的 early return**，其失败形状与 `export_xlsx` 的成功信封结构不兼容。同时请求两格式时，顶层 `success` / `status` / `code` / `blockers` 如何合并没有可行解——取 AND 会掩盖单格式成功，取 OR 会让失败被忽略。

**④ 运行时发射器会指向已下线工具**：`feasibility_next_actions` 的 `_NEXT_TOOLS` 把 `tables_export_xlsx` 作为**运行时返回给 Agent 的工具名**发射（`lvke_feasibility_delivery/service.py:430`），不是文档也不是测试夹具。合并后编排器会持续告诉 Agent 去调一个不存在的工具。

**⑤ CI 硬失败**：`scripts/capture_samples.py` 的核心回放链按线协议调用这两个工具名（`:575,598,605,968,976`），下线后 `samples_manifest` 的 summary 从 `{ok:5, defect:0}` 变成 `{ok:4, defect:1}`，而测试对该字典做精确比对。

**结论**：批次四整体撤回，净减从 -2 归零。**保留其中两项独立缺陷修复**（与压缩解耦，仍建议做）：

- 给 CSV 补上与 XLSX 对称的深度审查门禁；
- 修正"CSV 导出 13 张"的文档表述为 14 个文件。

### 批次五：协议 Resource 隔离加固（当前工作树已有未提交实现）

原 `standard_resource_entries()` 试图遍历所有 workspace，并被挂到无参协议 lister。复核后确认两点：

1. 旧实现因 `workspace_root(".").parent` 的路径规范化实际恒返回空，历史上并未形成已证实的 URI 泄露；
2. 风险仍然真实存在：协议 `ResourceLister = Callable[[], ...]` 没有 workspace/session 参数，一旦有人“修正”遍历路径，就会立刻跨 workspace 枚举。

**正确处置不是给 lister 增加一个调用方无法提供的 workspace 参数，而是：**

- 协议层 `resources/list` 动态 lister 恒为 `lambda: []`；
- 删除 `standard_resource_entries()`，防止未来误接回协议层；
- 工作区内枚举只走 `lvke_list_resources(domain="zero-material-delivery", workspace_id=...)`；
- 读取继续走带 `workspace_id` 的 `service.read_resource` 并保持 `resource_scope_mismatch` 门禁。

当前未提交工作树的 `server.py` / `service.py` 与 `tests/integration/test_zero_material_workspace_isolation.py` 已采用该方向。落地前要与并发改动负责人确认归属，不能在压缩提交中重复实现或覆盖。

---

## 四、明确不可压缩的部分

判定"不可压缩"与判定"可压缩"同等重要——以下是防止后人误合的记录。

### 4.1 注解冲突（硬阻碍）

| 簇 | 冲突 |
|---|---|
| `analysis_compare` / `analysis_normalize_compare` / `analysis_compare_benchmark` | **`readOnlyHint` 不一致**：`analysis_compare` 是只读，后两个是写。合并后无论取哪个值都会误导客户端的权限与缓存决策 |

### 4.2 跨 server 的同名动词是架构必然

`start`（5 个）、`status`（8 个）、`cancel`（3 个）、`resume`（3 个）分属不同 server，各有独立 store、独立 run 类型、独立 workspace 语义。**MCP 下不同进程无法合并工具**，这类"重复"不是冗余。

`dr_cancel` 是全系统唯一 `destructiveHint=True` 的工具，**绝不可与任何非破坏性工具合并**。

### 4.3 名义只读但实际很重的工具

不可并入通用读通道：

- `acquisition_get_artifact`：逐文件 sha256 + python-docx 解析 + zipfile 解 XLSX + 重跑四路数值一致性
- `report_get_readiness`：整体委托 `validate()`
- `finance_get_run`：有 `view` 参数与 summary/result/governance/full 四种投影
- `review_get`：从事件流投影并做 freshness 检查

### 4.4 通用 Resource 通道暂不能替代 get 类

`lvke_read_resource` / `lvke_list_resources` 覆盖 11 域，但**缺 `asset-acquisition` / `finance-model` / `reference`**；且 13 个 server 里 12 个的 lister 是 `lambda: []`（空），`resources/list` 不列举对象。

"把 get 全删、改用通用读"需要先补 3 个域 + 12 个非空 lister，工作量远超省下的工具数。**本轮不做**，留作后续独立议题。

### 4.5 planning 域内**不可跨组**合并（本方案只在组内合并）

批次三把 prepare / create / confirm **各自组内**合并成三个判别式工具。有人可能进一步问"既然都是入口，为何不合成一个 `planning_upsert(object_kind=, phase=)`？"——不行，三处实测证据：

| 看似可合的一对 | 承重差异 |
|---|---|
| `planning_create_cost_drivers` vs `planning_prepare_cost_drivers` | **产出状态相反**：create 落 `"status": "confirmed"`（`application.py:1498`）并生成 FinanceSpec 转换 ledger；prepare 落 `"status": "candidate"`（`lifecycle.py:522`）等待后续 calculate。表面只是 `minItems` 3 vs 1 的宽严两档，实为两条互斥产出路径 |
| `planning_create_build_scale` vs `planning_solve_build_scale` | **同一组规划约束在两处角色相反**：create 里 `capacity_floor_area_insufficient` / `plot_ratio_constraint_failed` / `building_coverage_constraint_failed` 是**阻断项**（`application.py:1331-1333`）；solve 里同样的计算结果是**数据字段**（`lifecycle.py:387-403` 把 `building_coverage` 等算成 float 存进候选，违规记入 `violations` 列表但不阻断）。合并会让"多方案求解允许不可行候选并存"这一语义消失 |
| `planning_create_labor_plan` vs `planning_infer_labor_plan` | **输入语义不同**：create 的 `positions[].headcount` 是调用方给定的结果且必填；infer 的 `position_requirements[]` 不含 headcount，它由班次/覆盖/自动化因子**推导**出来 |

因此 `planning_solve_build_scale` / `planning_infer_labor_plan` / `planning_calculate_cost_drivers` 三个工具**独立保留**，不并入 `planning_create`。

### 4.6 `finance_generate_package` 标着 DEPRECATED，但**不可下线**

这是全仓最容易踩的一个坑，单独立节警示。

它在 `servers/lvke_finance_model/server.py:13` 被明确标注 `DEPRECATED`，工具描述也写着"[DEPRECATED] 巨型组合入口；新工作流应显式调用 finance_run_model → tables_render"。看起来是本轮最该删的目标。**实测三条证据说明不能删**：

1. **它是财务附件的唯一写入路径**。`财务专业附表.xlsx` 的写入代码在 `domains/finance/run_service.py:1506`，而该行位于 `generate_workspace_finance_package`（`:1286-1733`，448 行）**函数体内部**。下线其唯一公开入口后，`lvke产出/{ws}/finance-tables/finance_artifacts/{run_id}/` 将永远为空——而读取方是 report 域的打包逻辑，读不到时**不产生任何 warning**。附件数从 N 静默变 0 且门禁不报错，这是最危险的降级形态。

2. **两个治理 code 仅在该函数体内可达**（全仓 grep 确认各 3 处引用全在此函数内）：`professional_finance_appendices`、`semantic_blockers`。下线后"行业 profile 缺正式交付输入"与"fact pack 进度表不一致"这两类真实缺陷再无任何公开入口会报出。

3. **替换路由无法表达**。`finance_run_model → tables_render` 是跨 2 个 server 的 3 步序列，不属于第一轮 manifest 的任何 `category` 枚举（`same_handler_alias` / `operation_or_dataset_route` / `global_resource_route` / `cross_service_move`）。硬塞 `cross_service_move` 会让迁移清单语义失真——它表达的是"同 handler 换 server"，而非"分解为多次调用"。

**结论**：保留该工具。若确要下线，前置条件是**先把附件写入与那两个 blocker 迁出该函数体**，这是一次独立重构，不属压缩范畴。

> 方法学教益：`DEPRECATED` 注解表达的是"不推荐新调用方使用"，不等于"实现已被替代"。判定可否下线必须看**函数体内是否还有独占的副作用与 blocker**，而不是看注解。

### 4.7 进程数不应再减（维持 14）—— 因为在本轮优化的维度上收益为零

**决定性数据**：进程合并**不减少工具数，也不减少公开面**。以最有合并理由的一对实测：

| | 工具数 | 公开面 |
|---|---:|---:|
| `lvke-finance-model` | 19 | 12,453 字符 |
| `lvke-finance-tables` | 8 | 3,332 字符 |
| **合并为一个进程后** | **27（不变）** | **15,785 字符（不变）** |

`tools/list` 是按工具枚举的，27 个工具无论分布在 1 个还是 2 个进程里，Agent 看到的上下文完全一样。**本方案的目标是压工具数与公开面，进程合并对这两个指标零贡献**，只改变部署形态。

因此这个议题与本轮无关，即使它有其他好处（少 2 个 Python 进程、少 2 次启动期 schema 校验），也应作为独立的部署优化议题评估，而不是混进压缩方案。

顺带记录四个候选各自的实质阻碍，供那个独立议题参考：

| 候选 | 阻碍 |
|---|---|
| finance-tables → finance-model | 收益为零（见上）；两者已共享 `domains/finance/`，但 store 分属 `finance-model` 与 `finance-tables` 两个 domain 目录，合并进程不改变这一点 |
| asset-acquisition → finance-model | 算法栈零共享：独立 `backend.py`（3,352 行）、独立十三表 key、独立 spec schema、独立 store |
| zero-material → feasibility-delivery | **门禁不对称**：零材料无 `release` 工具、无 release 门禁，`assurance_level` 硬编码 `estimate_preview`；合并还会把协议 lister 的潜在跨 workspace 风险搬进中央 Resource 网关进程，扩大未来误配置的影响面 |
| knowledge-governance → deliverable-review | 已有跨域读取耦合（`knowledge` 侧直接声明了一个指向 `deliverable-review/rubric_assessments` 的 store），但审查侧是 11k 行最大 server，再并入会加重启动期校验 |

**结论：14 个进程维持不变。** `server_manifest.py` 硬断言 14，[`MCP_INDEPENDENCE_PLAN.md`](../architecture/MCP_INDEPENDENCE_PLAN.md) 把“独立可运行”列为验收前提，而本轮又证明合并在目标维度上无收益——三个理由指向同一结论。

---

## 五、功能不缺失的保证机制

### 5.1 四道机制

1. **`allOf/if-then` 保校验**：每个 `object_kind` 的完整约束在注册态 schema 里仍然强制，服务端校验强度不降（§2.3 八项实测）。
2. **旧名过渡期**：旧名保留原 schema 与原 handler，新名使用 dispatcher；一个版本后再摘除。
3. **parity 测试**：按 §5.3 比较旧、新 `ToolSpec.handler`，写工具另测历史幂等重放。
4. **迁移 manifest**：32 个下线名各有且仅有一条替换路由，`removed_tool_count == len(entries)`。

### 5.2 逐工具的功能保全清单（模板）

每条合并提案落地时必须填这张表，缺一项不予合并：

| 检查项 | 判据 |
|---|---|
| 参数完整性 | 原工具每个 `properties` 键在新工具可达（顶层或 payload 内） |
| 必填性 | 原 `required` 逐项在对应 `if-then` 分支内保留 |
| 数值边界 | `minimum` / `maximum` / `minItems` / `pattern` 逐项保留（尤其 `minItems` 的 1 vs 3 差异） |
| blocker code | 原 service 层每个 blocker code 仍可触发（列出 code 清单逐条打勾） |
| 返回 status | 原每种 status 仍可达 |
| 注解 | `readOnlyHint` / `destructiveHint` / `idempotentHint` 与原组一致 |
| 对象固化 | 固化的 store / id_prefix / URI 段不变 |
| 诚实性约束 | 恒定的 `validation_complete=False`、恒定 blocker 等不因合并丢失 |

### 5.3 parity 测试骨架

```python
# tests/integration/test_mcp_compression_round2.py
from lvke_mcp.servers.lvke_project_planning import server as planning_server

CASES = [
    # (object_kind, 旧公开工具名, 旧 ID 字段, 对象 ID)
    ("build_scale", "planning_validate_build_scale", "build_scale_case_id", "scale_x"),
    ("labor_plan", "planning_validate_labor_plan", "labor_plan_id", "labor_x"),
]

def _strip_volatile(resp):
    """剥掉 transport 注入的时间/审计字段后再比对。"""
    drop = {"started_at", "finished_at", "duration_ms", "trace_id",
            "input_hash", "runtime_instance", "build_time", "build_commit"}
    return {k: v for k, v in resp.items() if k not in drop}

def test_merged_validate_matches_legacy(workspace):
    # 过渡期内旧、新 ToolSpec 同时存在；直接比较真实公开 handler。
    srv = planning_server.build_server()
    merged = srv._tools["planning_validate"]
    for kind, legacy_name, legacy_id_field, target_id in CASES:
        legacy = srv._tools[legacy_name].handler(
            {"workspace_id": workspace, legacy_id_field: target_id}
        )
        got = merged.handler({
            "workspace_id": workspace,
            "object_kind": kind,
            "target_id": target_id,
        })
        assert _strip_volatile(got) == _strip_volatile(legacy), f"{kind} 响应不等价"

def test_cross_kind_payload_rejected(workspace):
    """跨类串用必须被 schema 拒绝——这是 allOf/if-then 生效的证据。"""
    from jsonschema import Draft202012Validator
    srv = planning_server.build_server()
    spec = srv._tools["planning_confirm"]
    v = Draft202012Validator(spec.input_schema)
    bad = {
        "workspace_id": workspace,
        "object_kind": "labor_plan",
        "target_id": "labor_x",
        "idempotency_key": "parity-1",
        "payload": {  # 给 labor_plan 分支塞 build_scale 选择字段
            "selected_candidate_id": "scale-a",
            "rejected_candidate_ids": ["scale-b"],
            "selection_reason": "明确选择规模候选并舍弃其他方案",
        },
    }
    assert list(v.iter_errors(bad)), "跨类串用未被拒绝，allOf/if-then 失效"

def test_annotations_unchanged(workspace):
    srv = planning_server.build_server()
    spec = srv._tools["planning_validate"]
    d = spec.annotations.model_dump(by_alias=True)
    assert d["readOnlyHint"] is True      # 原 6 个 validate 全为只读
    assert d["destructiveHint"] is False
```

不得直接把 lifecycle/service 函数当成单个 `dict` handler 调用；它们多为位置参数 API。parity 的比较边界是 `ToolSpec.handler`，完整协议验收边界则是重启后的真实 MCP call。

> 注意 `model_dump(by_alias=True)`——`ToolAnnotations` 属性名是 snake_case，`getattr(a, "readOnlyHint")` 会静默返回 `False`，我在本轮分析中已踩过这个坑（它一度把 91 个只读工具全误标为"写"）。

---

## 六、收益与实施顺序

### 6.1 收益汇总

下表的工具数是确定值；“省字符”沿用初稿的 schema-only 估算，只用于排序，不再用于宣称完整 `tools/list` 或 token 收益。实现后必须按 §1.1 统一口径重测。

| 批次 | 合并项 | 净减 | 省字符 | 风险 |
|---|---|---:|---:|---|
| 一 | 死代码清理（4 项，见 §3.1） | 0 | 0 | 零 |
| 二 | `planning_validate`（吸收 6） | -5 | 1,407 | 低 |
| 二 | `finance_get_analysis`（吸收 4） | -3 | 701 | 低 |
| 二 | `delivery_transition`（吸收 2） | -1 | 437 | 低 |
| 二 | `source_task_status`（吸收 2） | -1 | 309 | 低 |
| 二 | `planning_compare`（吸收 2） | -1 | 287 | 低 |
| 三 | `planning_confirm`（吸收 7） | -6 | 3,602 | 中 |
| 三 | `planning_prepare`（吸收 5） | -4 | 4,562 | 中 |
| 三 | `planning_create`（吸收 4） | -3 | 5,800 | 中 |
| ~~四~~ | ~~导出多格式合一~~ | ~~0~~ | ~~0~~ | **已撤回**（5 条反对全部实测成立） |
| 五 | 协议 Resource 隔离加固 | 0 | 0 | 低（当前工作树已有实现） |
| **合计** | **下线 32 个旧名，新增 8 个入口** | **-24** | **约 17,105（待重测）** | |

**稳定态工具数为 193 → 169（-12%）**。完整公开面由 160,630 降至实测 141,035 字符（-12.2%）；该值来自实际聚合 schema 的完整序列化，不用 schema-only 字符或“字符数 ÷ 4”推导 token 结论。

按旧估算：批次二净减 11、批次三净减 13，批次三约占 schema 节省的 82%。planning 是主要目标，但是否值得单独实施必须看完整 `tools/list` 重测，不能再用 3,141/119,981 的混合口径作结论。

§2.3 的样本说明扁平化可能显著降低 schema 投影，但不能外推为完整公开面“必达 -20%”。稳定态验收只接受重启后实时 `tools/list` 的实测结果。

> **数字演进（每一步都源于实测，非估算调整）**：
> 初稿机械聚类估 -36 → 排除注解冲突等硬阻碍后 -28 → 加入 `delivery_transition` 重算 -27 → 移除 `report_validate`（它本身是发布门禁）-26 → **撤回整个批次四（openpyxl 非确定性破坏不变量②）定为 -24**。
>
> 四次下调，每次都是因为对抗验证找出了"合并会坏什么"。这个收敛过程本身说明：**机械聚类给出的 36 个候选里有 12 个（1/3）是不能动的**，压缩方案的价值不在于数字多大，而在于把不能动的部分识别出来。

### 6.2 实施顺序与验收门槛

```
批次零（冻结与对齐：确认当前 dirty worktree 中批次一/五的归属，不覆盖并发改动）
  ↓ 门槛: 记录 git diff、193 工具与 160,630 字符基线
批次一（死代码与 Resource 隔离加固；不处理 alias_tool 公开字段）
  ↓ 门槛: Conda focused tests 通过 + 14 server 冒烟 initialize 成功
批次二-A（新增 5 个低风险聚合入口，旧名仍保留；193 → 198）
  ↓ 门槛: 新旧 handler parity + schema Resource 可读 + 注解不变
批次三-A（新增 3 个 planning 聚合入口，旧名仍保留；198 → 201）
  ↓ 门槛: 跨类串用拒绝 + blocker 全覆盖 + 历史幂等重放 + Skills 支持新入口
过渡版本（201 工具）
  ↓ 门槛: 重启后真实 MCP 调用新旧两轨；不得以 pytest 替代
批次二/三-B（摘除 32 个旧名；201 → 169）
  ↓ 门槛: manifest 32 条唯一路由 + _NEXT_TOOLS/Skills/基线无旧名 + 完整 tools/list 重测
（批次四已撤回，不实施）
```

**每批之间必须重启 MCP 进程后复验**——当前会话仍跑旧代码，不重启的"验证通过"不算。

Conda 验证命令：

```bash
conda run -n lvke-mcp python -m pytest -q \
  tests/integration/test_mcp_compression.py \
  tests/integration/test_mcp_compression_round2.py \
  tests/integration/test_zero_material_workspace_isolation.py

conda run -n lvke-mcp python -m lvke_mcp.testing.smoke_test
```

全量测试也必须经同一环境运行：`conda run -n lvke-mcp python -m pytest -q`。代码冻结后重启一次，再进行真实 MCP 对话式验收；开发测试通过不等于 MCP 验收通过。

### 6.3 需要决策的事项

1. **是否启用过渡期**：本修订建议启用。新旧名并存时工具数会从 193 增至 201，公开面暂时增加；摘除 32 个旧名后才降至 169。

   受影响面必须按最终 **32 个旧名**重新扫描。初稿的“26 个名、约 36 处”混入已撤回候选，不能作为迁移完成判据。

   批次四已撤回，其涉及的 `tables_export_csv` / `_xlsx` 保持原样。迁移完成判据是对 manifest 中 32 个 `old_tool` 做全仓扫描，并对每个残留逐项判定为持久化历史字符串、兼容测试或必须修正的活跃调用方。

   > 全仓 193 个工具里有 **83 个零引用**（`skills/` 与 `tests/` 均未提及）。零引用不等于无用（可能只是文档未覆盖），但它是压缩风险的有效排序依据。
2. **`JobRepository` 去留**：删掉是减维护面，保留是给异步能力留基础设施。取决于是否计划支持 `task_support != forbidden`。
3. **`deliverable-review` 的 `workspace_metrics`**：实现完整但从未注册。是补注册（+1 工具）还是删除？影子期出口指标目前 MCP 客户端拿不到。

---

## 七、迁移 manifest 形状

沿用第一轮 `dev-docs/config/mcp-compression-migration.json` 的字段语义：`removed_tool_count` 表示**下线旧公开名数量**，必须等于 `entries` 长度，而不是净工具数。独立 v2 文件为 32；若与 v1 的 85 条合并成累计 manifest，则为 117。

32 个旧名必须逐条展开，不能只在 manifest 中写通配模式：

| 新入口 | 必须迁移的旧公开名 | 数量 |
|---|---|---:|
| `planning_validate` | `planning_validate_market_case`, `planning_validate_revenue_drivers`, `planning_validate_build_scale`, `planning_validate_cost_drivers`, `planning_validate_labor_plan`, `planning_validate_option_comparison` | 6 |
| `finance_get_analysis` | `finance_get_balance_sheet`, `finance_get_monte_carlo`, `finance_get_basis_of_estimate`, `finance_get_fact_pack` | 4 |
| `planning_compare` | `planning_compare_market_cases`, `planning_compare_revenue_candidates` | 2 |
| `source_task_status` | `source_parse_status`, `source_upload_status` | 2 |
| `delivery_transition` | `delivery_cancel`, `delivery_resume` | 2 |
| `planning_confirm` | `planning_confirm_market_case`, `planning_confirm_revenue_drivers`, `planning_confirm_build_scale`, `planning_confirm_cost_drivers`, `planning_confirm_labor_plan`, `planning_confirm_policy_basis`, `planning_confirm_option_comparison` | 7 |
| `planning_prepare` | `planning_prepare_market_case`, `planning_prepare_revenue_drivers`, `planning_prepare_cost_drivers`, `planning_prepare_policy_basis`, `planning_prepare_option_comparison` | 5 |
| `planning_create` | `planning_create_revenue_drivers`, `planning_create_build_scale`, `planning_create_cost_drivers`, `planning_create_labor_plan` | 4 |
| **合计** | | **32** |

`aggregate_contracts` 必须为以上 8 个入口分别记录 discriminator、kinds/operations、旧 ID 映射和稳定 schema URI；下面 JSON 只展示字段形状，正式文件必须补齐 32 条 entry。

```json
{
  "version": "lvke-mcp-compression.v2",
  "invariants": [
    "Only public routing changes; original business implementations remain importable internal libraries.",
    "Existing object IDs, lvke:// URIs, workspace checks, hashes, lineage, formulas and release gates are unchanged.",
    "Every removed public name has exactly one replacement route in this manifest.",
    "Discriminated entry points: root-level oneOf only when every branch's compact JSON string length is <= 2048; otherwise flat schema + enum discriminator + allOf/if-then."
  ],
  "removed_tool_count": 32,
  "added_aggregate_tool_count": 8,
  "net_tool_count_change": -24,
  "aggregate_contracts": {
    "planning_validate": {
      "arguments": ["workspace_id", "object_kind", "target_id"],
      "discriminator": "object_kind",
      "kinds": ["market_case", "revenue_drivers", "build_scale",
                "cost_drivers", "labor_plan", "option_comparison"],
      "legacy_id_mapping": {
        "market_case_id": "target_id",
        "build_scale_case_id": "target_id",
        "cost_driver_set_id": "target_id",
        "labor_plan_id": "target_id",
        "option_comparison_id": "target_id",
        "revenue_driver_set_id": "target_id"
      }
    }
  },
  "entries": [
    {
      "old_tool": "lvke-project-planning.planning_validate_market_case",
      "replacement": "lvke-project-planning.planning_validate(object_kind=\"market_case\")",
      "category": "operation_or_dataset_route"
    }
  ],
  "stable_schema_resources": [
    "lvke://schemas/project-planning-validate",
    "lvke://schemas/project-planning-confirm",
    "lvke://schemas/project-planning-prepare",
    "lvke://schemas/project-planning-create"
  ]
}
```

v2 manifest 验收断言：

```python
assert manifest["removed_tool_count"] == 32
assert len(manifest["entries"]) == 32
assert len({item["old_tool"] for item in manifest["entries"]}) == 32
assert manifest["added_aggregate_tool_count"] == 8
assert manifest["net_tool_count_change"] == -24
```

新增第 4 条不变量记录本轮的技术教训；其阈值文字必须写成“紧凑 JSON 字符串长度 <= 2048”，不能写“分支字节数 < 2048”。

---

## 八、附：候选簇的完整判定记录

| 簇 | 工具数 | 历史 schema-only 成本 | 历史占比 | 判定 |
|---|---:|---:|---:|---|
| A1 planning_prepare | 5 | 7,660 | 6.4% | 可合并（批次三） |
| A2 planning_create/solve/infer | 7 | 11,685 | 9.7% | 部分可合并：4 个 create 合并；`solve` / `infer` / `calculate` 语义独立**不并入** |
| A3 planning_validate | 6 | 1,702 | 1.4% | 可合并（批次二） |
| A4 planning_confirm | 7 | 4,428 | 3.7% | 可合并（批次三，需精确表达四种选择语义） |
| B1 finance_get | 5 | 1,243 | 1.0% | 合并 4 个，`finance_get_run` 除外（批次二） |
| B2 dr_get | 4 | 893 | 0.7% | **不合并**：`dr_get_bundle` 语义是聚合登记，`dr_get_report` 有"财务数字不得取自此报告"的专门约束 |
| C1 analysis_compare | 3 | 3,751 | 3.1% | **否决**：`readOnlyHint` 冲突 |
| C2 planning_compare | 2 | 582 | 0.5% | 可合并（批次二） |
| C3 tables_validate | 2 | 1,030 | 0.9% | **否决**：不是"整包 vs 单表"的粒度差，而是两个不同对象——`tables_validate` 键控 `run_id`，`tables_validate_table` 键控 `finance_tables_package_id`，`required` 不相交 |
| C4 report_validate | 2 | 542 | 0.5% | **否决**：`report_validate` 是发布门禁本身（`export_docx` 硬拒），并入恒 `validation_complete=False` 的 section 版会导致门禁降级 |
| D1 tables_export | 2 | 571 | 0.5% | **否决**：门禁不对称 + 顶层信封语义无可行解 + `_NEXT_TOOLS` 运行时发射 + CI 硬失败 |
| D2 acquisition_export | 2 | 714 | 0.6% | **否决**：门禁虽对称，但 openpyxl 每次 save 写入时钟（`docProps/core.xml`），重跑得不同 sha256 → 破坏不变量② |
| E1 source_task_status | 2 | 625 | 0.5% | 可合并（批次二） |
| E2 跨 server 同名动词 | 19 | — | — | **架构必然，不可合并** |
| G 进程合并 | — | — | — | **维持 14 个进程** |

> `A2` 的 `planning_solve_build_scale`（多方案求解，88 行）、`planning_infer_labor_plan`（按班次/覆盖/自动化推导，79 行）、`planning_calculate_cost_drivers`（数量×单耗×单价展开）各有独立算法，与 `create`（单方案直接落库）不是同一操作，合并会掩盖语义差别。

---

## 九、⚠ 当前工作树与并发改动

本方案修订时工作树已存在大量未提交改动，且部分与批次一、批次五重叠。至少包括：

- `_common/`、`domains/review/__init__.py` 删除；
- `transport.py` 的 `_public_output_schema` 删除；
- `tables_service.py` 的死赋值与 `_formal_delivery_gate` 删除；
- zero-material 协议 lister 改为空、`standard_resource_entries()` 删除及隔离测试新增；
- finance/report/resource-registry/feasibility-delivery 的并行修改。

这些改动的作者和提交边界不能从 docstring 或 diff 推断。实施批次零必须先记录并确认归属，再决定复用、拆分或等待；禁止覆盖、回退或重复实现现有未提交修改。

当前重验结论：

| 结论 | 状态 |
|---|---|
| 实施前拓扑 | 14 server / 193 工具 |
| 实施前完整 `tools/list` 基线 | 160,630 字符 |
| 实施后拓扑 | 14 server / 169 工具 |
| 实施后完整 `tools/list` | 141,035 字符 |
| Conda focused tests | 15 passed，29 subtests passed |
| Conda server smoke | 14/14 passed |
| `report_validate` 发布门禁 | 仍成立，不可合并 |
| zero-material Resource 策略 | 空协议 lister + workspace-scoped 工具通道 |

协调要求：

1. 批次一/五先确认当前 dirty diff 的归属，不重复修改。
2. 模块化拆分若会移动 planning、tables 或 report 函数，压缩实现应基于拆分后的稳定位置，避免并行编辑同一文件。
3. `tests/fixtures/baseline/tools-list/*.json` 只在每个阶段代码与工具数稳定后重生成；过渡期 201 与稳定态 169 分别保留可审计快照。

---

*本方案的确定性目标是 32 个旧名映射到 8 个聚合入口、稳定态净减 24。公开面收益、token 收益和 live MCP 可用性必须分别实测，不能互相替代。凡方案推断与当前源码、Conda 测试或重启后的真实 MCP 调用冲突，一律以后者为准。*
