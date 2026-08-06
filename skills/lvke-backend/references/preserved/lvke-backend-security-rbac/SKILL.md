---
name: lvke-backend-security-rbac
description: >
  Authenticated actor binding, tenant/workspace boundaries, and external
  professional signoff separation-of-duty for Lvke. Use when changing
  keyui_account, dashboard_auth, finance approvals, evidence reviews,
  professional-signoff register/verify, or tests for 假 actor / 跨租户 /
  职责分离. Never accept client-supplied reviewer identity over session.
---

# 认证身份与外签职责分离

## 当前安全边界

本地 Agent/MCP 场景不再维护角色矩阵或权限字符串。同 tenant 已认证用户同权；后端只负责：

- 登录/session/token 认证。
- tenant/workspace 边界。
- actor 真实身份绑定。
- 幂等、锁/fence、workspace version。
- 财务、证据、正式工件完整性门禁。
- 外签登记与核验不同人、hash/envelope 不可伪造。

## 认证 actor 硬规则

1. **唯一身份源**：session cookie 或带 scope 的 token。  
2. **拒绝**请求体自报 `actor` / `reviewer` / `approved_by` 与认证身份不一致。  
3. 财务/证据/发布的“复核、批准、确认”仍是业务状态，不是角色授权表。
4. 旧 `/approve`、`/reject` 仅为兼容 shim，须仍走同一认证 actor 与完整性门禁，不得旁路。

## 外签两阶段（通用可研）

路径概念：

```text
POST .../deliverable-artifacts/{id}/professional-signoff     → 登记
POST .../deliverable-artifacts/{id}/professional-signoff/verify → 核验
```

代码：`hermes_cli/deliverable_artifacts.py`（`external_professional_signoff.v1`）。

| 步骤 | 业务意图 | 约束 |
|---|---|---|
| 登记 | 人工登记外部专业签字证据 | 人工外部证据；`system_signature_performed=false`；evidence_hash **人工提供** |
| 核验 | 另一个认证 actor 核验证据 | **核验人 ≠ 登记人**；重算 envelope hash |

资产收购对称：`acquisition_service` register/verify + `/report-artifacts/...`。

`_signoff_integrity_failures`：schema、signoff_id 格式、`manual_external_evidence`、绑定字段、hash 镜像；**never infer a real signature**。

## 测试应覆盖的用例（写法要点）

```text
1. body.actor != session → 拒绝
2. 跨 tenant/workspace 写操作 → 拒绝
3. 无 Idempotency-Key 的写批准（若契约要求）→ 4xx
4. 同一人 register 又 verify → 拒绝
5. 篡改 evidence_hash / release 后 verify → integrity fail
6. system_signature_performed=true 的载荷 → 拒绝
7. formal 工件 basis 已失效仍 signoff → 拒绝
```

相关测试目录：`tests/test_professional_review.py`、`tests/test_deliverable_artifacts.py`、`tests/test_finance_acquisition_publish_release.py`、finance governance 测。

```bash
uv run pytest -q tests/test_professional_review.py \
  tests/test_deliverable_artifacts.py \
  tests/test_finance_governance_security.py
```

## 反模式

- 前端把 role 写进 localStorage 当权威  
- 测试里硬编码跳过认证 actor / tenant / 完整性门禁「先联调」
- 把 signoff API 200 写成「已专业签字」  
- 混用内部 release 与外部法律签章语义  

联用：`lvke-delivery-guardrails`、`lvke-docx-deliverable`、`lvke-acquisition-hengli`。
