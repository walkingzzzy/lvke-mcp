# Lvke MCP Services 技术参考文档

> 基于深度代码审查的完整技术文档 | 最后更新: 2026-08-28
> 覆盖 **14 个对外服务 / 180 个工具**（2026-08-29 运行时自省实测）

## 文档说明

本文档基于实际源代码的深度审查，记录 Lvke MCP 服务的**真实实现**，而非理论设计。所有工具签名、参数、行为描述均来自代码本身。

**核实方法**：工具名单、数量、类型（只读/写/破坏性）与 `required` 参数由运行时自省获得——逐个 `import` 14 个 server 模块并读 `_tools` 与 `annotations.model_dump(by_alias=True)`，而非阅读工具的 `description` 文本。行为描述取自 handler 实际调用的 service 函数体，因为 `description` 是给 Agent 看的说明，可能与实现不符。

**本文档同时记录实现缺陷**。凡代码与自身声明不一致处（如工具标 `readOnlyHint=True` 却固化对象、门禁读取一个恒为真的字段），一律在对应服务的「已知限制」中点明，不为文档整齐而掩盖。推测性内容显式标注「未验证」。

> **一处方法学警告**：`ToolAnnotations` 是 pydantic 模型，属性名为 snake_case（`read_only_hint`）。用 `getattr(a, "readOnlyHint")` 会静默取到 `False`，把 91 个只读工具全部误标为"写"。必须用 `model_dump(by_alias=True)` 才能拿到 camelCase 字段。

---

## 目录

