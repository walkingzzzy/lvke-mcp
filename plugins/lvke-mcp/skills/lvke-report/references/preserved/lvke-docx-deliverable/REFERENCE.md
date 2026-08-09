---
name: lvke-docx-deliverable
description: >
  Formal deliverable artifacts, basis fingerprint, invalidation, and no-Pandoc
  DOCX pipeline for Lvke. Use when changing report artifacts,
  formal export, basis_matches, invalidation, or discussing 正式工件, draft vs
  formal, basis 失效, 无 Pandoc.
---

# 正式工件与 basis 失效

## 两种工件面

| 面 | 用途 | 入口（概念） |
|---|---|---|
| **通用可研** | draft-export vs formal deliverable | `deliverable_artifacts` / `deliverable_artifact_api` |
| **资产收购** | 已确认 run 绑定 formal 包 | `acquisition_service` report-artifacts |

共通原则：`BASIS_SCHEMA_VERSION = deliverable_basis.v1`；**draft ≠ formal**。

## Formal 创建硬门（`_assert_formal_basis` 等）

```text
1. doc_kind / report_type 匹配（收购勿走错通用可行性通道）
2. publish readiness 快照满足
3. quality review 没有未关闭的 P0/P1 finding，且 basis_matches=true
4. current basis fingerprint 与审查时一致
5. 财务 run 与工件绑定一致（域内 gate）
6. 失败 → fail-closed，不生成「看起来正式」的脏包
```

`_capture_basis`：聚合 source basis 快照、研究、财务、正文、审查等；异常记入 basis 问题而非静默忽略。

## 失效（invalidation）

任一依据变化（原件 revision、证据裁决、finance run/spec hash、正文 revision、审查结论）→
**旧 formal 工件失效**，不得继续当发布真源。
状态机应保留历史记录 + 当前 current formal 指针（见 deliverable 状态结构）。

## 输出边界

- `draft` 是内部复核稿，`formal_candidate` 是通过技术校验的候选工件。
- 本产品不提供身份认证、角色权限、安全签审或法律签章。
- 技术工件成功不表示甲方已验收。

## 无 Pandoc

正式 DOCX 路径应覆盖：目录、表格/重复表头、横向附表、图片题注、分节分页、页眉页脚等（以现有 doc 服务测试为准）。
**禁止**「无 Pandoc 就降级成不可用草稿却标记 formal」。

## 测试

```bash
pytest -q tests/integration/test_mcp_delivery_chain.py \
  tests/integration/test_report_finance_regressions.py
```

## 反模式

- 依据已变仍下载旧 formal 当终稿
- formal 生成失败时返回 draft 并标正式
- 跳过 basis_matches 检查
- 把 artifact API 成功写成客户已验收

联用：`lvke-delivery-guardrails`、`lvke-finance`。
