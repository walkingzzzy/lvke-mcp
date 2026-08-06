---
name: lvke-local-verify
description: >
  Local verification matrix for the Lvke workbench when there is no remote CI.
  Use before claiming a change is done, before freeze-p0b/record-build, after backend
  or frontend edits; when the user asks 验收, 本地验收, 回归, smoke, focused, typecheck,
  verify_backend_requirements, 怎么测, or which tests to run. Prefer this over inventing
  ad-hoc test commands.
---


# 本地验收矩阵（无远程 CI）

本仓为**本地 Git**（无交付远程）。「准 CI」= 在本机按档位跑固定命令并保留 JSON/日志。

## 档位

| 档位 | 何时 | 命令 |
|---|---|---|
| **V0 语法/静态** | 任意 py 热修 | 见下 Ruff |
| **V1 Smoke** | 控制面/小改 | backend smoke |
| **V2 Focused** | 合并前/大域改动 | backend focused |
| **V3 Frontend** | web 改动 | typecheck + test |
| **V4 Golden** | 动金标/财务真链 | golden verify（需语料根） |
| **V5 Domain** | 只改一域 | 域 pytest 列表 |
| **V6 MCP** | MCP/Skills 契约、协议或 Resource 变更 | 独立 MCP 验收脚本 |

## 命令（复制即用）

### V0 — Ruff（核心目录 F 清洁）

```bash
uv run ruff check hermes_cli/finance hermes_cli/research_engine --select F,PLW1514
uv run ruff check .   # 全仓 PLW1514
```

### V1 — Backend smoke

```bash
uv run python scripts/verify_backend_requirements.py \
  --profile smoke \
  --json-report /tmp/lvke-smoke.json
```

覆盖：`manifest`（依赖金标可访问时）、`control`、`backup`。

### V2 — Backend focused（9 域）

```bash
uv run python scripts/verify_backend_requirements.py \
  --profile focused \
  --json-report /tmp/lvke-focused.json
```

域：manifest, control, jobs, sources, finance, golden, research-quality, deliverables, backup。  
也可用 `--phase finance --phase golden` 等子集。

### V3 — Frontend

```bash
npm --workspace web run typecheck
npm --workspace web run test
# 可选：npm --workspace web run lint
# 可选：npm --workspace web run build
```

### V4 — Golden

```bash
export LVKE_GOLDEN_DATA_ROOT=/path/to/corpus
uv run python scripts/golden_samples_manifest.py --verify
```

无语料根：记录 `skipped_golden_unavailable`，**不得**用于 P0B record-build。

### V5 — 常用域快捷

```bash
uv run pytest -q tests/hermes_cli/test_app_factory.py tests/hermes_cli/test_idempotency_service.py
uv run pytest -q tests/test_source_security.py tests/test_source_files_contract.py
uv run pytest -q tests/test_finance_spec_v3_acquisition.py tests/test_finance_reference_adjudication.py
uv run pytest -q tests/test_research_quantitative_semantics.py
uv run pytest -q tests/test_report_lifecycle.py tests/test_deliverable_artifacts.py
```

权威全量单测 runner（更慢）：

```bash
scripts/run_tests.sh
# 或限定：scripts/run_tests.sh tests/hermes_cli/
```

### V6 — MCP 与 Skills（不依赖 pytest）

```bash
uv run python scripts/mcp_acceptance.py \
  --json-report /tmp/lvke-mcp-acceptance.json
uv run python -m mcp_servers._common.smoke_test lvke-knowledge-governance
uv run python scripts/sync_codex_skills.py audit --json
```

覆盖 22 个本地 Server 的构建与 schema、11 个正式服务的 legacy/modern 协议矩阵、标准 Resource provider，以及纳入范围产品 Skill 的工具引用。用户明确禁止 `pytest` 时，MCP 验收使用 V6、关键工具直调、持久化和产物检查，不以 V5 替代。

## 变更 → 最低档位

| 改动面 | 最低 |
|---|---|
| `hermes_cli/finance/**` | V0 + V1 + finance 域 V5；真链则 V2/V4 |
| `research_engine/**` | V0 + research V5；资格相关加 fixture |
| `source_files*` / security | V1 + sources V5 |
| `report_lifecycle*` / deliverable* | V1 + deliverables V5 |
| `mcp_servers/**` / MCP Skills | V0 + V6；状态机变更另加关键直调和重启读回 |
| `web/**` | V3 |
| `golden_samples*` | V4 |
| 声称正式交付/P0B | V2 + V4 + 业务证据（见 `lvke-golden-p0b`） |

## 报告写法

- 贴命令 + 退出码 + 关键 passed/failed 摘要。  
- 使用五档状态；smoke 绿 ≠ 真实金标通过。  
- JSON report 路径写入说明，便于 record-build 做 sha256。

## 反模式

- 只跑单个文件 pytest 就说「后端全过」  
- 无金标根声称 golden 阶段通过  
- 用远程 CI 不存在当借口跳过本地 V1/V3  
- 把 `output/` 测试垃圾提交进 git  
