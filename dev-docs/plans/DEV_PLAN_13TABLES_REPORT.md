# 十三表与研究报告生产就绪开发方案

生成时间：2026-08-06　代码基线：`e713807`　适用仓库：`/Users/mac/Desktop/mcp_servers`

## 一、先纠正三个错误结论

在实测之前我作出过三个判断，现按实证逐条纠正，避免方案建立在错误前提上。

| 我先前的说法 | 实证结果 | 纠正 |
|---|---|---|
| 「MCP 无法产出新建项目十三表，因为 v3 只支持 2 种资产类型」 | 新建项目走 `finance_spec.v2` 全程可用：spec 创建 → 确认 → 运行，模型输出了投资/融资/逐年利润/现金流/折旧/工资/偿债/敏感性/情景**全部十三表数据** | **说法错误**。v3 是**收购专用**版本（酒店租赁/光伏），新建项目本就该走 v2，不是缺陷 |
| 「财务链走不通」 | `finance_run_model` 完整算出 IRR 6.30%、NPV −5855.43 万元、静态回收期 12.20 年、盈亏平衡点 39.63%，并跑了 25 条勾稽校验 | **说法错误**。链路是通的，卡在最后一道业务门禁 |
| 「262/262 工具验收通过」 | 单点探针只证明工具可调用，未证明业务可用 | **口径不足**。覆盖率 ≠ 生产就绪，见第五节 |

## 二、当前真实状态

### 2.1 已验证可用（有实证）

新建项目财务链在 workspace `acc20260806` 实测通过：

```
project_context_create      pctx_791c0dc1058047dea0a30a7e   ok
finance_prepare_spec (v2)   fsp_1266c1ccf6347fe63a158c1d   ok
finance_confirm_spec        fsp_8dd46f3d0976dd58d3278eea   ok（新不可变 revision）
finance_run_model           —                              blocked（见 2.2）
```

模型在 blocked 前已产出十三表全部数据结构：`income_statement`、`total_cost`、
`profit_distribution`、`wage`、`depreciation_table`、`amortization_table`、
`project_cashflow`、`capital_cashflow`、`debt_service`、`interest_during_construction`、
`working_capital`、`financial_plan`、`sensitivity` + `scenarios`。

25 条勾稽校验中 **19 条通过**，包括「资金筹措合计=总投资」「现金流表 IRR=技经指标 IRR」
「总成本=经营成本+折旧+摊销+利息」「折旧表原值×(1−残值率)/年限=折旧额」等硬校验。

### 2.2 阻断原因不是缺陷

```
blocking_issues:
  - 利息备付率 ICR>=1：第1年 ICR=-0.41<1，偿债风险（当年 EBITDA 不足以覆盖利息）
  - 利息备付率 ICR>=1：第2年 ICR=0.52<1，偿债风险
```

这是**正确的财务风险拦截**。我给的探针参数（4.2 亿投资、60% 贷款、客流爬坡首年仅 30%）
导致投产前两年 EBITDA 覆盖不了 1134 万元利息。按可研规范，ICR<1 属于融资方案不可行，
必须在报告中披露或调整方案，服务拒绝固化 run 与十三表包是对的。

**结论：这不需要修代码，需要修参数。** 真实项目应通过延长建设期宽限期、
降低贷款比例、或提高爬坡速度使 ICR≥1。

## 三、需要修的真实缺陷

### D1（P1）数据落库位置不符合要求

