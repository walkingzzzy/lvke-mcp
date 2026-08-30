# Lvke MCP 修复验收清单

## 配置状态

✅ **MCP 服务配置已同步**
- 位置: `~/.claude/config.json`
- 服务数: 14 个
- 工具总数: 当前 180 个（实时 `tools/list`；历史基线曾为 169 个）
- Resource 总数: 当前 242 个（实时 `resources/list`）
- `outputSchema`: 180/180 个工具（100%）

## 已完成的代码修复

### 1. 资产收购领域 Resource 路由 ✅
- [x] `resource_registry.py`: 注册 `asset-acquisition` 到 DOMAINS
- [x] `lvke_asset_acquisition/server.py`: 实现 `list_resources()` 和 `read_resource()`
- [x] `lvke_asset_acquisition/service.py`: 新增资源适配器方法
- [x] 验证路径: `lvke://asset-acquisition/runs/{run_id}`, `lvke://asset-acquisition/artifacts/{artifact_id}/csv/{table_id}`

### 2. 报告工件读取函数修正 ✅
- [x] `finding_rules.py`: `read_artifact_candidate_download()` → `read_artifact_download()`
- [x] 修复位置: 533 行和 557 行

### 3. 光伏收购税前现金流补充 ✅
- [x] `solar_engine.py`: 构建并输出 `project_pre_tax_cashflows_wan`
- [x] 计算逻辑: 收入 - 运营成本 - 维护资本 + 退出现金

### 4. 报告审查语义模式优化 ✅
- [x] `patterns.py`: 折现率模式增加"目标收益率|要求收益率|收益率门槛"
- [x] `normalize.py`: DSCR/ICR 单位从 `%` 改为 `{"", "倍"}`（无量纲）
- [x] `normalize.py`: 语义边界增加"及"、"和"、"与"

### 5. 财务 Basis 加载分派 ✅
- [x] `snapshots.py`: `_load_finance_run()` 识别 `acqrun_` 前缀并调用 acquisition_backend

### 6. 审查规则适用性调整 ✅
- [x] `rules.py`: `FIN.WORKING_CAPITAL.DRIVER` 和 `FIN.PERIOD.RECONCILIATION` 从 combined 专用改为通用可研也适用

## 待验收项目（需 MCP 工具调用）

### 核心功能验证

- [ ] **统一 Resource 路由读取资产收购文件**
  ```
  工具: lvke_list_resources(domain="asset-acquisition")
  预期: 返回 runs、artifacts、tables 等资源列表
  
  工具: lvke_read_resource(uri="lvke://asset-acquisition/artifacts/{id}/csv/cashflow")
  预期: 返回 CSV 内容，与原生读取字节完全一致
  ```

- [ ] **报告修订 lineage 一致性**
  ```
  场景: 导出 DOCX 期间切换 current revision
  预期: 工件仍绑定调用方指定的 report_revision_id
  
  场景: 重复调用 report_status
  预期: 同一 native_revision_id 不创建多个 public revision
  ```

- [ ] **联合审查修订绑定**
  ```
  场景: combined target 包含显式 report_revision 组件
  预期: ReviewPreparation.bindings.report_revision_id 等于组件指定 ID
  ```

- [ ] **DOCX 工件可读性**
  ```
  场景: 读取通用报告 DOCX artifact
  预期: 提取非空正文，无 report_content_unreadable 错误
  ```

- [ ] **资产收购 draft 门禁**
  ```
  场景: acquisition run 存在且通过
  预期: draft DOCX 不显示 finance_run_unavailable
  预期: 技术预览 technical_ready=true
  预期: 正式发布 formal_release_eligible=false（受限夹具）
  ```

- [ ] **语义识别准确性**
  ```
  输入: "低于8%的目标收益率及1.2的最低偿债备付率"
  预期: 8% 识别为折现率阈值，1.2 识别为 DSCR
  预期: 不产生 REPORT.NUMBERS.BOUND 或 COMBINED.NUMBERS.MATCH 误报
  ```

