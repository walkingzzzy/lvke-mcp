# MCP 独立项目架构与验收方案

## 1. 文档目的

本文定义 `mcp_servers` 作为独立 Python 项目的架构边界、代码组织、实施顺序和验收标准。

项目定位与目标运行边界：MCP 服务是独立项目，业务代码、运行时、工作区、配置、数据和工件均由 MCP 自己拥有；MCP 不调用、不导入、不读取 Hermes 的任何代码或运行时。Hermes 不属于 MCP 的调用链，Hermes 与 MCP 之间没有任何目标运行时调用；MCP 只接受 Codex 或其他独立 MCP Client 通过标准 MCP transport 发起的调用。

因此，本文不是“把 MCP 从 Hermes 中拆出来、期间仍依赖 Hermes 运行”的逐步搬迁方案。实现机制是一次性**直接搬移**：把现有纯计算层代码（`hermes_cli.finance`、`hermes_cli.research_engine` 等）整体搬进 MCP 自有包并改写 import 路径，行为不改、不重写；搬移完成后，MCP 对 `hermes_cli` 的 import 归零、运行时零调用。文中出现的 `hermes_cli`、`HERMES_*`、Hermes 路径、Hermes workspace 或 Hermes fallback，均属于禁止项、异常残留或验收失败证据，不属于 MCP 的正常实现依赖。

本方案的核心结论是：

> MCP 服务是供 Codex 或其他独立 MCP Client 调用的工具集合。Hermes 不参与该调用链；MCP 服务不得导入、调用、读取或依赖 Hermes 的任何代码、运行时、工作区或内部对象。

同时明确：本项目中的全部 MCP 服务不需要安全审查，也不建设安全隔离体系。MCP 是本地受控环境中由 Codex 调用的工具，不是面向不可信公众开放的账户平台或多租户 SaaS。

本方案只处理 MCP 独立项目本身及其直接依赖。不得把 MCP 业务逻辑放回 Hermes，也不得通过修改 Hermes 来满足 MCP 的独立性验收。

### 1.1 当前仓库核验快照（2026-08-03）

上面的内容是目标架构和本方案采用的边界，不是对当前提交已经达标的宣称。对当前工作区的只读核验结果如下：

- 根 `pyproject.toml` 的 distribution 仍是 `hermes-agent`；`mcp_servers/pyproject.toml` 当前不存在，因此 `mcp_servers/` 还不是可独立构建的 Python distribution。
- `mcp_servers/` Python 源码命中 116 处独立性违规扫描结果，其中 104 处为 `hermes_cli` 导入，另有 `tools`/`agent` 导入和 `HERMES_*` 环境变量引用。
- `hermes_cli/` 仍有 37 处直接导入 `mcp_servers`，覆盖财务计算、档案、模板、审查、资料和报告等模块。
- `mcp_servers/README.md` 仍明确写有“业务实现继续复用 `hermes_cli`、`tools` 与既有财务/DR/文档服务”。

因此当前代码状态应标记为 `implementation_incomplete` / `non_conforming`，而不是“已独立”。这些引用是待清理的真实实现依赖，不是方案允许的调用关系。方案的可行性结论为：目标架构可行，但必须先完成 MCP 自有 runtime、package、存储和领域实现，再通过独立安装和双向无调用验收。

## 2. 强制边界

### 2.1 允许的调用关系

```text
用户
  -> Codex
      -> MCP Client
          -> MCP Server
              -> MCP 自有领域逻辑
              -> MCP 自有存储
              -> 外部公开服务（例如 Tavily）

其他独立 MCP Client
  -> MCP Server

Hermes（范围外）
  不连接 MCP
```

### 2.2 禁止的调用关系

```text
MCP Server -> import hermes_cli
MCP Server -> Hermes 私有函数或内部 API
MCP Server -> Hermes workspace/data/config 目录
MCP Server -> 另一个 MCP Server 的 service/store Python 模块
MCP Server -> 运行时回退到 Hermes 实现
Hermes -> MCP Server（本项目不接入 Hermes 调用）
```

以下做法全部禁止：

```python
from hermes_cli import source_files_api
from hermes_cli.finance import run_service
from hermes_cli.keyui_workspace import workspace_root

try:
    from lvke_mcp.domain import run
except ImportError:
    from hermes_cli.finance import run_service as run
```

### 2.3 独立性的完成定义

MCP 服务必须同时满足：

1. 可以作为单独 Python distribution 构建和安装。
2. 干净环境中不安装 Hermes 仍可启动全部正式 MCP Server。
3. MCP 源代码不存在 `hermes_cli` import。
4. MCP 不读取 Hermes 的仓库根目录、工作区、数据库、配置和环境变量。
5. MCP 的测试不需要启动 Hermes。
6. Hermes 删除或不可用时，MCP 功能不受影响。
7. Hermes 与 MCP 之间不存在运行时调用；MCP 只由 Codex 或其他独立 MCP Client 通过 stdio 或 Streamable HTTP 等标准 MCP transport 调用。

### 2.4 不建设安全审查和安全隔离

本项目不对任何 MCP Server 开展或引入所谓的安全审查，独立项目建设也不包含安全体系建设。所有 MCP 服务统一遵守以下决定：

1. 不提供登录、注册、账号、会话和认证流程。
2. 不要求 `actor_id`、authenticated actor 或宿主身份注入。
3. 不建立财务复核人、业务复核人、法务复核人、报告复核人、最终审批人等角色权限矩阵。
4. 不建立 RBAC、ABAC、权限字符串、职责分离或越权判定。
5. 不建立 tenant 认证、tenant ownership、tenant scope hash 或跨 tenant 隔离。
6. 不对 Resource URI、cursor、token、分页快照附加安全主体或租户绑定。
7. 不设置“通过安全审查后才能生成、导出或读取”的门禁。
8. 不把内容质量检查、财务一致性检查或来源完整性检查描述为安全审查。

以下机制可以保留，但它们只属于工程正确性和数据完整性，不属于安全隔离：

- `workspace_id`：区分不同生成任务的数据目录。
- 输入 schema：避免错误参数导致计算失败。
- content hash、basis hash、lineage：保证结果可复算和可追踪。
- 幂等键、原子写入、文件锁：避免重复执行或并发写坏数据。
- 文件类型、大小和解析失败检查：保证工具能够稳定处理输入，不形成安全审批流程。
- `draft/partial/formal` 标记：表达资料和成果完整度，不表达安全等级。

如果未来部署环境发生根本变化，例如 MCP 被直接暴露为公共互联网服务，应由部署层另行处理网络和基础设施问题，不能把相关逻辑重新塞入本 MCP 业务工具和生成流程。

## 3. 功能保留原则

独立项目建设不等于删减业务能力。需要冻结的是外部可观察功能，而不是内部模块结构。

### 3.1 必须保留

- 项目上下文、输入适用性和规划对象。
- 文件导入、分块上传、解析、重试、取消和 Resource 读取。
- 公开资料发现、采集、快照、去重和来源状态。
- 数据分析、候选字段、冲突记录和 EvidencePack。
- FinanceSpec、确定性 FinanceRun、场景、敏感性和 Monte Carlo。
- 十三表生成、校验、CSV、XLSX 和 Resource 读取。
- ResearchPackage 和研究任务的可恢复执行。
- 报告草稿、修订、校验、DOCX 导出。
- 资产收购模型及对应表格。
- 可选的内容质量检查和知识整理能力。
- 内容 hash、basis hash、lineage、幂等和不可变对象语义。

### 3.2 不作为兼容目标

- Hermes 私有函数名和模块结构。
- Hermes document revision ID 或其他 Hermes 原生对象 ID。
- Hermes workspace、数据库、配置和目录布局。
- 硬编码的 `mcp-local-agent` 或其他伪 actor。
- 普通生成路径上的登录、角色、签审和发布前置条件。
- tenant、actor、RBAC、职责分离和安全审查状态。
- MCP 内部跨服务的直接 Python 调用。
- 重复的成功状态字段和重复工作流 envelope。

### 3.3 兼容策略

- 在一个明确的兼容期内保留现有 MCP tool name。
- 保留常用输入字段，并通过 schema alias 兼容旧字段。
- 对既有 MCP Resource URI 提供显式解析或版本映射，只读取 MCP 自有目录。
- 输出对象允许新增可选字段，不随意删除现有必要字段。
- 使用冻结 fixture 和 golden result 验证行为，不在生产代码中双写或回退 Hermes。

## 4. 目标代码结构

MCP 项目目标是采用可独立构建、安装和发布的包结构。当前仓库只是代码共存位置；截至当前核验，它仍未达到该目标，根项目仍是 `hermes-agent`，`mcp_servers/` 尚无自己的 `pyproject.toml`：

```text
mcp_servers/
├── pyproject.toml
├── README.md
├── dev-docs/architecture/MCP_INDEPENDENCE_PLAN.md # 独立化开发方案
├── src/lvke_mcp/
│   ├── runtime/
│   │   ├── config.py
│   │   ├── workspace.py
│   │   ├── storage.py
│   │   ├── resources.py
│   │   ├── jobs.py
│   │   ├── idempotency.py
│   │   └── transport.py
│   ├── contracts/
│   │   ├── envelope.py
│   │   ├── errors.py
│   │   ├── objects.py
│   │   └── schemas/
│   ├── domains/
│   │   ├── planning/
│   │   ├── sources/
│   │   ├── acquisition/
│   │   ├── analysis/
│   │   ├── research/
│   │   ├── finance/
│   │   ├── finance_tables/
│   │   ├── reports/
│   │   ├── asset_acquisition/
│   │   ├── review/
│   │   └── knowledge/
│   └── servers/
│       ├── project_planning.py
│       ├── source_files.py
│       ├── data_acquisition.py
│       ├── data_analysis.py
│       ├── deep_research.py
│       ├── finance_model.py
│       ├── finance_tables.py
│       ├── report_generation.py
│       ├── asset_acquisition.py
│       ├── deliverable_review.py
│       └── knowledge_governance.py
└── tests/
    ├── contract/
    ├── unit/
    ├── integration/
    ├── conversational/
    └── fixtures/
```

兼容入口只能导入 MCP 自有模块，不能导入 Hermes，也不能通过 Hermes 提供缺省实现。

## 5. 分层和依赖规则

每个正式服务统一使用以下方向：

```text
MCP transport/server
  -> application service/use case
      -> domain logic
      -> repository/provider port
          -> MCP-owned adapter
```

具体规则：

1. `server` 只负责工具注册、JSON Schema、协议错误转换和 Resource 注册。
2. `application` 负责单个领域用例，不承担跨 MCP 编排。
3. `domain` 是纯 Python 业务逻辑，不读取环境变量和文件系统。
4. `repository/provider` 使用 Protocol 或 ABC 定义能力。
5. 文件、SQLite、HTTP、Tavily 和 DOCX/XLSX 均作为 adapter 实现。
6. `service -> server._tool_*` 的反向依赖必须消除。
7. 一个领域不得 import 另一个领域的私有 store；共享对象通过公共 contract 和通用 ObjectRepository 读取。

## 6. MCP 自有运行时

### 6.1 配置

MCP 只使用自己的配置：

```text
LVKE_MCP_DATA_DIR       MCP 数据根目录
LVKE_MCP_CONFIG_DIR     MCP 配置目录
LVKE_MCP_TEMP_DIR       MCP 临时目录
LVKE_MCP_PROFILE        core 或 formal
TAVILY_API_KEY          公开检索 provider
```

禁止读取 `HERMES_*` 环境变量。默认数据目录必须由 MCP distribution 自己确定，不能通过仓库相对路径寻找 Hermes。

### 6.2 Workspace