**现状**：所有产出写入 `~/.lvke/workspaces/{workspace_id}/`，由
[workspace.py:13-19](src/lvke_mcp/runtime/workspace.py#L13-L19) 的 `data_root()` 决定，
读 `LVKE_MCP_DATA_DIR` 环境变量。仓库内 `lvke产出/` 目录当前只有一个
`ws-mirror_ws-mirror/report/test/a.txt` 测试残留。

**要求**：交付物落到仓库 `lvke产出/` 下。

**方案**：不改 `data_root()` 语义（控制面 sqlite、锁、缓存仍留在 `~/.lvke`，
避免把运行时状态塞进 Git），而是新增**交付物导出根**：

```python
# runtime/workspace.py 新增
def deliverable_root() -> Path:
    """正式交付物导出根目录，默认落到仓库 lvke产出/。"""
    configured = str(os.getenv("LVKE_DELIVERABLE_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[3] / "lvke产出"

def deliverable_dir(workspace_id: str, kind: str) -> Path:
    """kind ∈ {finance-tables, report, evidence, review}"""
    return deliverable_root() / str(workspace_id) / kind
```

改造点（导出类工具改写目标路径）：
- `tables_export_xlsx` / `tables_export_csv`（`domains/finance/tables_service.py`）
- `report_export_docx`（`domains/reports/`）
- `acquisition_export_tables_xlsx` / `_csv`（`domains/asset_acquisition/tables.py`）
- `review_export`（`domains/review/`）

目录约定：

```
lvke产出/{workspace_id}/
├── finance-tables/{run_id}/附表1..附表13.csv + 十三表.xlsx
├── report/{revision_id}/可研报告.docx + sections/*.md
├── evidence/{evidence_pack_id}/sources.json + locators.json
└── review/{review_id}/findings.csv + assessment.json
```

同时把 `lvke产出/ws-mirror_ws-mirror` 测试残留清掉，并在 `.gitignore` 中
排除大体积二进制（`*.xlsx`、`*.docx`）只保留 CSV 与 JSON 清单，避免仓库膨胀。
**该决定需用户确认**：是否要把 XLSX/DOCX 真正提交进 Git。

### D2（P2）`payback_period` 入参静默忽略

**现状**：[finance_calc/server.py:465-472](src/lvke_mcp/servers/finance_calc/server.py#L465-L472)
的 `input_schema` 缺 `additionalProperties: false`。传 `discount_rate=0.08`
（正确名为 `rate`）被静默丢弃，仍返回 `success:true`，但 `rate` 取默认 0，
导致动态回收期等于静态回收期而无任何提示。

**方案**：给 finance-calc 全部 7 个工具的 input_schema 加
`"additionalProperties": false`。经 D5 修复后，未知字段会被 transport 层
以 `invalid_argument` 拒绝。

### D3（P2）行业分类口径跨服务不统一

**现状**：`search_archive(industry="文化旅游")` 返回 0；档案库与研报库实际存
`industry="文旅"`。而 `ProjectContext.industry_code` 用「文化旅游」。
`industry_research` 库内取值为：能源/电力系统/汽车制造/化工新材料/农业/物流/
电子信息/装备制造/生物医药/**文旅**/建材/纺织服装/水利/新能源。

**方案**：新增 `domains/taxonomy/industry_alias.py`，建立规范名 ↔ 别名映射
（`文化旅游` ↔ `文旅` ↔ `tourism_catering` ↔ `cultural_tourism`），
在 `lvke_archive.search_archive`、`industry_research.search_report`、
`planning_resolve_industry_skill`、`planning_get_industry_constraints`
四处入口做归一。**归一必须可见**：响应里回显
`{"industry_requested": "文化旅游", "industry_matched": "文旅", "alias_applied": true}`，
不做静默替换。

### D4（P2）`planning_get_industry_constraints` 缺文旅行业

**现状**：`supported_industries` 仅 6 项（仓储物流/农业/制造/化工/机械/电子），
无文化旅游，尽管 `asset_type` 支持 `amusement_park` 且仓库有 8 个文旅 skill。

**方案**：在行业约束配置中补文化旅游参数组（用地强度、容积率区间、
停车配比、游客高峰系数、消防疏散、大型游乐设施安全规范引用），
数据来源标注为 `evidence_grade: C`（行业习惯默认），不冒充规范强制值。

### D5（P2）`delivery_create_from_sentence` 未提取 region

**现状**：句子含「湖北省咸宁市崇阳县」，`industry` 成功匹配
`tourism_catering`（confidence 0.66），但 `region` 返回空字符串，
且 `project_name` 直接把整句原文当项目名。

**方案**：接入已存在的 `domains/geo/administrative_names.py` 做省市县三级匹配；
项目名从句中剥离投资额与面积等数量短语。

## 四、开发任务分解

### 阶段 1：交付物落库（D1）

| # | 任务 | 交付 | 验收 |
|---|---|---|---|
| 1.1 | `workspace.py` 新增 `deliverable_root()` / `deliverable_dir()` | 代码 + 单测 | 未设环境变量时解析到仓库 `lvke产出/` |
| 1.2 | 改造 4 个导出工具写入路径 | 代码 | 导出后文件真实出现在 `lvke产出/{ws}/{kind}/` |
| 1.3 | 清理 `ws-mirror_ws-mirror` 残留，配 `.gitignore` | 目录 + 配置 | `git status` 干净，无大二进制入库 |
| 1.4 | 导出响应回显落库绝对路径 | 代码 | 响应含 `deliverable_path` 字段 |

### 阶段 2：契约修复（D2/D3/D5）

| # | 任务 | 交付 | 验收 |
|---|---|---|---|
| 2.1 | finance-calc 7 工具加 `additionalProperties:false` | 代码 | 传 `discount_rate` 返回 `invalid_argument` 而非静默忽略 |
| 2.2 | 新增 `industry_alias.py` 并在 4 处入口归一 | 代码 + 单测 | `search_archive(industry="文化旅游")` 命中「文旅」记录且回显 `alias_applied` |
| 2.3 | `delivery_create_from_sentence` 接地理词典 | 代码 | 一句话能解析出 `region.province/city/district` |

### 阶段 3：行业能力补齐（D4）

| # | 任务 | 交付 | 验收 |
|---|---|---|---|
| 3.1 | 补文化旅游行业约束参数组 | 配置 + 出处标注 | `planning_get_industry_constraints(industry_code="文化旅游")` 返回参数且标 `evidence_grade` |
| 3.2 | 补基础设施/社会事业等常见新建行业 | 配置 | `supported_industries` 覆盖仓库 skill 已声明的行业 |

### 阶段 4：端到端可行方案（真正回答「能不能出十三表和报告」）

这一阶段不改代码，是用**财务上自洽的参数**跑完整链路，产出可审阅的成品。

| # | 任务 | 关键动作 | 验收 |
|---|---|---|---|
| 4.1 | 设计 ICR≥1 的融资方案 | 降贷款比例至 40%、建设期后加 2 年宽限期、爬坡首年提至 45% | `finance_run_model` 返回 `ok` 且 `run_id` 非空 |
| 4.2 | 渲染并导出十三表 | `tables_render` → `tables_export_xlsx` / `_csv` | `lvke产出/` 下出现 13 个 CSV + 1 个 XLSX，勾稽全绿 |
| 4.3 | 生成研究报告 | 联网搜索 → EvidencePack → `dr_submit` → `report_prepare` → `report_propose`/`diff`/`apply` → `report_export_docx` | 产出九章 DOCX，财务数字全部来自 `run_id`，不出现手填数 |
| 4.4 | 交付审查 | `review_prepare` → `review_start` → `review_list_findings` → `review_retest` | 7 维度评分 ≥ `pass_score`，findings 全部 disposition |

### 阶段 5：验收方式改造（补上第五节暴露的方法论缺口）

| # | 任务 | 交付 | 验收 |
|---|---|---|---|
| 5.1 | 写链式验收 harness（对象链顺序 + ID 传递） | `tests/acceptance/chain_harness.py` | 19 个「需前置对象」工具能真正执行业务而非停在门禁 |
| 5.2 | 每工具补边界与错误场景 | 测试用例 | 场景数 ≥ 3/工具（正常/边界/错误） |
| 5.3 | 把阶段 4 固化为回归金标 | 金标清单 + 期望结果 | 重跑可复现同一 `run_id` 与十三表 hash |

## 五、验收口径的自我修正

本轮 262/262 覆盖是**可调用性验收**，不是生产就绪验收。两者差别：

| | 可调用性验收（已完成） | 生产就绪验收（待做） |
|---|---|---|
| 参数 | 单点探针，字段名靠猜 | 真实业务参数，符合财务规范 |
| 对象 | 探针 ID，多数停在 not_found | 真实对象链，逐级传递 |
| 判据 | `transport_success=true` | 产出可交付工件且勾稽全绿 |
| 结果 | 262/262 覆盖、92.7% 到达 handler | 十三表 + 九章报告 + 审查通过 |

**因此对「能否产出十三表和研究报告」的准确回答是**：

- 财务模型**具备**产出十三表全部数据的能力（已实证算出 13 张表的数据结构）
- 但在**当前参数下**被 ICR 门禁正确拦截，未固化 run 与表包
- 报告链（`report_prepare` → `propose` → `apply` → `export_docx`）**尚未跑通过一次**，
  因为它依赖 `run_id`，而 `run_id` 卡在上一步
- 所以：**不能说"已经能"，也不能说"不能"**。需要阶段 4 用自洽参数跑通后才有资格下结论

## 六、优先级与顺序

```
阶段 4.1（调参跑通 run_model）        ← 最高优先，决定能否下结论
  ↓
阶段 1（交付物落库 lvke产出/）        ← 用户明确要求
  ↓
阶段 4.2/4.3（十三表 + 报告导出）
  ↓
阶段 2（契约修复）                    ← 不阻断主链
  ↓
阶段 3（行业补齐）+ 阶段 5（验收改造）
```

## 七、需用户确认的三项

1. **XLSX/DOCX 是否入 Git**：`lvke产出/` 在仓库内，十三表 XLSX 与报告 DOCX
   是二进制大文件。建议只提交 CSV + JSON 清单，二进制加 `.gitignore`。
2. **控制面数据是否也迁移**：当前 sqlite、workspace 锁、解析缓存在 `~/.lvke`。
   建议保留原处（运行时状态不宜入库），只迁交付物。若要求全迁请明确。
3. **ICR 门禁是否可配**：当前 ICR<1 硬阻断。真实项目有时以「首两年由股东借款
   补足偿债」通过评审。是否需要支持显式声明该安排后放行（并在报告中强制披露）。

## 八、禁区（沿用既有约定）

不新增权限/认证/授权/安全门禁；不删除或覆盖已有不可变业务对象；
不新增联网搜索 MCP；凭据不写入仓库、日志或测试报告；
保留现有 error 对象与 partial 工件作为历史记录，通过版本与资格校验使其失效；
修复后仅创建本地 commit，不 push。
