---
name: meta-propose-apply-flow
description: 改稿三步流程（提案 → 复核 diff → 应用版本）的规范操作 + 何时该停下等用户确认 + 闸门冲突处理。
platforms: [linux, windows, macos]
metadata:
  conditions:
    tools_any: [report_propose_section, report_apply, report_diff]
---

# 提案 → 复核 → 应用 三步流程

绿科可研工作台用**版本链 + 提案审查 + 多重闸门**保护正文不被随意覆写。**所有改稿动作都必须走这三步**，不许跳步。

## 三步规范

### 第一步：report_propose_section（写一份完整提案）

```
report_propose_section
  workspace_id="<工作区>"
  summary="<改动说明，一行内简短>"
  content="<完整的修订后报告 markdown 全文>"
  rationale="<改动依据，可附 issue_id>"
  basis="<政策/数据依据>"
```

**关键约定**：

- `content` 是**完整正文** —— 包含未改动的章节也要原样带上
- 不是 patch / diff 格式
- 系统会拿 `content` 与 base revision 自动 diff，所以"完整 content"是必需

**典型场景**：

- 修改 1-2 章 → 拿 report_get_section 拿全文 → 在本地拼装新版 → 提案
- 全面改稿 → 同上但 content 改动多
- 仅微调（如改一个数字） → 还是要给完整 content；版本链不接受 patch

返回：`{proposal_id, base_revision_id, structure_ok}`

`structure_ok` 是结构闸门预检查结果（9 章是否齐全），但不阻断 propose；只在 apply 时 hard block。

### 第二步：report_diff（复核差异）

```
report_diff
  workspace_id="<工作区>"
  proposal_id="<上一步返回的 id>"
```

返回 `html_diff` 字符串，里面包含 add / remove / change 标记。

**关键约定**：

- 提案后**第一动作必须是 diff**
- 把 diff 显示给用户（或者总结给用户："本次修改：第 3 章新增 200 字关于市场需求测算"）
- 等用户确认 / 用户提出修改 → 决定继续 apply 还是 reject

**不能跳过这一步**直接 apply —— 用户必须看到改了什么。

### 第三步 - A：report_apply（应用版本）

```
report_apply
  workspace_id="<工作区>"
  proposal_id="<id>"
```

会跑 4 道闸门：

1. **锁闸门**：本会话是否持有写锁
2. **状态闸门**：proposal 仍是 open
3. **新鲜度闸门**：base_revision 还是当前 current
4. **结构闸门**：报告结构章节完整（按工作区所选结构：发改委新版 9 章 / 企业投资 14 章）
5. **财务闸门**（如配置）：required_markers 全部存在

任何一道闸门失败 → 返回详细错误，**不进入版本链**，原状态保留。

成功 → 返回 `applied_revision_id`，版本链增长 1。

### 第三步 - B：report_propose_section（拒绝提案）

```
report_propose_section
  workspace_id="<工作区>"
  proposal_id="<id>"
  reason="<必填，简短说明>"
```

提案状态变为 `rejected`，不进入版本链。reason 必填，留给版本审计追溯。

## 何时该停下等用户

**永远不要**未经用户确认就 apply，**除非**：

- 用户明确说"直接应用，不要复核"（极少见）
- 是"修复 typo / 标点 / 编号"级微调（一次提案改动 < 20 字）

通常应该：

```
1. propose
2. diff
3. → 给用户报告：
   "已生成提案 proposal_id=xxx，改动：
   - 第 3 章：新增 200 字关于市场缺口测算
   - 第 5 章表 5-1：单位投资由 8500 元/吨改为 9200 元/吨
   - 摘要：更新 IRR 数字与正文一致
   要 apply 吗？"
4. → 用户回复确认 → apply
   → 用户提出修改 → propose 新一版
   → 用户撤销 → reject
```

## 闸门冲突处理

### 幂等冲突

MCP 没有"会话写锁"这一层（旧工作台的 `lock_heartbeat` 已不存在），并发保护靠
幂等键：

