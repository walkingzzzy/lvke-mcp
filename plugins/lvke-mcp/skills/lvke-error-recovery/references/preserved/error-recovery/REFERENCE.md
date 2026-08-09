---
name: meta-error-recovery
description: 工作区被锁 / LLM 网关失败 / 提案冲突 / 财务一致性失败 等异常情况的 SOP，避免反复重试同一失败。
platforms: [linux, windows, macos]
---

# 异常恢复 SOP

agent 在工作过程中会遇到各种"工具调用失败"。**反复重试同一个失败 = 浪费时间 + 烦扰用户**。本 skill 给出每类失败的诊断 → 应对 → 何时放弃寻求人工干预 的清晰路径。

## 何时使用

任何工具调用返回 error 时，先看是否匹配本 skill 列出的 8 类。匹配 → 按 SOP 处理；不匹配 → 报告给用户并等指示。

## 8 类异常 + SOP

### 1️⃣ 工作区被锁（readonly conflict）

错误特征：
```
{"code": "workspace_locked", "message": "工作区正由 XX 占用"}
```

或 SSE 帧：`{"type": "error", "readonly": true, "message": "..."}`

SOP：
1. **不重试**：锁是会话级，重试也不会变
2. **告诉用户**：「工作区当前由 [谁] 编辑，本会话只读」
3. **提供选项**：
   - 「等对方完成」（推荐）
   - 「联系管理员接管」（admin 才能做）
   - 「切换到只读分析」（不写正文，只 doc_review 写 issue）

### 2️⃣ LLM 网关未配置

错误特征：
```
LLM 网关未配置（需 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL）。
```

通常出现在 doc_agent_api → run_workspace_agent_turn 阶段。

SOP：
1. **不重试**：环境变量没设置不会自己出现
2. 告诉用户：「LLM 网关未配置，请联系系统管理员设置 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL 环境变量后重启 hermes dashboard」
3. **降级**：仍可做静态审查（基于 prompt 内的知识 + skill），但不能 stream 给 agent

### 3️⃣ 提案 base_revision 过时

错误特征：
```
{"code": "stale_base", "message": "base_revision_id 不再是当前版本"}
```

SOP：
1. doc_read 拉最新版
2. 把本次改动重新合并到最新版
3. 重新 doc_propose（旧 proposal 自动作废，不需要 reject）
4. 重新 doc_diff
5. 重新 doc_apply

**告诉用户**：「检测到工作区在我们改稿期间被他人更新，已基于最新版重新生成提案 prop_yyy，请重新复核」

### 4️⃣ 结构闸门失败（缺章节）

错误特征：
```
{"code": "structure_invalid", "message": "缺章节 3 / 章节顺序错乱"}
```

SOP：
1. 检查 content 是否完整带了所有 9 章
2. 检查标题层级（`## 1. XXX`，不是 `### 1. XXX` 不是 `# 1. XXX`）
3. 检查标题编号顺序
4. 修正后重新 doc_propose

不要直接 reject —— 这是技术错误，不是内容错误。

### 5️⃣ 财务一致性闸门失败

错误特征：
```
{"code": "finance_marker_missing", "message": "required_markers 中 'XXX' 未在正文找到"}
```

SOP：
1. finance_view 看当前 required_markers 列表
2. doc_read 看正文是否真的缺这些关键词
3. 三种情况：
   - **正文确实没**：补充正文（如确实漏了 IRR 段）→ 重新 propose
   - **正文有但措辞不同**：调整正文用 markers 中的标准词
   - **markers 不合理**（如要求"折现率"但项目没用折现率）：与用户确认是否调整 finance.required_markers

### 6️⃣ doc_propose 内容为空 / 格式异常

错误特征：
```
{"code": "invalid_content", "message": "content 不能为空 / content 不是字符串"}
```

SOP：
1. 检查 content 参数是否漏传或传成 `None` / `null`
2. content 必须是 markdown 字符串
3. 重新调用

### 7️⃣ tool 调用反复超时

错误特征：连续 2 次以上同一工具 timeout / 5xx error。

SOP：
1. **第 2 次失败时停下**，不要尝试第 3 次
2. 告诉用户：「连续 N 次 [工具名] 调用失败，可能后端服务不稳定，建议稍后重试」
3. **降级方案**：
   - 如失败的是 doc_read → 用 context_view 拿摘要
   - 如失败的是 finance_view → 让用户提供财务数据手写到 prompt
   - 如失败的是 MCP server → 提示用户检查 MCP 配置

### 8️⃣ issue_update 找不到 issue

错误特征：
```
{"code": "issue_not_found", "message": "issue_id 不存在"}
```

SOP：
1. 不要凭印象 issue_update，issue_id 可能写错
2. **先 issue_list** 拿当前 open / in_progress 的 issue 真实 id
3. 用真实 id 重新 update

## 通用恢复原则

### 三重防线

1. **诊断**：先看 error code / message，分类错误
2. **应对**：本 skill 有 SOP 的按 SOP 走；没的话告诉用户
3. **不重试同一错误**：除非问题本身有时间敏感性（如锁过期），否则同一失败重试只会浪费 token

### 不放弃 vs 不傻等

| 情况 | 处理 |
|---|---|
| 可恢复（如 stale_base） | agent 自己修复，告诉用户结果 |
| 需用户决策（如选择接管 / 改 marker） | 停下问用户 |
| 后端故障（如 5xx） | 第 2 次失败就停，建议稍后重试 |
| 不应自动处理（如 admin 操作） | 不重试，让用户联系管理员 |

### 上下文丢失

如果 chat turn 中间被中断（超时 / 用户取消 / 工具失败重启），**先恢复上下文**：

```
context_view                    # 拿当前章节列表 + issue 数
issue_list status="open"        # 看是否之前已写过 issue
[与用户确认从哪步继续]
```

不要假设上下文还在 —— hubei-lvke 单 turn agent 每次都是新 session。

## 工具与 skill 联动

- `meta-workspace-navigation`：知道有哪些工具
- `meta-propose-apply-flow`：知道提案闸门细节
- `meta-error-recovery`（本 skill）：知道失败怎么救

三者构成 meta 元能力的完整闭环。

## 边界

- **不假装恢复成功**：如果工具真的失败 → 报告给用户，不要"按记忆继续"
- **不无限重试**：同一工具同一参数最多 2 次
- **不绕开闸门**：闸门失败 = 业务规则告诉你「这样做不对」，不要换一种方式绕过去
