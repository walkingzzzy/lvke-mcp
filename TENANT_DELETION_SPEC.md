# tenant 存储维度删除说明（TENANT_DELETION_SPEC）

> 状态：已完成（2026-08-03 交付并验收通过）
> 分支：`codex/mcp-remediation-20260729`
> 范围：仅 `mcp_servers/` 目录内的代码；**不碰 `hermes_cli/` 的任何代码**。

---

## 1. 背景与决策

MCP 服务是一组给 AI（Codex / Claude）调用的工具服务，不是对外提供的企业系统。

Hermes 早期给 MCP 服务套上了一整套「安全审查 + 认证 + 租户隔离」的壳：

- **actor 认证**：调用方必须携带宿主身份，不符即 `ActorScopeMismatch` 拒绝；
- **安全审查门禁**：工具一进来先做安全审查，卡了整整一周、无法验收；
- **tenant 隔离**：`tenant_id` 既是认证断言，又作为存储命名空间，把数据按 `/tenants/{sha256(tenant_id)}` 目录切分。

已确定（用户决策，逐条执行，无需再确认）：

> MCP 服务 **不需要认证、不需要数据库、不需要 RBAC、不需要 tenant、不需要角色分离、不需要安全审查**。
> 它就是 MCP 服务，不是臃肿庞大的系统。

### 两个独立工程

1. **独立化**（`MCP_INDEPENDENCE_PLAN.md`）：脱离 `hermes_cli` 依赖，独立运行、独立测试。
2. **删除安全门禁 + tenant 维度**（本说明）：拆成两个阶段执行——

   - **Batch 1（已完成）**：删除 actor 认证与安全审查门禁。
   - **Batch 2（本说明）**：删除 tenant 存储维度，只保留 `workspace_id`。

---

## 2. 删除目标（Batch 2：彻底删除 tenant 存储维度）

> 用户选择「**一次性彻底删干净**」：不留惰性参数、不留 stub、不留兼容层。

### 要删除的东西

| 类别 | 具体项 | 处理 |
|---|---|---|
| 导入 | `from hermes_cli.learning_memory import DEFAULT_TENANT_ID, _normalize_tenant_id` | 删除整行 import |
| 参数 | 所有函数/工具的 `tenant_id` 形参与调用点 | 删除 |
| 归一化 | `_normalize_tenant_id(...)` / `normalized_tenant` | 删除 |
| 哈希 | `tenant_scope_hash(...)`（及各服务本地复刻的 `_tenant_scope_hash`） | 删除 |
| 目录层 | `/tenants/{sha256}` 路径分区 | 删除，直接落在工作区根下 |
| 隔离 | `review_visible_to_tenant(...)`、`_owner_tenant_id_from_args(...)`、`_tenant_scoped_operation(...)` 等可见性隔离 | 删除，改为直接操作/直接可见 |
| 检查 | `record.get("tenant_scope_hash") != expected_scope`、`tenant_scope_mismatch`、`tenant_id != DEFAULT_TENANT_ID` 分支 | 删除 |
| 输出 | 返回结构里的 `tenant_scope_hash` / `tenant` 字段 | 删除 |
| Schema | 工具入参里的 `tenant_id` 属性、`_TENANT` 定义 | 删除 |

### 保留

- `workspace_id` —— 唯一的存储命名空间维度；
- 存储路径基座 `workspace_root(workspace_id)`（来自 `hermes_cli.keyui_workspace`）—— 属于**独立化**工程，不在本批次删除。

### 向后兼容说明

默认 `tenant="local"` 时，`/tenants/{hash}` 分区原本就是**空操作**（目录层被忽略，数据直接落在工作区根）。因此删除 tenant 目录层后，存量 `local` 数据路径不变，已落盘数据不需要迁移。

---

## 3. 现状盘点（2026-08-03）

### 3.1 已完成

- **Batch 1（安全门禁）**：已删 `actor_scope.py`、`tenant_scope.py`；`official_server.py` 不再注入/校验 actor、tenant；11 个正式 server 全部通过 import 验证 + 裸调用穿透测试。剩余 actor 残留为 0。
- **Batch 2 地基**：`_common/artifact_store.py` 已重写为无 tenant 版——删除 `tenant_scope_hash()`、`/tenants/{hash}` 目录层、所有 `tenant_id` 参数与 scope 校验，存储只按 `workspace_id` 分。
- **Batch 2（tenant 删除，2026-08-03 完成）**：31 个文件完成删除；`mcp_servers/` 内 `grep -ri tenant` 归零；11 个正式 server 全量 import + 裸调用冒烟 + 存储路径断言全部通过（见 §6 验收结果）。

### 3.2 残留规模（已清零）

- 含 `tenant` 的文件：**0 个**（原 32 个）
- `tenant` 出现总次数：**0 次**（原 1415 次）
- 从 `hermes_cli.learning_memory` 导入 `DEFAULT_TENANT_ID` / `_normalize_tenant_id` 的文件：**0 个**（原 13 个）
- 引用 `tenant_scope_hash` / `_tenant_scope_hash` 的文件：**0 个**（原 8 个）
- 唯一保留：`lvke_knowledge_governance/service.py:13` 的 `LearningMemoryError` 功能性 import（记忆采纳异常类，被 `except LearningMemoryError:` 实际使用，非 tenant 引用；按 §7 属独立化工程范围）