独立 workspace 布局：

```text
${LVKE_MCP_DATA_DIR}/
└── workspaces/{workspace_id}/
    ├── objects/
    ├── resources/
    ├── sources/
    ├── jobs/
    ├── documents/
    ├── exports/
    └── locks/
```

`workspace_id` 只是一次生成任务的数据命名空间，不是用户账户、租户、安全域、登录身份或审批角色。不同 workspace 的目录分开是为了避免任务结果互相覆盖，不是安全隔离。

### 6.3 对象和 Resource

统一对象最小结构：

```json
{
  "object_id": "finrun_xxx",
  "object_type": "FinanceRun",
  "workspace_id": "ws_xxx",
  "schema_version": "1.0",
  "status": "complete",
  "producer": "lvke-finance-model",
  "created_at": "ISO-8601",
  "content_hash": "sha256:...",
  "basis_hash": "sha256:...",
  "source_ids": [],
  "lineage": {},
  "payload": {}
}
```

Resource URI 由 MCP 自己拥有：

```text
lvke://{domain}/workspaces/{workspace_id}/{collection}/{object_id}
```

Resource resolver 只能访问 MCP 数据根目录。

### 6.4 文档和修订

建立 MCP 自己的 DocumentStore：

```text
documents/{document_id}/
├── revisions/{revision_id}.json
├── content/{content_hash}.md
└── exports/{artifact_id}.docx
```

支持：

- 创建草稿。
- 获取当前或指定 revision。
- 基于 revision 生成 diff。
- 基于 expected revision 原子 apply。
- 章节校验。
- DOCX 导出。

不得再调用 Hermes document service，也不得同时维护 Hermes revision 和 MCP revision。

### 6.5 Job 和幂等

统一 JobRepository，至少包含：

```text
queued -> running -> complete | partial | failed | cancelled
```

幂等键在执行前原子预留：

- 同 key、同 input hash：返回相同终态对象。
- 同 key、不同 input hash：返回 `idempotency_conflict`。
- 崩溃恢复只依赖 MCP 自己的 job/event/checkpoint 数据。

内部 checkpoint 可以保留，但只有真正需要 Codex 操作时才公开成工具。

## 7. Codex 与 MCP 的职责

### 7.1 Codex 负责

- 理解用户意图。
- 确定调用哪些 MCP 工具。
- 在多个 MCP Server 之间编排顺序。
- 根据来源和计算结果撰写正文。
- 对 warnings 和 blockers 向用户解释。
- 在确有必要时询问缺失业务参数。

### 7.2 MCP 负责

- 文件和公开来源的确定性处理。
- 数据对象固化和 Resource 提供。
- 财务计算和十三表。
- 报告内容的存储、修订、校验和导出。
- hash、lineage、幂等和错误诊断。
- 可选的内容、数字和来源质量检查。

### 7.3 MCP 不负责

- 再启动一个隐藏 LLM 或 Agent。
- 在服务内部代替 Codex 编排其他 MCP 服务。
- 要求普通生成用户登录或选择审批角色。
- 在草稿产生前执行正式发布门禁。
- 识别调用者身份、校验角色权限或执行安全审查。
- 建立 tenant、用户、角色或 Resource 访问隔离。

## 8. 生成与质量检查分离

### 8.1 默认 core profile

默认链路：

```text
project_create
-> source_add
-> research_run
-> finance_run
-> finance_tables_generate
-> report_generate_draft
-> artifact_read/export
```

缺少证据、财务输入或十三表时，允许生成：

```text
status=partial
delivery_class=draft
formal_delivery_ready=false
warnings=[...]
blockers=[仅针对正式用途的缺口]
```

缺项不得导致“没有任何草稿产生”。

### 8.2 可选质量审查 profile

只有用户明确要求检查报告质量、财务一致性或来源完整性时才加载：

```text
review_prepare
-> review_start
-> finding/remediation/retest
-> review export
```

该 profile 只检查已经存在的 artifact 内容，不是安全审查，不认证人员身份，也不执行角色签审、职责分离或安全发布审批。普通生成不要求 actor、role、attestation 或 release。

`review_export` 仅表示导出了内容质量检查结果，不能反向阻止 core profile 生成或导出草稿。是否把成果用于外部正式场景，由使用成果的人在 MCP 系统之外自行决定。

## 9. 跨服务协作方案

跨服务有两种允许方式：

### 9.1 Codex 编排（默认）

Codex 调用上游 MCP，取得对象或 Resource URI，再把必要输入传给下游 MCP。这是默认且推荐的方式。

### 9.2 标准 MCP Client 编排（仅必要时）

若确实需要一个 convenience orchestrator，它必须是边界外的 MCP Client，通过标准 MCP transport 调用各服务。它不能位于任何领域 service 内，也不能 import 目标服务模块。

`lvke_zero_material_delivery` 如果承担与 Codex 重复的内部编排职责，处理方式：

1. 优先删除该内部 orchestrator，由 Codex 完成同样调用链。
2. 如需保留“一键生成”，将其改造成单独 MCP Client 应用。
3. 禁止直接调用 `package_service.start_agent`、finance service、tables service 或 report service。
4. 禁止硬编码 actor。

## 10. 各模块独立实现清单

| MCP 模块 | 独立项目职责 | 实现要求 |
|---|---|---|
| `_common/artifact_store.py` | MCP 对象和 Resource 存储 | 只使用 MCP WorkspaceResolver 和 ObjectRepository |
| `_common/official_server.py` | MCP transport、协议、Schema 和错误转换 | 使用 MCP 自有 build metadata，不读取外部宿主身份或路径 |
| `lvke_source_files` | 文件导入、解析、恢复和 Resource | 使用 MCP 自有 SourceRepository、parser job 和领域错误 |
| `lvke_data_acquisition` | 公开来源发现、采集和快照 | 使用 MCP 自有 provider adapter 和 SourceRecord contract |
| `lvke_data_analysis` | 结构化分析和 EvidencePack | 使用 MCP 自有 analysis domain 和公共对象 contract |
| `lvke_deep_research` | ResearchJob、checkpoint 和 ResearchPackage | 只使用 MCP 自有任务、事件和 checkpoint repository |
| `lvke_finance_model` | FinanceSpec、FinanceRun 和分析 | 使用 MCP 自有 finance domain，不调用其他服务私有 handler |
| `lvke_finance_tables` | 十三表、校验和工件导出 | 只消费公共 FinanceRun contract，拥有自有 renderer/exporter |
| `lvke_report_generation` | DocumentStore、Revision、校验和 DOCX | 只消费公共对象和 Resource，不共享其他领域私有 Store |
| `lvke_asset_acquisition` | 资产收购模型和表格 | 使用 MCP 自有 finance primitives 和独立工件存储 |
| `lvke_deliverable_review` | 内容、数字、来源和结构质量检查 | 只检查 MCP Resource/Object，属于可选质量检查 profile |
| `lvke_knowledge_governance` | 知识候选、复核和版本化 | 使用 MCP 自有 candidate/review/release repository |
| `lvke_zero_material_delivery` | 可选的一键业务编排 | 优先由 Codex 编排；保留时必须是边界外 MCP Client |
| `excel_bridge` | XLSX/CSV 读写适配器 | 只消费公共 FinanceRun/FinanceTables schema，或合并到 exporter adapter |

任何 MCP 领域算法、适配器或运行时组件必须：

1. 位于 MCP 独立项目的 domain、application 或 adapter 包。
2. 不依赖外部宿主的数据结构、全局状态或路径。
3. 使用 MCP contract 作为输入输出。
4. 建立独立单元测试和 golden fixture。
5. 不与 Hermes 源文件保持运行时链接；任何命中均视为不合规残留。

## 11. 公共响应契约简化

标准结果建议统一为：

```json
{
  "status": "ok | partial | blocked | failed",
  "data": {},
  "resource_uris": [],
  "warnings": [],
  "blockers": [],
  "next_actions": [],
  "trace_id": "mcp_xxx",
  "content_hash": "sha256:...",
  "basis_hash": "sha256:...",
  "lineage": {}
}
```

兼容期内可以保留 `success`，但必须消除 `business_success`、`system_success`、`transport_success`、`completed`、`outcome` 等含义重叠字段。协议级失败由 MCP error 表达，业务缺项使用 `partial` 或 `blocked`。

## 12. 独立项目实施阶段

### 阶段 0：冻结独立项目基线

- 冻结所有正式 Server 的 `tools/list`。
- 保存输入输出 schema。
- 保存典型 Resource URI 和对象样本。
- 保存财务、十三表、CSV、XLSX、DOCX golden fixture。
- 记录当前错误码和兼容别名。
- 建立独立项目依赖清单和禁止依赖扫描基线。

完成标准：先完成当前实现依赖盘点，再能够在不依赖 Hermes 的前提下比较版本间的 MCP 外部行为；盘点阶段不得把现有违规引用误记为已完成。

### 阶段 1：完善独立 package 和 runtime

- 新建 MCP 自有 `pyproject.toml` 和独立依赖锁定文件。
- 建立 config、workspace、storage、resource、job 和 transport。
- 将 `_common` 中通用能力收敛到 MCP runtime。
- 固化 MCP 自有配置、存储、Resource、Job 和 transport 边界。

完成标准：最小 scaffold Server 可在未安装 Hermes 的虚拟环境启动并完成 initialize、tools/list、call_tool 和 resources/read。

### 阶段 2：完善基础资料链

- 完善 source files。
- 完善 data acquisition。
- 完善 data analysis。
- 完善 deep research 的 job/store/provider。

完成标准：从本地文件或 Tavily 来源生成 MCP 自有 EvidencePack/ResearchPackage，全程不读取 Hermes。

### 阶段 3：完善规划、财务和十三表

- 完善参数解析和 FinanceSpec。
- 完善确定性财务模型。
- 完善场景、敏感性和 Monte Carlo。
- 完善十三表 renderer、校验和导出。
- 对照冻结 fixture 做数字回归。

完成标准：FinanceRun 和十三表关键数字、公式、hash 与既定容差一致。

### 阶段 4：完善报告和文档工件

- 建立 DocumentStore。
- 完善 report prepare/generate/revision/validate。
- 完善 DOCX exporter。
- 修改缺项逻辑：允许 partial draft，正式用途才 fail-closed。

完成标准：Codex 可以在资料不完整时生成并导出明确标记的 draft DOCX。

### 阶段 5：完善可选领域

- 完善资产收购。
- 完善知识治理。
- 将 deliverable review 放入可选内容质量检查 profile。
- 删除 deliverable review 中的 actor、角色签审、职责分离和 release 权限门禁，只保留内容、数字和来源质量检查。
- 删除或外移 zero-material 内部 orchestrator。

完成标准：所有 profile 均不注册必须由特定身份或角色完成的工具；质量检查 profile 可按需单独启用。

### 阶段 6：独立性锁定与发布

- 保持所有 Hermes import、环境变量、路径推断和运行时 fallback 为零。
- 保持 Hermes -> MCP 的直接导入、进程内实例化和业务调用为零。
- 保持跨 MCP service/store 私有 import 为零。
- 删除 actor、tenant、RBAC、职责分离和安全审查代码。
- 更新 README、manifest 和启动命令。

完成标准：静态门禁、独立安装和 MCP-only 回归全部通过。

## 13. 测试和验收矩阵

### 13.1 静态独立性

```bash
rg -n --glob '*.py' --glob '*.toml' --glob '*.yaml' --glob '*.yml' \
  "hermes_cli|HERMES_|\.hermes|keyui_|from tools|from agent|import tools|import agent" \
  mcp_servers
```

