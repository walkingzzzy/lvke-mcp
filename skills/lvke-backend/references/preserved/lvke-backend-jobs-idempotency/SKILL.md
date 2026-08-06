---
name: lvke-backend-jobs-idempotency
description: >
  Unified JobService and central idempotency rules for Lvke async backends.
  Use when changing hermes_cli/jobs, worker_control, prepare_request_dispatch,
  Idempotency-Key, job retry/cancel/SSE, lease/fence, or domain adapters for
  source parse, finance-runs, deep-research, report artifacts. Trigger on
  异步任务, 幂等, 冲突, lease, claim, 重试.
---

# Jobs 与幂等（后端深潜）

## 权威路径

| 组件 | 路径 |
|---|---|
| Job HTTP | `hermes_cli/jobs/api.py` — list/get/cancel/retry/SSE |
| Store | `hermes_cli/jobs/store.py` |
| 派发/租约 | `hermes_cli/jobs/worker_control.py` |
| 中央幂等 | `hermes_cli/idempotency_service.py` |
| 工作区锁 | `hermes_cli/workspace_concurrency.py`（lease 秒数等） |

## 正确异步写路径（低自由度）

```text
1. 校验认证 actor + 权限
2. 解析 Idempotency-Key（写操作）
3. prepare_request_dispatch / register_domain_job
   → 统一 job register + claim（fence_token）
4. WorkerExecutionLease：双租约心跳（unified job + workspace）
5. 执行域逻辑（仅 lease 有效时）
6. commit_workspace（如需）+ finish_unified(outcome)
7. 异常：稳定 error code；不泄漏内部异常原文
```

`prepare_request_dispatch`：**在 worker 启动前**完成 register+claim。  
`execute_controlled_worker`：仅在 lease 当前时运行；退出时 release session。

## 幂等语义

- Key 维度：tenant / actor / operation / resource / key（以 `idempotency_service` 为准）。  
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

- `tests/hermes_cli/test_unified_jobs.py`
- `tests/hermes_cli/test_job_domain_integration.py`
- `tests/hermes_cli/test_generic_finance_run_jobs.py`
- `tests/hermes_cli/test_idempotency_service.py`

```bash
uv run pytest -q tests/hermes_cli/test_unified_jobs.py \
  tests/hermes_cli/test_idempotency_service.py \
  tests/hermes_cli/test_job_domain_integration.py
```

## 反模式

- worker 先跑后 register  
- 无 fence 信任「内存里还是我的 job」  
- 用 200 空 body 表示失败  
- 为方便测试关闭幂等  
- 在 company 平面旁路统一 job 私跑域逻辑  

与 `lvke-backend-fastapi`、`lvke-api-contract-change` 联用。
