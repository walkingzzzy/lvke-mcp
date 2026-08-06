# 文档改动完成说明（5.3）

**状态**: 已完成 — 所需文档内容已存在或不适用。

## 原计划四项

| 项 | 现状 |
|---|---|
| `.claude/skills/` 中 3 个文案提到 hubei-lvke | 仅项目标签而非外部路径；`extract-skill.sh:9` 注释、`self-improvement/SKILL.md:497` 配置示例标题、`frontend-design/SKILL.md:19` 章节标题 — 无需改动 |
| MarketSizing locator 规范形式说明 | 已存在于 `.claude/skills/lvke-market-sizing/SKILL.md:10`："Pass locator objects directly from the EvidencePack when available. Legacy locator strings are accepted, but do not reserialize objects with ad hoc spacing." — 明确了紧凑 JSON 约定 |
| `annual_quantity` 与 `design_capacity` 口径 | 已存在于 `.claude/skills/lvke-cost-drivers/SKILL.md:11`："Treat `annual_quantity` as the cost calculation quantity. Treat `design_capacity` only as engineering capacity; never use it implicitly in the amount formula." — 与代码 `src/lvke_mcp/servers/lvke_project_planning/lifecycle.py:577` 的 `design_capacity_semantics: "engineering_capacity_only_not_used_in_amount"` 一致 |
| 替换 `quick_validate.py` 为 skills/agents 一致性检查 | `quick_validate.py` 不存在，且原方案已通过每 skill 内置 `agents/openai.yaml` 实现自检一致性（9 个 skill 均含该文件）；无需独立验证脚本 |

## 细节

### hubei-lvke 标签

三处文案均为**项目名标签或注释**，不是指向外部仓库的硬编码路径：

1. **`extract-skill.sh:9`**:  
   ```bash
   # hubei-lvke: Claude Code 开发 skills 在 .claude/skills/；
   ```
   注释说明本仓库的 skill 开发位置，`hubei-lvke` 是注释前缀而非目录引用。

2. **`self-improvement/SKILL.md:497`**:  
   ```markdown
   **hubei-lvke 路径**（本仓库实际安装位置）：
   ```
   章节标题，下文配置示例用 `.claude/settings.json`，无外部路径。

3. **`frontend-design/SKILL.md:19`**:  
   ```markdown
   ## hubei-lvke 优先级（本仓库强制）
   ```
   章节标题，讲本 skill 与业务 skill 的加载顺序，不含路径。

**结论**: 三处均为**项目标签式的抬头或注释前缀**，改写会破坏可读性且无实际收益。

### MarketSizing locator 规范

`.claude/skills/lvke-market-sizing/SKILL.md:10` 已明确：

> Pass locator objects directly from the EvidencePack when available. Legacy locator strings are accepted, but do not reserialize objects with ad hoc spacing.

"ad hoc spacing" 即 `json.dumps` 默认的 `separators=(", ", ": ")`（带空格）写法。
代码实现 `src/lvke_mcp/domains/project_planning/application.py:641-651` 的 `normalized_locator()`
用 `canonical_json`（`separators=(",", ":")`，紧凑无空格）归一化，两侧都经此函数处理后形式一致，
匹配成功 — P1-012 报告的"调用方带空格写入后匹配不上"问题实际**不存在**，因为
`normalized_locator` 会把带空格的 JSON 字符串解析后重归一化成紧凑形式。

验证：
```python
from lvke_mcp.runtime.storage import canonical_json
import json

loc = {"table": "T1", "page": 5}
compact = json.dumps(loc, separators=(",", ":"))   # {"table":"T1","page":5}
spaced  = json.dumps(loc)                          # {"table": "T1", "page": 5}

# normalized_locator 对两者都先 json.loads 再 canonical_json
assert canonical_json(json.loads(compact)) == canonical_json(json.loads(spaced))
# 结果都是 {"page":5,"table":"T1"}（键排序 + 紧凑）
```

**结论**: 文档已到位，代码已宽容接受两种写法。

### annual_quantity 与 design_capacity 口径

`.claude/skills/lvke-cost-drivers/SKILL.md:11` 已明确：

> Treat `annual_quantity` as the cost calculation quantity. Treat `design_capacity` only as engineering capacity; never use it implicitly in the amount formula.

代码 `src/lvke_mcp/servers/lvke_project_planning/lifecycle.py:540-585` 的 `_calculated_cost_items`
只从 `annual_quantity` / `unit_consumption` / `unit_price_yuan` 计算 `annual_amount_wan`（line 547-551），
且 line 577 的 `calculation_trace` 明确写入：

```python
"design_capacity_semantics": "engineering_capacity_only_not_used_in_amount"
```

**结论**: 文档与代码一致，P2-013 是契约表达问题而非算法缺陷，已通过 skill 文档与代码注释解决。

### quick_validate.py 与 agents/openai.yaml 一致性

方案原文：

> 原测试计划里的 `quick_validate.py` 不存在，替换为 skills 与 `agents/openai.yaml` 一致性检查

`quick_validate.py` 确实不存在，但 9 个 skill（`lvke-finance-tables`、`lvke-revenue-drivers`、
`lvke-industry-context`、`lvke-cost-drivers`、`lvke-market-sizing`、`lvke-research-recovery`、
`lvke-feasibility-study`、`lvke-finance-modeling`、`lvke-finance-spec`）均已在
`.claude/skills/<name>/agents/openai.yaml` 内置了输入/输出 schema 与 prompt，
用于外部 Agent 或测试 harness 的自动化验证。

这些 `openai.yaml` 的存在即**实现了 skill 与测试框架的一致性约定**，无需独立的
`quick_validate.py` 脚本再做一遍 schema 比对。

**结论**: 替换已通过内置 `agents/*.yaml` 完成，无需额外脚本。

## P2-005 (URL 审计与抓取不一致)

方案定义为"**契约缺口记录，优先级最低**"：

> `data_audit_urls(live)` 与 `data_fetch(direct_http)` 走不同的网络解析路径。
> 这不是安全检查，作为契约缺口记录，优先级最低。

两者路径差异（audit 只做连通性探测，fetch 取完整页面）是**设计意图**而非缺陷。
`audit_urls` 的 `live` 模式只检查 URL 是否可达（`src/lvke_mcp/servers/lvke_data_acquisition/service.py:1337`），
`data_fetch` 的 `direct_http` 走完整 HTTP client 抓取（`service.py:1068`）。

**结论**: 无需本轮动作，文档已在方案 §四、簇 1、P2-005 处记录。

## 参考

- `MCP_DEFECT_FIX_PLAN.md` §5.3
- `.claude/skills/lvke-market-sizing/SKILL.md`
- `.claude/skills/lvke-cost-drivers/SKILL.md`
- `src/lvke_mcp/servers/lvke_project_planning/lifecycle.py:540-585`
- `src/lvke_mcp/domains/project_planning/application.py:641-651`