预期结果：MCP 源码和运行配置零匹配。文档可以说明禁止依赖，但文档、测试 fixture 和变更记录不应被当作运行时依赖扫描结果。

增加自动测试，解析 Python AST 并拒绝：

- `hermes_cli`、`tools`、`agent` 等 Hermes 项目模块 import。
- 从项目根目录推断数据路径。
- 跨领域私有模块 import。
- `service` import `server`。
- actor、tenant、RBAC 身份和权限模块重新进入 MCP runtime。

### 13.2 独立构建

```bash
cd mcp_servers
python -m build
python -m venv /tmp/lvke-mcp-clean
/tmp/lvke-mcp-clean/bin/pip install dist/*.whl
```

验收环境不得安装当前项目或 Hermes。

### 13.3 协议验收

每个正式 Server 验证：

- initialize。
- tools/list。
- 正常调用。
- 缺字段。
- 错误枚举。
- 幂等重复。
- 幂等冲突。
- resources/list/read。
- 分页和 cursor。
- 任务失败和恢复。

### 13.4 功能回归

- 文件内容和解析结果一致。
- 公开来源 locator、快照和冲突保留。
- 财务关键指标在规定容差内一致。
- 十三表表数、结构、公式和跨表关系一致。
- CSV/XLSX/DOCX 能实际打开和回读。
- content hash、basis hash 和 lineage 可复算。
- partial 草稿可以生成，且不能伪装为 formal。

### 13.5 进程和并发

- 全新 workspace 执行完整链路。
- 32 并发幂等请求。
- 32 独立 workspace 请求。
- 同 workspace 冲突写入。
- 进程中断后的 job 恢复。
- Resource 并发读取和分页快照稳定性。

## 14. CI 强制门禁

独立化后 CI 至少包含：

```text
mcp-lint
mcp-no-hermes-imports
mcp-layering-check
mcp-no-auth-or-tenant-gates
mcp-unit
mcp-contract
mcp-golden-finance
mcp-artifact-roundtrip
mcp-clean-install
mcp-stdio-smoke
mcp-conversational-acceptance
```

其中 `mcp-no-hermes-imports` 和 `mcp-clean-install` 为合并阻断项，不能只给 warning。

## 15. MCP 数据和版本升级

MCP 运行时只读取 MCP 自有数据目录。不同版本之间如需升级对象格式，使用 MCP 自有、离线、可审计的版本升级工具：

```text
MCP 旧版本目录只读导出
-> 校验 schema/hash
-> 转换为新版本 MCP 对象
-> 写入 LVKE_MCP_DATA_DIR
-> 生成 upgrade manifest
```

升级工具是临时开发工具，不打入 MCP 运行时 wheel。升级完成后，正式 MCP 服务只读取 MCP 自有目录。

升级 manifest 至少记录：

- old object/schema identifier。
- new object ID/Resource URI。
- source content hash。
- upgraded content hash。
- schema version。
- upgrade timestamp。
- 转换 warning 或失败原因。

禁止 MCP 正式服务在新目录找不到对象时静默读取任何外部项目目录。

## 16. 发布和回滚

### 16.1 发布方式

1. 发布 MCP 独立 wheel 和独立启动配置。
2. 使用新的空数据目录执行 MCP-only 验收。
3. Codex profile 切换到目标 MCP 版本。
4. 观察期内保留上一 MCP 版本配置，但不双写、不自动回退。
5. 验收完成后删除旧版本注册项。

### 16.2 回滚方式

回滚只能在 MCP Client 配置层切回上一 MCP 版本，不能在 MCP 内部调用 Hermes 或任何外部项目 fallback。不同版本数据目录必须分离，避免对象格式互相污染。

回滚触发条件：

- MCP 协议初始化失败。
- 关键财务结果超出冻结容差。
- 十三表或 DOCX/XLSX 无法回读。
- 已承诺工具在独立包中缺失。
- 数据升级 hash 不一致。

## 17. 风险和处理

| 风险 | 处理方式 |
|---|---|
| 独立实现改变财务口径 | 冻结输入和 golden result，逐字段比较 |
| 一次性大改导致难以定位问题 | 按 runtime、资料、财务、报告分阶段发布 |
| 为兼容外部系统重新引入 fallback | CI 静态门禁直接阻断 |
| 跨服务对象格式漂移 | 独立 versioned contracts 和 contract tests |
| 旧 MCP 对象无法升级 | 提供一次性离线升级器，不做外部目录回读 |
| 工具数量和状态机继续膨胀 | core profile 只保留任务导向工具，恢复细节内部化 |
| 正式门禁再次阻止草稿 | 对 partial draft 建立强制回归测试 |

## 18. 建议执行批次

为降低风险，建议按以下批次提交，每个批次都必须独立可测试：

1. `mcp-runtime-bootstrap`：独立包、配置、workspace、storage、transport。
2. `mcp-contract-freeze`：对象 schema、响应、Resource 和 golden fixture。
3. `mcp-source-independent`：source files、acquisition、analysis。
4. `mcp-research-independent`：research provider、job、checkpoint。
5. `mcp-finance-independent`：FinanceSpec、FinanceRun、分析模型。
6. `mcp-tables-independent`：十三表、CSV、XLSX。
7. `mcp-report-independent`：DocumentStore、draft、revision、DOCX。
8. `mcp-optional-domains`：资产收购、知识治理、可选内容质量 review。
9. `mcp-independence-lock`：独立性、分层和依赖树门禁。
10. `mcp-clean-acceptance`：全新环境和全新 workspace 最终验收。

不得在同一个批次同时搬移全部领域代码和删除全部旧入口。每个批次先达到外部契约等价（含与冻结 golden fixtures 逐项一致），再进入下一批次。

## 19. 最终验收清单

- [ ] `mcp_servers` 拥有独立 `pyproject.toml` 和 lock/dependency definition。
- [ ] 全部正式 Server 可以从独立 wheel 启动。
- [ ] Python 源码中不存在 `hermes_cli` import。
- [ ] 不读取任何 Hermes 环境变量、配置和工作区。
- [ ] 不直接 import 其他 MCP Server 的 service/store。
- [ ] 不存在登录、注册、认证、actor、tenant、RBAC、职责分离或安全审查门禁。
- [ ] Resource、cursor、token 和 workspace 不绑定用户、actor 或 tenant。
- [ ] Codex 可以完成资料、研究、财务、十三表和报告草稿链路。
- [ ] 缺少正式资料时仍能产生明确标记的 partial/draft。
- [ ] FinanceRun 和十三表通过 golden 数字回归。
- [ ] Resource、CSV、XLSX、DOCX 均实际回读成功。
- [ ] 幂等、冲突、并发和恢复测试通过。
- [ ] 所有 profile 均不要求登录、actor、角色或安全审批。
- [ ] 可选 review 只做内容、数字和来源质量检查，不做安全审查。
- [ ] Skills 仅描述 Codex 如何调用独立 MCP，不承载或调用 Hermes 代码。
- [ ] README 明确 MCP 是独立项目，不声明复用 Hermes 业务实现。
- [ ] 干净环境 MCP-only 对话式验收通过。

## 20. 决策摘要

本方案作出以下不可逆的架构决策：

1. MCP 是独立服务，不是 Hermes 适配层。
2. Hermes 不参与 MCP 调用链；MCP 只接受 Codex 或其他独立 MCP Client 的标准 MCP 调用。
3. Codex 是默认跨 MCP 编排者。
4. MCP 自己拥有 workspace、对象、任务、文档和工件。
5. MCP 业务算法、运行时和工件始终归 MCP 项目自己维护。
6. 普通生成和可选内容质量检查是两条不同的能力链。
7. 缺少正式依据只能降低交付等级，不能阻止草稿产生。
8. 任何 Hermes runtime fallback 都视为独立化失败。
9. 全部 MCP 服务不建设安全审查、安全隔离、身份认证、租户或角色权限体系。
10. workspace、hash、幂等和并发锁只用于工程正确性，不得包装成安全机制。
11. MCP 领域计算层采用「直接搬移」：从 `hermes_cli` 现有实现原样搬进 MCP 自有包（import 路径改写、业务行为不改、不重写）；冻结的 golden fixtures 是搬移忠实度证明，搬移后逐项与基线一致，任何数值或行为差异视为搬移失败。
12. 外部公开检索（Tavily 等）通过已配置的 MCP 搜索服务调用，MCP 服务不直接持有外部 API 凭据；搜索 provider 可用性由该 MCP 服务层负责，服务不可用时如实返回 `upstream_failure`。

## 21. 可用性保证的判定

方案文档完成不等于 MCP 版本已经通过发布验收。必须区分三个状态：

```text
方案完成：独立项目边界、实现规格和验收方法已经明确
实现完成：目标 MCP 功能、契约和工件已由 MCP 自有代码提供
验收完成：独立 wheel 在干净环境通过完整 MCP-only 回归
```

只有“验收完成”后，才允许声明 MCP 可以独立运行且核心功能正常。不得用以下结果代替验收：

- 仅通过 `tools/list`。
- 仅成功启动进程。
- 仅生成对象 ID 或空 package。
- 仅生成格式正确但数字未经回勾的 XLSX/CSV。
- 仅生成 DOCX 文件但未实际回读正文、表格和引用。
- 在 Python 环境中仍能间接 import Hermes。
- 使用旧 workspace、旧 FinanceRun 或旧 ResearchPackage 冒充新实现结果。

独立可用的最终证据必须全部来自：

```text
独立 MCP wheel
+ 未安装 Hermes 的干净 Python 环境
+ 新建 MCP workspace
+ 新建 Source/Research/Finance/Tables/Report 对象
+ 实际 Resource 和工件回读
```

## 22. 财务模型独立实现规格

### 22.1 目标边界

`lvke-finance-model` 必须拥有完整的确定性财务计算内核。它不能继续作为 Hermes finance service 的包装器，也不能通过报告、十三表或资产收购服务间接取得计算结果。

目标模块：

```text
domains/finance/
├── contracts.py
├── normalize.py
├── validate.py
├── investment.py
├── revenue.py
├── cost.py
├── tax.py
├── depreciation.py
├── working_capital.py
├── debt.py
├── cashflow.py
├── statements.py
├── indicators.py
├── scenarios.py
├── monte_carlo.py
├── consistency.py
└── engine.py
```

实现方式（直接搬移）：

- `hermes_cli.finance` 现有计算层（约 3.5 万行）原样搬入上述 `domains/finance/` 模块，import 从 `hermes_cli.finance.*` 改写为 `lvke_mcp.domains.finance.*`，不重写业务逻辑。
- 搬移完成后，`lvke-finance-model` 对 `hermes_cli` 的 import 归零，运行时零调用。
- §22.2–§22.8 的规格是对已搬代码的**符合性检查**：搬入的实现不满足规格时修改搬入代码，不得放宽规格；与冻结 golden fixtures 的逐项对比是搬移忠实度证明，任何差异（含静默丢字段）都算搬移失败。

### 22.2 FinanceSpec v1

必须在 MCP 内维护 versioned JSON Schema，至少覆盖：

| 分组 | 必需内容 |
|---|---|
| 项目 | 项目类型、建设期、运营期、计算期、币种、金额单位 |
| 投资 | 工程费、其他费、预备费、建设期利息、流动资金 |
| 建设明细 | 建筑、设备、安装、工程量、估算指标、数量和单价 |
| 收入 | 产品/服务、产能、销量、价格、爬坡率、增长率、收入公式 |
| 成本 | 原辅料、燃料动力、工资福利、修理、管理、销售、其他成本 |
| 税费 | 增值税、进项税、销项税、附加税、所得税、其他适用税费 |
| 资产 | 固定资产、无形资产、其他资产、残值率、折旧/摊销年限 |
| 融资 | 资本金、贷款、提款计划、利率、宽限期、还本方式 |
| 评价 | 基准收益率、折现率、评价口径、期末回收和残值 |
| 不确定性 | 场景参数、敏感性参数、Monte Carlo distributions |