```
错误：idempotency_conflict
含义：同一个 idempotency_key 之前提交过，但这次的内容指纹不同
处理：
- 想重放上次结果 → 用完全相同的入参重发，会返回原结果（idempotent_replay=true）
- 想提交新内容 → 换一个新的 idempotency_key（多数写工具要求至少 8 字符）
- 不要反复重试同键同调用：那只会重复拿到同一个冲突
```

### basis 过期

```
错误：basis_hash 不匹配 / expected_basis_hash 冲突
含义：上游对象已产生新 revision，手上的 basis 不再是最新
处理：
- 重新读当前 revision 拿新 basis_hash
- 基于新 basis 重新提案；不要拿旧 basis 强行 apply
```

### 新鲜度闸门失败

```
错误：base_revision 已过时，当前版本是 rev_XXX
原因：在 propose 与 apply 之间，有别人 apply 了一个版本
处理：
1. report_get_section 拉最新版
2. 把本次改动重新合并到最新版基础上
3. report_propose_section 生成新一份提案（旧 proposal 自动作废）
4. report_diff 复核
5. 重新 apply
```

### 结构闸门失败

```
错误：缺章节 X / 章节顺序错乱
处理：
- 检查 content 是否完整带了 1-9 章标题
- 标题层级必须 `## 1. XXX` / `## 2. XXX` 这样
- 不能漏 `##` 也不能改成 `###`
```

### 财务一致性闸门失败

```
错误：required_markers 中的 "内部收益率" 在正文未找到
处理：
- 检查正文是否包含 "内部收益率" / "IRR" 这种关键词
- 注意 required_markers 是 finance.required_markers 配置的
- 如果用户改稿确实移除了 IRR 段 → 与用户确认
- 如果是误判 → 用 finance_get_run 看下当前 markers 列表
```

## 版本链使用约定

- 版本**只增不改不删**
- 每次 apply 产生 1 个新 revision
- revision_id 是 `rev_XXXXXXXX` 格式，可用于回滚或对比
- `report_diff from_version=A to_version=B` 可对比任意两个版本
- 旧版本永远可读：`report_get_section revision_id=rev_XXXXXXXX`

## 工作示例：用户要求"把第 3 章规模数字调整为 15 万吨"

```python
# 1. 侦察
report_list_sections()
report_get_section(section_id=<第3章对应的 section_id>)     # 拿第 3 章原文
report_get_section(section_id=<第5章对应的 section_id>)     # 顺便看投资估算是否会受影响
finance_get_run()          # 顺便看财务摘要

# 2. 在思考过程中：
#    - 把 10 万吨 → 15 万吨
#    - 检查需要联动的处：投资估算 / 财务测算
#    - 写完整新版（含所有章节）

# 3. 提案
report_propose_section(
    workspace_id="ws-001",
    summary="第 3 章规模从 10 万吨上调至 15 万吨；第 5 章投资联动调整",
    content="<完整新版报告>",
    rationale="客户在 2026 投委会后提出扩大规模"
)
# 返回 proposal_id=prop_abc123

# 4. 复核
report_diff(workspace_id="ws-001", proposal_id="prop_abc123")
# 拿到 html_diff

# 5. 给用户报告（不直接 apply）
"已生成提案 prop_abc123：
- 第 3 章：规模 10 → 15 万吨
- 第 5 章：投资估算 23,580 → 32,470 万元
- 摘要：相关数字同步更新
影响：项目 IRR 从 11.5% → 10.8%（下降但仍超基准）
要 apply 吗？"

# 6. 用户确认 → apply
report_apply(workspace_id="ws-001", proposal_id="prop_abc123")
```

## 与其他 skill 协同

- 配合 `meta-workspace-navigation`：工具速查表
- 配合 `meta-error-recovery`：闸门失败的恢复
- 配合 `doc-review-*`：审查 → 写 issue → 改稿 → 提案 全链路
- 配合 `financial-modeling-*`：改财务相关内容时联动检查

## 边界

- **永不** `write_file` 改正文 —— 会跳过版本链
- **永不** 在用户未确认时 apply（除非微调）
- **永不** 同一个 chat turn 内 propose 5 次以上 —— 拆任务
- 如果改稿涉及超过 3 章 → 强烈建议拆成多次提案，每次聚焦 1-2 章
