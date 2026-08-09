---
name: lvke-backend-jobs-idempotency
description: >
  Job and idempotency rules for the local Lvke MCP runtime. Use when changing
  runtime/jobs.py, parse jobs, retries, checkpoints, idempotency keys, leases,
  conflicts, or recovery behavior.
---

# Jobs 与幂等（后端深潜）

## 权威路径

| 组件 | 路径 |
|---|---|
| 通用 Job | `src/lvke_mcp/runtime/jobs.py` |
| 资料解析 Job | `src/lvke_mcp/servers/lvke_source_files/_service/parse_jobs.py` |
| 领域幂等 | 各正式 server 的 mutation/service 层 |
| 持久化与锁 | `src/lvke_mcp/runtime/storage.py` 及领域 store |

## 正确异步写路径（低自由度）

```text
1. 校验工具输入和 `idempotency_key`
2. 计算稳定 input hash
3. 相同 key + 相同输入时重放结果
4. 相同 key + 不同输入时返回 `idempotency_conflict`
5. 长任务固化 job/checkpoint 状态、进度与恢复信息
6. 仅当前有效执行者提交结果
7. 异常返回稳定 error code 和可操作 next action
```

## 幂等语义

- Key 维度：workspace / operation / resource / key。
- **相同 key + 相同请求体** → 重放已有结果。
- **相同 key + 不同请求体** → 冲突（`IdempotencyError`），不得静默覆盖。
- Job retry：`retry_job` 也应带 Idempotency-Key 并走 recover 路径（见 `jobs/api.py`）。

## 冲突与恢复场景清单

```text
- [ ] 双 worker 抢同一 job：仅 claim 持有者可写
- [ ] lease 过期：旧 worker 写入必须拒绝
- [ ] 进程崩溃：queued 可恢复；running 稳定失败并可重试
- [ ] 客户端重复 POST：幂等重放，不产生双 run
- [ ] 客户端改 body 重用 key：明确冲突
- [ ] SSE 断线：可 after cursor 续订，不重复副作用
```

## 测试锚点

- `tests/integration/test_mcp_delivery_chain.py`
- `tests/integration/test_deep_research_quality.py`
- `tests/integration/test_refactor_guardrails.py`

```bash
pytest -q tests/integration/test_mcp_delivery_chain.py \
  tests/integration/test_deep_research_quality.py
```

## 反模式

- worker 先跑后 register
- 无 fence 信任「内存里还是我的 job」
- 用 200 空 body 表示失败
- 为方便测试关闭幂等
- 把 workspace 当成登录身份或权限边界

与 `lvke-mcp-backend`、`lvke-api-contract-change` 联用。