1. [概览与架构](#概览与架构)
2. [运行时层](#运行时层)
3. [数据层服务](#数据层服务)
4. [研究层服务](#研究层服务)
5. [规划层服务](#规划层服务)
6. [财务层服务](#财务层服务)
7. [交付层服务](#交付层服务)
8. [专项服务](#专项服务)
9. [调用流程示例](#调用流程示例)
10. [实现完整度评估](#实现完整度评估)

---

## 概览与架构

### 系统定位

Lvke MCP Services 是一套**端到端可行性研究 + 资产收购业务引擎**，为 AI Agent 提供确定性的业务工具集。核心特点：

- **不可变对象体系**：每次操作产生新 revision，保留完整历史
- **工作区隔离**：所有数据按 `workspace_id` 物理隔离，无跨租户泄漏
- **幂等键机制**：每个写操作必须提供 `idempotency_key`，重复调用返回相同结果
- **证据溯源**：每个数值绑定 `locator` + `content_hash`，明确来源可审计
- **确定性计算**：财务模型由 Python 函数保证可复现，不依赖 LLM

### 服务总览（14 个对外服务 / 180 个工具）

对外注册面固定为 **14 个 MCP 进程、180 个工具**。`src/lvke_mcp/testing/server_manifest.py` 硬断言 14 个进程，工具数由逐个 `build_server()` 运行时自省得到。第二轮将 32 个旧公开名收敛为 8 个聚合入口，迁移表见 `dev-docs/config/mcp-compression-migration-v2.json`。零材料服务为 10 个工具，交付审查服务因七域套件审查扩展为 22 个工具。

```
数据层（3）              研究层（1）           规划层（1）
├─ source-files      13  └─ deep-research  18  └─ project-planning  17
├─ data-acquisition  10
└─ data-analysis     11

财务层（3）              交付层（4）           专项（3）
├─ finance-model     18  ├─ report-generation      13  ├─ asset-acquisition      12
├─ finance-tables     8  ├─ deliverable-review     22  ├─ knowledge-governance    6
└─ (asset-acquisition   ├─ feasibility-delivery   10  └─ reference              12
    见专项)              └─ zero-material-delivery 10

14 个服务 / 180 个工具
```

> **`finance-calc` 不计入对外服务**。它的源码头注释明写 "The public process is no longer registered in user configuration"，未出现在 `~/.claude.json`，7 个 `calc_*` 工具已全部由 `lvke-finance-model.finance_calculate` 路由。本文档保留它的小节是为了说明迁移关系，不代表它是对外服务。
>
> **`servers/` 下另有 12 个子包不注册为 MCP 进程**（每个都有 `server.py`，故不能用"有无 server.py"判断是否对外）：`finance_calc`、`excel_bridge`、`map_geo`、`policy_search`、`statistics_cn`、`environmental_data`、`industry_research`、`lvke_archive`、`lvke_clients`、`lvke_experts`、`lvke_templates`、`scaffold`。前 11 个已被聚合进 `lvke-reference` / `source_inspect_workbook` / `finance_calculate`，作为**内部库**保留（`lvke_reference/service.py` 用 `importlib.import_module` 动态路由，因此静态 grep `import` 查不到依赖——这是审计时容易误判为"孤儿目录"的原因）；`scaffold` 是参考脚手架，故意不注册。85 条工具迁移映射见 `dev-docs/config/mcp-compression-migration.json`。

### 代码分层

真实业务实现在 `domains/`（最大的一层），`servers/` 只是协议适配壳：

| 层 | 行数 | 说明 |
|---|-----|-----|
| `domains/` | 48,284 | 业务实现，8 个子域（finance 28,020 最大） |
| `servers/` | 37,583 | 协议适配 + JSON Schema，26 个子包 |
| `adapters/` | 3,530 | `JSONArtifactStore` 实例化与持久化边界 |
| `runtime/` | 2,983 | transport / storage / jobs / workspace |

导入方向严格单向 **`servers → domains → runtime`**，`domains` 无一处 `import lvke_mcp.servers`。

### 证据资格分级系统

所有数据在系统中的证据资格由 `evidence_track` 字段标识：

| 级别 | 英文标识 | 含义 | 允许场景 |
|-----|---------|-----|---------|
| 真实证据 | `real` | 来自可审计的外部来源（URL、文件），含 locator + hash | 正式交付 |
| 来源重建 | `source_reconstructed` | 从客户报告/模板提取，原始公式不可得 | 流程验收 |
| 技术夹具 | `technical_fixture` | 为测试生成的受控数据 | 仅测试 |
| 受控假设 | `controlled_assumption` | Agent 或用户明确声明的假设 | estimate_preview |

**门禁规则**：
- `formal_release` 只接受 `real` 证据
- `review_candidate` 可接受 `source_reconstructed`，但需披露限制
- `estimate_preview` 接受 `controlled_assumption`，不可对外发布

### 不可变对象命名规范

- `workspace_id`: `^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`
- 资源 URI: `lvke://<domain>/workspaces/<workspace_id>/<uri_segment>/<object_id>`
- **对象 ID 是内容寻址，不是 UUID**：

```python
object_id = f"{id_prefix}_{sha256_json(payload).removeprefix('sha256:')[:24]}"
```

即前缀 + payload 规范化 JSON 的 SHA-256 前 24 位十六进制（如 `evp_a1b2c3...`）。这带来两个语义：**同 payload 天然去重**（`put()` 发现目标文件已存在即直接返回既有记录），以及 `preview_identity()` 可在写库前预先算出 ID——handler 因此能先构建并校验完整响应，把写操作留到最后一步。

> 例外：少数域自己生成 UUID 形式的 ID，如 asset-acquisition 的 `spec_{uuid4hex}` / `acqrun_{uuid4hex}`、report-generation 的 `deliverable_{32hex}`。

### 工作区存储结构

落盘有**三个刻意分离的根**，不是一棵树：

```
{LVKE_MCP_DATA_DIR or ~/.lvke}/workspaces/<workspace_id>/
├── mcp_objects/<domain>/<kind>/<object_id>.json    # 不可变对象（69 个 store / 12 个 domain）
├── jobs/<domain>/<job_id>.json                     # 可变 job 状态机
└── （各域自有的工作区文件，如 workspace_meta.json、finance.json、publish_readiness.json）

{LVKE_DELIVERABLE_DIR or 仓库根}/lvke产出/<workspace_id>/<domain>/<kind>/
                                                    # 交付物（十三表 CSV/XLSX、研报 DOCX）
```

`data_root` 存运行时状态，不适合入库；而十三表、研报、证据包这些**交付物**需要随仓库留存和复核，因此单独给一个根。`deliverable_root()` 的优先级是 `LVKE_DELIVERABLE_DIR` → `LVKE_MCP_DATA_DIR/lvke产出` → 仓库根 `lvke产出/`——中间那级是为了让隔离测试不把假数据写进仓库。`deliverable_dir()` 对 `workspace_id`/`domain`/`kind` **逐段**做路径穿越校验（而非只校验拼好的最终路径），因为任何一段能塞进 `../` 就能逃出交付物根。

**`mcp_objects/` 下的 12 个 domain**（共 69 个 `JSONArtifactStore` 实例，四元组为 domain/kind/id_prefix/uri_segment）：

| domain | kind 数 | 主要 kind（id_prefix） |
|-------|--------|---------------------|
| `project-planning` | 10 | project_contexts(pctx_) / market_cases(mkt_) / revenue_drivers(revdrv_) / cost_drivers(costdrv_) / labor_plans(labor_) / build_scale_cases(scale_) / option_comparisons(optcmp_) / policy_bases(policy_) / input_applicability(iapp_) / idempotency |
| `deep-research` | 9 | packages(drp_) / plan-revisions(drplan_) / plan-proposals(drpp_) / checkpoints(drcp_) / events(drevent_) / quality-reviews(drq_) / agent-sessions(drs_) / agent-transitions(drstate_) / idempotency |
| `zero-material-delivery` | 9 | intents(zmi_) / runs(zmr_) / assumptions(zma_) / technical_reports(zmrep_) / assumption_registers(zmareg_) / gap_registers(zmgap_) / evidence_manifests(zmev_) / manifests(zmman_) / idempotency |
| `data-analysis` | 7 | ingest_tasks(analysis_) / evidence_packs(evp_) / candidate_sets(cset_) / data_profiles(profile_) / financial_trends(ftrend_) / normalized_comparisons(ncmp_) / benchmark_comparisons(bench_) |
| `data-acquisition` | 6 | source_snapshots(src_) / discovery_sets(discovery_) / search_sets(search_) / source_collections(collection_) / url_audits(urlaudit_) / visual_captures(vcap_) |
| `finance-model` | 6 | specs(fsp_) / fact-packs(ffp_) / basis-of-estimate(fboe_) / balance-sheets(fbs_) / monte-carlo(fmc_) / idempotency(fidem_) |
| `deliverable-review` | 6 | preparations(rvprep_) / exports(rvexp_) / rubric_assessments(rva_) / rubric_comparisons(rvc_) / standard_applicabilities(stdapp_) / standard_evidence(stdev_) |
| `knowledge-governance` | 5 | candidates(knc_) / snapshots(kns_) / reviews(knr_) / releases(knrel_) / idempotency |
| `feasibility-delivery` | 4 | runs(fdr_) / checkpoints(fdc_) / releases(fdrp_) / idempotency(fdi_) |
| `report-generation` | 3 | preparations(rprep_) / revisions(rrv_) / task_bindings(rjob_) |
| `finance-tables` | 2 | packages(ftp_) / csv_exports(ftc_) |
| `asset-acquisition` | 2 | table_packages / mcp_idempotency(acqidp_) |

> 注意 `kind` 是磁盘目录名，`uri_segment` 是 URI 里的段名，两者常不同（如 `source_snapshots` ↔ `sources`、`ingest_tasks` ↔ `tasks`）。`finance-model` 的 run 与 `asset-acquisition` 的 spec/run 不走 `JSONArtifactStore`，各有自己的存储实现。

**`mcp_objects/` 不是 `JSONArtifactStore` 独占的**——以下子目录同在该树下，但由手写路径直接创建：

| 路径 | 性质 |
|-----|-----|
| `deliverable-review/events/` | append-only 事件日志 |
| `deliverable-review/idempotency/<digest>.json` + `.operation.lock` | 独立幂等账本与变更窗口锁 |
| `deep-research/agent-locks/<task_id>.lock` | 状态机转移锁 |
| `deep-research/.resume-signing-key`（0600） | 恢复令牌 HMAC 密钥 |
| `feasibility-delivery/` `knowledge-governance/` `zero-material-delivery/` 下的 `.idempotency.lock` | workspace 级幂等锁 |

`project_planning` 做级联 stale 标记时会 `rglob("*.json")` 扫整个 `mcp_objects/` 并**显式跳过路径含 `idempotency` 的文件**——说明代码本身知道这棵树是混合的。

工作区里另有若干**历史残留目录**（当前代码已无写入路径）：`source_files`（下划线版，现用连字符）、`revisions`、`deliverable_artifacts`、`report_artifacts`、`agent_proposals`、`deep_research_sessions`、`finance_audit_tenants`，以及 `mcp_objects/` 下的 `*_tenant_<64hex>` 系列——后者来自已废弃的多租户目录方案。

### 幂等性保证

系统里**有三套并存的幂等机制**，适用场景与冲突行为各不相同：

| 机制 | 位置 | 键 | 冲突行为 |
|-----|-----|---|---------|
| **内容寻址去重** | `JSONArtifactStore.put` | payload 的 sha256（即 object_id 本身） | 无冲突概念：同 payload 命中既有文件即返回；持 `FileLock(timeout=30)` 序列化"检查—替换" |
| **Job 预留** | 已删除 | — | `runtime/jobs.py` 已移除，不再提供该机制 |
| **领域层幂等 store** | 如 `asset_acquisition._mutation` | `sha256(key)` + `payload_hash` | 同键同 payload → 重放原响应并加 `idempotent_replay: True`；payload 不一致 → 返回 `IDEMPOTENCY_CONFLICT`。**只有 `success=True` 且 `status ∈ {ok, partial}` 才写入幂等记录** |

TTL 由 `LVKE_MCP_IDEMPOTENCY_TTL_SECONDS` 控制（默认 86400，clamp 到 60~604800），但**并非所有域都实现了过期清理**（见「实现完整度评估」的全局限制 9）。

**并非所有写工具都要求 `idempotency_key`**：`lvke-report-generation` 的 13 个工具全部没有该参数，写幂等完全依赖内容寻址；`lvke-reference` 的 12 个只读工具既不接受 `workspace_id` 也不接受 `idempotency_key`。

> **`JobRepository` 已删除**：`runtime/jobs.py` 不再存在。公开工具面为 14 服务 / 180 工具，`task_support` 仍全部为 `forbidden`。

---

## 运行时层

### transport.py - MCP 协议适配器

**职责**：
- 将官方 MCP SDK 封装为统一的 `OfficialStdioServer`
- 工具注册、Schema 验证、Resource 提供
- **input schema 压缩**（保留全部顶层参数与标量约束，深层对象改为 Resource 引用）；**output schema 在 `tools/list` 里整体置 `None` 不发布**，但服务端校验照旧
- 统一错误信封：`{success, status, resource_uris, warnings, blockers, next_actions}`

**核心类**：
```python
class OfficialStdioServer:
    def __init__(self, server_name: str, server_version: str, logger) -> None

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: ToolHandler,
        output_schema: dict[str, Any] | None = None,
        annotations: types.ToolAnnotations | None = None,
        task_support: Literal["forbidden", "optional", "required"] = "forbidden",
    ) -> None

    def register_resource_provider(self, lister, reader) -> None

    def register_schema_resource(
        self, uri: str, schema: dict[str, Any], *,
        name: str, title: str,
        description: str = "服务端执行的完整 JSON Schema。",
    ) -> None                     # 后 3 参是 keyword-only

    def register_task_adapter(self, adapter: TaskAdapter) -> None

    @property
    def tool_specs(self) -> tuple[ToolSpec, ...]      # 是 property，不是方法

    def streamable_http_app(self, **kwargs)
    async def run_stdio(self) -> None
    def serve_forever(self) -> None                   # 无参数，内部 anyio.run(self.run_stdio)
```

`register_tool` 在注册时就用 `Draft202012Validator.check_schema` 校验输入与输出 schema（schema 本身写错会在启动期炸，不会拖到调用期），重复注册同名工具 → `raise ValueError`。`annotations` 传 `None` 时注入的默认值是 `readOnlyHint=False, destructiveHint=False, openWorldHint=False`——即**默认按写操作对待**。

全系统 180 个工具的 `task_support` 均为默认 `"forbidden"`，即没有任何异步/轮询入口；`finance_run_model`、`acquisition_generate_artifact`、`delivery_start` 等重活都在单次调用里同步阻塞完成。

**输出信封**：各服务的 `_OUTPUT` schema 只强制 6 项（`success` / `status` / `resource_uris` / `warnings` / `blockers` / `next_actions`），但传输层实际会补齐到约 25 个字段：

```
业务态：  status, success, business_success, system_success, transport_success, completed, outcome
产物：    resource_uris, warnings, blockers, next_actions
版本：    service_version, build_commit, build_time, schema_version, runtime_instance
审计：    started_at, finished_at, duration_ms, input_hash, trace_id
溯源：    basis_hash, content_hash, lineage, coordination
条件字段：code + message（仅 business_success 为 false 时补）、domain_status、task_status
```

`input_hash` 是请求参数的 sha256，源码注释写明是为了可审计而 "without leaking request contents"。

**status 的规范集只有 9 个值**，其余一律被归一化：

```
ok | accepted | partial | empty | missing_inputs | blocked | incomplete | failed | upstream_failure

{applied, released, completed, done, cancelled}      → ok        原值存 domain_status
{pending, queued, running, started, processing}      → accepted  原值存 task_status
其它未知值 → success 为 false 则 blocked，否则 ok      原值存 domain_status
```

**`_attach_runtime_metadata` 会覆写业务层写的 `success`**：

```python
business_success = status in {"ok", "accepted"}
completed        = status == "ok"
success          = business_success        # handler 自己写的 success 被强制覆盖
```

因此 `status="partial"` 的响应即使业务层写了 `success=True` 也会变成 `False`，并自动补 `code = f"{server_name}.partial"`。领域自定义 status（如 report-generation 的 `agent_action_required` / `agent_drafted`）会被改写为 `ok`，原值移到 `domain_status`——**工具 docstring 承诺的 status 与实际出线的 status 字段不一致**。

**五条例外路径**（并非所有失败都走业务信封）：

| 路径 | 返回形式 |
|-----|---------|
| 未知工具 | **MCP 协议错误** `-32602 "Unknown tool"` |
| 输入 schema 校验失败 | **MCP 协议错误** `-32602` + 修复指引（不是业务信封） |
| handler 抛异常 | 错误信封 `{server}.internal_error`，`isError=true`，**无 structuredContent** |
| handler 返回非 dict | 错误信封 `{server}.invalid_tool_output` |
| outputSchema 校验失败 | 错误信封 `{server}.invalid_tool_output` |

> **两套信封长度不一致**（实测确认）：错误路径的 `sanitized_error_payload` 只有 21 个字段，**缺** `completed` / `outcome` / `service_version` / `build_commit` / `build_time` / `schema_version` / `runtime_instance`，**多** `retryable`，且 `blockers = [code]`。而正常路径即使业务失败 `blockers` 也可能保持 `[]`。此外错误路径时间戳用 UTC，正常路径用本地时区。

**公开面 schema 压缩**分两件事，容易混为一谈：

- **output schema 不发布**：`tools/list` 里 `outputSchema=None`。源码注释说明理由——完整输出 schema 仍是服务端校验的权威依据，但在 `tools/list` 里重复它"占了模型上下文的一大部分，且对 structuredContent 支持并非必需"。（`_public_output_schema` 函数存在但**是死代码**，全仓无调用点；反倒是旧 `stdio.py` 真的发布了 outputSchema。）
- **input schema 逐属性压缩**：超过 `_PUBLIC_SCHEMA_INLINE_LIMIT = 2 KiB` 的子 schema 被替换为带 `x-lvke-schema-uri` + `x-lvke-schema-pointer` 的引用形式，客户端按需回读 `lvke://schemas/<server>/<tool>/input`。源码注释强调两条不变量：「公开契约必须保留每一个顶层参数名」、「大 schema 逐个属性压缩，因此 properties 映射本身永不被替换成不透明存根」。即**始终保留顶层参数、必填项、容器类型及数组元素类型**。

另有不依赖工具名的稳定 schema 别名：`finance-spec-v3`、`asset-acquisition-spec`、`review-target`、`report-preparation` 等。

### storage.py - 不可变对象存储

**职责**：
- 提供 `JSONArtifactStore` 抽象，管理工作区内的 JSON 对象
- 文件锁保证并发安全
- 内容寻址（content hash）验证数据完整性
- 资源游标分页（base64编码，含快照hash防篡改）

**核心类**：
```python
class JSONArtifactStore:
    def __init__(self, domain: str, kind: str, id_prefix: str, uri_segment: str) -> None

    def put(
        self,
        workspace_id: str,
        payload: dict[str, Any],
        *,
        producer: str,
        status: str = "ok",
        source_ids: Iterable[str] = (),
        basis: Any | None = None,
        schema_version: str = "1.0",
        object_id: str | None = None,
    ) -> dict[str, Any]          # 返回完整 record，不是 object_id

    def get(self, workspace_id: str, object_id: str) -> dict | None
    def list(self, workspace_id: str) -> list[dict]
    def uri(self, workspace_id: str, object_id: str) -> str
    def resolve_uri(self, uri: str) -> dict | None
    def preview_identity(self, workspace_id: str, payload: dict) -> dict[str, str]
```

写入的 record 固定含 12 个字段：`object_id` / `workspace_id` / `schema_version` / `producer` / `created_at` / `content_hash` / `basis_hash` / `status` / `source_ids` / `resource_uri` / `payload`。`content_hash` 由 payload 算，`basis_hash` 在显式传 `basis` 时由 basis 算——两者分开，这是乐观锁（`expected_basis_hash`）能独立于内容变更的基础。

`put()` 在 `FileLock(target + ".lock", timeout=30)` 下先查 `target.exists()` 再 `os.replace` 临时文件，因为对象 ID 是内容寻址的，并发调用常指向同一文件。`get()` 与 `list()` 都会二次校验 `record["workspace_id"] == 入参`，不符视作不存在——这是工作区隔离的第二道关。

**分页函数**（游标含快照哈希，防集合漂移）：
```python
def paginate_resource_entries(
    entries: Iterable[dict],
    *,
    cursor: str = "",
    limit: int = 50,          # clamp 到 1~200
) -> dict  # {resources, next_cursor, has_more, snapshot_hash}
```
游标是 base64url 编码的 `{last_uri, snapshot_hash}`。集合发生变化时 `snapshot_hash` 不匹配 → `resource_list_changed`；游标本身损坏 → `resource_cursor_invalid`。

### workspace.py - 工作区与交付物路径

只有 4 个公开函数（**没有** `workspace_domain_dir`）：

```python
def data_root() -> Path
    # LVKE_MCP_DATA_DIR 或 ~/.lvke

def workspace_root(workspace_id: str) -> Path
    # {data_root}/workspaces/{workspace_id}
    # 注意：不创建目录、不校验 ID 格式（校验在 storage.require_safe_id）

def deliverable_root() -> Path
    # LVKE_DELIVERABLE_DIR → LVKE_MCP_DATA_DIR/lvke产出 → 仓库根 lvke产出/

def deliverable_dir(workspace_id: str, domain: str, kind: str) -> Path
    # {deliverable_root}/{ws}/{domain}/{kind}，逐段防路径穿越
```

路径段校验正则 `^[A-Za-z0-9_.一-鿿-]{1,160}$`（允许中文），并显式拒绝纯点段（`.` 与 `..`）。

---

## 数据层服务

### lvke-data-acquisition（数据采集服务）

**服务版本**: 0.1.0 | **实现深度**: 完整实现，含真实网络调用

**服务定位**：面向公网的数据采集入口，负责搜索、抓取 URL、固化不可变来源快照。**采集阶段不调用 LLM**，只负责网络IO + 安全检查 + 数据固化。

**安全机制（代码实测验证）**：
- URL 含 API 密钥前缀检测（`_PREFIX_RE`）→ 拦截
- SSRF/私网 IP 检测（`async_url_safety_decision`）→ 拦截
- 云 metadata 端点（`metadata.google.internal` 等）→ 无条件拒绝
- `import_external_snapshot` 需通过 HMAC-SHA256 签名验证（`LVKE_EXTERNAL_EXTRACT_RECEIPT_SECRET`）

**工具列表**：

| 工具名 | 类型 | 核心参数 | 实际行为 | 创建对象 |
|-------|-----|---------|---------|---------|
| `data_discover` | 只读* | `workspace_id`, `queries[]`(1-10); `limit_per_query`(1-20，默认 5), `auto_expand`, `target_count`(10-100), `domain_allowlist[]`, `domain_denylist[]`, `total_timeout_seconds`(默认 120) | 多轮搜索 → 去重 → domain 过滤 → 相关度评分 → **固化候选集** | `DiscoverySet` |
| `data_search` | 只读* | `workspace_id`, `query`, `limit` | 单次搜索，含相关度评分（≥0.25 为相关），**仍固化 SearchSet** | `SearchSet`（仅元数据） |
| `data_fetch` | 写 | `workspace_id`, `urls[]`(1-20); `content_mode`, `extraction_provider`(auto/tavily/direct_http，默认 auto) | 按 `extraction_provider` 走**互斥分支**（不是逐级兜底，见下）| `SourceSnapshot[]` |
| `data_import_external_snapshot` | 写 | `workspace_id`, `url`, `title`, `content`, `provider`, `provider_tool`, `content_kind`（**`extraction_receipt` 不必填**） | 校验 `(provider, provider_tool)` 二元组白名单、内容 hash；**仅当传了 receipt 才验 HMAC 签名** | `SourceSnapshot` |
| `data_collect` | 写 | `workspace_id`, `discovery_set_id`, `selected_candidate_ids[]` | 从已有 DiscoverySet 选择候选 → 复用 `data_fetch` 安全路径 | `SourceCollection` + `SourceSnapshot[]` |
| `data_audit_urls` | 写 | `workspace_id`, `urls[]`, `audit_mode` | `safety` 模式只做静态检查；`live` 模式真实连接验证可达性 | `UrlAudit` |
| `data_get_url_audit` | 只读 | `workspace_id`, `url_audit_id` | 读取 UrlAudit 对象 | 无 |
| `data_capture_source_view` | 写 | `workspace_id`, `source_snapshot_id`, `image_file_id`, `url`, `viewport`, `captured_at` | 绑定截图+来源快照，校验URL一致性、图片类型（PNG/JPEG）、hash | `VisualSourceCapture` |
| `data_get_visual_capture` | 只读 | `workspace_id`, `visual_capture_id` | 读取视觉捕获对象 | 无 |
| `data_provider_status` | 只读 | 无 | 检查 Tavily provider 配置和可用性（不含密钥） | 无 |

> \* `data_discover` 与 `data_search` 的 ToolAnnotations 都是 `readOnlyHint=True`，但两者都调用 `STORE.put` 固化对象（`DISCOVERY_STORE` / `SEARCH_STORE`）并返回 `discovery_set_id` / `search_set_id`。注解与行为不符，见「已知限制」。

**关键实现细节**：

`data_discover` 的 `auto_expand` 基于 `_FEASIBILITY_ANGLES` 轮询扩展查询——该元组共 **10 个元素，其中第一个是空串（代表裸主题本身）**，另 9 个中文角度为：政策 文件 / 市场 需求 规模 / 行业 现状 发展 / 技术 方案 参数 / 投资 成本 造价 / 运营 收入 电价 价格 / 竞品 案例 项目 / 区域 分布 布局 / 风险 问题 挑战。达到 `target_count` 后提前 break，不超额调用 API；达不到时诚实报 `partial`，源码注释明确 "Falling short is reported honestly as partial — never padded"。

`data_fetch` 的提取路径是**互斥分支，不是逐级兜底**——这一点容易误读：

- `extraction_provider ∈ {auto, tavily}` → 只走受信 Tavily 提取（含 HMAC 签名 → `formal_use_allowed=True`）。**失败即无条件 `return`，永不回退 direct_http**：
  - 本地配置缺口（`receipt_secret_unconfigured` / `provider_transport_unconfigured`）→ `status=blocked`、`code=trusted_extract_local_config_gap`、`retryable=False`，并在 `next_actions` 里提示补 `LVKE_EXTERNAL_EXTRACT_RECEIPT_SECRET` 或显式改用 `direct_http`。
  - 上游问题（`provider_returned_empty` / `provider_call_failed`）→ `status=upstream_failure`、`retryable=True`、`retry_after=5`。
  - 这个区分是刻意的（源码注释「本地配置缺口不应归因为 upstream_failure」）。
- `extraction_provider = direct_http` → **一开始就不尝试 Tavily**，直接走 `SourceExtractor` 受控直连兜底层（逐 URL 再过一遍密钥模式与私网/云 metadata 判定，含 DNS rebinding 防护），证据轨保持非 Tavily。

即：想用直连必须**显式**指定，`auto` 不会替调用方降级到不可信来源。

**已知限制**：
- **`data_discover` 与 `data_search` 的注解与行为矛盾**：两者都标 `readOnlyHint=True`，但都无条件 `STORE.put` 固化对象（`DiscoverySet` / `SearchSet`）。`data_search` 固化的是搜索元数据（query、results、skipped、provider、relevance_threshold、timing 全量落盘）并返回 `search_set_id` 与 `resource_uris`，只是不固化网页正文。
- **`data_import_external_snapshot` 的签名校验是条件性的**：`extraction_receipt` 不在 `required` 内，所有回执校验都包在 `if receipt:` 里。不传 receipt 时快照仍可导入，只是拿不到正式证据资格。
- provider 白名单是 `(provider, provider_tool)` **二元组集合**，含 `("tavily", "tavily_extract")` 与 `("tavily-hikari", "tavily_extract")` 两条；比对前把 provider 里的下划线归一为连字符，故 `tavily_hikari` 也能通过。
- **`data_audit_urls` 的 `safety` 模式并非纯静态检查**：它调 `_safe_public_url`，内含 DNS 解析后的私网/metadata 判定。与 `live` 的真实差别是后者额外做可达性验证。
- 截图 `formal_use_allowed` 永远为 `False`，截图不能作为正式证据。

---

### lvke-source-files（源文件管理服务）

**服务版本**: 0.1.0 | **实现深度**: 完整实现

**服务定位**：管理客户提供的原始文件（Excel、PDF等），提供安全扫描、解析和检查能力。

**安全限制**：
- `source_import_local_path` 仅在 stdio/local 传输下可用，且只接受 `LVKE_SOURCE_IMPORT_ROOTS` 或 `LVKE_EXTERNAL_CORPUS_ROOT` 指定目录中的文件
- `source_import_content` Base64 内容限 8 MiB（11,184,812 字符）
- 所有导入文件过 magic-byte 类型检查
- workspace_id 格式强制校验：`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`

**工具列表**：

| 工具名 | 类型 | 核心参数 | 实际行为 |
|-------|-----|---------|---------|
| `source_external_corpus_resolve` | 只读 | `project_name` | 校验外部资料清单、返回允许导入路径（不导入文件） |
| `source_import_content` | 写 | `workspace_id`, `original_filename`, `declared_mime`, `content_base64`, `expected_sha256` | 导入Base64文件，过安全扫描和解析链 |
| `source_import_local_path` | 写 | `workspace_id`, `local_path`, `original_filename`, `declared_mime` | 从受控本地路径导入（stdio only） |
| `source_upload_begin` | 写 | `workspace_id`, `original_filename`, `total_size`（≤128MiB）, `expected_sha256` | 创建24小时有效分块上传会话 |
| `source_upload_chunk` | 写 | `workspace_id`, `upload_id`, `offset_bytes`, `content_base64` | 按字节偏移上传块（≤4MiB/块），拒绝重叠 |
| `source_upload_commit` | 写 | `workspace_id`, `upload_id` | 校验连续性 + 总大小 + SHA-256 → 触发解析 |
| `source_upload_abort` | 写 | `workspace_id`, `upload_id` | 中止并清除暂存块 |
| `source_task_status` | 只读 | `workspace_id`, `task_kind`(parse/upload), `target_id` | 查询上传或解析状态；kind 与 `ups_`/`job_` ID 前缀必须匹配 |
| `source_file_list` | 只读 | `workspace_id`, cursor, limit | 分页列出文件（不暴露绝对路径） |
| `source_file_get` | 只读 | `workspace_id`, `file_id` | 读取元数据、安全扫描结果、解析状态 |
| `source_parse_retry` | 写 | `workspace_id`, `job_id` | 为 failed/partial/cancelled 创建新解析尝试 |
| `source_parse_cancel` | 写 | `workspace_id`, `job_id` | 取消 queued/running 解析（不删除原件） |
| `source_inspect_workbook` | 只读 | `workspace_id`, `file_id`, `operation`, `sheet`, `range` | 工作簿检查：list_sheets/read_cells/read_formulas/cross_sheet_refs/dependency_tree |

---

### lvke-data-analysis（数据分析服务）

**服务版本**: 0.1.0 | **实现深度**: 完整实现

**服务定位**：从已导入来源中提取结构化事实，构建可审计的证据包。**三道门**限制数值提取：复合单位门 + 最近标签+限定词门 + 单位相容门，任一不过则不给 `numeric_value`。

**工具列表**：

| 工具名 | 类型 | 核心参数 | 实际行为 |
|-------|-----|---------|---------|
| `analysis_ingest` | 写 | `workspace_id`, `source_snapshot_ids[]`, `file_ids[]` | 建立可查询分析任务（partial不冒充完整） |
| `analysis_status` | 只读 | `workspace_id`, `analysis_task_id` | 读取解析/入库状态 |
| `analysis_query` | 只读 | `workspace_id`, `analysis_task_id`, `query`, `limit` | 全文检索，返回原始 locator + 证据资格 |
| `analysis_extract_candidates` | 写 | `workspace_id`, `analysis_task_id`, `field_specs[]` | 按字段/别名/单位确定性提取事实候选，散文数值过三道门 |
| `analysis_profile_tabular` | 写 | `workspace_id`, `analysis_task_id`, `file_ids[]` | 表格画像：表头、维度、数值/公式统计（不重算公式） |
| `analysis_compare` | 只读 | `observations[]`, `comparison_mode` | 三种语义比较：来源对账/同业/可加总分部；期间不一致→partial |
| `analysis_normalize_compare` | 写 | `workspace_id`, `observations[]`, `conversion_rules[]` | 按规则归一化后比较，拒绝模糊猜测 |
| `analysis_compare_benchmark` | 写 | `workspace_id`, `subject`, `benchmarks[]` | 全字段精确匹配后比较（指标/期间/地区/单位/税基），口径不兼容→partial |
| `analysis_financial_trends` | 写 | `workspace_id`, `observations[]`, `methods[]` | 计算 YoY/QoQ/CAGR/共同比，缺基期→结构化问题 |
| `analysis_list_unit_rules` | 只读 | 无 | 读取受控精确单位字典 |
| `analysis_build_evidence_pack` | 写 | `workspace_id`, `analysis_task_id`, `selected_source_ids[]`, `fact_candidates[]`, `evidence_track` | 固化不可变 EvidencePack；调用方自报候选仅 estimate_preview 资格 |

**证据包结构**（`analysis_build_evidence_pack`）：

响应体返回的是标识与缺口，**不回传 `fact_candidates`**——候选事实只写进固化的 `payload`（进 `EVIDENCE_STORE`），要读得走 Resource：

```json
{
  "evidence_pack_id": "evp_...",
  "basis_hash": "sha256:...",
  "source_count": 3,
  "missing_fields": [{"field": "...", "reason": "...", "next_action": "..."}],
  "limitations": ["..."],
  "resource_uris": ["lvke://data-analysis/workspaces/<ws>/evidence-packs/evp_..."]
}
```

**关键区分**：只有带 `candidate_set_id` 的候选才是服务端签名的正式证据；调用方自己写的 `fact_candidates` 只有 `estimate_preview` 资格，抄进 pack 也不获得正式资格。

---

## 研究层服务

### lvke-deep-research（深度研究服务）

**服务版本**: 0.3.0 | **实现深度**: 完整实现

**服务定位**：Agent 主导的研究会话管理。**MCP 本身不启动 LLM**——Agent 用数据采集/分析工具收集依据，通过 `dr_submit` 固化带引用的发现。

**两段式质量模型**（这是本服务最容易误解的地方）：

- `dr_submit` 固化的包**恒为 `partial`**，不可作为完成态。
- `dr_confirm_quality` 会另外固化一个 `status="completed"` 的**新包**（新 `research_package_id`），并把 `quality_review_id` / `quality_review_status` 写进去。因此"研究包始终是 partial"只对 submit 产物成立。
- **完全通过时 `dr_confirm_quality` 确实会认证项目事实**：`project_fact_certified = (review_status == "passed" and evidence_policy != "source_reconstructed")`。源码注释说明这是修补门禁泄漏——`accepted_with_limitations` 意味着存在 `missing_fields`/`conflicts` 或走了来源重建，都不认证，否则会绕过正式交付门禁。
- P0-009 修复后写入是原子的：先用 `preview_identity` 推导 ID、构建并校验完整响应，最后才写 QualityReview 与 completed 包，确保 outputSchema 校验失败时**零写入**。

**设计原则**：
- `dr_submit` 只保存 partial；质量审计单独调用（已由代码保证）。
- checkpoint 只在真实存在时进资源清单，不给死链 URI。
- 恢复令牌密钥**首选** env `LVKE_DR_RESUME_SIGNING_KEY`；未配置时会在工作区内自行生成并持久化 32 字节随机密钥。密钥不进任何产物（不出现在 Resource URI 或 store 记录里）。

> **「预算耗尽 → partial，绝不伪造 done」这条只有注释没有实现**：`budget` 在 `application.py` 里只被透传与存取，全仓找不到任何预算比较逻辑（exhaust/exceed/remaining），`round_no` 也恒为硬编码 `None`。结论（不伪造 done）由"submit 恒 partial + 质量确认单独走"这个结构保证，而非由预算计量保证。

**工具列表**：

| 工具名 | 类型 | 核心参数 | 实际行为 |
|-------|-----|---------|---------|
| `dr_prepare` | 只读 | `workspace_id`, `topic`, `profile`, `industry`, `region` | 纯计算：生成 brief、子问题、预算方案（不消耗网络） |
| `dr_start` | 写 | `workspace_id`, `topic`, `profile`, `idempotency_key` | 建立 Agent 研究会话（不启动内置 LLM），返回 `task_id` |
| `dr_status` | 只读 | `workspace_id`, `task_id` | 轮询进度：status/轮次/预算/质量门 |
| `dr_cancel` | 破坏性写 | `workspace_id`, `task_id`, `reason` | 中止任务（终态不可恢复运行），`destructiveHint=True` |
| `dr_get_plan` | 只读 | `workspace_id`, `task_id`, `plan_revision_id` | 读取指定计划 revision（含 basis_hash） |
| `dr_propose_plan_revision` | 写 | `workspace_id`, `task_id`, `expected_basis_hash`, `changes` | 创建修订提案（不直接修改当前revision） |
| `dr_apply_plan_revision` | 写 | `workspace_id`, `task_id`, `proposal_id`, `expected_basis_hash` | 原子提交提案为新 revision |
| `dr_add_sources` | 写 | `workspace_id`, `task_id`, `expected_basis_hash`, `sources[]` | 绑定混合类型来源（8种：source_snapshot/evidence_pack/archive_chapter等） |
| `dr_remove_sources` | 写 | `workspace_id`, `task_id`, **`expected_basis_hash`**, `source_object_ids[]`, `reason` | 排除来源并记录原因（不删除快照或旧 revision）。5 个参数**全部必填** |
| `dr_list_events` | 只读 | `workspace_id`, `task_id`, `after_cursor`, `limit` | 游标读取结构化事件（不含 chain-of-thought） |
| `dr_create_checkpoint` | 写 | `workspace_id`, `task_id`, `expected_basis_hash`, `expires_in_seconds` | 固化当前状态 → 返回签名恢复令牌（格式：`drresume.v1.<...>`） |
| `dr_resume` | 写 | `workspace_id`, `resume_token`, `idempotency_key` | 校验签名令牌 → 创建新任务（原任务不变） |
| `dr_submit` | 写 | `workspace_id`, `task_id`, `report_md`, `citations[]`, `quality_summary` | 固化 Agent 发现为 partial research_package（不是完成态） |
| `dr_continue` | 写 | `workspace_id`, `task_id`; 可选 `supplemental_questions[]` | 开新续研任务。**真实门禁只判两件事**：未被取消、且已达终态（终态的定义是"存在已提交的研究包"），与 partial/blocked/needs_clarification 三个状态名无关 |
| `dr_confirm_quality` | 写 | `workspace_id`, `research_package_id`; 可选 `citation_coverage`, `accept_material_limitations` | 独立质量确认，**固化一个新的 `completed` 包**；完全通过时会写 `project_fact_certified=True`（见上文两段式质量模型） |
| `dr_get_report` | 只读 | `workspace_id`, `task_id` | 读取研究报告 Markdown + 引用审计（**财务数字不得取自此报告**） |
| `dr_get_evidence` | 只读 | `workspace_id`, `task_id` | 读取证据图谱 + 来源清单 |
| `dr_get_bundle` | 写* | `workspace_id`, `task_id` | **实际是纯读**：函数体只做 `PACKAGE_STORE.list` 过滤 + URI 字符串拼接，全程零写入。`research_package_id` 是 `dr_submit` 固化的，不是这里创建的。只登记真实存在的 artifact，缺失的诚实省略 |

**来源类型枚举**（`dr_add_sources` 支持的 8 种）：
`source_snapshot` | `evidence_pack` | `archive_chapter` | `reviewed_knowledge` | `policy_record` | `industry_record` | `source_reconstructed` | `technical_fixture`

---

## 规划层服务

### lvke-project-planning（项目规划服务）

**服务版本**: 0.1.0 | **实现深度**: 完整实现，**工具最多（36 个，14 只读 / 22 写）** | **代码规模**: server.py 1,511 + `lifecycle.py` 964 + `domains/project_planning` 2,374

**服务定位**：管理可研流程中的所有规划对象。严格遵循 **prepare → validate → confirm** 三步模式，每步都要显式进行，不自动选择或平均候选。

**收入模型类型**（`_REVENUE_SPEC` 的 5 种 oneOf）：
- `product_sales`：产品销售（数量 × 单价 × 达产率）
- `property_sales`：物业销售（可售面积 × 均价 × 去化率）
- `tourism`：旅游（游客量 × 人均消费 + 门票 + 固定收入）
- `gov_payment`：政府付费（年度金额 + VAT退税 + 财政补贴）
- `flat`：扁平化（直接给年度收入 + 爬坡率）

**证据绑定结构**（`_EVIDENCE_BINDING` 的 5 个必填字段；注意**并非所有**规划对象都用它——`_POLICY_CANDIDATE` 没有 `evidence_bindings` 字段，它用 `source_snapshot_id` + `content_hash` + `locator` 直接绑定）：
```json
{
  "source_id": "...",
  "source_type": "web_snapshot | controlled_file | technical_fixture | selected_fact | source_reconstructed | search_summary",
  "content_hash": "sha256:...",
  "locator": "页码/表格/单元格位置",
  "evidence_track": "real | source_reconstructed | technical_fixture | controlled_assumption"
}
```
> **`search_summary` 的拒绝点不止一处**：`_validate_market_payload` 里的 `search_summary_not_evidence` 同时被 `planning_validate_market_case` 与 `planning_confirm_market_case` 调用，因此确认阶段也会被拒；`_validate_option_evidence` 对方案比选独立做同样拒绝，且由 `planning_prepare_option_comparison` 调用——即方案比选在 prepare 阶段就拦。搜索摘要在这两条链上都不能充当证据。

**工具列表（按子领域分组）**：

**ProjectContext（项目上下文）**：

| 工具名 | 必填参数 | 行为 |
|-------|---------|-----|
| `project_context_create` | `context{}`, `idempotency_key` | 创建草稿。`_REQUIRED_CONTEXT_FIELDS` 是 7 项：`project_name` / `industry_code` / `project_type` / `region` / `objective` / **`report_type`** / `evidence_track`。`asset_type` / `target_type` / `transaction_structure` **不在必填内**（有默认值） |
| `project_context_validate` | `project_context_id`, `idempotency_key` | 校验并固化 InputApplicability；缺字段返回精确列表 |
| `project_context_revise` | `project_context_id`, `expected_basis_hash`, `patch{}`, `idempotency_key` | 乐观锁修订，返回下游 stale 清单 |
| `project_context_list` | `workspace_id` | 分页列出所有 revision |
| `planning_resolve_industry_skill` | `project_context_id` | 返回对应行业的主 Skill 名称 |
| `planning_get_industry_constraints` | `project_context_id` | 返回版本化行业规划技术参数（不是证据） |
| `planning_get_object` | `object_type`, `object_id` | 读取任意规划对象，`object_type` 枚举 **9 值**：ProjectContext / **InputApplicability** / MarketSizingCase / RevenueDriverSet / BuildScaleCase / CostDriverSet / LaborPlan / OptionComparison / PolicyBasis |

**市场分析**：

| 工具名 | 必填参数 | 行为 |
|-------|---------|-----|
| `planning_prepare_market_case` | `project_context_id`, `evidence_pack_id`, `candidates[]` | 创建多路径案例；候选含 method(top_down/bottom_up/analogy/capacity_factor)/market_size/target_share |
| `planning_compare_market_cases` | `market_case_id` | 逐对偏差比较，`aggregation=none` |
| `planning_validate_market_case` | `market_case_id` | 校验多路径、份额算术、evidence locator；拒绝 search_summary |
| `planning_confirm_market_case` | `market_case_id`, `selected_candidate_id`, `selection_reason`, `rejected_candidate_ids[]` | 固化选择（必须列出全部被拒选项） |

**收入驱动**：

| 工具名 | 必填参数 | 行为 |
|-------|---------|-----|
| `planning_prepare_revenue_drivers` | `project_context_id`, `market_case_id`, `candidates[]` | 固化多候选 RevenueDriverSet |
| `planning_create_revenue_drivers` | `project_context_id`, `market_case_id`, `revenue_spec{}`, `op_years` | 单步创建（复用财务收入展开器） |
| `planning_compare_revenue_candidates` | `revenue_driver_set_id` | 逐年收入差异比较 |
| `planning_validate_revenue_drivers` | `revenue_driver_set_id` | 校验收入模型和逐年曲线 |
| `planning_confirm_revenue_drivers` | `revenue_driver_set_id`, `selected_candidate_id`, **`rejected_candidate_ids[]`**, `selection_reason`(≥10 字) | 固化确认；`rejected_candidate_ids` 必须等于「全部候选 − 选中」，漏项即 `planning_rejected_candidates_incomplete` |

**建设规模**：

| 工具名 | 必填参数 | 行为 |
|-------|---------|-----|
| `planning_solve_build_scale` | `project_context_id`, `market_case_id`, `alternatives[]` | 对多方案确定性计算：产能 / 容积率**只判上限** `> plot_ratio_max` / 建筑密度 `≤ building_coverage_max` / 绿地率 `≥ green_ratio_min`。**`plot_ratio_min` 在本工具中一次都没被引用**——容积率下限只在单方案入口 `planning_create_build_scale` 生效 |
| `planning_create_build_scale` | `project_context_id`, `market_case_id`, `target_capacity{}`, `land_area_m2`, `constraints{}`, `facilities[]` | 单方案创建 |
| `planning_validate_build_scale` | `build_scale_case_id` | 至少要求1个可行候选 |
| `planning_confirm_build_scale` | `build_scale_case_id`, `selected_candidate_id`, **`rejected_candidate_ids[]`**, `selection_reason`(≥10 字) | 固化选择；同样强制 `rejected` 等于「全集 − 选中」 |

**成本驱动**：

| 工具名 | 必填参数 | 行为 |
|-------|---------|-----|
| `planning_create_cost_drivers` | `project_context_id`, `build_scale_case_id`, `invest_breakdown{}`, `operating_cost_items[]`(**minItems 3**) | `_INVEST_BREAKDOWN` 的 8 个必填字段：`construction_wan`（建设投资合计）+ `civil_wan` / `equipment_wan` / `installation_wan` / `other_wan` / `reserve_wan` / `interest_wan` / `working_capital_wan`。**`construction_wan` 是合计口径，须与其余明细闭合**，不闭合即阻断 |
| `planning_prepare_cost_drivers` | 同上，但 items 用 `_COST_CANDIDATE_ITEM`（含数量/单耗/单价可展开字段），且 **minItems 为 1 而非 3** | 固化候选（数量×单耗×单价待后续计算）。prepare 不校验条目数，三科目门槛要到 `planning_validate_cost_drivers` 才卡 |
| `planning_calculate_cost_drivers` | `cost_driver_set_id` | 按数量×单耗×单价×换算系数×损耗率计算，固化新 revision |
| `planning_validate_cost_drivers` | `cost_driver_set_id` | 校验投资闭合、明细完整性 |
| `planning_confirm_cost_drivers` | `cost_driver_set_id`, `confirmation_reason` | 确认并生成 FinanceSpec 转换 ledger |
| `planning_get_env_templates` | `project_type`, `pollutant_types[]` | 读取环保成本字段模板（不是合规结论） |

**劳动计划**：

| 工具名 | 必填参数 | 行为 |
|-------|---------|-----|
| `planning_create_labor_plan` | `project_context_id`, `build_scale_case_id`, `positions[]` | 每个岗位含：category/name/headcount/avg_wage_yuan/welfare_rate(0~1)/evidence_bindings |
| `planning_infer_labor_plan` | `project_context_id`, `build_scale_case_id`, `position_requirements[]` | 按 annual_workload/capacity_per_person_shift/shift_count/coverage_factor/automation_adjustment 推导人数 |
| `planning_validate_labor_plan` | `labor_plan_id` | 校验岗位/人数/工资/福利/计算轨迹 |
| `planning_confirm_labor_plan` | `labor_plan_id`, `confirmation_reason` | 确认并生成工资福利 FinanceSpec ledger |

**政策基础**：

| 工具名 | 必填参数 | 行为 |
|-------|---------|-----|
| `planning_prepare_policy_basis` | `project_context_id`, `candidates[]` | 分类政策来源为 applicable/pending_verification/excluded/expired |
| `planning_confirm_policy_basis` | `policy_basis_id`, `selected_candidate_ids[]`, `selection_reason` | 固化（过期或排除候选不可选） |

**方案比选**：

| 工具名 | 必填参数 | 行为 |
|-------|---------|-----|
| `planning_prepare_option_comparison` | `project_context_id`, `category`(equipment/building/process/site/operating_model), `criteria[]`, `options[]` | 多维加权评分；criteria 含 weight/direction(higher_is_better或lower_is_better) |
| `planning_validate_option_comparison` | `option_comparison_id` | 校验方案/指标/强制约束 |
| `planning_score_option_comparison` | `option_comparison_id` | 读取已固化评分（不重新评分） |
| `planning_confirm_option_comparison` | `option_comparison_id`, `selected_option_id`, `rejected_option_ids[]`(≥1个), `selection_reason`(≥10字) | 固化选择 |

---

## 财务层服务

### finance-calc（确定性计算器）— 已下线的兼容包装器

**服务版本**: 0.1.0 | **实现深度**: 兼容层，纯函数 | **传输层**: 旧 `StdioServer`（非 `OfficialStdioServer`）

> **不是对外服务，不计入 14 个之内**。源码头注释原文："Compatibility MCP wrapper for the internal deterministic calculator. The public process is no longer registered in user configuration. Keeping this thin wrapper for one migration version allows old parity checks and local callers to reach the same internal functions used by `finance_calculate`."
>
> 本节保留是为了说明迁移关系：这 7 个函数现由 `lvke-finance-model.finance_calculate` 的 `operation` 参数统一暴露，集成测试逐 operation 断言两者响应相等。**新流程不应调用本服务**。

工具由 `CALCULATOR_HANDLERS` / `CALCULATOR_INPUT_SCHEMAS` 字典驱动注册，共 7 个：

| 工具名 | 内部 operation | 行为 |
|-------|---------------|-----|
| `calc_irr` | `irr` | 逐年现金流求 IRR，第 0 年起且投资为负 |
| `calc_npv` | `npv` | 折现率 + 逐年现金流求 NPV |
| `calc_xirr` | `xirr` | 显式 ISO 日期 + Actual/365 口径 |
| `calc_xnpv` | `xnpv` | 显式 ISO 日期 + Actual/365 口径 |
| `calc_break_even` | `break_even` | 量价盈亏平衡点与安全裕度 |
| `payback_period` | `payback_period` | 静态 + 动态投资回收期 |
| `sensitivity_analysis` | `sensitivity` | 对 IRR 单因素敏感性扫描 + 弹性系数 |

---

### lvke-finance-model（财务模型服务）

**服务版本**: **0.3.0**（与 deep-research 并列为唯一两个非 0.1.0 的服务） | **实现深度**: 完整实现 | **代码规模**: server.py 2,860 + `domains/finance` 28,020 行（全系统最大的域：`finance_model.py` 3,684 / `table_render.py` 3,081 / `vendor_import.py` 2,302）

**服务定位**：确定性财务模型的唯一入口。工具内部**不调用 LLM 做算术**。缺输入时返回 `missing_inputs` 结构，不伪造十三表。

**治理链条**（源码验证的对象流）：
```
FactPack (候选) ──confirm──> FactPack (formal_candidate)
      │
      ▼
FinanceSpec (候选) ──confirm──> FinanceSpec (confirmed revision)
      │
      ├─ BasisOfEstimate（每个重大输入的方法/理由/locator/hash/证据资格）
      ▼
FinanceRun ──> 十三表 manifest + indicators + checks
      │
      ├─ BalanceSheet（披露账面权益组成 + 计算残差 + 勾稽差额）
      └─ MonteCarlo（seeded，仅固化 P5/P50/P95 与失败统计）
```

**工具列表**：

| 工具名 | 类型 | 核心参数 | 实际行为 |
|-------|-----|---------|---------|
| `finance_calculate` | 只读 | `operation`, `inputs{}` | 调用 finance-calc 同一批纯函数（irr/npv/xirr/xnpv/break_even/payback_period/sensitivity）；不创建 FinanceRun |
| `finance_prepare_fact_pack` | 写 | `fact_pack{}`(version/evidence_policy/domains/evidence), `idempotency_key` | 规范化并固化 `finance_fact_pack.v1` 候选；`source_reconstructed` 模式必须绑定已导入快照 + hash + locator + method |
| `finance_confirm_fact_pack` | 写 | `fact_pack_id`, `idempotency_key` | 服务端复核深度和逐事实来源绑定 → `formal_candidate`。注意 `source_reconstructed` 只是把 `project_fact_certified` 的**缺省值**置为 false（`raw.get("project_fact_certified", evidence_policy != "source_reconstructed")`），调用方在 fact_pack 里显式传 `true` 时本层不会强制翻回 false——真正的拦阻在下游 formal 门禁 |
| `finance_get_analysis` | 只读 | `kind`(balance_sheet/monte_carlo/basis_of_estimate/fact_pack), `target_id` | 按类型读取已固化高级财务分析，不重算 |
| `finance_prepare_spec` | 写 | `workspace_id`, 可选 `spec{}` / `input_revision{}` / `fact_pack_id` / `evidence_pack_ids[]`, `strategy` | 准备/复用 FinanceSpec；返回 `spec` + `spec_hash` + `assumptions_to_confirm` + `missing_inputs`；不调用内置 LLM |
| `finance_validate_spec` | 只读 | `spec{}`, `for_formal` | 校验结构、数值、可选正式交付缺项；**不计算任何财务指标** |
| `finance_confirm_spec` | 写 | `spec_id`, `idempotency_key`, `note` | 固化为新已确认修订，不原地改写候选 |
| `finance_build_basis_of_estimate` | 写 | `spec_id`, `planning_object_ids[]`, `evidence_pack_ids[]`, `entries[]`, `idempotency_key` | 每个 entry 必须含 `target_pointer`(指向 /spec/ 或 /input_revision/)、`method`、`selection_reason`(≥10字)、`locator`、`content_hash`、`evidence_eligibility` |
| `finance_run_model` | 写 | `workspace_id`, `idempotency_key`, 可选 `spec_id`/`spec`/`input_revision`, `mode`, `selected_scenario_id`, `valuation_date` | 运行确定性模型，返回 `run_id` + `indicators` + `checks` + `table_manifest` |
| `finance_get_run` | 只读 | `run_id`, `view`(summary/full/tables/checks) | 纯查询，不重算、不写库 |
| `finance_build_balance_sheet` | 写 | `run_id` | 仅从已通过勾稽的 FinanceRun 派生；**同时披露账面权益组成、计算权益残差及勾稽差额，不用残差静默补平** |
| `finance_run_monte_carlo` | 写 | `run_id`, `distributions[]`(≤3), `seed`, `sample_count`(10~10000) | 分布限 3 个字段：`revenue_scale`/`operating_cost_scale`/`construction_scale`；支持 uniform/triangular/normal；样本只在内存重算 |
| `finance_list_analyses` | 只读 | `workspace_id`, `resource_type`(all/balance_sheet/monte_carlo/basis_of_estimate/fact_pack) | 分页列出高级分析 Resource |
| `finance_read_analysis_resource` | 只读 | `uri`(须匹配 `^lvke://finance-model/workspaces/`) | 按 URI 读取同工作区分析 Resource |
| `finance_import_vendor_review` | 写 | `xlsx_path`, `cohort_xlsx_paths[]`, `valuation_date` | 导入甲方 xlsx 为只读公式参考档，检测本金重复/手工IRR/僵尸公式，用确定性模型重算生成双轨对照；**甲方原值永不作为对外数字源** |
| `finance_generate_package` | 写 | 多参数 | **[DEPRECATED]** 巨型组合入口；新流程应显式调用 `finance_run_model` → `tables_render` |

**Monte Carlo 分布 Schema**（严格 oneOf，字段固定）：
```json
{"field": "revenue_scale", "distribution": "triangular", "low": 0.8, "mode": 1.0, "high": 1.2}
{"field": "operating_cost_scale", "distribution": "normal", "mean": 1.0, "stddev": 0.1, "low": 0.7, "high": 1.3}
{"field": "construction_scale", "distribution": "uniform", "low": 0.9, "high": 1.15}
```

---

### lvke-finance-tables（十三表服务）

**服务版本**: 0.1.0 | **实现深度**: 完整实现

**服务定位**：**只消费 `run_id`**，绝不重新计算财务。渲染、校验、导出十三张标准财务表。

**固定表注册表**：权威 canonical 形式（`DELIVERY_TABLE_KEYS`）**全部是连字符写法**，`table_registry()` 直接遍历它，Resource URI 与 `_canonical_table_id()` 的归一化目标也都是连字符。实测正好 13 张：

`investment` | `interest-during-construction` | `working-capital` | `funding` | `income-statement` | `total-cost` | `wage` | `depreciation` | `amortization` | `profit-distribution` | `debt-service` | `cashflow` | `capital-cashflow`

下划线写法只是 `_TABLE_ID_ALIASES` 的 **7 个输入别名**（`construction_interest` / `working_capital` / `income_statement` / `total_cost` / `profit_distribution` / `debt_service` / `capital_cashflow`），由 `_canonical_table_id()` 映射到连字符 canonical。对外 `table_id` 枚举是两者的并集（20 个取值），但底层只有 13 张表。

**工具列表**：

| 工具名 | 类型 | 核心参数 | 实际行为 |
|-------|-----|---------|---------|
| `tables_render` | 写 | `run_id`, `format`(structured/markdown), `template_version` | 渲染十三表并固化 package；绝不重算财务 |
| `tables_validate` | 只读 | `run_id`, `validation_scope`(technical/**formal** 默认) | 校验 manifest 与必需表；formal 下任一 blocker 都返回业务失败 |
| `tables_export_xlsx` | 写 | `run_id`, `template_version` | 从同一 run_id 导出带 lineage 的 XLSX；不接收散乱财务参数 |
| `tables_export_csv` | 写 | `run_id`, `template_version` | 原生导出 **14 个** UTF-8 BOM CSV（13 张表 + `00_数据血缘.csv`）；只写标量单元格，不把 JSON 序列化入表。与 XLSX 对称：`csv_integrity` 不通过则 `validation_complete=False` 并报 `csv_delivery_quality_not_formal` |
| `tables_get_package` | 只读 | `finance_tables_package_id` | 读取已固化 package 摘要 |
| `tables_list_tables` | 只读 | `finance_tables_package_id`, `expected_run_id` | 列出固定表注册表与单表 Resource；不重新渲染 |
| `tables_get_table` | 只读 | `finance_tables_package_id`, `table_id`, `format`(structured/markdown/csv) | 按 table_id 读取单表 |
| `tables_validate_table` | 只读 | `finance_tables_package_id`, `table_id` | 局部校验单表；**结果不能替代整包勾稽或正式交付门禁** |

---

## 交付层服务

交付层有 4 个服务：`report-generation`（研报正文与 DOCX 工件）、`deliverable-review`（交付物审查）、`feasibility-delivery`（阶段编排）、`zero-material-delivery`（零材料 estimate_preview）。前两个负责"产出与审查"，后两个负责"编排与降级入口"。

### lvke-report-generation（研报生成服务）

**服务版本**: 0.1.0 | **实现深度**: 完整实现 | **代码规模**: server.py 421 行（薄）+ `domains/reports/` 5,979 行（artifacts.py 2,177 / doc_service.py 1,566 / application.py 1,149 / read_model.py 397 / readiness.py 253 / docx_fonts.py 183 / validation.py 146 / artifact_mirror.py 108）

**服务定位**：研报正文起草的**执行与完整性层，不是第二个写作模型**。`report_start` 的 docstring 直陈 "MCP is the execution and integrity layer, not a nested LLM client"——旧实现委托 web 报告生成器需要第二个 model gateway、产生不透明的二级 Agent 流程，现已改为 `report_propose → report_diff → report_apply` 的确定性交接，正文由调用方 Agent 起草。三条自我约束：

- **不把工程校验冒充专业审查**：`report_validate_section` 恒返回 `validation_complete=False`，并自述"章节局部校验不能替代整篇 report_validate 与统一交付审查"。
- **不让草稿冒充终稿**：`kind=draft` 的工件强制前置水印页 `DRAFT_MARKER = "专家参考稿/内部复核·非报批终稿"`，并把全部阻断项与警告写入正文与不可变 manifest。
- **不提供删除入口**：13 个工具无一能删除 preparation/revision/artifact，工件失效只标记 `status=invalidated` 而不清盘；无任何 `destructiveHint=True` 的工具。

**工具列表**（13 个，8 只读 / 5 写）：

| 工具名 | 类型 | 核心参数 | 实际行为 | 创建对象 |
|-------|-----|---------|---------|---------|
| `report_prepare` | 只读* | `workspace_id`, `evidence_pack_ids[]`(≤100), `research_package_ids[]`(≤100); 可选 `finance_binding{kind,run_id,package_id}`, `outline[]`(≤100) | 逐个回查 EvidencePack/ResearchPackage 存在性，按 `finance_binding.kind` 分流查通用 finance 或 asset_acquisition 后端，校验表包 run_id 归属，归一化 outline 为稳定 section 描述符。返回 `draft_ready`（可启动草稿）与 `formal_ready`（上游形式完备）两个独立状态；partial 表包/partial 研究可让 `draft_ready=true`，但必为 `formal_ready=false`，不得据此发布正式工件。必要绑定 blockers 仍 fail-closed；即便 blockers 非空也固化落盘（status=blocked, success=False） | `ReportPreparation`（rprep_） |
| `report_start` | 写 | `workspace_id`, `report_preparation_id`; 可选 `document_snapshot{content}` | preparation 缺失或 `blockers` 非空即拒。返回 `status=agent_action_required` + warning「MCP 不调用内置 LLM；正文由当前 Agent 起草」 | `ReportRevision`（rrv_，task_status=agent_drafting） |
| `report_status` | 只读* | `workspace_id`, `task_id` | 重抓当前文档快照并固化一条新 revision（task_status=agent_drafted）。legacy `gen_task` 分支是死路径（见已知限制） | `ReportRevision`（rrv_） |
| `report_list_sections` | 只读 | `workspace_id`, `report_revision_id` | 读 preparation 冻结的稳定章节描述符。**不读正文**，因此列出的章节可能尚未在正文中存在 | 无 |
| `report_get_section` | 只读 | `workspace_id`, `report_revision_id`, `section_id` | 按稳定 `section_id` 定位描述符，用标题在正文做 span 切片。outline 已固化但正文无对应标题时返回 `found_in_document=False` + warning，而非报错 | 无 |
| `report_propose` | 写 | `workspace_id`, `summary`, `proposed_content`, `basis{report_preparation_id,basis_hash,report_revision_id}` | 三重绑定校验（preparation 存在 + `basis_hash` 定长比较 + revision 双向绑定）。落盘只写服务端重建的 `verified_basis`，**不采信客户端 basis 的其余字段** | `AgentProposal`（prop_） |
| `report_propose_section` | 写 | 同上 + `section_id` | 单章补丁合并成完整文档：span 命中则原位替换并保留尾部空白；span 缺失（冻结 outline 先于正文存在）则按描述符 `order` 插到下一个已存在章节之前。强制 `merged_document_hash == sha256(proposed_content)` | `AgentProposal`（prop_） |
| `report_diff` | 只读 | `workspace_id`, `proposal_id` | `difflib.HtmlDiff(wrapcolumn=80).make_file(context=True, numlines=3)` 生成完整 HTML 页面。**apply 前必须调用** | 无 |
| `report_apply` | 写 | `workspace_id`, `proposal_id`; 可选 `enforce_structure`(默认 true) | 六项合一的新鲜度校验，任一不符统一返回 `proposal_basis_stale_or_mismatch`；单章提案再校验目标章节内容哈希未漂移（`section_patch_stale`） | `ReportRevision`（rrv_，串 parent） |
| `report_validate` | 只读 | `workspace_id`, `report_revision_id` | 四项确定性检查：结构、正文数字↔run 指标（容差 `max(0.05, abs(exp)*1%)`）、财务发布门禁（strict）、readiness 四维评分 | 无 |
| `report_validate_section` | 只读 | `workspace_id`, `report_revision_id`, `section_id` | 章节四检：标题存在、正文非空、无占位符、每条定量陈述有邻近引用（句级窗口 ±1）。**恒 `validation_complete=False`** | 无 |
| `report_get_readiness` | 只读 | `workspace_id`; 可选 `report_revision_id`（省略取最新） | 整体委托 validate() 后重排为 readiness 视图，不固化新对象 | 无 |
| `report_export_docx` | 写 | `workspace_id`, `report_revision_id`; 可选 `kind`(draft/formal_candidate), `mirror_to_project` | `formal_candidate` 先跑 validate()，`valid=False` 即 `report_validation_blocked`。三次 basis 指纹冻结 + 三次 formal 断言 + 字体规范化 ×2 + 落盘前后各一次完整性校验 + 字体审计 | `DeliverableArtifact`（deliverable_{32hex}） |

> \* `report_prepare` 与 `report_status` 标注 `readOnlyHint=True`，但两者都调用 `STORE.put` 固化不可变对象。注解与实际写行为不一致，见「已知限制」。

**readiness 四维评分**（`readiness.py`）：

```
weights = {"structure": 0.20, "data": 0.30, "argument": 0.30, "expression": 0.20}
manual_review_required = score < 85 or bool(c_level)
```

**正式工件（formal_candidate）门禁链**（逐条 fail-closed）：

1. `report_validate` 必须 `valid=True`，否则 `report_validation_blocked`——不进入工件生成。
2. `doc_kind` 必须为 `feasibility` 且 `report_type != asset_acquisition`，否则 `FORMAL_ARTIFACT_TYPE_UNSUPPORTED`（资产收购正式工件归 `lvke-asset-acquisition`）。
3. `readiness.publishable` 必须严格 `is True` 且 `blockers` 为空，否则 `FORMAL_READINESS_BLOCKED`。
4. 财务门禁返回的 `bound_run_id` 必须与工件 basis 的 `run_id` 一致，否则 `FINANCE_GATE_BOUND_RUN_MISMATCH`——防止"门禁通过了另一个 run"被当作本工件的通过。
5. 导出期三次 `_capture_basis` 指纹比较（生成前、装配前、落盘后），任意两次不等即 `BASIS_CHANGED_DURING_EXPORT` 并 `rmtree` 已生成目录。
6. 工件目录先写临时目录 → `_verify_files` → `os.replace` 原子改名 → 再 `_verify_files`。反向检查 `rglob` 实际文件与索引求差，多出的算 `UNINDEXED_ARTIFACT_FILE`。
7. DOCX 字体三道关：生成时 normalize → 写完元数据再 normalize → 读回落盘文件 `audit_docx_fonts`，locale 字体残留或审计抛异常一律 `docx_font_audit_failed`。

**工件是"重新验证式"读取**：`get_artifact` 每次都在 `FileLock` 下重算 basis，用 `_basis_change_reasons` 逐项比对 14 类漂移原因（`DOCUMENT_REVISION_CHANGED` / `DOCUMENT_CONTENT_CHANGED` / `SOURCE_FILES_CHANGED` / `PUBLISH_READINESS_CHANGED` 等），发现漂移即持久化写回 `status=invalidated`、`current=false`。

**已知限制**：

- **`report_export_docx` 的 `report_revision_id` 不决定导出内容**。`artifacts._document_snapshot(workspace_id)` 只接受 workspace_id，读的是 `workspace_meta.json` 的 `current_revision_id`；传入的 revision_id 仅用于存在性检查与 formal 的 validate。若工作区指针已前移，指定旧 revision 仍导出最新正文，且 formal 校验的是旧修订、落盘的是新正文。
- **13 个工具全无 `idempotency_key`**。写幂等完全依赖内容寻址去重，但 payload 含 `document_snapshot` 等易变字段，实践中重复调用通常产生新对象而非幂等命中。`artifacts` 层的 `operation_id` 重放机制在 MCP 路径上不可达（`export_docx` 不传该参数）。
- **只读工具有隐式建仓副作用**：`report_validate` / `report_get_section` / `report_validate_section` / `report_get_readiness` 经 `doc_service.ensure_workspace` 会为未初始化的工作区创建 `workspace_meta.json` 并落一条 `source=bootstrap` 的初始大纲修订。首次调用只读工具即可无声建出一个工作区。
- **死路径与死字段**：`report_status` 的 legacy 分支依赖 `load_gen_task`，而 `save_gen_task` 与 `BINDING_STORE.put` 全仓无调用者（已 grep 确认），该分支只能返回 `task_not_found`；`report_prepare` 读 `args["project_metadata"]` 但该字段不在 `additionalProperties: False` 的 schema 内，恒为 `{}`；`report_start` 的 `chapters` 参数只记入 payload，无任何消费者。
- **`finance_binding` 在本服务无持久化写入口**：`artifacts.bind_finance_run` 存在但本服务 13 个工具都不调用（它只被 `domains/asset_acquisition/` 调用）。`_capture_basis` 因此走降级路径绑定"工作区最新 run"，与 `report_prepare` 声明的 `finance_binding.run_id` 之间没有强制一致性检查。
- **附表门禁实际不可达**：`_GOVERNED_SNAPSHOTS` 只含 `evidence_pack`，`appendix_manifest` 从不被快照，故 `appendix_files` 恒为空列表，全部附表相关门禁形同虚设。
- **`audit_no_run` 已 fail-closed**：财务已接地但无测算留痕时直接记 blocker；代码不读取 `LVKE_STRICT_AUDIT`。
- **DOCX 生成会 fork 外部进程**：`subprocess.run(pandoc, timeout=60)`，缺失或失败时静默回退 python-docx。两条路径产出的 DOCX 保真度不同，而 manifest 不记录实际走了哪条。
- **`export_docx` 返回服务器本地绝对路径** `deliverable_path`，与本服务其他部分"只暴露 `lvke://` URI"的口径不一致。
- **结构校验是双向包含判定**（`chapter in title or title in chapter`），源码注释自陈"宽松判定，容忍编号前缀差异"，短标题易被无关章节意外匹配而漏报缺章。
- `list_resources` / `resolve_resource` 实现完整但**未注册为工具**，只经 `lvke-feasibility-delivery` 的 `lvke_read_resource` 间接调用；server.py 的 next_actions 却提示调用一个并不存在的 `report_list_resources`。

---

### lvke-feasibility-delivery（可研交付编排服务）

**服务版本**: 0.1.0 | **实现深度**: 完整实现 | **代码规模**: 1,560 行（server.py 164 / service.py 1,304 / contracts.py 67 / store.py 24）

**服务定位**：把可研交付拆成 **11 个业务阶段 + 1 个终态**，用不可变 run 快照记录"哪个阶段引用了哪些真实对象"，并在 formal 范围做跨服务发布门禁。它**只存引用不存副本**——`_resolve_object` 的 docstring 写明 "Delivery orchestration stores references only; it never creates a shadow copy of a domain object"。它也**不生成任何交付物**：全目录无 docx/xlsx/openpyxl/requests 引用，不调用 `deliverable_dir`，十三表与 DOCX 一律由下游工具产出；formal 门禁只去读下游服务已固化的结论，不重算财务、不撰写研报、不做质量审计。

**阶段枚举**（`contracts.py`，12 项）：

```
project → research → market → option → scale → drivers
→ finance_spec → finance_run → finance_tables → report → review → released
```

> `feasibility_stage` 的 `stage` 参数 enum 取 `STAGES[:-1]`，即 **11 个可显式设置的阶段**；`released` 是终态，只能由 `feasibility_release` 产生。

**阶段状态**：`pending` | `in_progress` | `partial` | `blocked` | `completed` | `stale`
**Run 状态**：`in_progress` | `partial` | `blocked` | `completed` | `stale` | `released`
**交付模式**：`estimate_preview` | `review_candidate` | `formal_release`
**发布范围**：`process_acceptance` | `project_delivery`
**证据策略**：`formal_evidence` | `source_reconstructed` | `technical_fixture` | `controlled_assumption`

**每阶段必需的输出对象类型**（`stage` 与 formal 门禁共用同一张表）：

| 阶段 | required output kind |
|-----|---------------------|
| `project` | `ProjectContext` |
| `research` | `ResearchPackage` |
| `market` | `MarketSizingCase` |
| `option` | `OptionComparison` |
| `scale` | `BuildScaleCase` |
| `drivers` | `CostDriverSet` + `LaborPlan` + `RevenueDriverSet`（**三者缺一即 `stage_output_type_invalid` + `missing:<Kind>`**） |
| `finance_spec` | `FinanceSpec` + `BasisOfEstimate` |

**工具列表**（10 个，5 只读 / 5 写）：

| 工具名 | 类型 | 核心参数 | 实际行为 | 创建对象 |
|-------|-----|---------|---------|---------|
| `feasibility_start` | 写 | `workspace_id`, `delivery_mode`, `idempotency_key`; 可选 `project_context_id`, `evidence_policy`(默认 formal_evidence) | 校验三个枚举后固化初始 run。传了 `project_context_id` 但解析不到真实 ProjectContext 即 `project_context_not_found`；解析成功则 `project` 阶段预置 completed 并把 `current_stage` 推到 `research` | `FeasibilityDeliveryRun`（fdr_） |
| `feasibility_status` | 只读 | `workspace_id`, `delivery_run_id` | 原样返回 run 快照 + `current_stage` + `preview_only`。不做任何校验或推进 | 无 |
| `feasibility_stage` | 写 | `workspace_id`, `delivery_run_id`, `stage`, `status`, `idempotency_key`; 可选 `input_refs[]`, `output_refs[]`, `basis_hash`, `expected_basis_hash`, `reopen` | **不原地改写**：每次成功都 put 一个新 run 并回传新 `delivery_run_id` + `parent_run_id` | `FeasibilityDeliveryRun`（fdr_） |
| `feasibility_next_actions` | 只读 | `workspace_id`, `delivery_run_id` | 优先用阶段自存 `next_actions`，否则查内置 `_NEXT_TOOLS` 静态映射，产出 `{tool, arguments, reason}` 可执行描述符。**这张表就是官方推荐调用顺序的权威来源** | 无 |
| `feasibility_validate` | 只读 | `workspace_id`, `delivery_run_id`; 可选 `scope`(technical/**formal**，默认 technical) | technical 只查阶段顺序、completed 阶段的 refs 与 basis_hash 齐备性、引用可解析性；partial/blocked/stale 仅记 warnings。formal 额外要求每阶段都 completed 并叠加全部跨服务门禁 | 无 |
| `feasibility_checkpoint` | 写 | `workspace_id`, `delivery_run_id`, `idempotency_key`; 可选 `reason` | 只记 run_id、basis_hash、current_stage、reason、created_at，**不复制阶段数据** | `FeasibilityCheckpoint`（fdc_） |
| `feasibility_resume` | 写 | `workspace_id`, `checkpoint_id`, `idempotency_key` | 深拷贝旧 run payload 生成新 run：`parent_run_id` 指向旧 run、status 重置 in_progress、清空 next_actions。旧 run 与历史修订不变 | `FeasibilityDeliveryRun`（fdr_） |
| `feasibility_release` | 写 | `workspace_id`, `delivery_run_id`, `idempotency_key`; 可选 `release_scope`, `release_note` | **自己重跑一遍 formal 校验，不信任先前 validate 结果**。命中 `project_fact_evidence_missing` 时单独返回该 code 并提示改用 `process_acceptance` | `FeasibilityRelease`（fdrp_） |
| `lvke_list_resources` | 只读 | `workspace_id`, `domain`; 可选 `resource_type`, `cursor`, `limit`(1-200，默认 50) | 共享 `runtime.resource_registry` 的**跨域分页转发器**，按 domain 委派给对应域的原生 list 实现，不转换 URI/记录/二进制 | 无 |
| `lvke_read_resource` | 只读 | `workspace_id`, `uri`(≤8192) | 从 URI authority 段识别领域后委派读取，强制 `record.workspace_id == 入参`，跨工作区读取 fail-closed | 无 |

> `resource_registry.DOMAINS` 覆盖 11 个域，因此这两个工具是**全系统唯一的动态 Resource 访问入口**——其他服务（如 report-generation）刻意关闭了协议层 Resource 通道，因为"协议层 Resource 调用没有 workspace 身份"。

**引用校验四类拒绝**（`_validate_reference`，每个 `input_refs`/`output_refs` 都过）：

1. 解析不到且 URI 内嵌 workspace 与入参不符 → `<stage>_<role>_ref_wrong_workspace`（此前误报 not_found，P1-017 已修）；否则 `ref_not_found`。
2. `content_hash` 不以 `sha256:` 开头 → `content_hash_missing`。
3. `content_hash != sha256_json(payload)` → `content_hash_mismatch`。
4. `basis_hash` 不以 `sha256:` 开头 → `basis_hash_missing`。

**上游重开的级联失效**（全系统唯一的变更传播机制）：`reopen=true` 或回退阶段时，`STAGES[target+1:]` 中除 `released` 外全部置 `status=stale`、`warnings=["upstream_stage_reopened"]`、`blockers=["downstream_invalidated"]`，并在响应回传 `stale_stages`。

**formal 范围的跨服务门禁**（择要，全部 fail-closed）：

- **estimate_preview 不可正式发布**：formal 校验遇 `delivery_mode=estimate_preview` 直接加 `preview_cannot_formal_release`；且 `start` 时该模式会把 `project_delivery` **静默降级**为 `process_acceptance`。
- **证据资格**：formal + `controlled_assumption` → `controlled_assumption_formal_forbidden`；formal + `project_delivery` + `source_reconstructed` → `project_fact_evidence_missing`；`source_reconstructed` 且 `project_fact_certified=True` → 拒绝。
- **研报九章硬门禁**：正文里 `第1章`…`第9章` 命中数 < 9 → `report_nine_chapters_required`；`section_lineage` 中同时具备 `upstream_refs` + `citation_locators` + `upstream_basis_hashes` 完整绑定的章节 < 9 → `report_section_lineage_incomplete`。
- **跨服务绑定一致性**：`finance_run` 的 spec_id/spec_hash 必须落在 `finance_spec` 阶段输出内；`finance_tables` 的 run_id 必须等于 `finance_run_ids[0]`；`report` 的 `upstream.run_id` 与 `upstream.finance_tables_package_id` 必须分别对上，否则 `*_binding_mismatch`。
- **下游结论复核而非复算**：`finance_tables` 若 `integrity.status != passed` 则调 `tables_service.validate(scope=formal)`；`report` 调 `domains.reports.validation.validate_report`，无效即 `report_readiness_failed`，抛异常即 `report_readiness_unverifiable`。
- **review 阶段**：`active_blocking_finding_ids` 非空 → `review_open_blocker`；findings 中存在 `open`/`pending`/`needs_revision`/`in_progress` → `review_open_finding`；`validation_complete=False` → `review_not_complete`。

**幂等**：4 个写工具全部强制 `idempotency_key`。键先 sha256 再与 `sha256_json(request)` 一起比对——同键不同请求返回 `idempotency_conflict`，同键同请求重放原响应并加 `idempotent_replay: true`。全程持 workspace 级 `FileLock`（timeout=30s）。

**已知限制**：
- `feasibility_stage` 的 `completed` 判定只校验引用可解析与类型齐备，不校验被引对象自身的业务有效性；深层内容正确性推给 formal 门禁。
- 跨服务 resolver 曾不一致（MCP-P1-017），技术阶段可把不存在的 URI 登记为 completed。

---

### lvke-zero-material-delivery（零材料交付服务）

**服务版本**: 动态取 `service.SERVICE_VERSION`（非字面量） | **实现深度**: 部分实现 | **代码规模**: 2,794 行（service.py 1,813 / artifact_delivery.py 378 / acceptance.py 319 / server.py 207 / industry_profiles.py 77）

**服务定位**：在甲方**一份原始材料都没有**的前提下，从一句话推出行业路线、生成受控假设包，再**只经既有领域边界**（research / project_planning / finance_model / finance_tables / reports）跑完财务与十三表，最后固化技术预估研报。`execute()` 的 docstring 写明 "Execute only through existing domain boundaries; never grant release"。

**它不认证项目事实，且这一点是硬编码的**：零材料轨全部返回路径写死 `validation_complete=False` 与 `input_evidence_complete=False`；AssumptionPackage 的 `evidence_boundary` 固定为 `{"grade": "C", "production_claim_allowed": False}`；EvidenceManifest 固定写入 `controlled_assumptions_are_evidence: False` 与 `formal_evidence_ready: False`。正式验收须走拟定模板包 → `delivery_confirm_formal_promotion` → **新建** `pctx_*` / `fdr_*` 链，零材料 `zmr_*` 不原地升级。

**工具列表**（10 个；`delivery_generate_template_pack` / `delivery_confirm_formal_promotion` 为晋升入口，`delivery_transition` 聚合 cancel/resume）：

| 工具名 | 类型 | 核心参数 | 实际行为 | 创建对象 |
|-------|-----|---------|---------|---------|
| `delivery_create_from_sentence` | 写 | `workspace_id`, `sentence`(2-4000), `idempotency_key`; 可选 `project_name`/`region`/`industry`/`project_nature`/`report_type` | `_resolve_route` 对行业规则做关键词子串打分：`explicit_industry` 精确等于 code 或 label 直接命中；strong_keywords 命中 >1 条 → `ambiguous_route`；无命中 → `missing_route` | `DeliveryIntent`(zmi_) + `DeliveryRun`(zmr_) |
| `delivery_start` | 写 | `workspace_id`, `delivery_run_id`, `idempotency_key` | **本服务唯一的重活入口，同步执行**。串起 research→planning→finance→tables→reports 全链 | `AssumptionPackage`(zma_)、`TechnicalReport`(zmrep_)、各 register/manifest |
| `delivery_status` | 只读 | `workspace_id`, `delivery_run_id` | 返回 run 全视图、stage、progress、resume_token。progress 按 11 段固定阶段表算 `round(index*100/10)` | 无 |
| `delivery_get` | 只读 | `workspace_id`, `object_id` | 按 `_RESOURCE_STORES` 顺序遍历 store，首个命中即返回 | 无 |
| `delivery_list_assumptions` | 只读 | `workspace_id`, `assumption_package_id`; 可选 `limit`(5-10，默认 10) | 按 `confirmation_priority_score` 降序排列全部假设字段，`confirmation_items` 只取 `confirmed` 为假的前 limit 条 | 无 |
| `delivery_confirm_assumptions` | 写 | `workspace_id`, `assumption_package_id`, `confirmations[]`(1-20，每项 `name`+`value`), `idempotency_key` | 复合入口：确认 + 自动重算。`name` 不在原包字段内即 `unknown_assumption_field`；命中字段改写为 `source_type=user_confirmed`、`method=user_override` | `AssumptionPackage`(revision+1) + 新 Run |
| `delivery_generate_template_pack` | 写 | `workspace_id`, `delivery_run_id`, `idempotency_key` | 按适用标准需求生成拟定模板包（MD+JSON），不调用 LLM | 拟定模板包 |
| `delivery_confirm_formal_promotion` | 写 | `template_pack_id`, `responsible_party`, `confirmation_note`, `idempotency_key` | 确认晋升为 `sim_a_formal` 并导入**新**可研链资料；只返回 `next_actions`，不调用 `feasibility_release` | SourceFile + 晋升记录 |
| `delivery_get_artifacts` | 只读 | `workspace_id`, `delivery_run_id` | 把 `object_refs` 逐一试解拼成 `resource_uris`，另返回 `artifact_uris` 与 `manifest_uri` | 无 |
| `delivery_transition` | 写 | `workspace_id`, `operation`(cancel/resume), `delivery_run_id`, `idempotency_key`; cancel 必填 `reason` | cancel 新建已取消快照且不删除工件；resume 仅允许从 cancelled Run 创建恢复快照 | `DeliveryRun` |

**已知限制**（本服务是全系统限制最多的一个）：

- **行业档**：已含旅游餐饮、制造、环境公用、园区基建、城轨、房地产、墓地等七档 profile。路由仍是朴素子串匹配，`confidence` 是计数公式，不代表统计置信度。
- **协议 Resource 枚举已 fail closed**：无 workspace 身份的 `resources/list` 动态 lister 恒空；工作区对象枚举和读取只走带显式 `workspace_id` 的统一 Resource 工具通道，并保留 `resource_scope_mismatch` 门禁。
- **零材料 DOCX 表格**：`|` 行走 `docx.append_markdown_pipe_table`，不再整行丢弃。
- **5 个 stage 声明但永不可达**：`researching` / `report_ready` / `awaiting_confirmation` / `confirmed_estimate_ready` 在阶段表中定义但全服务无赋值点；`failed` 仅在守卫中被接受。`_stage_progress` 也无法区分 `cancelled`、`received` 与非法 stage（三者都返回 0）。
- **成功路径也永远带 blocker**：`execute()` 无条件追加 `research_evidence_pending` 与 `planning_market_evidence_pending`。
- **`resume_token` 无签名无有效期**：它只是记录的 `content_hash`，`delivery_resume` 只吃 `delivery_run_id`、从不校验 token。该字段是可读的一致性指纹，不构成恢复凭证（对比 `dr_resume` 的 `drresume.v1.<签名>`）。
- **幂等存储线性增长且无过期**：每次写操作全量读取该 workspace 的幂等记录逐条比对，且存的是**整份 response**（`delivery_start` 的 response 含约 29 条 artifact_uris）。既无 TTL 也无清理逻辑。
- **5 处 `next()` 无 default**：`service.py:539/540/541/828`，下游变体命名一改即抛未捕获 `StopIteration`，被 transport 兜成 `internal_error`。

---

### lvke-deliverable-review（交付物审查服务）

**服务版本**: 0.1.0 | **实现深度**: 完整实现，**仓内代码量最大的 server**（11,155 行 / 9 文件：service.py 5,230 / report_checks.py 2,035（51 个检查函数）/ financial_checks.py 1,741（15 个复算函数）/ rules.py 890 / server.py 651 / rubrics.py 324 / contracts.py / store.py）

**服务定位**：对财务表、研报、联合交付包做规则化审查与整改复测。核心诚实性约束是**永不输出"项目已符合国家标准"**——`review_validate_standards` 只汇总"待补证 / 技术夹具满足 / 已附真实证据待专业复核"三态，把合规判断留给持证专业人员。

**工具列表**（15 个，8 只读 / 7 写）：

| 工具名 | 类型 | 核心参数 |
|-------|-----|---------|
| `review_prepare` | 写 | `workspace_id`, `idempotency_key`, `target`; 可选 `project_context`, `rule_pack_ids[]`, `industry_overlays[]` |
| `review_start` | 写 | `workspace_id`, `idempotency_key`, `review_preparation_id`; 可选 `mode`(quick/deep，默认 quick), `execution`(sync/async), `deployment_mode`(enforced/shadow，默认 enforced) |
| `review_get` | 只读 | `workspace_id`, `review_id` |
| `review_list_findings` | 只读 | `workspace_id`, `review_id`; 可选 `severity`(P0-P3), `status`, `category`, `location`, `cursor`, `limit`(≤200) |
| `review_get_finding` | 只读 | `workspace_id`, `review_id`, `finding_id` |
| `review_disposition_finding` | 写 | `workspace_id`, `review_id`, `finding_id`, `disposition`（4 个 oneOf 分支，见下） |
| `review_retest` | 写 | `workspace_id`, `idempotency_key`, `review_id`, `target`, `remediation_evidence[]`(**minItems 1, maxItems 200，全部必填**) |
| `review_compare_assessments` | 只读* | `workspace_id`, `before_assessment_id`, `after_assessment_id` |
| `review_list_rubrics` | 只读 | `workspace_id`; 可选 `project_context`（直接返回模块级常量 `RUBRIC`，不读任何存储） |
| `review_score_section` | 只读* | `workspace_id`, `report_revision_id`, `section_id`; 可选 `rubric_id`(常量 `feasibility-section`) |
| `review_resolve_standards` | 写 | `workspace_id`, `project_context`, `facilities[]`(≤500)；**`idempotency_key` 在 schema 里非必填** |
| `review_list_requirements` | 只读 | `workspace_id`, `standard_applicability_id` |
| `review_attach_requirement_evidence` | 写 | `workspace_id`, `idempotency_key`, `standard_applicability_id`, `requirement_id`, `resource_uri`, `locator`, `content_hash`, `evidence_track` |
| `review_validate_standards` | 只读 | `workspace_id`, `standard_applicability_id` |
| `review_export` | 写 | `workspace_id`, `idempotency_key`, `review_id`, `formats[]`(json/markdown/docx/xlsx) |

**关键枚举**：
- `severity`：`P0` | `P1` | `P2` | `P3`（排序权重 P0=0 … P3=3）
- finding `status`**声明** 9 值：`open` | `confirmed` | `rejected` | `remediation_in_progress` | `false_positive_appeal` | `waiver_requested` | `waived` | `resolved` | `superseded`
- **但只有 6 个可达**（见「已知限制」）：`review_disposition_finding` 的别名表把 12 个入参归一到 **5 个** status，复测另写 `remediation_in_progress`：

  | disposition 入参别名 | 归一后 status |
  |---|---|
  | `confirm` / `confirmed` | `confirmed` |
  | `remediate` / `remediation_in_progress` | `remediation_in_progress` |
  | `reject` / `rejected` / `false_positive` / `false_positive_appeal` | `false_positive_appeal` |
  | `appeal_waiver` / `compliance_waiver` / `waiver_requested` | `waiver_requested` |
  | `resolve` / `resolved` | `resolved` |

  注意 `reject` 归到的是 `false_positive_appeal`（误报**申诉**）而非 `rejected`——驳回只是发起申诉，不是终态。
- `mode`：`quick`（强制同步）| `deep`（可异步，起 daemon 线程）
- `deployment_mode`：`enforced` | `shadow`（影子模式只记录不阻断）
- `rule_pack_ids` / `industry_overlays`（同一套 10 值）：`core-deliverable` | `finance-core` | `report-core` | `combined-core` | `generic-feasibility` | `amusement-feasibility` | `asset-acquisition` | `hotel-acquisition` | `solar-acquisition` | `mineral-processing`
- `target_type`（9 值）：`finance_run` | `finance_tables_package` | `finance_xlsx` | `finance_xlsx_source` | `acquisition_run` | `acquisition_tables_package` | `report_revision` | `report_artifact` | `combined_deliverable`
- `project_type`：`generic_feasibility` | `asset_acquisition`
- `transaction_structure`：`new_build` | `operation_lease` | `asset_acquisition` | `equity_acquisition` | `ppp` | `other`
- `rubric_id`：常量 `feasibility-section`（`review_score_section` 的唯一取值）
- `asset_type`：`general` | `amusement_park` | `solar_power` | `hotel_lease` | `mineral_processing`

**Store 四元组**（6 个）：`preparations`(rvprep_) / `exports`(rvexp_) / `rubric_assessments`(rva_) / `rubric_comparisons`(rvc_) / `standard_applicabilities`(stdapp_) / `standard_evidence`(stdev_)

> `store.py` 里**没有** `JSONArtifactStore`（已确认 0 处引用）。它只含 `ReviewEventStore`——append-only 事件存储，路径 `mcp_objects/deliverable-review/events/{review_id}/{sequence:08d}.json`，带 `sequence` + `previous_event_hash` + `event_hash` 哈希链，`filelock` 串行化 append。审查状态是**从事件流投影出来的**，不是存在某个可变记录里。上表 6 个 store 声明在 `service.py`（4 个）与 `rubrics.py`（2 个）。

**评分机制**（`rubrics.py`，唯一 1 个 rubric）：`rubric_id="feasibility-section"`、`rubric_version="feasibility-section.v1"`、`pass_score=4.0`、`hard_floor=3`、`scorer` 字段硬编码 `"deterministic_rules"`。

| dimension | weight | dimension | weight |
|---|---|---|---|
| `data_support` | 0.18 | `internal_consistency` | 0.18 |
| `finance_binding` | 0.16 | `scale_reasonableness` | 0.12 |
| `compliance_boundary` | 0.12 | `risk_completeness` | 0.12 |
| `decision_readability` | 0.12 | | |

权重合计 1.00。**通过是双条件**：加权分 ≥ 4.0 **且**每个 applicable 维度 ≥ 3（hard_floor）。加权只对 `applicable=True` 的维度计算并按可用权重**重新归一**。

**"不调用隐藏 LLM"如何做到**：输入只有三个不可变读模型（`resolve_revision_record` / `get_section` / `validate_section`），打分是纯正则计数 + 阈值分段（每维 clamp 到 1–5），固化时 `basis` 绑定 `revision_basis_hash` + `section_content_hash`。同一 revision+section 必得同一分数与同一内容寻址 `object_id`。我对本服务 8 个 .py 全文 grep `openai|anthropic|llm|completion|prompt_template`，除正则字面量外无任何模型调用。

**shadow vs enforced**：两种模式**执行完全相同的规则**，findings 与 verdict 计算无差别。`shadow` 的额外产出是：`shadow_comparison`（对比 legacy gate 与统一结论，差异分类 `both_pass` / `both_fail` / `legacy_pass_unified_block` / `legacy_block_unified_pass` / `unavailable`）、`technical_verification_verdict`（该判定**排除** `manual_review_required=True` 的 finding），以及恒定追加的 blocker `shadow_mode_release_forbidden`——**影子模式永不可发布**。`evidence_track == "controlled_assumption"` 同样恒加 `controlled_assumption_release_forbidden`。

**"没跑到"永不算通过**：未执行的适用规则逐条写入 `incomplete_reasons` 为 `rule_not_executed:{rule_id}`，而 `verdict_for` 只要有 `incomplete_reasons` 就返回 `incomplete`。verdict 四值：`pass` | `conditional_pass` | `fail` | `incomplete`。

**财务复算容差**（`service.py` 层独立于 `financial_checks.py` 的复算）：

| 检查 | 规则 | 容差 |
|-----|-----|-----|
| 总投资 = 建设投资 + 建设期利息 + 流动资金 | `FIN.INVESTMENT.BALANCE` P0 | `max(0.01, abs(total)*1e-8)` |
| 资金来源 = 资本金 + 贷款 + 补贴 = 总投资 | `FIN.FUNDING.BALANCE` P0 | 同上 |
| 独立 IRR 复算 | `FIN.IRR.INDEPENDENT_RECALC` P0 | `0.01` 百分点 |
| 独立 NPV 复算 | `FIN.NPV.INDEPENDENT_RECALC` P0 | `max(0.01, abs(npv)*1e-8)` |
| 现金流符号变化 > 1 次 | `FIN.IRR.MULTIPLE_SIGN_CHANGES` P1 | — |

报告↔run 的数字复算容差不同：**IRR 类放宽到 0.5% 相对，其余 1e-6 相对**（`max(0.01, abs(expected) * (0.005 if metric in {project_irr, capital_irr} else 1e-6))`）。

**导出真实依赖**：DOCX = **python-docx**（纯内存 `io.BytesIO`），XLSX = **openpyxl**（单 sheet `findings`，18 列，`freeze_panes="A2"` + `auto_filter`）。**不依赖 LibreOffice**——`LVKE_REVIEW_SOFFICE` 与导出无关，它只用于 `FIN.XLSX.RECALC` 的深度重算（缺失则返回 incomplete `libreoffice_recalc_worker_unavailable`；重算在临时目录对副本 `chmod(0o400)` 只读操作、180s 超时，事后比对原文件 sha256，被改动即 `libreoffice_recalc_mutated_original`）。

**导出的不可变性**：`export_id = "rvexp_" + sha256(export_basis)[:24]`，basis 含 `event_chain_hash`——同一事件链同一格式集必得同一 export_id。落盘用 write-once：已存在且内容不同即 `immutable_export_conflict`。**导出前先校验既有全部导出记录与文件完整性**，任一失败即 `review_export_integrity_failed`；新记录写完再校验一遍。

**整改复测的语义**：`review_retest` 不是"重跑原审查"，而是**对显式新目标版本创建子 review 并与父 review 双向关联**。前置门槛：父审查须 `validation_complete`、不得有未完成复测、目标必须保持原类型与逻辑身份、且**新目标 hash 必须不同于原目标**（否则 `retest_target_not_newer`）。前后 findings 的关联主键是 `sha256(rule_id + category + 剔除易变键后的 target_location + source_issue_id)`——易变键集合 `{run_id, target_id, report_revision_id, document, workbook, file_path, formula}` 被剔除，因此换了 run_id 或文件名的同一问题仍能配上。**关闭条件双重保守**：既要新审查里找不到，又要该规则确实被执行过且 incomplete 里没有 `rule_not_executed:{rule}`——规则没跑到一律计入 `remaining`，不算修好。阻断性 finding 只能由"更新目标版本的成功复测"关闭（`disposition=resolved` 时若不满足即 `successful_retest_required`）。

**标准适用性三态**（`review_validate_standards` 的核心，也是"永不输出已符合"的落点）：

| evidence_track | evidence_status | 含义 |
|---|---|---|
| 未附证据 | `pending_evidence` | 待补证 |
| `technical_fixture` | `satisfied_technical_fixture` | 技术夹具满足（仅技术链路，非项目事实） |
| `source_reconstructed` | `satisfied_source_reconstructed_process_acceptance` | 来源重建，仅"过程验收" |
| `real` | `evidence_attached_pending_review` | 已附真实证据**待专业复核** |
| `controlled_assumption` | `unable_to_determine` | 无法判定 |

**为什么永不输出"已符合"**（两层硬保证）：

1. `formal_compliance_determined` 硬编码 `False`、`compliance_conclusion` 硬编码 `"not_determined"`，三个工具（resolve / attach / validate）都没有任何分支能改写。
2. 唯一能让整体 `status="ok"` 的路径是 `technical_complete`，而它的定义**要求 `evidence_track == "technical_fixture"`**——只有走技术夹具轨才可能"完成"，而这条轨的语义本身就是"非项目事实"。`real` 轨即使全部附齐证据，`status` 恒为 `partial`，blocker 恒为 `standard_evidence_validation_incomplete`，next_action 恒为"补充真实不可变证据并完成质量核验"。计数字段也命名为 `formal_evidence_claim_count`（主张，而非结论）。

**`attach_requirement_evidence` 的三道 fail-closed**：Resource 必须在**本工作区**的 `data-acquisition`/`data-analysis` 域内可解析（URI 前缀含 workspace_id，跨工作区必失败）；`content_hash` 必须与实际 Resource 一致；`evidence_track` 必须**三方相等**——请求值 == 适用性对象值 == 来源 Resource 自身的 track。

**适用性判定的 fail-open-to-pending 设计**：设备清单为空时判"适用"而非"排除"，源码注释逐字说明理由——"Missing equipment inventory must widen the pending scope rather than silently exclude a potentially mandatory large-facility standard"，返回 reason `facility_inventory_pending` 并附 warning。catalog 本身 fail-closed：文件不可读、`requirements` 非列表/为空、`requirement_id` 缺失或重复，一律 `standard_catalog_invalid`。

> \* `review_score_section` 标 `readOnlyHint=True`，但会调 `ASSESSMENT_STORE.put` 固化 `RubricAssessment`（`status` 取 `passed` / `needs_revision`）。该对象随后被 `lvke-knowledge-governance.knowledge_submit_candidate` 跨域读取作为准入依据——即评分必须落盘才有下游价值，注解与行为不符。

**已知限制**（2026-08-28 复核）：

- **`readOnlyHint=True` 但实际固化对象（2 例）**：`review_score_section` 与 `review_compare_assessments` 都标只读，却分别调 `ASSESSMENT_STORE.put` / `COMPARISON_STORE.put` 产出 `rva_*` / `rvc_*` 并返回其 `resource_uri`。工具描述本身也自认会写（"以确定性规则评分并固化 RubricAssessment"）。缓解因素是 object_id 内容寻址、同输入重复调用得同一对象，故语义上幂等——但契约仍被违反，且这两个工具不走幂等包装、无 `idempotency_key`。
- **豁免终态已补**：`review_disposition_finding(action=approve_waiver)` 写入 `waived`；P0 仍不可豁免。`rejected` / `superseded` 仍无写入路径。
- **规则来源已入库**：`src/lvke_mcp/config/review_rule_sources/{finance-report-core,accounting-tax-core,hotel-mining-core}.json` 存在，`professional` 检查可产生待专业核验 finding。拟定 `sim_a_formal` 轨不生成无法豁免的 professional pending。
- **`review_standards.lock.json` 不存在，恒走物料回退**：首选路径 `config/review_standards.lock.json` 实测不存在，永远落到从 `docs/研报资料库/交付型资料源/06_标准方法包/` 读 manifest 并独立重算 SHA-256 比对的回退路径（该目录存在，实测 PKG-STD-001/021 可 gate passed）。风险：若部署目录不含 `docs/` 且 `LVKE_GOLDEN_DATA_ROOT` 未设，全部标准包判 incomplete → 所有审查 verdict 恒 `incomplete`。
- **`review_resolve_standards` 标为写却不要求 `idempotency_key`**：schema 里列了该字段但**未放进 `required`**（其余 6 个写工具都必填）。服务层因此自造 `"standards-" + sha256(workspace+context+facilities)[:40]`——同一入参的两次调用被视为同一操作并返回缓存响应，调用方无法主动区分两次独立解析。
- **标准 catalog 字段与工具描述不符**：`review_list_requirements` 描述承诺"标准编号、版本、主题、适用设施"，但 `config/review_standard_requirements.json` 的 6 条需求**只有** `requirement_id`/`title`/`description`/`applicable_project_types`/`required_evidence`——**无 standard_number、无 standard_version、无 topic**，且 `applicable_facility_types` 一条都没有。因此 `facilities` 入参（最多 500 条）与设施分支**当前完全不影响结果**，`facility_inventory_pending` / `facility_type_not_present` / `facility_inventory_match` 三个 reason 不可达。这 6 条也都不是国标条文，而是内部交付规范（十三表、九章、市场证据链等）。
- **报告规则被项目名硬编码触发**：`report_checks.py:1864-1865` 写着 `hotel = "hotel-acquisition" in overlays or ("恒立" in content and "酒店" in content)`、`mineral = ... or "黄鹰岩" in content or ("石灰岩" in content and "绿色工厂" in content)`。特定项目名/矿名进了通用规则引擎——其他酒店/矿产项目若未显式带 overlay 则不触发对应 P0 规则，反之含这些字样的无关文本会误触发。
- **async 深度审查是进程内 daemon 线程，不跨重启**：`_ASYNC_THREADS` 是模块级内存字典，MCP 进程退出即中断。虽有后续 `review_get` 时补跑的机制，但重启后首次查询前该 review 一直停在非终态。
- **`workspace_metrics` / `list_resources` / `read_resource` 实现完整但一个都没注册为工具**；Resource 侧只注册了 `lambda: []` 的空 lister，故 `resources/list` 不列举任何审查对象，只能靠已知 URI 直读。影子期出口指标（≥14 天 + P50/P95 分位）因此**只能内部调用，MCP 客户端无法获取**。
- **事件链对早期记录放宽校验**：首个开发迭代写的事件没有 `previous_event_hash`，这些记录仍可读，只有显式声明 previous 的才严格校验。且 `events()` 对 JSON 解析失败的文件**静默跳过**，而 `verify_event_chain` 会记为 `event_unreadable`——即投影出的 findings 可能已缺失某些事件，只有调了 `verify_event_chain` 的路径才会暴露。
- **`review_list_findings` 支持 `review_area` 过滤但 schema 不允许传**：service 层读 `args["review_area"]` 并过滤，但 schema `additionalProperties: False` 且未声明该字段——传入会被传输层拒绝，该能力对外不可达。
- **`rule_pack` 版本号拼接产生歧义串**：`compose` 用 `".".join(各包 version)`，酒店场景实测得 `"1.0.0.1.2.0.1.0.0.1.0.0.1.2.0"`——5 个语义化版本用 `.` 连接后无法反解，且会直接展示在导出的 markdown 里。（`rule_pack_id` 用 `"+"` 连接，反而无此问题。）
- 对外 schema 中 `review_attach_requirement_evidence` 因工具名超长被 MCP 客户端侧改写（形如 `review_attach_requiremen_<hash>`）。**本仓库服务端无任何工具名截断逻辑**（`register_tool` 只做重名与 schema 校验；全仓最长注册名 34 字符），该改写来自客户端的名称长度上限。

---

## 专项服务

### lvke-asset-acquisition（资产收购服务）

**服务版本**: 0.1.0 | **实现深度**: 完整实现 | **代码规模**: server.py 453 + service.py 565（MCP 契约适配层）+ `domains/asset_acquisition/` 5,563 行（backend.py 3,352 / model.py 1,172 / tables.py 1,039）

**服务定位**：酒店租赁与光伏两类资产收购的确定性财务测算与交付。只消费 `finance_spec.v3`，按 `asset_type` **判别式路由**（`hotel_lease` 走月度模型，`solar_power` 走年度运营模型）。三条自我约束：

- **不接受客户端自带的确认效力**：`acquisition_save_spec` 无条件覆写 `confirmation_status="candidate"` 并 pop 掉 `confirmed_by`/`confirmed_at`。
- **不接受客户端自带的完整性事实**：`_sanitize_client_evidence_claims` 递归剥离 `evidence_bindings` 与 8 个 `_UNTRUSTED_EVIDENCE_ASSERTION_KEYS`（`source_sha256`/`parse_job`/`integrity_status` 等），哈希与落库都基于剥离后的躯体，证据一律由服务端从 workspace 资料状态重建。
- **不在渲染层重算财务**：十三表 `formula_lineage` 每行硬编码 `"recalculated": False`。

**治理链条**：

```
acquisition_validate_spec（纯校验，无 workspace_id，不落库）
        ↓
acquisition_save_spec ──► spec_{uuid4hex}  confirmation_status="candidate"
        ↓
acquisition_confirm_spec ──► 新 spec_{uuid4hex}（parent_spec_id 指向上一版）
        ↓   confirmation_scope: project_candidate | process_acceptance
acquisition_run_model ──► acqrun_{uuid4hex}
        ├──► acquisition_get_run（summary/result/governance/full 四种投影）
        ├──► acquisition_create_scenario_matrix ──► scenario_matrix_{uuid4hex}（≤64 组）
        ├──► acquisition_solve_max_price（二分求解，不新建对象）
        ├──► acquisition_generate_artifact ──► artifact_{uuid4hex}（真实 DOCX/XLSX IO）
        └──► acquisition_render_tables ──► acquisition_tables_package_*
                    └──► acquisition_export_tables_xlsx / _csv
```

**工具列表**（12 个，3 只读 / 9 写；除 `validate_spec` 外全部强制 `idempotency_key`）：

| 工具名 | 类型 | 实际行为 |
|-------|-----|---------|
| `acquisition_validate_spec` | 只读 | 唯一必填 `spec`，**无 workspace_id、无 idempotency_key**。同时跑 `validate` 与 `validate_for_formal`，再叠加收购专属判别式 |
| `acquisition_save_spec` | 写 | 内联 validate，`valid=false` 时不落任何库、原样返回校验结果 |
| `acquisition_confirm_spec` | 写 | 不改写候选，创建**新**确认修订。fail-closed 顺序：`SPEC_NOT_FOUND` → `CONFIRMATION_SCOPE_INVALID` → `PROJECT_FACT_EVIDENCE_MISSING` |
| `acquisition_run_model` | 写 | 只消费 `confirmation_status=="confirmed"` 的 spec；`scenario_id` 必须严格等于 `selected_scenario_id`，否则 `SCENARIO_NOT_FOUND` |
| `acquisition_get_run` | 只读 | 纯投影，不重算不写库 |
| `acquisition_create_scenario_matrix` | 写 | 逐组 `apply_scenario` + 重跑模型；笛卡尔积上限 64 |
| `acquisition_solve_max_price` | 写 | 二分求解（`tolerance_wan=0.01`, `max_iterations=100`；未给 upper 时最多 32 次翻倍寻找括住区间，括不住返回 `converged=false` + `feasible_region_not_bracketed`） |
| `acquisition_generate_artifact` | 写 | 真实外部文件 IO 与同步阻塞发生地 |
| `acquisition_get_artifact` | 只读* | 名义只读，实际全量重新验真（见已知限制） |
| `acquisition_render_tables` | 写 | 只消费固化 run，绝不重算财务 |
| `acquisition_export_tables_xlsx` | 写 | 要求 `payload.integrity.status=="passed"`，否则 `TABLE_PACKAGE_INCOMPLETE` |
| `acquisition_export_tables_csv` | 写 | 同上两道门；13 个 CSV 用 `utf-8-sig`（UTF-8 BOM） |

**收购专属十三表**（与通用可研的十三表完全不同）：
`transaction_bridge` | `investment_funding` | `purchase_price_allocation` | `monthly_timeline` | `hotel_revenue` | `lease_revenue` | `operating_cost_working_capital` | `depreciation_amortization` | `debt_schedule` | `tax_calculation` | `project_cashflow` | `equity_cashflow` 等

**关键枚举**：
- `confirmation_scope` 入参：`project_candidate` | `process_acceptance`；**落库值是另一套**：`estimate_preview` | `process_acceptance` | `formal_input`
- `confirmation_status`：`candidate` | `confirmed`
- `delivery_mode`：`estimate_preview` | `process_acceptance` | `formal_candidate`
- `model_version`：`acquisition_model.v3`（月度酒店）| `acquisition_model.solar.v1`（年度光伏）
- `run.status`：`queued` | `running` | `succeeded` | `failed` | `cancelled`

**门禁要点**：
- **判别式粒度门**：`hotel_lease` 必须 `calculation_granularity=="monthly"` 且 `operating_mode ∈ {owner_lessor, mixed_owner_operator}`；`solar_power` 必须 `"annual"`。不满足即 `acquisition_mode_invalid`。
- **`process_acceptance` 需同时满足 6 类条件**：scope 正确、`evidence_policy=="source_reconstructed"`、`project_fact_certified` 必须 `is False`、`business_decision_status=="not_selected"`、`reconstruction_records` 至少一条七键齐全、`process_acceptance_basis` 齐备。
- **情景独立性**：维度字段必须在 `INDEPENDENT_SCENARIO_FIELDS` 内；`hotel_lease` 下未显式改动 `lease_portfolio.market_rent` 时该值必须逐字节不变，否则 `SCENARIO_INDEPENDENCE_VIOLATION`（防止收购价联动经营参数）。
- **最高价阈值必须与 Spec 一致**：`target_irr`/`min_dscr` 与 `decision_thresholds` 用 `math.isclose(rel_tol=abs_tol=1e-12)` 比对，否则 `MAX_PRICE_THRESHOLD_MISMATCH`。
- **工件四路数值一致性**：md / docx / xlsx / report_data 四路 token 与数值全部 passed 才发布，任一失败 `ARTIFACT_MISMATCH` 且不留下目录。

**已知限制**：
- **`consistency_ok` 现按年结资产负债表投影判定**（现金+固定资产=资产合计、有息负债+权益=负债和权益合计），与 spec/证据 `issues` 脱钩。缺列或勾稽失败则为 False，`RUN_INCONSISTENT` 可达。
- **重建记录必填键两处不一致**：`backend.RECONSTRUCTION_RECORD_FIELDS` 是 7 键（无 `original_formula_available`），而 `runtime/source_reconstruction` 要求 8 键（含该字段且必须严格 True/False）。
- **`transaction.repayment` 枚举三方不一致**：输入 schema 是 `[equal_principal, equal_payment, bullet, custom]`，`validate_for_formal` 白名单是 `{equal_principal, equal_payment, annuity, bullet, interest_only}`，model.py 对不认识的值另有回退。
- **`transaction.transaction_taxes` 无法作为情景维度传入**：它在 `INDEPENDENT_SCENARIO_FIELDS`（16 个之一）里，但 server.py 的 `dimensions` properties 只列了 15 个字段且 `additionalProperties=false`。
- **`acquisition_get_artifact` 标 `readOnlyHint=True` 但开销极重**：逐文件 sha256（1 MiB 分块）、python-docx 解析整个 DOCX、zipfile 解 XLSX，并完整重跑一次数值一致性。
- **scenario-matrices 的 URI 解析是 O(runs × matrices) 暴力扫描**，且被 `list_runs(limit=100)` 截断——第 101 个及更旧 run 上的情景矩阵无法通过 URI 读取，静默返回 None。
- **全部 12 个工具 `task_support="forbidden"`，无异步入口**：`run_model`（完整财务模型）、`create_scenario_matrix`（最多 64 次重跑）、`generate_artifact`（DOCX+XLSX 生成 + 四路校验）都在单次 MCP 调用里同步阻塞完成。
- 六档收购价候选可建但 confirm 曾全部 `SPEC_VALIDATION_FAILED`（MCP-P1-018）。

---

### lvke-knowledge-governance（知识治理服务）

**服务版本**: 0.1.0 | **实现深度**: 完整实现（轻量域） | **代码规模**: 870 行（service.py 595 / server.py 274）

**服务定位**：把"已通过确定性 rubric 评分、且绑定了可定位不可变证据"的知识候选，沿 **候选 → 快照 → 审核 → 发布** 四步固化成内容寻址的不可变对象。四条明确的不做：

- **不做评分**：rubric 由 `lvke-deliverable-review` 的 `review_score_section` 产出，本服务只以 `passing` 布尔值做准入判断，从不重算分数。
- **不做知识检索/向量召回/记忆合并**：schema 里的 `supersedes_memory_id`、`conflict_key`、`layer`、`scope` 被原样存入 payload，service 层没有任何一行代码读取它们。
- **不认证项目原始事实**：任一证据 `evidence_track == "source_reconstructed"` 时自动把 `project_fact_certified` 置 false 并把 `evidence_policy` 降级为 `source_reconstructed`。
- **不产出任何 DOCX/XLSX/网络请求**：全部 IO 为本地 JSON 读写。它也**不返回 `partial`**——实测只产出 `ok` 与 `blocked` 两种状态。

**工具列表**（6 个，2 只读 / 4 写）：

| 工具名 | 类型 | 核心参数 | 实际行为 | 创建对象 |
|-------|-----|---------|---------|---------|
| `knowledge_submit_candidate` | 写 | `workspace_id`, `candidate{}`, `idempotency_key` | 先 `_validate_evidence` 逐条校验 `evidence_bindings`，再**跨域读** deliverable-review 的 `rubric_assessments` 校验存在性、`payload.passing`、`report_revision_id` 一致性 | `KnowledgeCandidate`（knc_） |
| `knowledge_list_candidates` | 只读 | `workspace_id`; 可选 `candidate_status`, `industry`, `section_id`, `candidate_type`, `offset`, `limit` | 对每一条**实时重算**派生状态（再扫 releases/reviews/snapshots 三个目录），用派生状态而非持久化字段做过滤 | 无 |
| `knowledge_get_candidate` | 只读 | `workspace_id`, `candidate_id` | 聚合全部 lineage：snapshots / reviews / releases | 无 |
| `knowledge_create_snapshot` | 写 | `workspace_id`, `candidate_id`, `idempotency_key` | 把 `evidence_bindings` 投影为仅含 resource_uri/locator/content_hash/evidence_track 四字段并算 `evidence_fingerprint` | `KnowledgeSnapshot`（kns_） |
| `knowledge_review_candidate` | 写 | `workspace_id`, `candidate_id`, `decision`, `reason`, `idempotency_key` | `decision ∈ {accepted, rejected, needs_revision}`；`reason` 为空回落读 `review_note`，仍空则 `knowledge_review_reason_required` | `KnowledgeReview`（knr_） |
| `knowledge_publish_release` | 写 | `workspace_id`, `candidate_id`, `review_id`, `idempotency_key` | **三重门禁**：候选存在 + review 存在且其 `candidate_id` 与入参一致 + 该 review 的 `decision == "accepted"`。即 reviewed-first，未审核不得发布 | `KnowledgeRelease`（knrel_） |

**已知限制**：`knowledge_review_candidate` 的 `rubric_assessment_id` 缺省时从候选 payload 继承，且**不校验其存在性**。

---

### lvke-reference（本地参考聚合服务）

**服务版本**: 0.1.0 | **实现深度**: **薄路由门面** | **代码规模**: 本体仅 244 行（server.py 70 / service.py 174），实际行为寄生在 9 个 legacy server 模块上

**服务定位**：纯只读的本地参考资料聚合门面（service.py docstring 自述 "Thin routing facade over the former support MCP handlers"），把 9 个早期独立 MCP server（industry-research / lvke-clients / lvke-experts / policy-search / lvke-archive / lvke-templates / environmental-data / statistics-cn / map-geo）的私有 `_tool_*` 处理函数，用 `importlib.import_module` 动态收拢成 12 个 dataset 驱动的统一入口。

**它的"不做"是彻底的而非声明式的**：12 个工具的 ToolAnnotations **一律** `readOnlyHint=True / destructiveHint=False / idempotentHint=True / openWorldHint=False`——这是全系统唯一一个 12 个工具全为只读的服务。它不固化任何不可变对象、**不接受 `workspace_id`、不接受 `idempotency_key`**，因此也不参与工作区隔离与幂等体系。

**工具列表**（12 个，全部只读）：

| 工具名 | 核心参数 | dataset / 说明 |
|-------|---------|---------------|
| `reference_search` | `dataset`; `query`, `filters`, `limit`(1-200，默认 20) | `industry_reports` / `clients` / `experts` / `policies` / `archive` |
| `reference_get` | `dataset`, `record_id`; `view` | 上述 5 类 + `templates` |
| `reference_list` | `dataset`; `owner_id`, `filters` | `environment_locations` / `client_projects` / `expert_specialties` / `statistics_dictionaries` / `templates` |
| `reference_observe` | `dataset`, `subject`; `period`, `filters` | `air_quality`(subject→city) / `water_quality`(→section_or_basin) / `statistics`(→name) |
| `reference_verify` | `dataset`(仅 `policy`), `record_id`; `as_of` | 两跳校验：先取 policy 全文拿 `doc_number or title` 当 citation，再验有效性 |
| `template_fill` | `template_id`, `data`; `format`(仅 markdown) | 纯字符串渲染，**不落盘、不生成 DOCX/XLSX** |
| `geo_query` | `operation`(geocode/nearby_pois), `query_or_point`; `radius_km`(≤100), `category`, `limit` | 离线本地数据 |
| `geo_distance_matrix` | `origins[]`, `destinations[]`; `mode`(仅 `haversine_with_highway_estimate`) | **离线几何计算，无外部地图 API**；端点支持三形态：地名字符串、`{address}` 或 `{name}`、`{lat,lng,label}`（含经纬度范围校验） |
| `archive_find_similar_projects` | `brief`; `top_n`(默认 5) | 每项带 `similarity`(round 4) 与 `industry_match` |
| `archive_extract_structure` | `report_id`; `with_appendix`(默认 true) | **要求 `storage.mode()=="sqlite"`，否则 fail-closed 返回 `index_unavailable`** |
| `archive_compare_cases` | `report_ids[]`(1-8); `dim` | `dim` 缺省 `indicators`，实际接受 structure/appendix/indicators/key-indicators 四值（报错文案只列了前 3 个） |
| `archive_get_template_paragraph` | `scene`(7 值); `industry`, `top_k`(默认 3) | 同样要求 sqlite 模式；先按 `corpus="lvke"` 检索，无果再不限 corpus 重检索 |

**已知限制**：
- **底层数据是极小 seed**：实测记录数 `policy_search` 22 条、`map_geo` 68 POI、`industry_research` 14 份、`lvke_archive` 11 条、`lvke_clients` 9、`lvke_experts` 9、`statistics_cn` 6 指标、`environmental_data` 8+8。所有 `LVKE_*_DATA_DIR` 未设置时代码回退 seed。
- **`filters` 在部分分支被完全丢弃**：`reference_list` 的 `environment_locations` / `expert_specialties` / `statistics_dictionaries` 三个分支硬编码传 `{}`。
- **`filters` 可覆盖顶层参数**：`reference_search` 先 `args = {**filters, "limit": limit}` 再对 query 做 `setdefault`，因此 `filters` 里已有 `query`/`keyword` 时顶层 `query` 被忽略。
- **`geo_query` 的 geocode 分支静默忽略** `radius_km`/`category`/`limit`。
- 档案索引已在 `~/.lvke/archive_index/` 建好（reports 51 / chunks 2982），但 `LVKE_ARCHIVE_DATA_DIR` 未设置时服务仍走 seed（`mode=legacy`），此时两个依赖 sqlite 的 archive 工具返回 `index_unavailable`。

---

## 调用流程示例

官方推荐调用顺序的权威来源是 `lvke_feasibility_delivery/service.py` 的 `_NEXT_TOOLS` 静态映射——`feasibility_next_actions` 的返回值就是按它算的。卡住时调 `feasibility_next_actions(delivery_run_id)` 即可恢复：它按 `current_stage` 取工具清单，并把 `output_refs[0]` 自动填进 `finance_validate_spec.spec_id` / `finance_get_run.run_id` / `tables_validate.finance_tables_package_id` / `report_validate.report_revision_id` 四个参数，同时返回 `missing_inputs`。

### 贯穿全流程的阶段落账

每个领域步骤完成后都要调 `feasibility_stage(status=completed)`，传真实 `input_refs` / `output_refs` / `basis_hash`。两个最容易踩的门禁：

1. **父阶段绑定**：当前阶段的 `input_refs` 必须与上一阶段的 `output_refs` **有交集**，否则 `stage_parent_binding_missing`。
2. **`basis_hash` 白名单**：`basis_hash` 只能是 ① 任一 output 对象自己的 `basis_hash`，或 ② `sha256_json({input_refs, output_refs, output_basis_hashes})`。自造哈希一律 `stage_basis_hash_mismatch`。

### 三种模式在链上的实际差别

| | `estimate_preview` | `review_candidate` | `formal_release` |
|---|---|---|---|
| `release_scope` | 传 `project_delivery` 会被**静默改写**为 `process_acceptance` | 保留传入值 | 保留传入值 |
| `finance_run_model` 要求 confirmed spec | 否 | 是，否则 `spec_confirmation_required` | — |
| `finance_run_model` 要求 BoE | 否 | 是，且必须 `formal_ready` 且 `spec_id` 匹配 | — |
| `feasibility_validate(scope=formal)` | **恒定失败**，追加 `preview_cannot_formal_release` | 可通过 | 可通过 |
| 十三表 | `delivery_mode="draft"`、`draft_only=true` | 门禁通过才 `formal` | 同左 |

> `finance_run_model` 的 `mode` 白名单只有 `estimate_preview` 与 `review_candidate` 两个值，传 `formal_release` 会被**静默降级**为 `estimate_preview`。

### A. 通用可研全流程（有客户资料）

```
① 资料 → 证据
source_import_local_path | source_import_content | upload_begin→chunk→commit
→ source_parse_status → analysis_ingest → analysis_extract_candidates
→ analysis_build_evidence_pack                    ⇒ evidence_pack_id

② 上下文与运行
project_context_create → project_context_validate → feasibility_start
                                                  ⇒ delivery_run_id

③ 研究
dr_prepare → dr_start → data_discover → data_fetch|data_collect
→ dr_submit（恒 partial）→ dr_confirm_quality      ⇒ quality_review_id

④ 规划四段（每段 prepare/solve → validate → confirm 三拍）
planning_prepare_market_case → validate → confirm         ⇒ market_case_id
planning_prepare_option_comparison → score → confirm
planning_solve_build_scale → validate → confirm           ⇒ build_scale_case_id
planning_prepare_revenue_drivers → confirm
planning_prepare_cost_drivers → calculate → validate → confirm
planning_infer_labor_plan|create → validate → confirm

⑤ 财务
finance_prepare_spec → finance_validate_spec
→ finance_build_basis_of_estimate → finance_confirm_spec
→ finance_run_model → finance_get_run              ⇒ run_id

⑥ 十三表
tables_render → tables_validate → tables_export_xlsx → tables_export_csv
                                                  ⇒ finance_tables_package_id

⑦ 报告（九章，逐章循环）
report_prepare → report_start
→ [ report_propose_section → report_diff → report_apply → report_validate_section ] ×9
→ report_validate → report_get_readiness → report_export_docx

⑧ 审查
review_prepare → review_start → review_list_findings → review_get_finding
→ [整改：propose_section→diff→apply] → review_retest → review_export

⑨ 发布
feasibility_validate(technical) → feasibility_validate(formal) → feasibility_release
```

**每一步的关键 fail-closed**：

| 步骤 | 必填上游 ID | 产出 | fail-closed 要点 |
|-----|-----------|-----|----------------|
| `analysis_ingest` | `file_ids[]` 和/或 `source_snapshot_ids[]` | `analysis_task_id` | 单次 >100 来源 → `source_id_limit_exceeded`；解析未终态 → `parse_not_complete` 并整体降 partial |
| `analysis_build_evidence_pack` | `analysis_task_id` + `candidate_set_id` | `evidence_pack_id` | `selected_source_ids=[]` 显式空 → `no_selected_sources`，**不回退全量**；`selected_candidate_ids` 不带 `candidate_set_id` → `candidate_set_required` |
| `dr_confirm_quality` | `research_package_id` | 新包 + `quality_review_id` | **跳过它直接把 `dr_submit` 的包当 output_refs，formal 一定挂** `research_quality_confirmation_required` + `research_quality_not_accepted` |
| `planning_confirm_market_case` | `market_case_id` + `selected_candidate_id` + `rejected_candidate_ids[]` | 新 `market_case_id` | `rejected_candidate_ids` 必须等于「全集 − 选中」，否则 `market_rejected_candidates_incomplete`（**防隐式合并**） |
| `feasibility_stage(drivers)` | — | — | `output_refs` 必须**同时**含 `CostDriverSet` + `LaborPlan` + `RevenueDriverSet`，缺一即 `stage_output_type_invalid` + `missing:<Kind>` |
| `finance_build_basis_of_estimate` | `spec_id` + `planning_object_ids[]` + `evidence_pack_ids[]` | `basis_of_estimate_id` | 每条 entry 必须含 `target_pointer`（指向 `/spec/` 或 `/input_revision/`）、`method`、`selection_reason`（**≥10 字符**）、`locator`、`content_hash`、`evidence_eligibility` |
| `finance_confirm_spec` | `spec_id` + `idempotency_key` | 新 `spec_id` | 缺投资额 → `missing_input:total_investment_wan`；收入输入不全 → `missing_input:annual_revenue_wan_or_revenue_driver` |
| `finance_run_model` | `spec_id` + `idempotency_key` | `run_id` | `spec_id` 与 `spec`/`force_flat` 同传 → `invalid_argument` |
| `tables_render` | `run_id` | `finance_tables_package_id` | run 的不可变质量审计不过 → `finance_run_consistency_failed`，不生成 package |
| `tables_export_xlsx` | `run_id` | XLSX | 正式资格是**两个条件的合取**：package 门禁 `validation_complete` 且本次导出深度审查 `validation_complete`。只满足前者追加 `xlsx_delivery_quality_not_formal`。**XLSX 写盘成功本身不抬升资格** |
| `report_prepare` | 非空 `evidence_pack_ids` + 非空 `research_package_ids` + `finance_binding` | `report_preparation_id` + `basis_hash` | 同传 `finance_binding` 与旧 `run_id` → `ambiguous_finance_binding`（不做静默优先级裁决） |
| `review_start` | `review_preparation_id` + `idempotency_key` | `review_id` | `mode=quick` 且 `execution=async` → `review_execution_invalid`；同 key 绑不同请求 → **故意抛** `idempotency_key_conflict` 而非缓存，以免污染事件日志 |
| `feasibility_release` | `delivery_run_id` | `release_id` + 新 run(status=released) | **内部重跑一次 formal 校验，不信任先前 validate**；`lineage_hash = sha256_json({lineage, stage_bindings})` |

### B. 资产收购流程（酒店 / 光伏）

```
acquisition_validate_spec → acquisition_save_spec → acquisition_confirm_spec
→ acquisition_run_model → acquisition_get_run(view=governance)
→ acquisition_solve_max_price
→ acquisition_create_scenario_matrix
→ acquisition_render_tables → export_tables_xlsx → export_tables_csv
→ acquisition_generate_artifact → acquisition_get_artifact
```

**`confirmation_scope` 决定证据资格**：

| scope | 前置条件 | 效果 |
|------|---------|-----|
| `project_candidate`（默认） | spec 的 `evidence_policy` **不能**是 `source_reconstructed`，否则 `PROJECT_FACT_EVIDENCE_MISSING` | 走 `validate_for_formal`；`evidence_binding.formal_ok` 假 → `EVIDENCE_REVIEW_REQUIRED` |
| `process_acceptance` | `process_acceptance_gaps` 必须为空，否则 `PROCESS_ACCEPTANCE_BASIS_INCOMPLETE` 并列出缺项 | 强制写入 `project_fact_certified=false` + `business_decision_status="not_selected"`；只走宽松 `validate` |
| `estimate_preview` | 由 spec 内 `delivery_mode` 判定，非入参枚举 | 只走宽松 `validate`；run 保留 `formal_spec_valid=false` 及全部正式 blocker |

**与 report/review 的自动衔接点**：`render_tables` 时 package 的 integrity 一旦 `passed`，会自动把 `acquisition_tables_package_id` 绑定进 `report_artifacts` 的 `finance_binding`，`binding_kind="asset_acquisition"`。

`report_prepare` 在 `kind="asset_acquisition"` 分支下额外做**四个字段逐一比对** package 与 run：`spec_hash` / `input_hash` / `model_version` / `evidence_binding_hash`，任一不等即 `acquisition_tables_{field}_mismatch`。

**收购路线接回编排时的 kind 替换**：当 `finance_spec` / `finance_run` / `finance_tables` 阶段的 output 解析出 `AcquisitionFinanceSpec` / `AcquisitionRun` / `AcquisitionTablesPackage` 时，`required_kinds` 自动替换为对应收购类型。因此**收购路线的 `finance_spec` 阶段不需要 `BasisOfEstimate`**，单个 `AcquisitionFinanceSpec` 即可满足；`finance_tables` 阶段的必需表清单也切换为收购十三表（判据是 `package_schema` 以 `acquisition_` 开头）。

### C. 零材料交付流程

```
delivery_create_from_sentence → delivery_start → delivery_list_assumptions
→ delivery_confirm_assumptions（内部自动重算）→ delivery_status → delivery_get_artifacts
```

> **顺序陷阱**：必须先 `delivery_start` 才有 `assumption_package_id` 可列。`AssumptionPackage` 是在 `delivery_start` 内部由 `_build_assumption_package(intent)` 首次创建的，`delivery_create_from_sentence` 只产出 intent 和一个空壳 run。

`delivery_start` 内部一次性串起五个领域，全程走既有边界、从不授予 release：

```
_start_research → _create_project_context
→ finance.validate_spec(for_formal=false) → prepare_spec → confirm_spec
→ finance.run_model(mode="estimate_preview")
→ tables.render → tables.export_csv + export_xlsx
→ report_generation.prepare(finance_binding={kind:"generic_feasibility", ...})
```

五个提前返回点，每个都带明确的 stage 回退：

| 失败点 | status | 回退 stage | blocker |
|-------|-------|-----------|---------|
| `validate_spec` 不过 | `model_blocked` | `planning_ready` | `finance_spec_validation_failed` |
| `prepare_spec` 无 spec_id | `model_blocked` | `planning_ready` | `finance_spec_prepare_failed` |
| `confirm_spec` 无 spec_id | `model_blocked` | `planning_ready` | `finance_spec_confirm_failed` |
| `run_model` 无 run_id | `model_blocked` | `planning_ready` | `finance_run_failed` |
| `tables.render` 无 package_id | `artifact_failed` | `finance_ready` | `finance_tables_render_failed` |

**零材料链结构上无法产出可发布的 report revision**：即使全部成功，返回的 blockers 也恒定包含 `research_evidence_pending` 与 `planning_market_evidence_pending`——因为 `report_prepare` 传的 `evidence_pack_ids` 与 `research_package_ids` 都是空的，必然触发 `evidence_pack_required` + `research_package_required`。这是设计上的永久阻断。

`delivery_confirm_assumptions` 保存确认后**自己调 `start()`**，用派生的 `recalculation_key = "zmd-auto-recalc-" + sha256(...)[:32]`，把 `automatic_recalculation: true` 合进返回值——调用方不需要手动再调一次 `delivery_start`。

> **零材料 run 不是 `lvke-feasibility-delivery` 的 run**，两者是不同 server、不同 store。零材料交付没有 `feasibility_release` 通路；即便把它的对象喂给 `feasibility_start(delivery_mode="estimate_preview")`，formal 校验也会因 `preview_cannot_formal_release` 恒定失败。

### `evidence_policy` 四轨对 formal 发布的影响

| policy | formal 校验行为 |
|-------|---------------|
| `formal_evidence` | 允许 `project_delivery` |
| `source_reconstructed` | `release_scope=project_delivery` → `project_fact_evidence_missing`（须改 `process_acceptance`）；必须提供非空 `reconstructed_source_ids` 与 `reconstruction_records`，每条记录的 `source_uri` 须能解析且 `content_hash` 一致；`project_fact_certified=true` → `source_reconstructed_cannot_certify_project_fact` |
| `technical_fixture` | 阶段对象携带该 policy → `formal_evidence_policy_forbidden:{id}:{policy}` |
| `controlled_assumption` | 直接 `controlled_assumption_formal_forbidden` |

一致性门禁覆盖的 kind 集合：`{evidence_pack, ResearchPackage, FinanceSpec, BasisOfEstimate, FinanceRun, FinanceTablesPackage, AcquisitionFinanceSpec, AcquisitionRun, AcquisitionTablesPackage, ReportRevision, ReviewRun}`——任一对象的 `evidence_policy`/`evidence_track` 与 run 声明不符即 `evidence_policy_mismatch:{object_id}`。

> **未验证**：`_NEXT_TOOLS` 中 `research` 阶段列出的 `dr_prepare` / `dr_start` / `data_discover` / `data_fetch` / `analysis_build_evidence_pack` 是**并列候选清单**，源码未编码它们之间的先后顺序；上文 ③ 给出的顺序取自 `lvke-feasibility-study/SKILL.md` 的叙述性 workflow，不是服务端强制的。同理 `_NEXT_TOOLS` 的 `review` 阶段未列 `review_prepare`，但 `review_start` 必填 `review_preparation_id`，故它是事实前置。

---

## 实现完整度评估

本章不靠印象打分。评级依据是：server + domain 合计代码量、是否有真实算法与外部 IO、以及全仓 grep 与运行时自省的结果。

**方法学前提**：全仓 `src/` 下 `TODO` / `FIXME` / `NotImplementedError` 实质为 **0**——5 处 `TODO|XXX` 字面量全部是占位符**检测正则**（`domains/reports/read_model.py:330`、`servers/lvke_deliverable_review/service.py:1783` 等），不是待办标记。因此"薄包装"的判据只能是行数 + 是否有真实算法/IO，不能靠标记。

**架构分层**（server 层普遍是传输壳，真实业务在 `domains/`）：

| 层 | 行数 | 文件数 | 职责 |
|---|-----|-------|-----|
| `domains/` | 48,284 | 79 | 业务实现（finance 28,020 / reports 5,980 / research 5,606 / asset_acquisition 5,563 / project_planning 2,374 / templates 658 / geo 77 / review 1） |
| `servers/` | 37,583 | 94 | 协议适配 + schema（26 个子包） |
| `adapters/` | 3,530 | 14 | `JSONArtifactStore` 实例化与持久化边界 |
| `runtime/` | 2,983 | 15 | transport / storage / jobs / workspace |
| `testing/` | 2,090 | 5 | 权威 manifest + 协议测试套件 |
| `contracts/` | 7 | 1 | 空壳 |

导入方向严格单向：**`servers → domains → runtime`**，`domains` 无一处 `import lvke_mcp.servers`（已 grep 验证）。

### 逐服务完整度

| 服务 | 实现深度 | 证据 | 已知缺口 |
|-----|---------|-----|---------|
| `lvke-finance-model` (16) | 完整实现 | server 2,860 + `domains/finance` 28,020；`finance_model.py` 3,684 / `table_render.py` 3,081 / `vendor_import.py` 2,302（openpyxl 真实读公式）；真 IRR/NPV/XIRR | FinanceSpec v3 扩展字段未在公开 `spec` 参数暴露（ERR-005 实测拒收 11 个字段）；BoE 需 confirmed planning 对象 |
| `lvke-deliverable-review` (15) | 完整实现 | 审查规则源已入库 `src/lvke_mcp/config/review_rule_sources/`；豁免有 `approve_waiver → waived` | `review_standards.lock.json` 仍不存在，标准快照走物料回退；`report_checks.py` 仍有项目名硬编码 |
| `lvke-project-planning` (17) | 完整实现 | 5 个判别式聚合入口复用原业务 handler；真实算法：容积率/密度/绿地约束求解、数量×单耗×单价×损耗展开、班次/覆盖/自动化定员推导 | 复杂分支完整 schema 需通过稳定 `lvke://schemas/project-planning-*` Resource 回读 |
| `lvke-data-analysis` (11) | 完整实现 | `service.py` 2,147 + server 440；单位归一化、期间对账、共同比、CAGR、locator 三道门 | 无 domain 层承载，全在 service.py；`analysis_profile_tabular` 曾 160/160 `invalid_tool_output` |
| `lvke-data-acquisition` (10) | 完整实现（外部依赖 config-gated） | `service.py` 1,743；真实 SSRF/URL 安全门、HMAC receipt、`domains/research/url_safety.py` 274 行 | **Tavily 是唯一 provider**；未配 `TAVILY_MCP_URL`/`LVKE_MCP_TAVILY_SERVER` 时 `configured_transport()` 返回 None → 全链不可用 |
| `lvke-deep-research` (18) | 完整实现 | server 1,009 + `domains/research` 5,606；`extractor.py` 1,485 / `quantitative.py` 578 / `source_normalizer.py` 412 真实公共后缀与来源分级 | P0-009（质量确认失败仍写 completed）的验收测试是 grep 源码字符串而非行为断言 |
| `lvke-source-files` (13) | 完整实现 | `service.py` 1,240 + repository 490 + `workbook_inspection.py` 208；真实分块上传、SHA-256 校验、openpyxl 公式与跨表依赖树 | **PDF 只做 magic-byte 校验**：依赖清单无任何 PDF/OCR 库（已 grep 确认），但 `service.py:911` 仍返回 `ocr_status: pending` |
| `lvke-asset-acquisition` (12) | 完整实现 | service 565 + server 453 + domains 5,563（`backend.py` 3,352 月度酒店/年度光伏模型、`tables.py` 1,039 openpyxl 导出） | `consistency_ok` 现按资产负债表投影判定；重建记录必填键两处不一致；六档 confirm 曾全部 `SPEC_VALIDATION_FAILED` |
| `lvke-report-generation` (13) | 完整实现 | server 421 薄，但 `domains/reports` 5,980：`artifacts.py` 2,177（python-docx 真实 DOCX）/ `doc_service.py` 1,566 / `readiness.py` 253 | `export_docx` 的 revision_id 不决定内容；13 工具全无 idempotency_key；多处死路径与死字段 |
| `lvke-knowledge-governance` (6) | 完整实现（轻量域） | `service.py` 595，21 个函数；真实 filelock 幂等、四步状态机、证据校验 | 无独立 domain 层；`rubric_assessment_id` 继承时不校验存在性 |
| `lvke-feasibility-delivery` (10) | 部分实现 | 1,560 行；阶段机 + stale 传播 + checkpoint/resume 真实 | 跨服务 resolver 曾不一致（MCP-P1-017），技术阶段可把不存在的 URI 登记为 completed |
| `lvke-zero-material-delivery` (10) | **部分实现** | 含拟定模板包与 `delivery_confirm_formal_promotion`；七档行业路由 | 零材料轨仍不认证项目事实；`zmr_*` 不原地升级；路由仍是关键词子串 |
| `lvke-finance-tables` (8) | **薄包装** | server 133 行只做 `table_id` 别名映射 + schema；真实渲染在 `domains/finance/tables_service.py` 880 + `table_pack.py` 459 + `tables_application.py` 467 | 整包 formal 校验被三项语义 blocker 阻断（`investment_quantity_indicator` / `working_capital_reconciled` / `supporting_schedules_formula_driven`） |
| `lvke-reference` (12) | **薄路由门面** | `service.py` 174 行，首行自述 "Thin routing facade"；纯 `importlib` + dataset→旧 handler 分派 | 底层 9 个 seed 服务数据量极小（详见下） |

### 全局已知限制

1. **Tavily 是唯一联网 provider**：`domains/research/providers/` 只有 `tavily.py`，且集成测试主动断言"不允许非 Tavily provider"。未配置对应环境变量时 `configured_transport()` 返回 None，采集全链不可用（返回 `blocked` 而非 `upstream_failure`）。
2. **9 个参考数据服务全部跑在极小 seed 上**：实测记录数 `policy_search` 22 条、`map_geo` 68 POI、`industry_research` 14 份、`lvke_archive` 11 条、`lvke_clients` 9、`lvke_experts` 9、`statistics_cn` 6 指标、`environmental_data` 8+8。所有 `LVKE_*_DATA_DIR` 未设置时代码回退 seed。
3. **档案索引已建但未接上，且语料不是可研报告库**：`~/.lvke/archive_index/metadata.sqlite`（reports 51 / chunks 2,982）已生成，但 `LVKE_ARCHIVE_DATA_DIR` 未设置时服务日志显示 `mode=legacy`。且索引 51 条中 28 条 `corpus_origin=method`、10 条 `project`，`source_path` 指向 `MCP_INDEPENDENCE_PLAN.md`、`README.md` 等**本仓自己的方案文档**——只有 13 条 client 是真甲方材料。
4. **PDF 无内容读取、无 OCR**：只有 `%PDF-` magic-byte 判断，`pyproject.toml` 依赖清单无任何 PDF/OCR 库（已 grep 确认），但服务仍返回 `ocr_status` 字段。
5. **零材料行业档**已含房地产与墓地；路由仍是关键词子串匹配。
6. **十三表整包无法通过 formal 校验**：三项语义 blocker 未解，CSV/XLSX 的正式资格因此拿不到。
7. **`domains/review` 是空壳**：只有 1 行 `__init__.py`，真实审查逻辑全在 server 目录下。
8. **`_common/` 是待清理的兼容垫片**：全部文件形如 `from lvke_mcp.runtime.transport import *`，注释写明"切完即删"，且全仓已无任何 import 引用它（已 grep 确认）。
9. **幂等记录无 TTL 与清理**：`asset-acquisition` 与 `zero-material-delivery` 的领域层幂等 store 每次写操作都全量读盘线性扫描，且存整份 response，长期使用会同步膨胀磁盘与延迟。`LVKE_MCP_IDEMPOTENCY_TTL_SECONDS` 只对部分域生效。
10. **测试不可用 `unittest discover`**：`tests/` 无法 import，必须直接跑文件。
11. **审查规则源已入库，标准锁文件仍缺**：`src/lvke_mcp/config/review_rule_sources/` 现有 finance/accounting/hotel 三份 JSON，`professional` finding 可产生。`review_standards.lock.json` 仍不存在，标准快照继续走 `docs/研报资料库/` 物料回退。
12. **`JobRepository` 已删除**：公开面为 14/180，异步 job 预留实现不再存在。
13. **豁免终态已补**：`approve_waiver` 可把 finding 推到 `waived`；P0 仍不可豁免。`rejected` / `superseded` 写入路径仍可能不完整。

### 文档化程度 vs 实现程度的不一致告警

1. **注解与行为矛盾（已确认 6 个工具）**：以下工具标 `readOnlyHint=True` 却都调 `STORE.put` 固化不可变对象——

   | 工具 | 固化对象 | 备注 |
   |-----|---------|-----|
   | `data_discover` | `DiscoverySet` | 返回 `discovery_set_id` |
   | `data_search` | `SearchSet` | 固化搜索元数据，不含网页正文 |
   | `report_prepare` | `ReportPreparation` | 即便 blockers 非空也落盘 |
   | `report_status` | `ReportRevision` | 每次调用都重抓快照并新建 revision |
   | `review_score_section` | `RubricAssessment` | 该对象是 knowledge-governance 的准入依据，必须落盘才有下游价值 |
   | `review_compare_assessments` | `RubricComparison` | 工具描述自认会写 |

   另有两类"名义只读、实际很重"：`acquisition_get_artifact` 会逐文件 sha256 + 解析 DOCX/XLSX + 重跑数值一致性；`report_validate` / `report_get_section` / `report_get_readiness` 会为未初始化工作区**无声建仓**。依赖 `readOnlyHint` 做权限、缓存或重试决策的客户端会被误导。
   
   （反例：`dr_prepare`、`planning_score_option_comparison`、`review_validate_standards` 等名字像写操作的工具经核实确为纯读，注解正确。）
2. **收购 `consistency_ok` 已按资产负债表投影计算**，不再恒为 True。
3. **验收证据强度不足**：`test_mcp_acceptance_20_defects.py` 的 19 个测试里有 8 个是 `read_text()` + `assertIn` 的**源码字符串断言**（断言某文件里出现 "P0-009"、"P1-017" 等字样）。这类断言只能证明有人写了那行注释，不能证明行为已修。最关键的 P0-009（证据等级误升级 + 非原子写入）正属于此类。另有 2 个测试 `skipTest` 兜的 SKILL.md 路径实测不存在，即零覆盖但计入"通过"。
4. **旧拓扑遗留的口径冲突**：验收报告基线写 24 服务 / 262 工具，当前是 14/180；第一轮 manifest 记录 85 条，第二轮 v2 manifest 记录 32 条。报告里的缺陷 ID 与当前工具名之间需经两轮 migration manifest 映射才成立。
5. **两个薄层在文档里与其他服务平级**：`lvke-finance-tables`（server 133 行）与 `lvke-reference`（174 行，自述 thin facade）的能力完全取决于被代理的下层。本文档已在各自小节标注"薄包装/薄路由门面"，但读者需知其数据只有几十条 seed。
6. **`lvke-zero-material-delivery` 的 `resources/list` 跨 workspace 泄露**是本文档记录的唯一跨租户可见性缺陷，与概览章"所有数据按 workspace_id 物理隔离，无跨租户泄漏"的表述直接冲突——该表述对其余 13 个服务成立，对本服务的协议层 Resource 通道不成立。