关键收入输入必须 fail-closed：

- 有 `annual_revenue_wan`；或
- 有完整的分产品数量、价格、爬坡和期间公式。

缺少收入驱动时可以保存 `draft FinanceSpec`，但不得创建声称已经计算完成的 FinanceRun。

原 `finance_confirm_spec` 的人工 actor 确认语义删除。替换为确定性操作：

```text
finance_validate_spec
finance_freeze_spec
finance_run_model
```

`freeze` 表示输入快照不可变，不表示人员审批。

### 22.3 输入规范化

MCP 自有 normalizer 必须处理：

- 万元、元、百分比和小数比例的单位转换。
- 年、月和建设期月份转换。
- 旧字段别名到 canonical field 的映射。
- 顶层字段与嵌套字段冲突。
- 空字符串、`null`、零和缺失的差异。
- 分产品收入与年度汇总收入冲突。
- 总投资与分项投资冲突。
- 资本金、贷款和其他资金来源的合计冲突。

所有规范化决定写入 `input_adoption_ledger`；冲突返回精确 JSON Pointer，不静默覆盖。

### 22.4 最低计算公式

所有公式必须在代码和 contract 文档中使用同一 canonical 名称。最低公式如下：

```text
工程费用 = 建筑工程费 + 设备购置费 + 安装工程费 + 其他工程分项
建设投资 = 工程费用 + 工程建设其他费 + 预备费
项目总投资 = 建设投资 + 建设期利息 + 流动资金

产品收入[t,p] = 销量[t,p] * 含税或不含税价格[t,p]
营业收入[t] = sum(产品收入[t,p]) + 其他收入[t]

变动成本[t] = sum(产销量[t,p] * 单位变动成本[t,p])
经营成本[t] = 总成本费用[t] - 折旧[t] - 摊销[t] - 利息支出[t]
总成本费用[t] = 经营成本[t] + 折旧[t] + 摊销[t] + 利息支出[t]

销项税[t] = 应税销售额[t] * 适用增值税率
应纳增值税[t] = max(0, 销项税[t] - 可抵扣进项税[t] - 上期留抵[t])
附加税[t] = 应纳增值税[t] * 附加税综合率
应纳税所得额[t] = 利润总额[t] - 可弥补亏损[t] + 纳税调整[t]
所得税[t] = max(0, 应纳税所得额[t] * 所得税率)

年折旧额 = 折旧基数 * (1 - 残值率) / 折旧年限
年摊销额 = 摊销基数 / 摊销年限

期末贷款余额[t] = 期初贷款余额[t] + 当期提款[t] - 当期还本[t]
利息支出[t] = 按约定时点口径计算的计息余额 * 年化利率

项目投资净现金流[t] = 现金流入[t] - 现金流出[t]
资本金净现金流[t] = 资本金口径流入[t] - 资本金口径流出[t]
财务计划净现金流[t] = 经营 + 投资 + 筹资现金流净额

NPV = sum(CF[t] / (1 + discount_rate)^t)
IRR = 使 NPV 等于 0 的收益率；无根或多重根必须明确标记
DSCR[t] = 可用于还本付息资金[t] / 当期应还本付息额[t]
ICR[t] = 息税前利润或约定可付息资金[t] / 利息支出[t]
```

具体税制、计息时点、折旧起始期和舍入规则必须写入 FinanceSpec，不得隐藏在代码常量中。

### 22.5 一致性判定

以下任一重大不一致都必须使 `consistency_ok=false`：

- 投资分项之和不等于建设投资或总投资。
- 资金来源不等于资金使用。
- 流动资金重复计入或完全遗漏。
- 分产品收入不等于收入合计。
- 成本树不等于成本合计。
- 税费、利润和现金流之间无法勾稽。
- 折旧、摊销与资产原值、年限不一致。
- 借款期末余额不能由期初、提款和还本复算。
- 任一期债务偿还存在资金缺口。
- 生命周期累计现金流存在未解释资金缺口。
- DSCR/ICR 使用的分子分母与明细表不一致。

容差必须按字段类型定义，金额、比例和期间不得共用一个全局容差。

### 22.6 Monte Carlo

`distributions` 使用显式 schema：

```json
{
  "annual_revenue_wan": {
    "distribution": "normal",
    "mean": 10000,
    "stddev": 800,
    "minimum": 0
  }
}
```

允许的首版枚举：`fixed`、`uniform`、`triangular`、`normal`、`lognormal`。每种分布必须定义参数、边界、截断、非法样本处理和单位。

- 相同 spec、distributions、sample count 和 seed 必须产生相同结果。
- 单样本计算失败不能静默排除，必须计入 `failed_samples`。
- 部分样本失败时状态为 `partial`。
- 达到失败比例阈值时整体为 `failed`。
- 输出至少包含 NPV、IRR、最低现金、DSCR 的分位数和失败概率。

### 22.7 FinanceRun v1

FinanceRun 必须完整绑定：

```text
spec_id
spec_hash
input_hash
basis_hash
evidence_pack_ids
evidence_binding_hash
engine_version
calculation_policy_version
run_hash
lineage
```

FinanceRun payload 必须包含十三表所需全部期间明细，不允许十三表服务重新猜测或重新计算关键业务数据。

### 22.8 财务验收

至少冻结六类项目 fixture，每类包含正常、边界、错误和幂等场景。逐项验证：

- 每年收入、成本、税费、利润、折旧、摊销、现金流。
- 贷款提款、利息、还本和期末余额。
- IRR、NPV、回收期、DSCR、ICR。
- 总投资和资金平衡。
- 三场景和敏感性结果。
- 固定 seed Monte Carlo 结果。
- 输入 hash、run hash 和 basis hash 可复算。

金额默认比较到分或明确的万元小数位；IRR/比率使用单独容差。容差必须写入 fixture metadata。

## 23. Deep Research 独立实现规格

### 23.1 职责定义

DR MCP 负责检索、获取、解析、固化和组织研究证据。Codex 负责根据这些证据进行推理和撰写。DR MCP 不启动 Hermes Agent，也不依赖隐藏 LLM gateway。

目标模块：

```text
domains/research/
├── contracts.py
├── query_plan.py
├── providers/
│   └── tavily.py
├── fetch.py
├── extract.py
├── normalize.py
├── deduplicate.py
├── conflict.py
├── citations.py
├── jobs.py
├── checkpoints.py
└── package.py
```

### 23.2 标准生命周期

```text
research_prepare
-> research_start
-> provider search
-> fetch/extract
-> normalize/deduplicate
-> checkpoint
-> research_status
-> research_build_package
```

`resume`、`cancel` 只控制 MCP 自有 ResearchJob。计划修改可以由 Codex 重新调用 `research_prepare` 完成，不需要暴露复杂的人工 plan approval 状态机。

### 23.3 检索 provider（Tavily）

- 正式公开搜索只使用 Tavily。
- Tavily 通过已配置的 MCP 搜索服务（`tavily-hikari`）调用，由该 MCP 服务持有并管理凭据；lvke-data-acquisition / lvke-deep-research 不直接持有外部 API key。搜索 provider 可用性由该 MCP 服务层负责，不构成单点硬依赖。
- Tavily 服务不可用时返回 `upstream_failure`，并如实上报 provider 状态（`data_provider_status`）。
- 不回退到 `mcp_web_search` 或 Hermes 搜索实现。
- 保存 query、provider、请求时间、响应时间、结果排序和 provider request ID。
- 搜索摘要只能作为发现线索，不能直接升级为已回读证据。

### 23.4 来源处理

每个 SourceRecord 至少包含：

```text
source_id
requested_url
final_url
title
publisher
published_at
retrieved_at
mime_type
http_status
content_hash
locator
extracted_text
source_status
duplicate_of
redirect_chain
warnings
```

必须保留：

- URL 失效和 HTTP 状态。
- 重定向前后地址。
- 内容重复和转载关系。
- 同一事实的来源冲突。
- 发布时间与检索时间差异。
- 无法解析或只有摘要的状态。

公开无法取得的权属、合同、批复、资本金和贷款资料标记为 `missing_private_material`。这属于资料缺口，不是安全阻断，也不能伪造来源。

### 23.5 ResearchJob 和 checkpoint

ResearchJob 必须持久化：

```text
job_id
workspace_id
query_plan
provider_calls
source_ids
status
progress
checkpoint_id
input_hash
created_at/updated_at
error
```

checkpoint 只保存恢复所需的查询、已处理来源和待处理队列。恢复 token 绑定 job 和 input hash，不绑定 actor 或 tenant。

### 23.6 ResearchPackage v1

只有实际获取并回读的来源可以进入正式 sources。Package 至少包含：

- 研究问题和查询计划。
- SourceRecord IDs。
- 已验证 locator。
- 来源冲突和重复关系。
- 覆盖范围和缺口。
- provider 状态。
- content hash、basis hash 和 lineage。
- `complete` 或 `partial` 状态。

没有真实来源时可以返回 ResearchJob 和缺口清单，但不得创建看似完整的 ResearchPackage。

### 23.7 DR 验收

- Tavily 正常、超时、限流、空结果和上游失败。
- 有效 URL、404、重定向、重复页面和内容变化。
- 中文行政区、政策文号、统计年份和发布时间解析。
- checkpoint 后杀进程并恢复。
- cancel 后不得继续写入结果。
- 相同 input hash 幂等返回相同 package。
- 实际读取全部 package Resource 和来源 Resource。
- 随机抽取 locator，与固化文本和 content hash 对照。

## 24. 十三张财务表独立实现规格

### 24.1 兼容基线冻结

当前源码没有自有 `_TABLE_SPECS`、`DELIVERY_TABLE_KEYS` 和结构化 renderer。因此十三表独立实现的第一步不是猜表名，而是通过当前 MCP 工具对一个冻结 FinanceRun 执行：

```text
tables_render
tables_list_tables
tables_get_table（逐表）
tables_export_csv
tables_export_xlsx
tables_read_resource（逐 Resource）
```

将实际返回的 13 个 `table_id`、编号、标题、顺序、行列、单位、期间和 manifest 固化到：

```text
contracts/finance_tables/v1/registry.json
tests/fixtures/finance_tables/v1/
```

该冻结过程只调用 MCP，不读取 Hermes 源码。冻结后新实现不得运行时读取旧系统。

### 24.2 固定注册表

`registry.json` 必须恰好包含 13 张表。每项至少定义：

```json
{
  "table_id": "stable-id",
  "delivery_no": "附表编号",
  "title": "表名",
  "order": 1,
  "unit": "万元",
  "period_axis": "construction_and_operation_years",
  "required_rows": [],
  "source_paths": [],
  "formula_policy_version": "finance-tables-v1"
}
```

不论旧编号如何，13 张表合起来必须完整表达以下业务内容：

1. 建设投资、工程费、其他费、预备费及投资分类。
2. 建筑、设备、安装明细及工程量乘估算指标。
3. 建设期利息和流动资金计算。
4. 项目总投资使用计划与资金筹措闭合。
5. 分产品量价、爬坡、收入、增值税和附加税。
6. 原辅料、燃料动力、人工及其他成本明细树。
7. 固定资产原值、残值和折旧。
8. 无形资产、其他资产和摊销。
9. 利润、所得税和利润分配。
10. 项目投资现金流。
11. 项目资本金现金流。
12. 借款提款、利息、还本和余额。
13. 财务计划现金流、资金缺口及适用的偿债指标。

