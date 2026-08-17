# 交互式研报生成开发方案（2026-08-08，v3 文档修订版）

> v1 的方向正确但流程错误，v2 已按代码实测全面重写。v3 仅修订交互编排说明，不修改 MCP、Skill、插件、测试或基线。v1 的三条严重错误（重复显式 start、四检查点接入、首次不落盘）均已作废，见 §8 勘误。

## 1. 背景与已确认根因

前三轮 MCP 对话式验收都用 `delivery_create_from_sentence → delivery_start` 一步生成十三表与报告。第三轮实测：假设包 6 个字段**全部 `confirmed=false`** 就进入了财务模型。

| 字段 | 注入值 | 区间 | 敏感度 | 优先级 |
|---|---|---|---|---|
| `total_investment_wan` | 37763.48 万元 | 16815–84670 | critical | 64 |
| `annual_revenue_wan` | 8640 万元/年 | 6221–11059 | critical | 64 |
| `loan_ratio` | 0.45 | 0.20–0.72 | high | 36 |
| `build_period_months` | 24 月 | 18–32 | high | 27 |
| `loan_rate` | 0.042 | 0.038–0.061 | high | 18 |
| `operating_period_years` | 18 年 | 8–20 | medium | 8 |

**两个独立根因（均已读码确认，不再是猜测）**：

**根因 A — 确认环节从未执行。** `delivery_list_assumptions` / `delivery_confirm_assumptions` 是为此预留的入口，三轮验收都没调。

**根因 B — 句中明确数值根本不被解析。** 输入句写"总投资约 1.35 亿元"，模型用了 3.78 亿。原因不是被覆盖，是从未被读取：

- `_service/intake.py` 只保存原句并解析行业路线，全文无金额抽取逻辑（无 `re.search` / 无单位匹配）
- `_service/assumptions.py:_build_assumption_package(intent)` 只读 `intent["industry"]`，经 `build_industry_scenarios` 取行业矩阵种子
- `assumptions.py` 中 `sentence` 一词**仅出现在第 145 行的 `source_precedence` 声明里**：

```python
"source_precedence": [
    "sentence_explicit_input",      # ← 声明为最高优先级
    "immutable_public_evidence",
    "industry_region_benchmark",
    "controlled_assumption",
],
```

声明把 `sentence_explicit_input` 列为第一优先级，但代码路径中无任何一处读取句子数值。**这是一条声明与实现不符的优先级链**，属独立缺陷，建议单独立项修复（§7-1）。在修复前，只能由交互执行者从原句提取明确值、归一化并写入最终 `confirmations`；这只是交互层临时补偿，不是服务端缺陷已修复。

---

## 2. 可行性边界（读码结论）

| 目标 | 判定 | 依据 |
|---|---|---|
| 6 项财务参数确认后重算技术预估 | **可行，不改 MCP** | `delivery_confirm_assumptions` 内部自动重算 |
| 四检查点全部影响最终十三表 | **当前不可行** | 零材料链自建 ProjectContext，无外部对象绑定入口 |
| 消除首次无效生成与落盘 | **需新增 prepare-only 接口** | `start()` 内部即渲染并导出 |
| 正式可研交付（formal） | **不可行** | 输出恒为 `estimate_preview`，研究/规划证据 blocker 未解 |

本方案只通过交互编排落实第一项。第二至四项以及服务端解析、校验和自动化验收缺口均保留为未解决技术债，见 §7。

---

## 3. 设计原则

### 3.1 交互载体不绑定特定宿主工具

plan 模式禁止一切写操作。`delivery_create_from_sentence`、`delivery_start`、`delivery_confirm_assumptions` 全部创建不可变对象并落盘，在 plan 模式下会被拒绝。**不能在 plan 模式里跑确认链。**

plan 模式仅适合可选的第一段只读调研（`planning_get_industry_constraints`、`archive_find_similar_projects`、`reference_*`、`tavily_search`），把参数建议写入 plan 文件，退出后再执行。

进入执行阶段后，优先使用宿主提供的结构化提问能力；不可用或调用失败时，立即降级为编号纯文本提问。交互流程不得依赖 `AskUserQuestion` 或任何单一客户端工具。

### 3.2 选项数值必须来自 MCP 返回

