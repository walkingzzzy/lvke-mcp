---
name: meta-precedent-driven-drafting
description: |
  撰写任意可研章节时的强制工作流：先检索绿科历史档案库找标杆案例，
  再综合套话模板与章节规范产出草稿，最后链式调用审查 skill 自检。
  跳过任一步骤视为质量回退。
platforms: [linux, macos, windows]
metadata:
  conditions:
    tools_any:
      - mcp_lvke_archive_search_archive
      - mcp_lvke_archive_find_similar_projects
---

# 案例驱动撰写法（强制工作流）

> Use this skill **whenever** the user asks you to draft / 起草 / 扩写 / 续写 / 补写
> any chapter or section of a feasibility report. This is the **mandatory** drafting
> workflow. Skipping any step counts as a quality regression — including when the
> user only asks for a single paragraph.

## 0. 适用范围与边界

**适用**：第 1-9 章正文撰写、单段补写、政策必要性论证、风险分析等。

**不适用（绕过本 skill）**：
- 纯审查任务（用 `doc-review/*` skill 即可）
- 纯财务计算（直接调 `mcp_lvke_finance_calc_*`）
- 纯检索查询（用户只问"有没有相似项目"时，直接调 `find_similar_projects` 返回结果即可，不必生成草稿）

## 1. 标准 8 步流程

### Step 1 — 抽取项目画像

从工作区上下文（`context_view` 或用户消息）抽取以下字段：

| 字段 | 示例 |
|---|---|
| `industry` | 新能源-光伏 / 房产建筑 / 教育 / 路桥工程 |
| `project_type` | 新建 / 改扩建 / 改建 |
| `region` | 湖北省 + 市/县 |
| `scale` | 50 MW / 200 床 / 10 万吨 |
| `report_type` | 可研 / 申请报告 / 资金申请 / 实施方案 |
| `chapter` | 用户要写的章节**主题/标题**（如「运营方案」「风险管控」，不要只记章号——两套结构章号语义不同） |

> 任一字段缺失时**主动询问用户一次**，不要瞎填。

### Step 2 — 找标杆（必做）

```python
mcp_lvke_archive_find_similar_projects(
    brief={
        "industry": <Step 1>,
        "type": <Step 1.project_type>,
        "region": <Step 1.region>,
        "scale": <Step 1.scale>,
        "summary": "<one-sentence project description>",
    },
    top_n=5,
)
```

返回少于 3 个时，放宽 `industry` 到大类（如 `"新能源-光伏"` → `"新能源"`）再试一次。
仍 < 2 个时，说明 corpus 覆盖薄，需告知用户"标杆不足，输出靠 LLM 通识 + 规则 skill"。

### Step 3 — 取章节原文

对 Step 2 的 top 3 案例：

```python
mcp_lvke_archive_get_chapter(report_id=<rid>, chapter=<与本章主题对应的章>)
```

> 注意：历史报告结构不一（有发改委新版 9 章、也有企业投资 14 章），**按章节主题**
> 找标杆报告里对应的那一章（如本章写「风险管控」，就找标杆里的风险/社会稳定风险章），
> 不要机械按相同章号取。

关注三类信息：
1. **论证逻辑**——必要性怎么推、规模怎么测、投资怎么估
2. **关键数字**——指标量级、单价、IRR / 投资强度
3. **政策引用文号**——是否仍现行有效（用 `mcp_lvke_policy_search_verify_active` 复核）

### Step 4 — 加载撰写规范

**按章节主题（而非章号）加载起草规范** —— 报告结构现支持两套：
- 政府投资项目（发改委新版 9 章）：概述 / 背景需求产出 / 选址与要素保障 / 建设方案 /
  运营方案 / 投融资与财务 / 影响效果分析 / 风险管控 / 结论建议
- 企业投资项目（14 章）：含环境影响 / 节能评价 / 劳动保护 / 社会稳定风险分析 /
  不确定性分析 / 社会影响分析 等法定专章

因此**不要**用 `report-drafting-chapter-<章号>` 死套章号，而是按**当前所写章节的主题**
选最相关的规范 skill（若批量起草编排已在指令里给出 `skill_view(name=...)`，直接用它）：

```text
skill_view(name="report-drafting-chapter-<主题>")   # overview/background/scheme/investment/financial/risk/... 按主题匹配
skill_view(name="industry-context-<行业类>")         # 若 industry-context 有对应 skill
```

主题匹配参考：概述/总论→overview；背景/必要性→background；需求/规模/产出→demand-scale；
选址/建设条件/建设方案/技术/工程→scheme；投资估算/资金筹措→investment；
财务/投融资/效益→financial；风险/不确定性/敏感性→risk；招标/实施进度/运营/保障→safeguard；
结论/建议→conclusion。**环境影响/节能评价/劳动保护/社会影响等无专属 skill 的章节**：
无对应 chapter skill，按通用规范 + 该章标准小节结构撰写，并确保符合相应专项法规口径。

