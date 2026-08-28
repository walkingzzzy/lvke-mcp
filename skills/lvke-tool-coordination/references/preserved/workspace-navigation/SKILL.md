---
name: meta-workspace-navigation
description: 工作区导航标准操作流程：用 lvke-report-generation / lvke-deliverable-review / lvke-finance-model 的真实工具读正文、写审查意见、提案改稿与核对数字。
platforms: [linux, windows, macos]
metadata:
  conditions:
    tools_any: [report_list_sections, report_get_section, report_propose_section]
---

# 工作区导航元能力

读工作区内的报告正文、写审查意见、改稿落版本，全部走 MCP 工具，**不要用
`read_file` / `terminal grep` 去翻工作区里的报告内容**——那样拿到的是脱离
不可变对象链的文本，没有 revision 与 basis_hash，改稿无法落版本。

> 历史说明：本文件早期版本描述的是一套 HTTP 工作台的 11 个 `doc_*` /
> `issue_*` / `context_view` / `lock_heartbeat` 工具。那套接口已不存在，
> 当前 14 个 MCP server 里没有任何同名工具，照那份指引起手会连续拿到
> `Unknown tool`。下表是同等能力的真实替代。

## 能力对照（旧名 → 现在真正可用的工具）

| 旧 `doc_*` 名 | 现在用 | 属于 |
|---|---|---|
| `context_view` | `report_list_sections` + `report_get_readiness` | lvke-report-generation |
| `doc_read` | `report_get_section`（按稳定 `section_id`，不按章号猜） | lvke-report-generation |
| `doc_propose` | `report_propose_section`（单章）/ `report_propose`（多章） | lvke-report-generation |
| `doc_diff` | `report_diff` | lvke-report-generation |
| `doc_apply` | `report_apply` | lvke-report-generation |
| `doc_reject` | 不再需要：提案未 apply 就不生效，直接改用新提案 | — |
| `doc_review` | `review_prepare` → `review_start` | lvke-deliverable-review |
| `issue_list` | `review_list_findings` | lvke-deliverable-review |
| `issue_update` | `review_disposition_finding` | lvke-deliverable-review |
| `finance_view` | `finance_get_run`（view=summary） | lvke-finance-model |
| `lock_heartbeat` | 不再需要：MCP 用幂等键而非会话写锁 | — |

## 起手动作（先侦察再决策）

```
report_list_sections(workspace_id=..., report_revision_id=...)   # 章节清单与稳定 section_id
review_list_findings(workspace_id=..., review_id=..., status="open")  # 已有未关闭问题
report_get_section(workspace_id=..., report_revision_id=..., section_id=...)  # 目标章节正文
```

`report_list_sections` 返回的 `section_id` 是稳定标识，后续读写都用它；
不要用"第 3 章"这类序号去猜，章号会随大纲调整变化。

## 常见任务的工具流

### A. 审查全文找问题

```
report_list_sections                      # 拿全部 section_id
report_get_section (逐章)                  # 读正文
review_prepare(target={target_type:"report_revision", target_id:...})
review_start(review_preparation_id=...)   # 产出 findings
review_list_findings                      # 读结论
```

### B. 单章质量评分

```
report_get_section  → 读正文
review_score_section(report_revision_id=..., section_id=...)   # 确定性评分，不调隐藏 LLM
```

### C. 改稿（提案 → 复核 → 落版本）

```
report_propose_section(section_id=..., proposed_content=..., basis={...})
report_diff(proposal_id=...)              # apply 前必须先 diff 复核
[与用户确认 diff]
report_apply(proposal_id=...)             # 落新 revision（含结构与财务一致性闸门）
```

### D. 跟踪与关闭 findings

```
review_list_findings(status="open")
review_disposition_finding(finding_id=..., disposition="remediate")
[整改并产出新 revision]
review_retest(review_id=..., target=..., remediation_evidence=[...])
review_disposition_finding(disposition="resolved", retest_review_id=...)
```

### E. 数字一致性核查

```
finance_get_run(view="summary")           # 财务口径与关键指标
tables_get_table(table_id=...)            # 需要具体表时按 table_id 读（lvke-finance-tables）
report_get_section (投资估算章、财务分析章)
[比对后写 findings]
review_start / review_list_findings
```

## 工具组合规则

### 必须成组出现

- `report_propose_section` ↔ `report_diff` ↔ `report_apply`：顺序固定，
  `report_apply` 只接受已 diff 复核过的 proposal_id。
- findings 写入 ↔ `review_disposition_finding`：审查完必须给处置，
  否则 findings 永久停在 open，正式发布会被 `review_open_finding` 阻断。

### 不应单独调用

- `report_apply`：不许跳过 `report_diff` 直接 apply（除非用户明确说无须复核）。
- `review_retest`：必须带 `remediation_evidence`，空整改证据的复测不成立。

### 互斥

- 不要一次 `report_propose` 改完 9 章 → 拆成 3-4 次提案，diff 才可复核。
- 不要在写 findings 的同一轮里提案改稿 → 先审查出问题，再针对性改稿。

## 何时用通用工具

| 场景 | 用 |
|---|---|
| 报告引用的外部政策 / 数据 | `data_discover` → `data_collect`（lvke-data-acquisition） |
| 政策是否现行有效 | `reference_verify(dataset="policy")`（lvke-reference） |
| 财务计算 | `finance_calculate`（lvke-finance-model），不要手算也不要脑估 |
| 甲方给的 Excel / PDF | `source_import_local_path`（lvke-source-files），不是 `read_file` |
| 工作区内的报告正文 | **永远** `report_get_section`，不是 `read_file` |

## 边界

- 所有工具都必须显式传 `workspace_id`，没有隐式当前工作区。
- 读写正文都绑定具体 `report_revision_id`：revision 不可变，
  改稿产出新 revision 而不是原地覆盖。
- 写类工具需要 `idempotency_key`（多数要求至少 8 字符）；同键重放返回原结果，
  换内容同键会被判 `idempotency_conflict`。