- [ ] **光伏税费复算**
  ```
  工具: review_start(光伏 acquisition run)
  预期: FIN.TAX.RECALC 状态为 executed（不再是 rule_input_unavailable）
  ```

- [ ] **情景矩阵敏感性**
  ```
  前置: 调用 acquisition_create_scenario_matrix 并完成计算
  预期: FIN.SENSITIVITY.RERUN 状态为 executed
  ```

- [ ] **Combined 规则裁剪**
  ```
  场景: 纯 acquisition combined deliverable
  预期: FIN.WORKING_CAPITAL.DRIVER 和 FIN.PERIOD.RECONCILIATION 不在 rule_not_executed 列表
  ```

### 样本生成与验收

- [ ] **通过样本生成**
  ```
  步骤:
  1. 调用 acquisition_solve_max_price(target_irr=8%, min_dscr=1.2)
  2. 设置购买价为求解结果的 99%
  3. 运行完整模型
  4. 创建 scenario matrix
  5. 生成报告并绑定技术夹具证据
  6. 执行 review(evidence_track=technical_fixture, review_purpose=process_acceptance)
  
  预期:
  - technical_verdict = pass
  - release_verdict = pass
  - overall_verdict = pass
  - 0 个开放 finding
  - 0 个 incomplete reason
  ```

- [ ] **风险样本保留**
  ```
  场景: 保留原 3 亿元、60% 融资样本
  预期: 
  - 最低 DSCR < 1.2
  - 产生 FIN.DEBT.COVERAGE P0 finding
  - finding 状态 = open
  ```

- [ ] **真实资料轨诚实阻断**
  ```
  场景: evidence_track=real, review_purpose=project_delivery
  缺失: 权证、并网协议、历史发电、财务报表
  预期:
  - formal_release_eligible = false
  - blockers 明确列出缺失资料
  - 不创建伪造的 formal evidence
  ```

## 技术债务与已知限制

### 暂不修复（按设计）

- **build_time 不完整**: 工作树有 43 个 tracked modification，fail-closed 符合预期
- **Codex 工具投影缺字段**: `taskSupport=forbidden` 在服务端正确但 Codex 不展示
- **最低 DSCR 风险**: 真实财务问题，不应通过降低门槛解决

### 待后续迭代

- **artifact_domain 歧义**: 字段语义是"存储族"，考虑改名或自动解析
- **旧 artifact 兼容**: 缺 report_revision_id 时只在唯一候选时回推
- **scenario matrix 存储**: 审查应从持久化 store 读取，不只检查 run 顶层

## 验收流程

1. **干净构建**
   ```bash
   # 在独立 worktree 生成 build metadata
   git worktree add /tmp/lvke-clean main
   cd /tmp/lvke-clean
   # 生成 build metadata
   # 验证 build_time 完整
   ```

2. **重启 Claude Code**（已完成）
   - 加载 14 个 MCP 服务
   - 验证当前 180 个工具可用，并确认 242 个 Resource 可枚举

3. **冒烟测试**
   ```bash
   cd src
   pytest tests/integration/test_asset_acquisition_artifact_gates.py -v
   pytest tests/integration/test_report_finance_regressions.py -v
   ```

4. **完整测试套件**
   ```bash
   pytest --maxfail=5
   ```

5. **实时 MCP 验收**
   - 创建新 workspace
   - 逐项执行"待验收项目"清单
   - 读取 13 个 CSV 和 XLSX
   - 检查 DOCX 字体、OFL、字形覆盖
   - 逐页渲染 PNG 视觉检查

## 成功标准

- ✅ 所有单元测试和集成测试通过
- ✅ 14 个服务全部 initialize 成功
- ✅ 当前 180 个工具全部可调用（历史验收记录中的 169 不代表当前分母）
- ✅ 统一 Resource 路由读取资产收购文件成功
- ✅ 通过样本三个 verdict 均为 pass
- ✅ 风险样本稳定产生 DSCR P0
- ✅ 真实资料轨诚实阻断，不伪造证据