若冻结 manifest 的表名或拆分方式不同，保持原 13 个 `table_id`，但必须通过 rows/sections 完整覆盖上述语义。不得为了凑足 13 张表复制空表。

### 24.3 Renderer 输入边界

十三表只消费一个固化 FinanceRun。输入为：

```text
workspace_id
run_id
template_version
format
```

Renderer 不接受散乱财务参数，不调用财务引擎重新计算 IRR、税费或债务。它只做：

- 从 FinanceRun 取期间明细。
- 按 registry 映射为行列。
- 计算展示合计和明确的勾稽单元格。
- 生成 structured table、CSV 和 XLSX。

### 24.4 表结构

每张 structured table 至少包含：

```text
table_id
delivery_no
title
unit
periods
columns
rows
source_run_id
source_paths
formula_refs
content_hash
```

每行必须有稳定 `row_id`、label、unit、values 和 source/formula。禁止把 JSON 字符串塞进 CSV 单元格代替结构化行列。

### 24.5 跨表勾稽

必须自动校验：

- 表内合计等于分项之和。
- 总投资在所有相关表中一致。
- 资金来源等于资金使用。
- 收入、成本、税费与利润表一致。
- 折旧、摊销与成本及现金流一致。
- 利润表与现金流的非现金调整一致。
- 贷款余额与提款、还本一致。
- 利息与成本、现金流一致。
- 期末现金能够由财务计划现金流复算。
- DSCR/ICR 与债务明细一致。

任何数字差异必须返回表、行、列、expected、actual 和 tolerance。

### 24.6 XLSX 和 CSV

XLSX 必须包含：

- 13 个稳定顺序的业务 sheet。
- manifest sheet。
- run_id、package_id、content hash、basis hash 和 lineage。
- 可读的单位、期间、数字格式和公式说明。

CSV package 必须包含：

- 恰好 13 个业务 CSV。
- 一个 manifest JSON/CSV。
- 每个文件的 SHA-256。
- UTF-8 BOM 或契约指定的稳定编码。

partial FinanceRun 可以导出 draft XLSX/CSV，必须标记 `delivery_class=draft` 和 `formal_delivery_ready=false`。这里是成果完整度标记，不是安全审批。

### 24.7 十三表验收

每个验收 FinanceRun 必须执行：

1. 断言 manifest 恰好 13 项且 ID 唯一。
2. 逐表 `tables_get_table`，验证合法行列、单位和期间。
3. 逐表执行 `tables_validate_table`。
4. 执行整包 `tables_validate` 和全部跨表勾稽。
5. 实际读取 13 个表 Resource。
6. 实际读取 CSV manifest 和全部 13 个 CSV。
7. 使用独立 XLSX reader 回读 13 个 sheet。
8. 对关键数字与同一 FinanceRun 逐字段比较。
9. 复算文件 hash、table bundle hash、basis hash 和 lineage。
10. 验证另一个 run_id 不会读取或复用本 package。

## 25. 研报生成独立实现规格

### 25.1 职责边界

Codex 在当前对话中生成报告正文；`lvke-report-generation` 负责确定性地接收、保存、修订、校验和导出。MCP 不调用 Hermes 报告生成器，也不启动第二个 LLM。

目标调用链简化为：

```text
report_prepare
-> report_generate_draft
-> report_get_revision
-> report_diff/report_apply（需要改稿时）
-> report_validate
-> report_export_docx
```

### 25.2 ReportPreparation v1

允许绑定：

- ResearchPackage snapshot 或 Resource URI。
- FinanceRun snapshot 或 Resource URI。
- FinanceTablesPackage snapshot 或 Resource URI。
- EvidencePack snapshot 或 Resource URI。
- 报告 outline、模板版本和项目上下文。

下游服务只能通过公共 ObjectRepository/ResourceResolver 读取这些对象，不能 import 上游 service/store。

上游缺项处理：

```text
完整输入 -> status=ready
部分输入 -> status=partial，仍可生成草稿
输入格式损坏或引用对象不存在 -> status=blocked
```

资料缺少不是格式损坏。缺 ResearchPackage、FinanceRun 或十三表时，应记录 `formal_gaps`，不能阻止 `report_generate_draft`。

### 25.3 正文提交契约

`report_generate_draft` 必须接收 Codex 生成的内容：

```json
{
  "workspace_id": "ws_xxx",
  "report_preparation_id": "rprep_xxx",
  "title": "项目可行性研究报告",
  "content_markdown": "# ...",
  "sections": [],
  "citations": [],
  "expected_basis_hash": "sha256:...",
  "idempotency_key": "..."
}
```

调用成功立即产生 MCP 自有 `ReportRevision`，不需要 actor、人工审批或 Hermes native revision。

### 25.4 DocumentStore 和 Revision

ReportRevision 至少包含：

```text
report_revision_id
parent_revision_id
report_preparation_id
content_markdown
outline
section_index
citations
content_hash
basis_hash
status
created_at
```

`report_diff` 必须基于明确的 base revision。`report_apply` 使用 expected revision/content hash 做原子比较，防止并发覆盖；这是数据一致性，不是权限控制。

重复 apply 返回原结果，不产生重复 revision；旧 revision 上的冲突修改返回 `revision_conflict`。

### 25.5 报告校验

校验分为确定性检查：

- 标题和章节结构。
- 必需章节存在性。
- 空章节和重复章节。
- 正文投资、收入、成本、IRR、NPV、DSCR 等数字与 FinanceRun 一致。
- 正文表格与 FinanceTablesPackage 一致。
- 引用 locator 是否存在于绑定的 ResearchPackage/EvidencePack。
- 技术 fixture、controlled assumption 和搜索摘要是否被明确标记。
- 上游 basis hash 是否发生变化。

质量检查返回 findings、warnings 和 formal gaps，但不得删除草稿或阻止 draft DOCX 导出。

### 25.6 DOCX renderer

MCP 自有 DOCX exporter 必须实现：

- Markdown 标题到 Word heading 映射。
- 段落、列表、表格、图片和分页。
- 自动目录字段或稳定目录页。
- 页眉、页脚、页码和文档元数据。
- 中文字体和英文字体映射。
- FinanceTables 的可读嵌入或附录引用。
- 引用列表和来源 locator。
- draft/partial 水印或文档属性标记。

导出后必须使用独立 DOCX reader 回读：

- 文档非空。
- 标题和章节数量正确。
- 关键数字存在且与 revision 一致。
- 表格数量和关键单元格一致。
- 字体名称合法。
- artifact hash 和 revision content hash 绑定正确。

### 25.7 研报验收

- 完整上游输入生成完整草稿。
- 缺 ResearchPackage 仍生成 partial draft。
- 缺 FinanceRun 仍生成 partial draft，并不伪造财务数字。
- 错误 basis hash 返回冲突。
- 同幂等键返回相同 revision。
- 重复 apply 不生成新 revision。
- 旧 revision 并发 apply 返回冲突。
- 正文数字被修改后 validate 能定位具体字段和章节。
- DOCX 实际回读成功。
- 全链路不产生 Hermes revision、artifact 或目录写入。

## 26. 独立项目依赖合规矩阵

### 26.1 项目基线

MCP 的目标基线是不依赖 Hermes，且 Hermes 不调用 MCP。以下内容不是 MCP 的正常依赖，而是必须持续保持为零的禁止项：

```text
hermes_cli、tools、agent 等 Hermes 项目模块
HERMES_* 环境变量
Hermes workspace、数据库、config、data 和相对路径推断
Hermes runtime fallback、subprocess 和动态 import
```

如静态扫描或动态测试发现以上任一项，状态应为 `non_conforming`，不得将其登记为待修复业务能力，也不得以兼容为理由保留。

当前工作区的扫描结果已经命中 `non_conforming`：MCP 源码存在 `hermes_cli`、`tools`、`agent` 和 `HERMES_*` 引用；Hermes 源码也存在直接导入 `mcp_servers` 的调用。两侧引用都必须清理或移出独立项目运行路径，才能进入最终验收。这里的“清理”是独立性合规处理，不是把 Hermes 纳入 MCP 项目，也不是保留 Hermes 对 MCP 的调用。

### 26.2 机器可读清单

阶段 0 必须生成并提交：

```text
quality/independence_dependency_scan.json
```

每个依赖点包含：

```json
{
  "mcp_file": "...",
  "line": 1,
  "forbidden_reference": "...",
  "capability": "finance_run",
  "owner_module": "lvke_mcp.domains.finance.engine",
  "contract": "FinanceRun.v1",
  "golden_fixture": "...",
  "status": "absent | non_conforming | fixed",
  "verification_test": "..."
}
```

正常版本的清单只允许 `absent`；当前版本必须如实登记为 `non_conforming`，修复后再重新执行独立安装验收。不得把现有 Hermes 引用登记为“待修复业务能力”或兼容项。

### 26.3 分类处置

| 禁止依赖类别 | 独立项目处理方式 | 合规条件 |
|---|---|---|
| 外部 workspace/path | MCP WorkspaceResolver | 只读写 MCP 数据目录，Resource 回读通过 |
| tenant/actor/auth | 不进入 MCP runtime | schema 和 runtime 不出现相关字段或门禁 |
| 外部 source/research task | MCP SourceRepository、ResearchJob | 上传、解析、checkpoint、resume 测试通过 |
| 外部 finance/document service | MCP finance engine、DocumentStore | FinanceRun、Revision 和 golden 回归通过 |
| 外部 table/artifact renderer | MCP table registry、renderer、exporter | 13 表、CSV/XLSX/DOCX 实际回读通过 |
| 外部 review/release gate | MCP 自有内容质量检查 | 不依赖身份、角色或外部发布权限 |

### 26.4 静态独立性门禁

CI 同时执行文本扫描和 AST 扫描：

```bash
rg -n --glob '*.py' --glob '*.toml' --glob '*.yaml' --glob '*.yml' \
  "hermes_cli|HERMES_|\.hermes|keyui_|from tools|from agent|import tools|import agent" \
  mcp_servers

rg -n -P --glob '*.py' \
  '^\s*(from|import)\s+mcp_servers\b|importlib\.(import_module|__import__)\([^)]*mcp_servers' \
  hermes_cli
```

第一条扫描 MCP -> Hermes 依赖，第二条扫描 Hermes -> MCP 的直接 Python 调用。还必须扫描兼容入口、动态 import、字符串模块名、subprocess 命令和配置文件，防止以延迟导入方式绕过检查。

最终两侧扫描中不得存在 `non_conforming`；源码扫描必须为零；独立 wheel 的依赖树不得包含 Hermes distribution 或 Hermes 项目内部模块。

## 27. 四域端到端验收场景

最终验收必须在全新 workspace 依次执行：

```text
1. 创建项目上下文
2. 导入公开资料或本地输入
3. Tavily 检索并形成 ResearchPackage
4. 创建、校验并冻结 FinanceSpec
5. 创建 FinanceRun
6. 生成并读取十三表 package
7. Codex 基于上游对象生成正文
8. MCP 固化 ReportRevision
9. 校验正文数字和引用
10. 导出并回读 DOCX、XLSX、CSV
```

调用日志至少记录：

```text
tool
input summary
input hash
started_at/finished_at/duration_ms
status
code
warnings/blockers/next_actions
object IDs
resource URIs
content hash
basis hash
lineage
```

必须增加以下反向断言：

- Python module graph 不包含 Hermes。
- 运行期间没有打开 Hermes workspace/data/config 文件。
- 输出对象全部位于 `LVKE_MCP_DATA_DIR`。
- 所有对象和工件均由本次新 workspace 生成。
- 删除或隐藏项目根目录后，完整链路仍能运行。

