---
name: lvke-backend-fastapi
description: >
  Backend implementation skill for the Lvke company FastAPI plane (hermes_cli).
  Use when adding or changing API routes, company allowlist, COMPANY_ROUTER_ALLOWLIST,
  platform 写路由, 公司版, jobs, idempotency, Idempotency-Key, workspace fence/version,
  actor, SQLite control plane, source-files, finance HTTP, report-lifecycle,
  advance, deliverable artifacts, company_server, uvicorn, or "后端/公司版 API".
  Trigger on hermes_cli/** tests for those modules.
---


# Lvke 后端（FastAPI / 公司平面）

## 架构要点

```text
company_server / app_factory
  → RouterRegistry + COMPANY_ROUTER_ALLOWLIST
  → authenticated session/token
  → domain routers (documents 含 deep-research, finance, source-files, lifecycle, jobs, …)
  → jobs worker_control + idempotency_service
  → 文件/SQLite 权威存储
```

入口：`LVKE_API_PROFILE=company` 或 `uvicorn hermes_cli.company_server:app`。  
Deep Research HTTP 挂在 **documents**（`doc_api.py`），不是独立 allowlist 名。

## 实现工作流

1. **先定平面** — 业务写接口必须能进 allowlist；禁止给 company 挂平台写路由。  
2. **认证身份** — reviewer/approver 只认 session/token actor，拒绝 body 自报身份绕过。  
3. **写路径** — 需要时：workspace_version + fence、中央幂等（tenant/actor/operation/resource/key）、统一 Job register→claim→heartbeat→finish。  
4. **错误** — 稳定 error code；不向客户端泄漏内部 traceback / 密钥。  
5. **测试** — 域测放在 `tests/` 或 `tests/hermes_cli/`；能进 `verify_backend_requirements.py` 的 PHASES 则挂上。  
6. **文档** — 改契约时同步 `docs/` 现行入口（见 `docs/README.md`），勿只改历史方案。

## 关键文件

| 关注点 | 路径 |
|---|---|
| 公司入口 | `hermes_cli/company_server.py`, `app_factory.py` |
| 路由表 | `hermes_cli/router_registry.py` |
| 财务 HTTP | `hermes_cli/finance_contract_api.py`, `doc_api.py`（finance-runs） |
| 资料 | `hermes_cli/source_files_api.py`, `source_security.py` |
| 生命周期 | `hermes_cli/report_lifecycle.py`, `report_lifecycle_api.py` |
| Job | `hermes_cli/jobs/` |
| 幂等 | `hermes_cli/idempotency_service.py` |
| 工件 | `hermes_cli/deliverable_artifact_api.py` |

## lifecycle.advance 约束

- 只推进 **automatic** 阶段；人工门抛 `HUMAN_GATE_REQUIRED`。  
- artifact 需调用方证明正式工件状态与绑定关系。
- 状态以 `inspect` 从权威存储重建为准。

## 财务 API 约束

- 正式算数：`POST .../finance-runs` + `run_workspace_finance_model(allow_prepare_llm=False)`。  
- GET finance-model 默认不重算。  
- 批准：`POST .../approvals`；旧 `/approve` 仅为兼容 shim。  
- 详见 skill `lvke-finance-dual-track`。

## 验证

```bash
uv run python scripts/verify_backend_requirements.py --profile smoke
uv run pytest -q tests/hermes_cli/test_app_factory.py tests/hermes_cli/test_idempotency_service.py
uv run ruff check hermes_cli/finance hermes_cli/research_engine --select F,PLW1514
```

## 反模式

- 为方便测试关闭认证 actor / 幂等 / formal gate
- 在 company 应用挂载 config/env/models 等平台写路由  
- 用 LLM 结果直接写入 13 表单元格  
- 把 `output/` 或金标二进制提交进 git  