完整假设来自 `delivery_list_assumptions` 返回的 `assumptions[]`，待确认项来自 `confirmation_items[]`；只有持久化的 AssumptionPackage 对象内部使用 `fields[]`。每个选项的基准值、单位、区间、风险提示必须取自对应项的 `value`、`range.{low,base,high}`、`unit`、`validation_condition`、`confirmation_priority_score`。

唯一例外是用户原句中明确给出的值：交互执行者可以把它作为用户输入值归一化后写入 `confirmations`，但不得据此编造 MCP 基准或行业区间。缺单位、字段映射不清或同一字段出现冲突值时必须追问。

### 3.3 不改 MCP 代码

本方案仅修订编排说明文档。所依赖的工具契约均已实测确认存在，但服务端行为不因本文档发生变化。

---

## 4. 正确流程（仅一次显式 `delivery_start`）

```text
1. delivery_create_from_sentence(workspace_id, sentence, idempotency_key)
     → delivery_intent_id, delivery_run_id (stage=intent_resolved)

2. delivery_start(workspace_id, delivery_run_id, idempotency_key)
     → assumption_package_id (zma_*), 新 delivery_run_id (stage=preview_ready)
     ⚠ 此步内部已完成 finance_run + tables.render + export_csv + export_xlsx + report
       这些工件不可阻止生成，只可不展示。定位为"参数发现预览"。

3. delivery_list_assumptions(workspace_id, assumption_package_id, limit=10)
     → assumptions（完整六项）+ confirmation_items（待确认项）
     → confirmation_items 按 confirmation_priority_score 降序

4. 先处理原句中的明确值，再对其余字段逐项发问 + 单位归一化（§5）
     → 明确值不重复逐项询问；歧义值必须追问

5. 展示包含全部六项的最终参数摘要，取得用户批准
     ← 原句明确值也必须在此步批准；摘要位于 confirm 之前

6. delivery_confirm_assumptions(workspace_id, assumption_package_id,
                                confirmations=[…], idempotency_key)
     → 内部自动调用 start()，返回 automatic_recalculation=true
     → 返回的 run 即最终技术预估 run

7. 直接读取第 6 步返回的 finance_run_id / finance_tables_package_id / 工件 URI
     不再调用 delivery_start
```

**调用与计算次数**：正确流程显式调用一次 `delivery_start`，`delivery_confirm_assumptions` 内部再自动调用一次 `start()`，因此共有两轮生成。v1 在确认后又手工调用 `delivery_start`，才会造成第三轮计算。实际 `lifecycle.py` 在确认成功后构造 `zmd-auto-recalc-*` 幂等键并调用 `start()`，第 6 步返回的就是最终结果。

### 4.1 首次预览工件的处置（防误交付）

第 2 步已在参数确认前落盘一整套 FinanceRun、十三表、XLSX/CSV 与 DOCX。它们与第 6 步的最终产物**同名同构、仅 package_id 与 run_id 不同**，且基于未确认参数。若不加处置，交付时极易拿错。

业界 human-in-the-loop 实践对此有明确要求：审批门之前的副作用必须幂等，否则应移到审批之后或隔离到下游（LangChain HITL / LangGraph interrupts 文档均列此为硬规则，并把"审批前写入记录"列为反模式）。当前 `start()` 不满足幂等，因此必须在编排层补处置约定。

**强制记录**：第 2 步返回后立即登记待作废标识，并在最终交付说明中显式声明：

```text
已作废（参数发现预览）：
  delivery_run_id            = zmr_<首次>
  finance_run_id             = run_<首次>
  finance_tables_package_id  = ftp_<首次>
正式技术预估（第 6 步返回）：
  delivery_run_id            = zmr_<确认后>
  finance_run_id             = run_<确认后>
  finance_tables_package_id  = ftp_<确认后>
```

**禁止用 `delivery_transition(operation="cancel")` 关闭首次 run。** `lifecycle.py:50` 对 `stage == "cancelled"` 的 run 直接阻断后续操作，而 `delivery_confirm_assumptions` 内部要调用 `start()`；取消预览 run 会使确认链无法完成。`list_assumptions` 只按 `assumption_package_id` 读取、不校验 run stage，因此保留该 run 不影响确认流程。

**导出纪律**：只对第 6 步返回的 `finance_tables_package_id` 调用 `tables_export_xlsx` / `tables_export_csv`。首次预览的工件不展示、不导出、不写入交付清单。