四域完成门槛：

| 领域 | 必须成功的结果 |
|---|---|
| 财务 | FinanceRun 可复算，关键指标和期间明细通过 golden 回归 |
| DR | Tavily 真实来源、SourceRecord、checkpoint 和 ResearchPackage 可读取 |
| 十三表 | 恰好 13 表，逐表、CSV、XLSX、hash 和跨表勾稽通过 |
| 研报 | partial/完整草稿、revision、数字校验和 DOCX 回读通过 |

任何一项未达到，整体状态只能是 `implementation_incomplete`，不得宣布 MCP 已独立可用。

## 28. 实际功能范围说明

### 28.1 用户可见的完整工作流

独立 MCP 的第一目标不是复刻当前内部状态机，而是确保 Codex 可以完成以下真实工作：

```text
用户提出项目需求
-> Codex 创建项目工作区和项目上下文
-> Codex 导入用户文件并调用公开资料检索
-> MCP 固化来源、提取结构化数据并形成研究资料包
-> Codex 补充或选择财务参数
-> MCP 计算 FinanceRun、场景和财务指标
-> MCP 从同一 FinanceRun 生成十三表
-> Codex 基于研究、财务和十三表编写研报正文
-> MCP 保存报告修订、检查数字和引用、导出 DOCX
-> 用户按需要求内容质量检查
```

流程中不存在登录、actor、tenant、角色签审或安全审查步骤。用户不需要在生成过程中逐阶段点击批准。只有缺少无法合理推断的业务参数时，Codex 才向用户询问。

### 28.2 正式 MCP 服务功能

| 服务 | 实际输入 | 必须完成的处理 | 可用输出 |
|---|---|---|---|
| `lvke-project-planning` | 项目名称、地区、行业、类型、规模、目标和约束 | 规范化项目边界；创建上下文、规模、收入/成本驱动、劳动计划和方案版本 | ProjectContext、缺失参数、hash、lineage、Resource |
| `lvke-source-files` | 文本、base64、本地文件或分块内容 | 导入、分块合并、解析、重试、取消；提取页码、sheet 和行列 locator | file_id、parse_job_id、原文件和解析 Resource |
| `lvke-data-acquisition` | 查询、地区、行业、目标来源数 | Tavily 搜索、URL 获取、重定向记录、快照、去重和失效记录 | DiscoverySet、SourceCollection、SourceSnapshot |
| `lvke-data-analysis` | Source/File Resource | 字段候选、表格画像、单位转换、口径比较、冲突和证据组织 | CandidateSet、DataProfile、EvidencePack |
| `lvke-deep-research` | 研究主题、query plan、公开来源策略 | Tavily 检索、回读、SourceRecord、去重、冲突、checkpoint、resume、cancel | ResearchJob、ResearchPackage、来源 Resource |
| `lvke-finance-model` | ProjectContext、EvidencePack、FinanceSpec | 投资、收入、成本、税费、资产、融资、现金流和指标计算 | FinanceSpec、FinanceRun、BOE、场景和 Monte Carlo |
| `lvke-finance-tables` | 固化 run_id | 生成固定 13 表、逐表和跨表校验、CSV/XLSX 导出及回读 | FinanceTablesPackage、13 表 Resource、CSV、XLSX |
| `lvke-report-generation` | 上游对象、outline、Codex Markdown 正文 | preparation、revision、diff/apply、数字/引用校验和 DOCX | ReportRevision、validation、DOCX artifact、Resource |
| `lvke-asset-acquisition` | 收购价格、税费、融资、月度运营和退出参数 | 月度资产收购模型、回报、偿债、估值和专用表格 | AcquisitionRun、分析、CSV/XLSX、Resource |
| `lvke-deliverable-review` | 已生成的报告、FinanceRun 和表格对象 | 内容、数字、结构、引用检查；finding、整改建议和复测 | 质量检查结果及导出工件 |
| `lvke-knowledge-governance` | 已验证片段、参数、方法和来源 | 重复、过期、冲突和适用范围检查；知识对象版本化 | KnowledgeCandidate、KnowledgeEntry、Resource |

### 28.3 项目规划功能细节

- 创建和读取不可变 ProjectContext。
- 记录项目类型、行业、地区、建设内容和报告目标。
- 创建 BuildScale、RevenueDriverSet、CostDriverSet、LaborPlan 和 OptionComparison。
- 用户修改输入时创建新版本，不覆盖旧版本。
- 上游内容变化时返回需要重算的下游对象类型。
- 同一输入和幂等键返回同一对象。

完成条件：财务、研究和报告只消费公共 ProjectContext contract，不 import planning service。

### 28.4 文件和资料功能细节

`lvke-source-files` 必须支持：

- 直接内容导入。
- 大文件分块上传、完成和取消。
- PDF、DOCX、XLSX、CSV、TXT、Markdown 解析。
- 解析状态、失败原因和重试。
- 原始文件、解析文本、页、sheet 和表格 Resource。

文件类型、大小、解码和解析检查只用于保证工具能够稳定处理输入，不形成安全扫描、隔离区或审批流程。

`lvke-data-acquisition` 必须支持：

- Tavily 单查询和多查询发现。
- 目标数量和 partial 结果。
- URL 状态、重定向、内容类型和获取时间。
- 重复 URL、重复内容和转载关系。
- 失效 URL、空结果和 `upstream_failure`。
- 中文行政区、地址和 POI 覆盖诊断。

`lvke-data-analysis` 必须支持：

- 来源摄入和字段 locator。
- 表格字段、类型、单位和期间画像。
- 同名字段的单位、地区、年份和统计口径比较。
- 冲突、缺失和转换规则记录。
- EvidencePack 构建和 Resource 读取。

### 28.5 DR 功能细节

实际执行链：

```text
research_prepare
-> research_start
-> Tavily search
-> URL fetch/extract
-> SourceRecord
-> deduplicate/conflict
-> checkpoint/status
-> ResearchPackage
```

DR 支持任务中断恢复和取消。Codex 读取 ResearchPackage 后完成分析与写作；DR MCP 不调用第二个 LLM，也不调用 Hermes Agent。

### 28.6 财务模型功能细节

实际功能包括：

- FinanceSpec 创建、规范化、校验和冻结。
- 建设投资、建设期利息和流动资金。
- 产品产能、销量、价格、爬坡和收入。
- 成本、税费、折旧、摊销和利润。
- 资本金、贷款、利息和还本。
- 项目投资、资本金和财务计划现金流。
- IRR、NPV、回收期、DSCR 和 ICR。
- 基准、乐观、悲观场景。
- 单因素敏感性和 Monte Carlo。
- 一致性检查和 Basis of Estimate。

FinanceRun 必须包含十三表所需全部期间明细。任何计算结果均可从 FinanceSpec、engine version 和 policy version 复算。

### 28.7 十三表功能细节

- 只读取一个明确 run_id。
- 生成恰好 13 张 structured table。
- 提供逐表 list/get/validate 和 Resource。
- 校验表内、跨表和 FinanceRun 勾稽。
- 导出 13 个 CSV 和 manifest。
- 导出包含 13 个业务 sheet 和 manifest 的 XLSX。
- 使用独立 reader 回读 CSV、XLSX 和 Resource。

十三表服务不重新运行财务模型，也不接受散乱财务参数。

### 28.8 研报生成功能细节

- 创建 ReportPreparation。
- 接收当前 Codex 提交的 Markdown 草稿。
- 创建和读取 ReportRevision。
- 基于明确 base revision 生成 diff 和原子 apply。
- 校验章节结构、正文数字、表格和引用。
- 缺资料时仍保存并导出 partial draft。
- 生成并实际回读 DOCX。
- 提供 revision 和 artifact Resource。

MCP 不自行调用 LLM 写正文。报告正文由当前对话中的 Codex 生成。

### 28.9 资产收购、质量检查和知识功能

`lvke-asset-acquisition`：实现收购价格、税费、融资、交割、月度经营、债务、现金流、估值、退出和专用表格。它复用 MCP 自有 finance primitives，不调用通用 finance server 私有 handler。

`lvke-deliverable-review`：只保留内容、数字、十三表结构、引用 locator、finding、整改建议和复测。删除 actor、角色、职责分离、attestation 和 release 权限门禁。检查结果不能阻止原草稿读取和导出。

`lvke-knowledge-governance`：创建带来源和适用范围的知识候选，执行确定性重复、过期和冲突检查，形成版本化 KnowledgeEntry。不建立 reviewer 身份或知识发布权限。

### 28.10 支撑服务处置

| 支撑服务 | 目标处置 |
|---|---|
| `finance-calc` | 合并到 MCP finance domain 的纯计算 primitives；旧工具保留兼容期 |
| `excel-bridge` | 合并为 finance tables 和 report 的 exporter adapter |
| `lvke-archive` | 独立只读资料库；数据目录由 MCP 配置指定 |
| `lvke-templates` | 模板作为 MCP package resources 管理 |
| `lvke-clients` | 只保留业务联系人资料查询，不演变为账户系统 |
| `lvke-experts` | 只保留专家资料检索，不承担 actor 或审批角色 |
| `policy-search` | 纳入 acquisition/provider 体系或作为独立只读服务 |
| `statistics-cn` | 独立统计数据查询和来源 Resource |
| `industry-research` | 行业静态资料和查询，不替代真实 ResearchPackage |
| `environmental-data` | 环境公开数据查询和来源 Resource |
| `map-geo` | 地址规范化、地理编码和 POI 覆盖诊断 |

## 29. 实际开发流程

### 29.1 单个功能的标准开发流水线

每个工具或领域能力必须按以下顺序开发，不允许先引入外部宿主依赖再补独立实现：

```text
1. 冻结当前 MCP 外部契约和成功样本
2. 建立独立 contract/schema
3. 编写纯 domain 单元测试
4. 实现纯 domain 逻辑
5. 实现 MCP 自有 repository/provider adapter
6. 实现 application service
7. 接入 MCP server 和 Resource
8. 执行 golden 对比和异常测试
9. 执行独立依赖和分层扫描
10. 在干净 wheel 环境复测
```

每个功能任务卡必须填写：

```text
功能名称
当前 MCP tool
输入 schema
输出 schema
禁止的外部宿主依赖扫描结果
目标 MCP module
正常场景
边界场景
错误场景
幂等场景
golden fixture
Resource/artifact
完成测试
独立性扫描证据
```

### 29.2 基线冻结流程

只通过当前 MCP transport 记录行为，不把任何外部项目内部结构作为 MCP contract。

1. 启动当前正式 MCP profile。
2. 冻结 `tools/list` 和全部 JSON Schema。
3. 为每个工具保存正常、缺字段和错误输入响应。
4. 新建一次完整测试 workspace。
5. 保存 ResearchPackage、FinanceRun、十三表和 ReportRevision 样本。
6. 实际保存 CSV、XLSX、DOCX 和 Resource 内容。
7. 生成 `independence_dependency_scan.json`。

交付物：

```text
tests/fixtures/baseline/tools-list/
tests/fixtures/baseline/contracts/
tests/fixtures/baseline/research/
tests/fixtures/baseline/finance/
tests/fixtures/baseline/finance-tables/
tests/fixtures/baseline/report/
quality/independence_dependency_scan.json
```

退出条件：四个核心领域均有至少一个可回放成功样本；无法取得的样本登记为当前实现缺陷。

### 29.3 独立骨架开发流程

1. 创建独立 `pyproject.toml`。
2. 建立 `src/lvke_mcp` package。
3. 实现 Config、WorkspaceResolver 和 ObjectRepository。
4. 实现 Resource registry/read/list。
5. 实现 JobRepository、checkpoint 和幂等预留。
6. 固化 MCP transport，确保不存在 actor/tenant 注入。
7. 建立独立 server entry points。

