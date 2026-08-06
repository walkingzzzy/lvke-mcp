---
name: lvke-api-contract-change
description: >
  Checklist for changing Lvke HTTP/API contracts across backend and frontend.
  Use when editing routes, OpenAPI shapes, error codes, Idempotency-Key behavior,
  finance-runs, approvals, source-files, deep-research, report-lifecycle,
  deliverable-artifacts; or when the user mentions 接口变更, 契约, 前后端联调,
  breaking change, 响应字段. Use together with backend/frontend skills on dual-sided edits.
---


# API 契约变更（前后端同步）

## 何时用本 skill

任何会改变**路径、方法、请求/响应字段、错误码、鉴权、幂等、异步 job 形态**的改动。

## 变更前

1. 定位路由归属：是否在 `COMPANY_ROUTER_ALLOWLIST`（`hermes_cli/router_registry.py`）。  
2. 确认平面：company 禁止顺手挂平台写路由。  
3. 标清兼容策略：  
   - **可加字段**（客户端应忽略未知）  
   - **破坏性**：字段删除/改义/错误码变更 → 需同步前端与测试，必要时留 shim 期  

## 后端检查清单

```text
- [ ] 路径挂在正确 router；company allowlist 已包含
- [ ] 认证：actor 来自 session/token，不信任 body 自报身份
- [ ] 写操作：认证 actor、tenant/workspace 边界、业务完整性门禁
- [ ] 需要时：Idempotency-Key + 中央幂等；冲突返回稳定码
- [ ] 长任务：统一 jobs（202 + job_id + SSE/轮询），不暴露内部异常原文
- [ ] 错误：稳定 code + message；request-id
- [ ] 测试：契约测 / hermes_cli 测更新
- [ ] docs 现行入口更新（docs/README 链到的现行文档），非只写历史方案
```

## 前端检查清单

```text
- [ ] web/src/lib/keyui-api.ts 与 features/*/api.ts 同步
- [ ] 类型与默认值；勿 any 吞协议
- [ ] 高信任动作 UI：按后端状态/人工门禁禁用、隐藏或说明
- [ ] 异步：job 状态机与失败态
- [ ] 双轨/正式态文案不被破坏（参考 vs 修正 vs 批准）
- [ ] npm --workspace web run typecheck && test
```

## 高频契约面

| 域 | 后端 | 前端 |
|---|---|---|
| 财务 run | `doc_api` finance-runs；`finance_contract_api` | keyui-api + finance-v3 |
| 收购批准 | `.../approvals`, reference-reviews | FinanceV3Workbench |
| 资料 | `source_files_api` | features/sources |
| DR | `doc_api` `/deep-research/*` | DeepResearch 页 |
| 生命周期 | `report_lifecycle_api` | 若已接则同步 |
| 工件/签字 | deliverable / report-artifacts signoff | publish 相关页 |

## 推荐顺序

```text
1. 后端契约 + 测试红/绿
2. 更新前端客户端与 UI
3. V1/V3 本地验收（lvke-local-verify）
4. 更新 docs 现行说明
5. 说明是否 breaking；旧客户端影响
```

## 反模式

- 只改后端不管 web，或只改 UI 假数据  
- 用 200 + 空 body 表示失败  
- 破坏 formal/认证/业务门禁「为了联调方便」
- 错误信息塞 stack / 密钥  
- 忘记 company allowlist 导致公司版 404  

## 验证

```bash
uv run python scripts/verify_backend_requirements.py --profile smoke
npm --workspace web run typecheck
npm --workspace web run test
```