---

## 5. 检查点 ②：六项确认（本方案唯一实施范围）

### 5.1 发问顺序

按 `confirmation_priority_score` 降序：`total_investment_wan`(64) → `annual_revenue_wan`(64) → `loan_ratio`(36) → `build_period_months`(27) → `loan_rate`(18) → `operating_period_years`(8)。

若原句已包含某字段的明确数值、阿拉伯数字和可识别单位，先按 §5.3 归一化并放入摘要，不再重复逐项提问。这里的“明确”要求字段和值一一对应；缺单位、只有模糊描述或同字段存在冲突值时仍按上述顺序询问。

### 5.2 问题模板（备选基准全部取自 MCP 返回）

```text
参数：total_investment_wan（总投资）
MCP 基准：37763.48 万元
区间：16815.32 – 84670.30 万元
敏感度 critical ｜ 决策影响 critical ｜ 置信度 0.42
验证条件：须确认参数，并以合同、测绘、报价或权属等材料替换

A. 采用基准 37763.48 万元
B. 采用下限 16815.32 万元
C. 采用上限 84670.30 万元
D. 我提供实际值（需按 §5.3 归一化）
```

### 5.3 自由输入必须归一化后回显再确认

契约允许 `value` 为 number/integer/string/boolean，但 `finance_align.py:202` 起直接 `float(values.get(...))`。用户输入 `"1.35亿元"` 会在自动重算阶段抛错。

**强制步骤**：

1. 读取该字段的 `unit`（如 `万元`）
2. 把用户口语数值换算为该单位下的**纯数值**：`1.35亿元` → `13500`
3. 回显：「已归一化为 13500 万元（原输入：1.35亿元），确认？」
4. 用户确认后才写入 `confirmations[].value`，且必须是 number 类型

`loan_ratio` / `loan_rate` 类字段单位为「比例」，`45%` → `0.45`，同样回显确认。

调用前还必须执行客户端约束检查：

- `total_investment_wan`、`annual_revenue_wan` 必须为大于 0 的数值
- `loan_ratio`、`loan_rate` 必须位于 `[0,1]`
- `build_period_months`、`operating_period_years` 必须为正整数
- 超出 MCP 返回的行业 `range.low/high` 只提示偏离风险，不阻止用户提交

这些检查是交互执行者的客户端防线，当前服务端没有字段级契约保证，不能表述为服务端已校验。

**原句补偿示例**：用户输入“总投资 1.35 亿元”，交互执行者换算为 `total_investment_wan=13500`，在最终六项摘要中展示“13500 万元（原输入：1.35 亿元）”。用户批准摘要后，以 number 类型写入 `delivery_confirm_assumptions.confirmations`。已实测该确认值会使 Finance 输入变为 `13500.0`，但原句抽取本身仍依赖交互执行者遵循本文档。

### 5.4 source_ref 与 note 不具备证据效力

`confirmations[]` 支持 `source_ref` 与 `note`，应填写数值出处（如"甲方提供合同"）。但必须清楚：

- 二者仅为文本记录，**不参与证据资格判定**
- 确认后假设包字段写 `source_type="user_confirmed"`（`lifecycle.py:306`）
- 而 FinanceSpec 仍固定记录 `source="controlled_assumption"`（`finance_align.py:252`）
- 真实公开证据必须经 `SourceSnapshot` → `EvidencePack` 通道

`user_confirmed` 与 `controlled_assumption` 描述的是两个不同维度：前者表示确认方式，后者表示证据资格，因此可以同时成立。**摘要中不得把“用户确认值”表述为“已有依据”或 `formal_evidence`，也不得尝试在文档层修改现有标签。**

### 5.5 摘要表三类来源

| 类别 | 含义 | 证据资格 |
|---|---|---|
| 用户确认值 | 用户提供或选定 | `controlled_assumption`（假设包内标 `user_confirmed`） |
| MCP 基准值 | 用户接受 `range.base` | `controlled_assumption` |
| 公开证据值 | 来自 EvidencePack，可溯 locator/hash | `formal_evidence` 候选 |

`controlled_assumption` 与 `technical_fixture` 永远不能升级为 `formal_evidence`。

---

## 6. 本次修订与执行边界

### 6.1 仅修订本文档