退出条件：scaffold server 在未安装 Hermes 且不依赖当前仓库根目录的虚拟环境完成 initialize、tools/list、call_tool 和 resources/list/read。

### 29.4 纵向切片独立实现流程

每个服务一次完成一个可用纵向切片：

```text
Schema
-> Domain
-> Repository
-> MCP Tool
-> Resource
-> Test
-> 独立性扫描
```

不能只创建空 domain 文件后宣布完成，也不能长期保留外部宿主实现或隐式 fallback。

实现顺序：

```text
source-files
-> data-acquisition
-> data-analysis
-> deep-research
-> project-planning
-> finance-model
-> finance-tables
-> report-generation
-> asset-acquisition
-> deliverable-review
-> knowledge-governance
-> support services
```

每个纵向切片的进入条件：

- 输入输出 contract 已冻结。
- 正常、边界、错误和幂等 fixture 已存在。
- 目标 module 和 repository port 已确定。
- 对应公共 contract、Resource 和 repository port 已登记。

每个纵向切片的退出条件：

- domain 单元测试通过。
- MCP transport contract test 通过。
- Resource 或工件实际回读通过。
- 对应 golden fixture 通过。
- 独立依赖和分层扫描通过。
- clean install 环境通过。

### 29.5 核心链路联调流程

联调只使用真实 MCP tool call，不直接调用 Python service 函数：

1. Codex 创建新 workspace。
2. 导入文件。
3. Tavily 采集公开来源。
4. 创建 ResearchPackage。
5. 创建、校验并冻结 FinanceSpec。
6. 创建 FinanceRun。
7. 生成并读取十三表。
8. Codex 生成 Markdown 正文。
9. MCP 保存 ReportRevision。
10. 校验正文并导出 DOCX。

联调失败必须归入以下一类：

```text
contract
domain
provider
storage
transport
upstream
```

不得用 catch-all `internal_error` 关闭问题。

### 29.6 独立性和最终验收流程

1. 确认独立性扫描中不存在 `non_conforming` 项。
2. 执行源码、AST、动态 import 字符串和依赖树扫描。
3. 构建独立 wheel。
4. 在空虚拟环境安装 wheel。
5. 隐藏项目根目录并启动服务。
6. 使用全新 workspace 重跑完整链路。
7. 执行 32 并发和幂等冲突批次。
8. 实际回读全部 Resource、CSV、XLSX 和 DOCX。

退出条件：第 19、22.8、23.7、24.7、25.7 和 27 节全部通过。

### 29.7 每日开发闭环

```text
开始：选择一个已有明确 contract 的纵向切片
开发：先测试，再 domain，再 adapter，再 MCP tool
中段：运行该切片单测和 contract test
结束：更新 dependency inventory 和完成证据
禁止：引入未登记的外部宿主依赖、fallback 或临时跨服务私有 import
```

代码评审只检查功能正确性、独立边界、可维护性和测试证据，不安排安全审查。

## 30. 详细开发周期

### 30.1 排期假设

标准排期按以下固定投入估算：

| 角色 | 人数 | 主要责任 |
|---|---:|---|
| MCP 技术负责人 | 1 | contract、runtime、分层、集成和独立性门禁 |
| 财务领域开发 | 1 | FinanceSpec、engine、指标、Monte Carlo |
| 研究与数据开发 | 1 | source、acquisition、analysis、DR、Tavily |
| 文档与工件开发 | 1 | DocumentStore、十三表 exporter、DOCX |
| 测试与验收开发 | 1 | fixture、golden、协议、并发和 clean install |

基线为 **5 人全职、24 个自然周、约 120 人周**。其中前 18 周完成资料、DR、财务、十三表和研报核心链路，后 6 周完成资产收购、质量检查、知识、支撑服务、独立性锁定和最终回归。该估算基于当前约 5 万行 MCP 业务代码和当前依赖清理工作量，不包含 Hermes 改造或 Hermes 调用接入，不是未经验证的承诺日期。

周期不包含：

- 安全审查、安全加固和渗透测试。
- 登录、注册、认证、tenant 或 RBAC。
- Hermes Web、桌面端或账户系统修改。
- 当前 MCP 范围之外的新业务产品。

### 30.2 24 周执行表

| 周期 | 开发内容 | 当周交付物 | 退出条件 |
|---|---|---|---|
| 第 1 周 | 冻结 tools/list、schema、错误码和 Resource | contract baseline、工具分母 | 正式工具全部有清单 |
| 第 2 周 | 创建四域 golden workspace；建立独立依赖和分层扫描 | 四域 fixture、independence scan | 禁止依赖清单可执行 |
| 第 3 周 | 独立 package、Config、WorkspaceResolver | 可构建 wheel、独立目录 | 无 Hermes 的 scaffold 可启动 |
| 第 4 周 | ObjectRepository、Resource、Job、幂等、transport | runtime v1 | 协议和存储 contract test 通过 |
| 第 5 周 | source files 导入、分块、解析、Resource | SourceFile v1 | 文件链路 clean install 通过 |
| 第 6 周 | Tavily acquisition、URL fetch、SourceRecord | Acquisition v1 | 正常、失败、重复和重定向通过 |
| 第 7 周 | data analysis、候选字段、EvidencePack | EvidencePack v1 | locator、单位和冲突测试通过 |
| 第 8 周 | DR Job、checkpoint、resume、cancel | ResearchJob v1 | 中断恢复和取消通过 |
| 第 9 周 | ResearchPackage、来源覆盖和缺口 | ResearchPackage v1 | 真实来源 package 可回读 |
| 第 10 周 | FinanceSpec schema、normalizer、validate/freeze | FinanceSpec v1 | 别名、冲突、缺收入测试通过 |
| 第 11 周 | 投资、收入、成本、税费、折旧和摊销 | finance engine 第一段 | 期间明细 golden 通过 |
| 第 12 周 | 融资、债务、现金流、IRR/NPV/DSCR/ICR | FinanceRun v1 | 财务主结果 golden 通过 |
| 第 13 周 | 场景、敏感性、Monte Carlo、一致性 | finance analysis v1 | 固定 seed 和缺口测试通过 |
| 第 14 周 | 冻结 13 表 registry；实现 structured renderer | 13 structured tables | 逐表和跨表勾稽通过 |
| 第 15 周 | CSV/XLSX exporter、manifest、Resource 回读 | tables package v1 | 13 CSV、13 sheet、hash 通过 |
| 第 16 周 | DocumentStore、ReportPreparation、revision/diff/apply | ReportRevision v1 | partial draft 和冲突测试通过 |
| 第 17 周 | 报告数字/引用校验和 DOCX exporter | report artifact v1 | DOCX 正文、表格、字体和 hash 回读通过 |
| 第 18 周 | 四核心领域全链路联调和缺陷收口 | core release candidate | DR、FinanceRun、13 表、ReportRevision 全链路通过 |
| 第 19 周 | 资产收购模型和专用表格独立化 | AcquisitionRun v1 | 月度模型和工件回归通过 |
| 第 20 周 | 内容质量 review 和知识整理独立化 | review/knowledge v1 | 无 actor/角色/release 门禁 |
| 第 21 周 | finance-calc、excel-bridge、archive、templates、clients、experts | 支撑服务第一批 | 独立 wheel 工具可调用 |
| 第 22 周 | policy、statistics、industry、environmental、map 服务 | 支撑服务第二批 | provider、seed 和 Resource 回归通过 |
| 第 23 周 | 独立性回归、32 并发、故障恢复 | independence candidate | 扫描无 non_conforming；源码和依赖树合规 |
| 第 24 周 | 全新环境 MCP-only 对话验收、切换和回滚演练 | final release candidate | 第 19 节清单全部通过 |

### 30.3 周内开发节奏

| 时间 | 工作 |
|---|---|
| 周一 | 冻结本周 contract、fixture 和验收条件 |
| 周二至周三 | domain、repository 和 provider 开发 |
| 周四 | MCP adapter、Resource 和工件联调 |
| 周五 | golden、异常、clean install 测试及依赖清单更新 |

当周退出条件未满足时，不得把任务标记完成。缺陷进入下一周最高优先级，并移动后续关键路径。

### 30.4 关键路径和依赖

```text
独立 runtime
-> Source/ResearchPackage
-> FinanceSpec/FinanceRun
-> 十三表
-> ReportRevision/DOCX
-> 完整回归
```

可有限并行的任务：

- source files 与 runtime 后半段。
- DR provider 与 data analysis。
- DOCX renderer 原型与财务模型开发。
- fixture 建设与各领域开发。

不能提前宣称完成的任务：

- FinanceRun 未稳定前，十三表只能做结构原型。
- 十三表 package 未稳定前，报告数字和表格校验不能定版。
- 四域未联调前，不能开始最终独立性验收。

### 30.5 人员不足时的周期换算

| 实际投入 | 预计周期 | 说明 |
|---|---:|---|
| 5 人全职 | 24 周 | 全部正式及支撑服务的标准基线 |
| 4 人全职 | 29-33 周 | 文档/工件与测试需交叉承担 |
| 3 人全职 | 38-44 周 | 财务、十三表和支撑服务难以并行 |
| 2 人全职 | 56-68 周 | 仅适合分服务逐步交付 |
| 1 人全职 | 90-110 周 | 高风险，不建议一次建设全部服务 |

周期不得通过取消 golden 回归、工件实际回读或 clean install 验收来压缩。可调整的是非核心支撑服务和可选质量检查的交付顺序。

### 30.6 里程碑

| 里程碑 | 时间 | 可演示结果 |
|---|---:|---|
| M1 独立骨架 | 第 4 周末 | 无 Hermes 启动、调用、存储和 Resource |
| M2 资料研究独立 | 第 9 周末 | Tavily 到 ResearchPackage 完整链路 |
| M3 财务独立 | 第 13 周末 | FinanceSpec 到 FinanceRun 和分析 |
| M4 十三表独立 | 第 15 周末 | 13 表、CSV、XLSX 和回读 |
| M5 研报独立 | 第 17 周末 | Codex 正文到 ReportRevision 和 DOCX |
| M6 核心链路独立 | 第 18 周末 | DR、财务、十三表和研报完整联调 |
| M7 全服务建设 | 第 22 周末 | 正式及支撑服务均由独立包提供 |
| M8 MCP 独立验收 | 第 24 周末 | 零外部宿主依赖和全链路验收 |

### 30.7 每周人力安排

| 周期 | 技术负责人 | 财务开发 | 研究数据开发 | 文档工件开发 | 测试验收开发 |
|---|---|---|---|---|---|
| 1-2 | contract、inventory | 财务 fixture | DR fixture | 表格/报告 fixture | 基线录制工具 |
| 3-4 | runtime、transport | finance contract | provider ports | artifact ports | runtime contract test |
| 5-7 | 集成和 code review | FinanceSpec 预研 | source/acquisition/analysis | parser/exporter 支持 | 资料链测试 |
| 8-9 | DR 集成 | 财务 schema | DR job/package | DOCX 原型 | DR 恢复测试 |
| 10-13 | 财务集成 | finance engine 主责 | 数据 fixture 支持 | 表格 registry 准备 | 财务 golden 主责 |
| 14-15 | 表格集成 | 勾稽和指标支持 | 来源链回归 | renderer/CSV/XLSX 主责 | 表格回读主责 |
| 16-17 | 报告集成 | 数字校验支持 | 引用校验支持 | DocumentStore/DOCX 主责 | 报告并发和回读 |
| 18 | 核心联调主责 | 财务回归 | DR 回归 | 工件回归 | 核心 MCP-only 验收 |
| 19-20 | 可选领域集成 | 资产收购支持 | 知识来源支持 | 收购工件/review | 可选领域回归 |
| 21-22 | 支撑服务集成 | finance-calc 支持 | 公开数据服务 | excel/templates 支持 | 支撑服务协议回归 |
| 23-24 | 独立性收口和发布主责 | 财务总回归 | DR 总回归 | 工件总回归 | MCP-only 总验收 |

