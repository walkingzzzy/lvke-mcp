# 模块化重构验证协议

> 配套文档：`MODULARIZATION_PLAN.md`（目标、波次、文件清单）
> 本文只写「怎么验证、什么算通过」。方案文档不再重复验证细节。

## 1. 基线

当前基线绑定 Wave 4 之后的 `chore(refactor): 基线快照推进到 Wave 4 之后` 提交。

| 项 | Wave 0 | 当前（Wave 4 后） |
|---|---|---|
| Python | 3.13.14（conda 环境 `lvke-mcp`） | 同 |
| 集成测试 | 75 passed, 0 skipped, 87 subtests | 同 |
| MCP smoke | 14/14 | 同 |
| 公开 server / 工具 / 资源 | 14 / 169 / 27 | 同 |
| `src/lvke_mcp` 文件 / 行数 | 208 / 95,084 | 433 / 101,282 |
| ≥800 行文件 | 37（合计 65,928 行） | 15（合计 18,190 行） |
| 历史循环 import | 3（见 §4） | 3，无新增 |
| 历史禁止方向跨层边 | 3 组（见 §4） | 3 组，无新增 |

文件数与总行数上升是门面模式的预期代价：每个实现包多一个 `__init__.py`，
每个门面多一段 re-export。判定指标是**超长文件数与其合计行数**，不是总行数。

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
conda run -n lvke-mcp python scripts/module_metrics.py --check tests/fixtures/baseline/refactor/module_metrics.json
conda run -n lvke-mcp python scripts/api_snapshot.py  --check tests/fixtures/baseline/refactor/api_snapshot.json

