# 模块化重构验证协议

> 配套文档：`MODULARIZATION_PLAN.md`（目标、波次、文件清单）
> 本文只写「怎么验证、什么算通过」。方案文档不再重复验证细节。

## 1. 基线

| 项 | 值 |
|---|---|
| 基线 commit | `e4f3385`（`docs: 同步 169 工具拓扑…`）之后的 Wave 0 提交 |
| Python | 3.13.14（conda 环境 `lvke-mcp`） |
| 集成测试 | 75 passed, 0 skipped, 87 subtests |
| MCP smoke | 14/14 |
| 公开 server | 14 |
| 公开工具 | 169 |
| 资源条目 | 27 |
| `src/lvke_mcp` 文件 / 行数 | 208 / 95,084 |
| ≥800 行文件 | 37（合计 65,928 行） |
| 历史循环 import | 3（见 §4） |
| 历史禁止方向跨层边 | 3 组（见 §4） |

Wave 0 之前存在的 2 个 skip 已修复（`lvke-market-sizing` / `lvke-cost-drivers`
的 SKILL.md 已迁到 `skills/lvke-project-planning/references/preserved/`），
因此**当前不存在批准的 skip**。任何新增 skip / xfail 一律视为失败。

## 2. 必跑命令

```bash
# 1) 功能与契约（含护栏门禁）
conda run -n lvke-mcp python -m pytest -q tests/integration

# 2) 14 个 server 真实 stdio smoke
conda run -n lvke-mcp python -m lvke_mcp.testing.smoke_test

# 3) 语法与字节码
conda run -n lvke-mcp python -m compileall -q src/lvke_mcp

# 4) 边界与 API 快照比较（不写文件，只比较）
conda run -n lvke-mcp python scripts/module_metrics.py --check quality/module_metrics.json
conda run -n lvke-mcp python scripts/api_snapshot.py  --check quality/api_snapshot.json

# 5) 独立性与架构扫描
conda run -n lvke-mcp python scripts/independence_scan.py --strict
```

`pytest` 必须在仓库根目录执行（`tests/integration/test_refactor_guardrails.py`
按仓库根解析 `quality/` 与 `tests/fixtures/baseline/`）。

## 3. 护栏工具

### `scripts/module_metrics.py`

产出 `quality/module_metrics.json`：行数、消费者清单、导入图、分层边、循环。

- 消费者清单覆盖 `src`/`tests`/`scripts` 的静态 import **和**字符串懒加载。
- 懒加载识别包含一层间接：`runtime/resource_registry.py` 的
  `def _module(name): return import_module(name)` 这类薄封装会被识别，
  调用点 `_module("lvke_mcp...")` 记为真实边。只匹配 `import_module` 字面参数会
  系统性漏掉 24 条 `runtime → servers/domains` 边。
- `--check` 只判定**新增**禁止方向跨层边与**新增**循环；历史边保持记为
  `preserved`，消失记为 `improved`。同包内新增文件边是搬移的预期结果，不报错。

### `scripts/api_snapshot.py`

产出 `quality/api_snapshot.json`：208 个模块、2,804 个公开符号的签名与实现归属。

- 采集方式是**真实 import**，覆盖门面 re-export、`__getattr__` 代理和运行时注入；
  静态解析做不到这一点。
- 签名里的对象地址（`<... at 0x…>`）被抹掉，否则 `dataclasses.MISSING`
  这类哨兵默认值会让每次运行都「签名变化」。
- `--check` 判定：模块消失 / 不可导入 / 符号消失 / 签名变化 / kind 变化 /
  类公开成员消失 = 失败；新增模块、新增符号 = 通过；
  `defined_in` 变化 = 通过并记为 `implementation moved (facade ok)`，
  这正是门面转发的预期信号。

### `scripts/freeze_baseline.py`

重新冻结 `tests/fixtures/baseline/` 下的 tools/resources/contracts。

**只有在契约被有意变更时才重新冻结，并且必须与代码改动同一个 PR。**
纯拆分 PR 重新冻结基线等于把门禁关掉。

## 4. 冻结的历史债（禁止新增，不在拆分 PR 中修）

循环 import（3 个）：

1. `asset_acquisition.backend ↔ asset_acquisition.tables ↔ finance.gate ↔ reports.artifacts ↔ reports.readiness`
2. `finance.run_service ↔ finance.table_pack`
3. `runtime.resource_registry ↔ servers.lvke_feasibility_delivery.service`

禁止方向的跨层边（3 组）：

| 边 | package 级明细 |
|---|---|
| `adapters → domains` | `adapters.spreadsheets → domains.finance` |
| `runtime → domains` | `runtime → domains.finance`、`runtime → domains.reports` |
| `runtime → servers` | `runtime → 11 个 server 包`（`resource_registry` 懒加载） |

这些边在方案 §2.3 里已确认为现状的一部分。治理另立 ADR。

## 5. 单个拆分 PR 的验收清单

1. 起点工作区干净，记录 commit SHA。
2. 生成目标模块消费者清单：
   `python scripts/module_metrics.py --output /tmp/before.json`，
   查 `consumers[<module>]` 与 `dynamic_loads`。
3. 只做代码搬移 + 局部 import 调整 + 门面 re-export。
   不合并重复函数，不改 `None`/`0.0`/空列表语义，不修业务逻辑。
4. 实现包用 `_<name>/` 命名，**不允许** `foo.py` 与 `foo/` 并存。
5. 跑完 §2 的 5 组命令，全绿。
6. `api_snapshot --check` 的 `implementation moved` 条目逐条确认是预期的门面转发。
7. `module_metrics --check` 输出的 `new allowed cross-layer package edge` 逐条确认。
8. PR 说明写明：拆分前后模块归属、未迁移的消费者、保留大文件的理由
   （>600 行需给理由）。

## 6. 失败处理

- 基线本身失败时，不得用「拆分前后失败数相同」当通过。先修基线或先登记已知失败。
- 出现 import cycle、签名变化、数值差异、协议差异时，停止当前 PR，
  不在同一 PR 里顺手修业务逻辑。
- 契约门禁失败时，先判断是「代码错了」还是「基线过期」。
  Wave 0 就遇到过一次基线过期：冻结的 `report_prepare` inputSchema
  早于 `edb4b4d` 的 `project_metadata` 修复，属于基线该刷新，不是代码回归。
  这个判断必须有依据（git log / 代码 diff），不能默认刷新基线。