## 31. 工作包和完成定义

### 31.1 WP-01 独立 Runtime

任务：独立 package、Config、WorkspaceResolver、ObjectRepository、Resource、Job、幂等和 transport；删除 actor/tenant 注入。

完成定义：空环境中启动三个代表性 Server，完成对象写读、Resource、并发和崩溃恢复测试。

### 31.2 WP-02 资料与研究

任务：SourceFile、Tavily acquisition、analysis、EvidencePack、ResearchJob、checkpoint 和 ResearchPackage。

完成定义：输入一个用户文件和一个研究主题，输出可回读的真实 SourceRecord、EvidencePack 和 ResearchPackage；Tavily 失败返回明确状态。

### 31.3 WP-03 财务模型

任务：FinanceSpec、normalizer、freeze、财务 engine、指标、场景、Monte Carlo、FinanceRun、BOE 和一致性。

完成定义：六类项目 fixture 的关键指标和期间明细全部通过容差比较，且不 import Hermes。

### 31.4 WP-04 十三表

任务：13 表 registry、structured renderer、跨表校验、CSV/XLSX exporter 和 Resource。

完成定义：同一 run_id 的 13 表、13 CSV、13 XLSX sheet 全部实际回读并与 FinanceRun 一致。

### 31.5 WP-05 研报和工件

任务：ReportPreparation、DocumentStore、ReportRevision、diff/apply、数字/引用校验、DOCX exporter 和回读。

完成定义：完整和 partial 两类草稿均可生成；并发 revision 不丢失；DOCX 正文、表格、字体和 hash 回读通过。

### 31.6 WP-06 可选领域和支撑服务

任务：资产收购、内容质量 review、知识整理、支撑服务建设或合并。

完成定义：没有角色审批和安全隔离；所有保留工具在独立 wheel 可用；被合并工具有兼容映射。

### 31.7 WP-07 独立性收口和发布

任务：独立性扫描、clean install、并发、MCP-only 验收、Client 配置切换和回滚演练。

完成定义：项目根目录不可见时完整链路仍成功，且输出只写入 MCP 数据目录。

### 31.8 工作包依赖

| 工作包 | 前置工作包 | 可以开始的条件 |
|---|---|---|
| WP-01 Runtime | 无 | baseline contract 已冻结 |
| WP-02 资料与研究 | WP-01 storage/provider ports | SourceFile 和 SourceRecord contract 已冻结 |
| WP-03 财务模型 | WP-01；WP-02 可并行 | FinanceSpec 和 golden fixture 已冻结 |
| WP-04 十三表 | WP-03 | FinanceRun v1 字段和数字稳定 |
| WP-05 研报 | WP-01；最终联调依赖 WP-02/03/04 | DocumentStore contract 已冻结 |
| WP-06 可选领域 | WP-01；按领域依赖 WP-03/05 | 核心链路不再变化 |
| WP-07 独立性收口发布 | WP-01 至 WP-06 | 所有 inventory 项已有处置结果 |

## 32. 开发管理和进度报告

### 32.0 当前工作区实测状态（2026-03-14）

本节以当前代码、工作区和本轮命令结果为准；前文历史快照和已有勾选不能替代退出验收证据。

| 工作项 | 状态 | 当前证据 |
|---|---|---|
| `baseline-success` | `in_development` | 已有 FinanceRun、ResearchPackage roundtrip fixture；DR 空 workspace 的 stdio 生成与 Resource 回读曾成功。四核心领域的成功 golden、FinanceRun 空 workspace 成功链和完整工件回读基线尚未闭环。 |
| `architecture-boundaries` | `in_development` | 共享财务计算、工作簿 adapter、模板 catalog，以及 Finance Tables、Source Files、Data Analysis、Data Acquisition、Report 和 Research repository 已迁出 Server 私有实现；扫描器 v3 当前仍报告 `37 matches / 9 files` 跨 Server 私有导入或 `domains -> servers` 反向依赖，模块边界尚未闭环。 |
| `remove-state-machines` | `domain_tests_passed` | 资产与 deliverable review 的 actor/approval/signoff/release 状态机已物理删除，保留 finding、retest、event-chain 与确定性 validation；全仓禁用业务语义扫描为 `0 matches / 0 files`。该状态只表示代码迁移和当前扫描通过，不代表自动化验收或总体独立化完成。 |
| `automated-acceptance` | `not_started` | `tests/` 仍以 baseline fixture 为主；独立性、contract、golden、工件、并发和恢复自动化验收尚无完整通过证据。 |
| `clean-install` | `not_started` | 尚未完成独立 wheel 在全新虚拟环境中的安装、全部正式 Server 启动和 MCP-only 全链路验收。 |
| `progress-evidence` | `in_development` | 本节开始按实际命令与工件记录状态；只有代码、自动测试、Resource/工件回读、独立性扫描和 clean install 均有证据时才可标记 `done`。 |

当前确定的语义边界：

- MCP 不包含身份、权限、认证、RBAC、职责分离、人员审批或安全审查语义。
- `run_id` 只用于不可变结果寻址、幂等、回读、content hash/basis hash 绑定和 lineage，不表示批准、权限或人员责任。
- 正式结果资格只由输入快照、engine/policy version、内容 hash、工件完整性、来源绑定和数值一致性等确定性校验决定。
- 内容质量检查可以保留，但必须表现为 deterministic validation/check，不能形成 actor、批准、签审或 release 状态机。

本轮已完成身份、人员审批、签审、发布资格状态机的代码迁移：资产确认和历史事件、deliverable validation、FinanceSpec、Finance evidence binding、FactPack seal、恒力现金流假设、报告提案、归档参考投影均不再记录或判定 actor/reviewer；`formal_delivery_ready`、`publish_eligibility`、`formally_deliverable`、`release_condition` 已统一迁移为 validation/evidence 完整性字段。旧持久化对象中的 `confirmed_by`、`reviewer`、`reviewed_at` 等字段仅通过 `mapping.pop("legacy_key", None)` 只读忽略，不参与结果、hash 或状态判定。

本轮已验证：`.venv/bin/python -m compileall -q src scripts` 通过；asset acquisition、deliverable review、finance model、finance tables、finance-calc、excel-bridge、report generation、zero-material delivery、templates、archive、source files、data analysis 和 data acquisition 的 `build_server()` 已分别通过实测；已检查本轮编辑文件无 lint 诊断。架构迁移已将纯财务计算归入 `domains.finance.calculations`，将工作簿读取、公式解析和 XLSX 导出归入 `adapters.spreadsheets`，将静态模板目录归入 `domains.templates.catalog`，并将 Finance Tables、Source Files、Data Analysis、Data Acquisition、Report 与 Research 的持久化和只读能力归入公共 repository；旧 Server 私有实现路径已无跨模块源码引用。Source Files repository 已在临时 `LVKE_MCP_DATA_DIR` 中完成提交、解析、service 回读和 source/analysis hash 一致性烟测，原有 `source-files/state.json` 与工件布局保持不变。`scripts/independence_scan.py` v3 已扩展普通 `actor/reviewer/authorized`、身份时间字段和同义资格字段规则，当前报告 MCP→外部项目正向扫描 `0 matches / 0 files`，内部架构越界由 `76 matches / 25 files` 降至 `37 matches / 9 files`，禁用业务语义 `0 matches / 0 files`；兼容文本为 `4 matches / 1 file`，仅是旧键逐项 `pop(..., None)` 清洗，不参与 conformance；反向扫描因 `/Users/mac/Desktop/hermes_cli` 不存在而为 `unverified`。因此身份/审批/release 状态机删除任务已有代码和扫描证据，架构边界迁移仍在进行，整体状态仍为 `non_conforming`，不能认定项目独立性验收通过。尚未验证：四核心成功 golden、完整自动测试、全部 stdio `initialize/tools/list/resources/list`、clean wheel install、MCP-only 验收和有效反向独立性证明；架构边界及第 19 节最终验收清单保持未完成。

### 32.1 状态定义

所有任务只能使用以下状态：

```text
not_started
contract_frozen
in_development
domain_tests_passed
mcp_tests_passed
clean_install_passed
done
blocked
```

`done` 必须同时有代码、测试、Resource/工件证据和独立性扫描证据。只有代码完成但未通过 clean install，不得标记 `done`。

### 32.2 每周报告内容

- 本周完成的 tools 和 contracts。
- 独立性扫描结果和违规项数量。
- 财务 golden 通过比例。
- 十三表通过张数和跨表失败项。
- DR 成功来源数、失败 URL 和 upstream 状态。
- ReportRevision 和 DOCX 回读状态。
- clean install 状态。
- 当前 blockers、负责人和预计解除时间。
- 对 24 周关键路径的影响。

### 32.3 进度量化

整体进度不能按代码行数计算，按以下权重计算：

| 工作包 | 权重 |
|---|---:|
| 独立 Runtime | 15% |
| 资料与 DR | 20% |
| 财务模型 | 25% |
| 十三表 | 15% |
| 研报和 DOCX | 15% |
| 可选领域与支撑服务 | 5% |
| 独立性收口和最终验收 | 5% |

每个工作包只有达到本节完成定义后才能计入全部权重。部分完成按通过的 contract/golden case 比例计算。

### 32.4 延期判定

以下情况必须调整排期，不得降低验收要求：

- 当前 MCP 无法产生可冻结的成功 fixture。
- 财务旧结果存在无法解释的不一致。
- Tavily 长期不可用。
- 十三表旧 manifest 不是固定 13 张。
- DOCX 当前输出无法回读。
- 发现未登记的外部宿主依赖或运行时 fallback。

范围调整优先顺序：

1. 延后非核心支撑服务。
2. 延后资产收购和知识整理。
3. 延后可选内容质量 review。
4. 不得延后 FinanceRun、DR、十三表、研报草稿和工件回读。

### 32.5 开发启动条件

正式进入第 1 周前必须确认：

- 具备 5 人基线团队，或接受人员不足对应的延长周期。
- Tavily 测试凭据可用于 DR fixture。
- 至少六类财务项目输入 fixture 可以建立。
- 当前 MCP profile 可以用于冻结外部 contract。
- 开发只修改 MCP 子项目和 MCP 测试/质量资产。
- 不安排安全审查、登录、认证、actor、tenant 或 RBAC 工作。

如果启动条件不完整，先记录具体 blocker，不得用受控假设伪造四域验收通过。

### 32.6 第一周可直接执行的任务

| 日期 | 任务 | 产出 |
|---|---|---|
| 第 1 天 | 运行全部正式 Server 的 tools/list；冻结工具分母 | `tools-list/*.json` |
| 第 2 天 | 冻结输入输出 schema、Resource template 和错误码 | `contracts/*.json` |
| 第 3 天 | 对四核心领域执行正常和错误调用 | 调用记录和原始响应 |
| 第 4 天 | 新建 baseline workspace，生成可取得的对象和工件 | baseline fixture |
| 第 5 天 | 生成独立性 dependency scan；评审第 2 周 fixture 缺口 | scan 和 blocker 清单 |

第一周结束时必须能够回答：当前有哪些工具、哪些工具实际可用、哪些核心成功样本缺失、MCP 如何在不依赖 Hermes 的情况下完成全部核心链路。