# 5) 独立性与架构扫描
conda run -n lvke-mcp python scripts/independence_scan.py --strict
```

`pytest` 必须在仓库根目录执行（`tests/integration/test_refactor_guardrails.py`
按仓库根解析 `tests/fixtures/baseline/`）。

**基线路径是 `tests/fixtures/baseline/refactor/`，不是 `quality/`。**
`quality/` 在 `.gitignore` 里，把重构基线放那里会让门禁在干净 clone 上
静默跳过（`test_refactor_guardrails.py` 用 `assertTrue(path.exists())`
挡住了这种跳过，但命令行 `--check` 会直接 `FileNotFoundError`）。
`quality/` 只用于 `independence_scan.py` 的可再生扫描产物。

`api_snapshot.py` **不带 `--check` 时会覆写**受版本控制的基线文件。
比较用途一律带 `--check`；只有确认要推进基线时才省略它。

## 3. 护栏工具

### `scripts/module_metrics.py`

产出 `tests/fixtures/baseline/refactor/module_metrics.json`：行数、消费者清单、
导入图、分层边、循环。

- 消费者清单覆盖 `src`/`tests`/`scripts` 的静态 import **和**字符串懒加载。
- 懒加载识别包含一层间接：`runtime/resource_registry.py` 的
  `def _module(name): return import_module(name)` 这类薄封装会被识别，
  调用点 `_module("lvke_mcp...")` 记为真实边。只匹配 `import_module` 字面参数会
  系统性漏掉 24 条 `runtime → servers/domains` 边。
- `--check` 只判定**新增**禁止方向跨层边与**新增**循环；历史边保持记为
  `preserved`，消失记为 `improved`。同包内新增文件边是搬移的预期结果，不报错。
- **循环按「参与其中的门面模块集合」归一化后比较，不按节点序列精确匹配。**
  实现搬进 `_impl/` 后，同一历史环的路径必然多出子模块节点
  （`a → b → facade` 变成 `a → b → _impl.x → _impl.y → facade`），
  按序列比较会把它同时报成「新增环」和「已解决环」——环总数不变却门禁失败。
  归一化把 `reports._artifacts.query` 折叠回同级门面 `reports.artifacts`
  （去掉下划线前缀，丢弃子模块段），而不是截断到父包 `reports`：后者会把
  `_artifacts` 与 `_doc_service` 两个不同门面的环折叠成同一个 key，真的新环
  就会被吞掉。Wave 2.8 加此归一化时同步验证了三种情况：真新环仍被抓到、
  不同门面不混淆、同环改写不误报。

### `scripts/api_snapshot.py`

产出 `tests/fixtures/baseline/refactor/api_snapshot.json`：当前 433 个模块、
4,759 个公开符号的签名与实现归属（Wave 0 时为 208 / 2,804）。

- 采集方式是**真实 import**，覆盖门面 re-export、`__getattr__` 代理和运行时注入；
  静态解析做不到这一点。
- 签名里的对象地址（`<... at 0x…>`）被抹掉，否则 `dataclasses.MISSING`
  这类哨兵默认值会让每次运行都「签名变化」。
- `--check` 判定：模块消失 / 不可导入 / 符号消失 / 签名变化 / kind 变化 /
  类公开成员消失 = 失败；新增模块、新增符号 = 通过；
  `defined_in` 变化 = 通过并记为 `implementation moved (facade ok)`，
  这正是门面转发的预期信号。

### `scripts/split_fidelity.py`

按 AST 比较搬移前后的顶层定义，把「纯搬移」变成可自动验证的条件。

用法：`python scripts/split_fidelity.py <搬移前 ref> <门面路径> <实现包目录>`

判定：函数体 AST 不一致 = 失败（**语义等价的改写也算失败**）；定义凭空消失
= 失败；同一定义出现在多个实现文件 = 失败（复制而非搬移）；门面仍保留同名
定义 = 通过。

存在的理由是 §2 的五组门禁只覆盖**接口**层，查不出下面两类事故——两者都能让
全部测试继续变绿：

1. **语义等价改写**：正则里 `一-鿿` 被写成 `一-鿿`。对 `re` 完全等价，
   测试全过，但这是重写而非搬移，diff 从此不可复核。
2. **helper 复制而非搬移**：同一个 `_locator_text` 被复制进两个子模块。
   两份都能用，没有任何门禁失败，但从此存在两份会各自漂移的实现。

### `scripts/module_split.py`

按符号归属机械搬移的通用驱动器。配置只声明「哪个符号归哪个组」，
组间 import 清单、未用 import 剪枝、组间成环检测、紧邻前置注释迁移全部由脚本
推导。**手写组间 import 清单是 Wave 2.4/2.5 两次 `NameError` 事故的根因，
因此一律不手写。**

- `--outline <rev> <path>` 打印顶层符号大纲与行号跨度，用于规划分组。
- 配置里的符号必须**全覆盖**原文件顶层节点，漏一个就报 `unassigned symbols`。
  这是防漏搬的主要护栏。
- `keep_in_facade` 显式声明不搬移的顶层节点（典型是
  `if __name__ == "__main__": main()` 入口样板——门面模块本身就是 `python -m`
  的启动路径，这个块搬进实现包后永不触发）。名字必须真实存在，否则报错。
- 成环即拒绝并打印环路径。**环通常有两种性质，必须分清**：
  假环（脚本误判）改脚本；真环（业务固有递归）把互相递归的组合并。
  两个真实例子见 §7。

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

## 7. 拆分时必查的六类陷阱（Wave 1–4 实测）

拆分前按这个清单扫一遍，比撞上再回溯便宜得多。

### 7.1 被 monkeypatch 的符号

**先扫**：`grep -rn 'patch.object(<模块名>\|patch("<模块路径>' tests/`
（AST 扫描更可靠，`patch` 的第一个字面量参数 + `patch.object` 的前两个参数）。

搬进实现包后 `patch.object(门面, "_x", ...)` 会**静默失效**：实现包按
`from .base import _x` 绑定了自己的引用，patch 只重绑门面属性。
Wave 3.1 的 `tables_service` 因此让 `render()` 返回 `success=False`。

三条走不通的路：实现包回指 import 门面（造出实现包→门面反向边和真的新循环）、
`sys.modules[...]` 查表（绕开静态 import 图让门禁扫不到，但反向耦合真实存在，
是规避门禁）、改测试去 patch 实现包（放宽验收）。

**正解**：实现函数加仅关键字注入点（默认回落 base），门面定义同名包装函数
显式传入门面自身属性。patch 生效、实现包零反向依赖、对外签名不变。
对应方案 §5.1「会被 monkeypatch 的模块级状态不依赖普通 re-export，
改用明确的 state owner 或兼容代理」。

### 7.2 按源文件路径扫字面量的测试

**先扫**：`tests/` 里所有形如 `src/lvke_mcp/**.py` 的字符串常量，
检查路径是否仍存在、被断言的字面量是否还在该文件里。

`test_mcp_acceptance_20_defects.py` 有多处 `Path("src/...").read_text()` +
`assertIn`。实现搬走后断言失败。修法是把路径指向新位置，**不放宽断言内容**。
Wave 1/2.5/3.7 各修过一处。

### 7.3 `independence_scan.py` 的语义豁免按路径登记

被豁免的行搬到新文件后豁免失效，扫描报 `non_conforming`。这不是放宽规则，
搬移时需同步改 `_SEMANTIC_EXEMPTION_RULES` 里的路径，**文本与正则完全不变**。
Wave 4 一次同步了 5 条。

### 7.4 可选依赖兜底块里绑定的名字

`try: from x import Y / except ImportError: ...` 里的 `Y` 属于原模块公开表面
（`api_snapshot` 基线里有它），门面必须 re-export——但要**照抄条件性**：

```python
if _HAS_INVESTMENT_BREAKDOWN:
    from ._finance_model.base import InvestmentBreakdown
```

无条件 import 会把可选依赖变成硬依赖。Wave 3.4 靠 `api_snapshot --check`
抓到这处漏 re-export（`symbol disappeared`）。

同一类问题的另一面：`module_split.bound_names` 原先不递归 `Try`/`If`/`With`，
这些块里定义的名字查不到归属，跨组引用**既不报错也不生成组间 import**，
直接 `NameError`。Wave 3.4 修复。

### 7.5 假环 vs 真环

**假环**：`module_split.referenced()` 原先把所有字符串常量按整词计入符号引用，
于是 `{"status": ...}` 的 dict 键、`data.get("start")` 的键名被当成对同名函数的
引用。Wave 2.6/2.7 的分组都曾被这种假环挡住。已改为只解析注解位置的字符串。

**真环**：业务固有递归，改分组消不掉，只能把互相递归的组**合并**。两例：

- `finance_model`：`compute_financials` 调 `_apply_custom_calcs` /
  `_build_scenarios`，后两者又回调 `compute_financials`（缩放重算与自定义目标
  求解都要重跑主计算）。合并为一个 `engine` 组，1,681 行，有意保留。
- `deliverable_review`：审查状态机三条回边。解法是把共用原语下沉
  （retest 分类原语放 `base`，因为 `events._project_events` 也要用），
  并把 `get_review` 归 `lifecycle` 而非 `events`（它会触发异步恢复，
  是生命周期操作而非纯投影）。

判据：如果两个符号必须互相调用才能完成一次业务操作，它们属同一事务边界。

### 7.6 模块级状态与标准库同名

- 锁、线程池、store 实例只能有**一份**，放在最底层组由其他组 import。
  验证方式是 `facade._LOCK is base._LOCK`。Wave 2.8/3.3/3.6/4 都验过。
- 子模块名不要与标准库冲突：`_model/calendar.py` 里 `import calendar` 因绝对
  导入仍拿到标准库，能跑，但极易误导。Wave 3.2 改名 `period_dates.py`。