本次只更新 `INTERACTIVE_FEASIBILITY_PLAN_20260808.md`，不新建或修改 Skill、catalog、plugin 镜像、MCP 工具、Python 实现、测试、基线或 manifest。本文档描述的是人工/Agent 编排约定，不会自动改变服务端行为。

**v3.1 增补（本轮）**，两项均在文档层，未触碰代码：

| 位置 | 增补内容 | 触发原因 |
|---|---|---|
| §4.1 | 首次预览工件的作废登记与导出纪律；明确**禁止** `delivery_transition(cancel)` 关闭预览 run | 外部 HITL 实践要求审批前副作用幂等；且读码发现 cancel 会阻断确认链（`lifecycle.py:50`） |
| §7.1 | 7-1 服务端修复须用确定性正则抽取，禁止 LLM 填参 | 外部基准显示纯 LLM 抽取结构化财务数据仅还原约 12.5% |

### 6.2 与“一句话交付合同”的适用边界

本流程仅在 `delivery_mode=zero_material` 且用户显式要求交互式确认时使用。用户未要求交互确认时，仍按既有一句话交付规则执行，不因本文档自动增加六项追问。

### 6.3 当前无法由文档提供的能力

- prepare-only：首次 `delivery_start` 仍会生成并落盘 FinanceRun、十三表和报告
- 服务端字段级校验：§5.3 只是客户端/交互执行者约束
- 外部对象绑定：ProjectContext、BuildScaleCase、OptionComparison、EvidencePack 仍不能注入零材料链
- 自动来源口径统一：现有 `user_confirmed` 与 `controlled_assumption` 标签均保持不变

### 6.4 不改动项

MCP 工具契约、`external_corpora.v1.json`、财务模型、十三表渲染逻辑、Skill 与插件分发结构。

---

## 7. 处理决定与未解决技术债

| 编号 | 本次处理 | 服务端状态与后续建议 |
|---|---|---|
| 7-1 | **交互层临时补偿。** 从原句识别六项明确值，按 §5.3 归一化，纳入最终摘要并写入 `confirmations`；缺单位、映射不明或冲突时追问。 | 句中数值仍未被服务端解析，是已确认缺陷。服务端修复须用**确定性规则抽取**（见 §7.1 补充），不得依赖 LLM 解析填参。 |
| 7-2 | **保留为后续接口建议，优先级应高于"优化项"。** 本流程接受首次预览工件落盘但不展示，并按 §4.1 登记作废标识。 | 当前不存在 `delivery_prepare_assumptions`。首次预览产物与最终产物同名同构，是误交付风险源；prepare-only 契约是消除该风险的根治手段，不只是省一次空转。 |
| 7-3 | **明确延期。** 本方案不把外部规划或证据对象接入零材料链。 | 需要外部 ProjectContext、规划候选和 EvidencePack 时，改走 `feasibility_start → research/evidence → planning → FinanceSpec → FinanceRun → tables/report/review/release` 完整链。 |
| 7-4 | **客户端防线。** 在提交前执行 §5.3 的类型、单位、硬约束和软区间检查。 | 服务端仍没有字段级契约保证；后续应在 `delivery_confirm_assumptions` 写入或重算前实施原子校验。 |
| 7-5 | **文档解释语义。** `user_confirmed` 表示确认方式，`controlled_assumption` 表示证据资格，二者可以同时成立。 | 现有标签不修改；用户确认和 `source_ref` 均不能把数值升级为正式证据。 |
| 7-6 | **强化手工验收。** 按 §9 检查六字段传播、恰好 13 张表、13 个业务 CSV 和 DOCX 中文。 | 自动化缺口仍存在：`acceptance.py` 只确认一个收入字段，XLSX 断言仍是 `>= 13`，后续应单独补强。 |
| 7-7 | **文档层解决。** 优先使用宿主结构化提问；不可用时使用编号纯文本。 | 不再把 `AskUserQuestion` 作为流程成立的前置条件，不需要服务端改动。 |

### 7.1 补充：7-1 的服务端修复必须走确定性规则

修复 `intake.py` 的数值抽取时，实现方式须限定为**确定性正则/单位字典**，禁止改为"让模型读句子后填参数"。

外部工程证据支持这一限定：

