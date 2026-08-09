---
name: lvke-api-contract-change
description: >
  Checklist for changing Lvke MCP tool and Resource contracts. Use when editing
  tool names, input/output schemas, error codes, idempotency, Resources,
  finance runs, source files, deep research, reports, or migration mappings.
---


# MCP 契约变更

## 何时用本 skill

任何会改变**工具名、输入/输出字段、错误码、幂等、Resource 或异步 job 形态**的改动。

## 变更前

1. 定位服务归属和 `server.register_tool` 注册点。
2. 读取内部完整 schema、公开紧凑 schema 和稳定 Resource URI。
3. 标清兼容策略：
   - **可加字段**（客户端应忽略未知）
   - **破坏性**：字段删除/改义/错误码变更 → 需同步依赖它的 Skills、调用方与测试，必要时留 shim 期

## 检查清单

```text
- [ ] 工具注册在正确的 14 个公开 server 之一
- [ ] `workspace_id` 只作为数据命名空间，不引入身份或权限语义
- [ ] 写操作需要时使用 idempotency key；冲突返回稳定码
- [ ] 长任务返回 job/checkpoint/resume 信息
- [ ] 错误：稳定 code + message；request-id
- [ ] 测试：schema、工具注册和行为测试更新
- [ ] docs 现行入口更新（docs/README 链到的现行文档），非只写历史方案
```

## 高频契约面

| 域 | MCP server |
|---|---|
| 财务 run | `lvke-finance-model` |
| 资料 | `lvke-source-files` / `lvke-data-acquisition` |
| DR | `lvke-deep-research` |
| 报告 | `lvke-report-generation` |
| 审查 | `lvke-deliverable-review` |

## 推荐顺序

```text
1. MCP 契约 + 测试红/绿
2. 更新相关 Skills 的工具名和参数
3. 本地 stdio 与 Codex 验收
4. 更新 docs 现行说明
5. 说明是否 breaking；旧 Skill 调用影响
```

## 反模式

- 只改 server 而不更新依赖它的 Skills
- 用 200 + 空 body 表示失败
- 绕过确定性校验、证据或业务完整性门禁
- 错误信息塞 stack / 密钥
- 在 Skill 中保留已经压缩移除的旧工具名

## 验证

```bash
pytest -q tests/integration/test_mcp_compression.py \
  tests/integration/test_mcp_compression_round2.py
```