### Step 5 — 套话段落候选

只在需要"套话"的章节段调用（典型：第 2 章必要性、第 7 章风险、第 9 章结论）：

```python
mcp_lvke_archive_get_template_paragraph(scene="<scene>", industry=<Step 1>, top_k=3)
```

`scene` 枚举：`policy-driver` / `necessity` / `market-demand` /
`risk-financial` / `risk-policy` / `conclusion` / `site-selection`。

### Step 6 — 产出草稿

综合 Step 2-5 的素材撰写本项目章节正文。**强制要求**：

1. **证据链尾注**：凡参考真实案例的论证段落，结尾标
   `[案例参考: <report_id> 第<N>章]`，方便用户回溯。
2. **财务数字不许心算**：投资额、IRR、NPV、回收期等任何数字，**必须**先调
   `mcp_lvke_finance_calc_*` 系列工具，把计算结果原样写入；不允许在脑子里估。
3. **政策必须核验**：引用政策文号前调
   `mcp_lvke_policy_search_verify_active(citation=<文号>)`；
   返回失效或不存在的文号一律不用。
4. **真实数字替换占位符**：套话段落中的 `<<企业名>>` / `<<金额>>` 占位符
   全部替换为本项目实际值，不允许残留。
5. **不允许照搬**：参考案例的数字、项目名、客户名一律改写为本项目自有，
   只能复用论证结构与表述风格。

### Step 7 — 自审三件套

依次 `skill_view` 并执行：

1. `doc-review-numerics-cross-check` —— 正文 vs 表格 vs 财务附录数字一致
2. `doc-review-citation-verification` —— 政策文号、统计来源可核验
3. `doc-review-consistency` —— 前后表述一致、口径一致

**任一项 fail**：回 Step 4 / Step 6 修正，不得交付。修正后再次跑自审。

### Step 8 — 学习沉淀

任务结束时，只在用户明确要求更新知识或 Skill 时执行持久化操作：

1. 可复用经验先写成候选，附来源和适用边界，经复核后再进入知识治理服务。
2. 反复出现的撰写模式按 `skill-creator` 流程创建或更新 Skill，并完成验证。

不得调用未注册的 `memory` 或 `skill_manage` 工具，也不得把单项目判断自动提升为长期规则。

## 2. 输出结构示例

完成一次撰写后，AI 的最后一条消息应满足：

```
## 第<N>章 · <章节名> · 草稿 v1

<正文……>

> 数据来源：mcp_lvke_finance_calc_irr / mcp_lvke_statistics_cn_query_indicator …
> 案例参考：r-xxx 第 N 章；r-yyy 第 N 章；r-zzz 第 N 章
> 自审：numerics ✓ · citation ✓ · consistency ✓

(若想进一步细化某段，告诉我具体段落，我会保留其他段不动)
```

## 3. 异常处理

| 情况 | 处理 |
|---|---|
| `lvke-archive` 工具返回 `index_unavailable` | 告诉用户：档案库索引未生成，请先运行 `python scripts/build_archive_index.py --stage all`，并继续用 LLM 通识 + 规则 skill 产出**降级版**草稿，明确标注"无标杆参考"。 |
| `find_similar_projects` 返回 0 条 | 放宽 industry 大类后再试；若仍 0 条，仅按 Step 4-6 走，跳过 Step 2-3，但要在草稿尾注里写"标杆不足" |
| 同一份案例被多个步骤连续引用 | 不要重复全文加载；用 Step 3 的章节内容缓存到本轮上下文即可 |
| 用户中途换章节 | 重新走一次 Step 4-5；Step 2 的标杆可保留 |

## 4. 与已有 skill 的关系

- 本 skill 是**入口/调度**，不替代 chapter skill；Step 4 调用它们
- `meta/propose-apply-flow` 负责"草稿如何提案进工作区"；本 skill 在 Step 6 后衔接它
- `meta/error-recovery` 处理工具失败；本 skill 不重复定义

## 5. 反例（不要做）

❌ 用户："帮我写第 6 章财务" → 直接开始写正文
   - 错过 Step 2 标杆检索，IRR / 投资强度容易偏离行业 baseline
❌ 写完后只跑了 numerics 自审 → 跳过 citation / consistency
❌ 引用了 `[国发〔2018〕XX 号]` 但没调 verify_active → 可能已失效
❌ 套话段落里 `<<企业名>>` 残留没替换 → 评审专家一票否决
