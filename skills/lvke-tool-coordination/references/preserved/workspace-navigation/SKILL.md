---
name: meta-workspace-navigation
description: 工作区文档工具的标准操作流程：11 个 doc_*/issue_*/finance/context/lock_heartbeat 工具的使用时机与组合方式。
platforms: [linux, windows, macos]
metadata:
  conditions:
    tools_any: [doc_read, doc_propose, issue_list]
---

# 工作区导航元能力

绿科可研工作台暴露 **11 个文档工具**。这些工具组合起来能完成全套审查与改稿。**优先用这些工具，不要用 `web_search` / `read_file` / `terminal grep` 等通用工具去找工作区内的报告内容**。

## 11 个工具速查

| 工具 | 用途 | 何时用 |
|---|---|---|
| `doc_read` | 读报告正文 | 起手第一动作，了解全文或某章节 |
| `doc_review` | 审查报告 / 写入审查意见 | 不传 issues 时 = 拉取审查上下文；传 issues = 写入 issue_center |
| `doc_propose` | 创建一份完整提案修订 | 准备落版本前必须先提案 |
| `doc_apply` | 应用提案落版本（含一致性闸门） | 用户确认后才应用 |
| `doc_reject` | 拒绝提案 | 提案审查发现问题后撤回 |
| `doc_diff` | 看提案 vs 当前版本的差异 | 应用前必须先 diff 复核 |
| `issue_list` | 列出问题中心的所有 issue | 跟踪 / 复核审查意见 |
| `issue_update` | 更新单条 issue 状态 | 标记 in_progress / resolved / ignored |
| `finance_view` | 读财务摘要（只读） | 与正文做数字一致性 cross-check |
| `context_view` | 读工作区全局上下文（章节列表 / issue 摘要 / 财务） | 任何任务开始的"侦察"动作 |
| `lock_heartbeat` | 续期当前会话的写锁 | 长任务中（>10 分钟）每隔几分钟续一次 |

## 起手三动作（任何任务都先做）

```
1. context_view             # 拿章节列表 + issue 数 + 财务摘要 → 总体了解
2. issue_list status=open   # 看是否已有未处理审查意见
3. doc_read range=""        # 或具体章节如 range="1"
```

这三个动作在任何任务开始时都几乎是无脑必做的，单独跑成本很低。**先侦察再决策**。

## 常见任务的工具流

### A. 审查全文找问题

```
context_view
doc_read range=""                         # 全文
[加载相关 doc-review-* skill]
doc_review review_type="X" issues=[...]   # 写入 issue
```

### B. 审查单章

```
context_view                              # 拿章节列表
doc_read range="3"                        # 第 3 章正文
[加载 doc-review-* skill]
doc_review review_type="X" issues=[...]   # 写入 issue
```

### C. 改稿（重大修改）

```
context_view
doc_read range=""
[与用户确认改稿方向]
doc_propose summary="..." content="<完整新版正文>"
doc_diff proposal_id=...                  # 复核
[与用户确认 diff]
doc_apply proposal_id=...                 # 落版本（含闸门）
```

### D. 跟踪 issue

```
issue_list status="open"
[逐条处理]
issue_update issue_id=... status="in_progress"
[完成]
issue_update issue_id=... status="resolved"
```

### E. 数字一致性核查

```
context_view
finance_view                              # 拿财务摘要
doc_read range="5"                        # 第 5 章投资估算
doc_read range="6"                        # 第 6 章财务分析
[比对]
doc_review review_type="consistency" issues=[...]
```

## 工具组合规则

### 必须组合的 ↔️ 关系

- `doc_propose` ↔ `doc_diff` ↔ `doc_apply`：三者顺序固定
- `doc_review` issues 写入 ↔ `issue_update` 状态流转：审查后必跟踪

### 不应单调用的工具

- `doc_apply`：不许跳过 doc_diff 直接 apply（除非用户明确说"无须复核"）
- `doc_reject`：必须有 reason，不要空 reject

### 互斥的工具

- 不要 `doc_propose` 同时改 9 章 → 拆成 3-4 次提案
- 不要在写 issue 时同时 `doc_propose` → 先审查写 issue → 再改稿提案

## 何时用通用工具（而不是 doc_*）

| 场景 | 用 |
|---|---|
| 报告里引用的外部政策 / 数据 | `web_search` / `mcp-policy-search` |
| 报告里提到的财务公式 | `terminal` 跑 Python 计算 / `mcp-finance-calc` |
| 用户提供的辅助文件（如甲方给的 Excel） | `read_file` |
| 工作区内的报告本身 | **永远** `doc_read`，不是 `read_file` |

## 长任务保活

如果一次任务跑超过 **5 分钟**：

```
# 每 3-5 分钟调一次
lock_heartbeat
```

否则其他用户的「接管」请求会让本次会话写入失败。

## 边界

- `doc_*` 工具默认作用域是当前 SSE chat 的 workspace_id（已通过 `HERMES_WORKSPACE_ID` 环境变量绑定）。**所有 doc_* 调用都应显式传 `doc_id`** —— 显式优于隐式
- 不要尝试用 `terminal` 去 `cat` / `grep` 工作区文件 —— 不是 path 暴露问题，而是绕过了版本链
- 不要在 `chat` 中同时操作两个 workspace —— 会破坏锁语义

## 配套 skill

加载完本 skill 后，根据任务再加载：

- 审查 → `doc-review-*`
- 起草 → `report-drafting-*`
- 财务 → `financial-modeling-*`
- 异常处理 → `meta-error-recovery`
- 提案应用 → `meta-propose-apply-flow`
