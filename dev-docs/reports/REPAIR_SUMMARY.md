# Lvke MCP 修复总结（2026-08-14）

## 诊断结果

**审计报告分析**: 14 个问题中，**5 个确定缺陷 + 3 个待确认**，真实代码缺陷占比 36%-57%。

### 确认存在的缺陷（已修复）

| # | 问题 | 严重性 | 根因 | 修复位置 |
|---|------|--------|------|----------|
| 1 | 统一 Resource 路由缺 asset-acquisition | 验收阻断 | DOMAINS 未注册 | `resource_registry.py:15-27` |
| 2 | report_export_docx 忽略传入 revision | P0 | 只传 workspace_id | `export.py:47`, `lifecycle.py:225` |
| 3 | 联合审查替换显式修订 | P0 | 组件未携带自身 ID | `target_resolve.py:365-396` |
| 4 | 调用不存在的读取函数 | 验收阻断 | 函数名错误 | `finding_rules.py:533,557` |
| 5 | "8%目标收益率"误判 DSCR | P0 误报 | 模式不完整 + 单位错误 | `patterns.py:44`, `normalize.py:57` |
| 6 | 财务 run 错误路由 | P1 | acqrun_* 强制返回 None | `snapshots.py:219` |
| 7 | 审查规则适用性不一致 | P1 | combined 规则未裁剪 | `rules.py:215-216` |
| 8 | 光伏缺税前现金流 | P1 | 输出字段遗漏 | `solar_engine.py:158` |

### 非代码缺陷（暂不修复）

- **情景矩阵缺失**: 输入数据未生成（验收流程问题）
- **DSCR 不满足阈值**: 真实业务风险（当前参数下项目不可行）
- **证据资格不足**: 技术夹具按设计不能升级为正式证据
- **build_time 不完整**: 脏工作树的 fail-closed 保护
- **Codex 不显示 taskSupport**: 宿主侧可观测性问题

### 契约易用性问题（待后续）

- **artifact_domain 歧义**: 字段语义是"存储族"而非"内容类型"

## 修复内容

### 1. 资产收购 Resource 路由

```python
# resource_registry.py
DOMAINS = (
    ...,
    "asset-acquisition",  # 新增
)

# 委托到资产收购服务
if domain == "asset-acquisition":
    from lvke_mcp.servers.lvke_asset_acquisition import service
    return service.list_resources(workspace_id)
```

### 2. 报告工件修订绑定

```python
# export.py
artifact_id = _create(
    workspace_id,
    report_revision_id=revision_id,  # 显式传递
    kind=kind,
    template_version=template_version,
)

# lifecycle.py
def _create(..., report_revision_id: str | None = None):
    if report_revision_id:
        basis = _capture_basis_from_revision(report_revision_id, ...)
    else:
        basis = _capture_basis(workspace_id, ...)
```

### 3. 联合审查修订传递

```python
# target_resolve.py (report_revision 组件)
bindings = {
    **upstream,
    "report_revision_id": target_id,  # 新增
}
```

### 4. 函数名修正

```python
# finding_rules.py
text = deliverable_artifacts.read_artifact_download(  # 修正函数名
    workspace_id, artifact_id, filename
)
```

### 5. 语义模式优化

```python
# patterns.py
"discount_rate": r"折现率|基准收益率|目标收益率|要求收益率|收益率门槛",

# normalize.py
_METRIC_UNITS = {
    "dscr": {"", "倍"},  # 从 {"%"} 改为无量纲
    "icr": {"", "倍"},
}
```

### 6. 财务 run 分派

```python
# snapshots.py
def _load_finance_run(workspace_id: str, run_id: str):
    if run_id.startswith("acqrun_"):
        from lvke_mcp.domains import asset_acquisition
        return asset_acquisition.get_run(workspace_id, run_id)
    # 否则走通用可研路径
```

### 7. 光伏税前现金流

```python
# solar_engine.py
pre_tax_cf = [
    row["revenue_wan"] - row["opex_wan"] 
    - row["maintenance_capex_wan"] 
    + (exit_value if i == len(annual)-1 else 0)
    for i, row in enumerate(annual)
]

return {
    ...,
    "project_pre_tax_cashflows_wan": pre_tax_cf,
}
```

## 配置同步

✅ **Claude Code MCP 配置已更新**
- 位置: `~/.claude/config.json`
- 从插件源同步: `/Users/mac/Desktop/mcp_servers/plugins/lvke-mcp/.mcp.json`
- 服务数: 14 个
- 环境变量: 正确配置数据目录、Tavily、外部语料等

## 下一步

### 立即验收（需重启后）

1. **工具可用性**
   ```
   预期: 169 个工具全部可调用
   验证: 查看当前会话工具列表
   ```

2. **Resource 路由**
   ```bash
   lvke_list_resources(domain="asset-acquisition", workspace_id="test")
   lvke_read_resource(uri="lvke://asset-acquisition/artifacts/{id}/csv/cashflow")
   ```

3. **修订一致性**
   ```
   - 导出 DOCX 期间切换 current revision
   - 检查工件绑定的 report_revision_id
   ```

4. **语义识别**
   ```
   输入: "低于8%的目标收益率及1.2的最低偿债备付率"
   预期: 8% → 折现率, 1.2 → DSCR
   ```

### 干净构建（可选）

```bash
# 生成完整 build metadata
git worktree add /tmp/lvke-clean main
cd /tmp/lvke-clean
# 验证 build_time 完整
```

### 测试套件

```bash
cd /Users/mac/Desktop/mcp_servers
conda activate lvke-mcp
pytest tests/ -v --maxfail=5
```

## 验收清单

详见 [`VERIFICATION_CHECKLIST.md`](../verification/VERIFICATION_CHECKLIST.md)，包含：
- 核心功能验证（9 项）
- 样本生成与验收（3 项）
- 技术债务追踪

## 文件清单

```
/Users/mac/Desktop/mcp_servers/
├── dev-docs/reports/REPAIR_SUMMARY.md       # 本文件
├── dev-docs/verification/VERIFICATION_CHECKLIST.md # 详细验收清单
└── src/lvke_mcp/
    ├── runtime/resource_registry.py        # 域注册 + 路由
    ├── domains/reports/
    │   └── _artifacts/
    │       ├── export.py                   # revision 传递
    │       ├── lifecycle.py                # basis 捕获
    │       └── snapshots.py                # run 分派
    ├── domains/asset_acquisition/
    │   └── _model/solar_engine.py          # 税前现金流
    └── servers/
        ├── lvke_asset_acquisition/
        │   ├── server.py                   # Resource 实现
        │   └── service.py                  # 适配器
        └── lvke_deliverable_review/
            ├── _service/
            │   ├── target_resolve.py       # 修订绑定
            │   └── finding_rules.py        # 函数名修正
            └── _report_checks/
                ├── patterns.py             # 语义模式
                └── normalize.py            # 单位兼容
```