- 一项以 Claude Opus 4 为被测模型的三路对比基准显示，纯 LLM 抽取只能还原约 12.5% 的结构化数据，而 regex + LLM 混合式接近全量保留
- 另有研究报告混合验证策略（regex 匹配 + LLM 判别）把主体幻觉率从 65.2% 降到 1.6%
- 多方实践共识：金额、单位、日期这类有形式规律的字段应由规则层抽取，模型只作兜底与判别

这与本项目既有边界一致——MCP 负责确定性计算，Agent 不负责算数与取数。若用模型抽金额，等于把幻觉引入财务模型的最上游。

建议实现形状：

- 在 `intake.py` 增加金额+单位正则（覆盖 `1.35亿元`、`13500万元`、`50MW`、`0.4161元/kWh` 等形态）
- 命中后写入 `intent`，由 `_build_assumption_package` 以显式值覆盖行业种子，并把该字段 `source_type` 标为 `sentence_explicit_input`
- 未命中或多值冲突时**不猜**，保持种子值并留给检查点 ② 追问
- 同步修正 `source_precedence` 的声明与实现不一致（当前声明 `sentence_explicit_input` 为最高优先级，但代码从不读句子）

---

## 8. v1 勘误

| v1 结论 | 实际 | 依据 |
|---|---|---|
| 确认后需再次显式调用 `delivery_start` | 错误。确认工具内部自动重算；再手工 start 会把正确流程的两轮生成增加为三轮 | `lifecycle.py:373` 起调用 `start()` |
| 首次 start 后"不调用 `tables_export_*`"即可避免导出 | 错误。`start()` 内部即导出，只能不展示 | `orchestration.py:216` |
| 四检查点均影响最终模型 | 错误。检查点 ①③ 未接入零材料链，用户选择不影响十三表 | `orchestration.py:98` 自建 context |
| 句中数值"需读码确认" | 已确认为缺陷，非猜测 | `intake.py` 无抽取；`assumptions.py` 只读 industry |
| 摘要在最终 start 之前展示 | 表述不清。应明确为「在 `delivery_confirm_assumptions` 之前」 | 见 §4 第 5 步 |

---

## 9. 验收方式

### 9.1 真实对话手工验收

1. 用一句话描述项目，至少包含“总投资 1.35 亿元”，用于验证交互层是否兜住根因 B。
2. 显式调用一次 `delivery_start`；确认预览工件已经落盘但不向用户展示，只读取假设包。
3. 调用 `delivery_list_assumptions`，确认 `assumptions[]` 包含六项，`confirmation_items[]` 按 `confirmation_priority_score` 降序。
4. 将原句总投资归一化为 `total_investment_wan=13500`，不重复询问该字段；对其余未明确字段逐项提问。所有 MCP 备选基准、单位和区间必须与返回值一致。
5. 对自由输入执行 §5.3 的单位换算、硬约束检查和回显；对超出行业区间的值只给出风险提示，不擅自拒绝或改值。
6. 展示包含全部六项及其来源的最终摘要，取得用户批准后调用 `delivery_confirm_assumptions`，检查 `automatic_recalculation=true`。
7. 只读取确认工具返回的最终 run，不再显式调用 `delivery_start`。XLSX 必须恰好 13 张附表；业务 CSV 必须恰好 13 个，lineage CSV 不计入；DOCX 中文必须可见。
8. 逐一断言最终十三表使用的六项参数与批准摘要一致；其中总投资必须为 `13500` 万元且不等于行业种子 `37763.48` 万元。
9. 检查用户确认值仍按 `controlled_assumption` 解释，没有被描述为已有依据或 `formal_evidence`。

以上验收补偿了当前 `acceptance.py` 只确认一个收入字段、XLSX 只断言 `>= 13` 的覆盖缺口，但不代表自动化测试缺陷已经修复。

### 9.2 本次文档变更检查

本次不运行 pytest，也不更新任何测试计数或基线。提交前执行 Markdown 差异检查；若文件仍未纳入 Git 跟踪，使用 `--no-index` 检查其完整内容：

```bash
git diff --check -- INTERACTIVE_FEASIBILITY_PLAN_20260808.md
git diff --no-index --check /dev/null INTERACTIVE_FEASIBILITY_PLAN_20260808.md
```

同时检查工作区变更范围。本次操作只允许触及本文件；仓库中原先存在的其他用户改动保持原样，不计入本次修订。