### 3.3 文件级残留清单（已全部清零）

Batch 2 已对原清单中全部 31 个文件完成删除，逐文件 `grep -i tenant` 归零。原表格不再保留。删除收尾还清理了 33 处删参数遗留的「尾部逗号 + 空行」格式（`lvke_deliverable_review/service.py`）。

---

## 4. 统一删除规则（每个文件适用）

按顺序执行，逐条 self-check：

1. 删 import：`DEFAULT_TENANT_ID`、`_normalize_tenant_id`、`tenant_scope_hash`、本地 `_tenant_scope_hash` 定义。
2. 删函数/工具签名里的 `tenant_id` 形参。
3. 删调用点里的 `tenant_id=...` 关键字参数。
4. 删 `_normalize_tenant_id(...)`、`normalized_tenant` 中间变量。
5. 删 `if tenant_id != DEFAULT_TENANT_ID:` 之类的分支——**分支体按「本地默认分支」语义保留**（即原来 `== local` 才走的路径是唯一路径）。
6. 删 `tenant_scope_hash` 目录拼接：`root / "tenants" / ...` → `root`；`base / "tenants" / ...` → `base`。
7. 删 `record.get("tenant_scope_hash") != expected_scope` 校验，改为直接放行。
8. 删返回结构/JSON 里的 `tenant_scope_hash`、`tenant` 字段。
9. 删服务自带隔离函数（`review_visible_to_tenant`、`_owner_tenant_id_from_args`、`_tenant_scoped_operation`、`_owner_tenant_id_from_args` 等）及调用点。
10. 删 `server.py` 里 `tenant_id` 入参 schema 与 `_TENANT` 常量。
11. 自验：`.venv/bin/python -c "import <模块>"` 通过。
12. 最终：文件内 `grep -i tenant` 为 0（或仅剩无关字符串，如英文注释）。

**规则 5 关键**：`tenant` 相关分支里，`tenant_id == DEFAULT_TENANT_ID`（local）分支是唯一会走的路径，所以删除时要**保留该分支体**，不是删整块。反过来，`tenant_id != DEFAULT_TENANT_ID` 才执行的逻辑是死代码，连同条件一起删。

---

## 5. 已知断链（必须先修）

`mcp_servers/lvke_zero_material_delivery/artifact_delivery.py`

- 第 16 行：`from mcp_servers._common.artifact_store import require_safe_id, tenant_scope_hash`
  - `tenant_scope_hash` 已从 `artifact_store.py` 删除 → **ImportError，必须先修**。
  - 处理：import 改为 `require_safe_id`；第 33 行 `root / "tenants" / tenant_scope_hash(...)` → `root`；删除第 14 行 `from hermes_cli.learning_memory import DEFAULT_TENANT_ID, _normalize_tenant_id`；所有 `tenant_id` 形参/调用点按规则删除。

---

## 6. 执行方式

- 按域并行分派子代理，**文件所有权互不重叠**，避免并发写冲突。
- 每个子代理只改自己负责的文件，遵守第 4 节统一规则，改完自验 import。
- 完成后主流程统一收口验证：

### 验收标准（全部满足才算完成）

1. `grep -ri tenant --include="*.py" mcp_servers/`（排除 `__pycache__`）返回 0 个有效命中。
2. 11 个正式 server 全部 `.venv/bin/python -m <server>` 可 import / 可启动。
3. 每个 server 的关键工具裸调用（不带 `tenant_id`、不带认证）能穿透到业务逻辑，不再被任何 tenant/actor 门禁拦截。
4. 存储路径断言：任一 `workspace_id` 下数据直接落在 `workspace_root(workspace_id)`，路径中不含 `tenants`。
5. `hermes_cli.learning_memory` 的 import 在 `mcp_servers/` 内清零（与独立化工程接轨）。

### 验收结果（2026-08-03 全部通过）

1. ✅ `grep -ri tenant --include="*.py" mcp_servers/` = **0 个文件命中**（排除 `__pycache__`）。
2. ✅ 11 个正式 server 全部 import 通过（含 `excel_bridge`、`zero_material_delivery` 共 13 个模块全部 OK）。
3. ✅ 8 个关键工具裸调用冒烟探针全部穿透到业务逻辑，无 tenant/actor 门禁拦截、**零 TypeError**；跨服务调用点签名逐一核对一致。
4. ✅ `JSONArtifactStore.put()` 实测落在 `~/.lvke/workspaces/{workspace_id}/mcp_objects/{domain}/{kind}`，路径**不含 `tenants` 段**。
5. ✅ 唯一残留为 `LearningMemoryError` 功能性 import（`lvke_knowledge_governance/service.py:13`，非 tenant 引用，独立化工程范围，见 §7）。

---

## 7. 与 MCP_INDEPENDENCE_PLAN.md 的关系

- 本说明只负责「删安全门禁 + 删 tenant 维度」，**不解决** `hermes_cli` 的独立化依赖。
- 独立化仍按 `MCP_INDEPENDENCE_PLAN.md` 进行（把纯计算层 lift-and-shift 搬移，不重写）。
- 两者互不阻塞：本说明删完 tenant 后，`hermes_cli.learning_memory` 的残留 import 会在独立化阶段清空。
