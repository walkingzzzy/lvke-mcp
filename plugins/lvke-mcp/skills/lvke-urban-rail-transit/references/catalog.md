# 城市轨道交通能力目录

按当前任务选行，只读需要的来源，不预加载。

| 任务 | 能力 | 主要工具 | 来源 SKILL |
|---|---|---|---|
| 定客运量与票价 | 轨道客流预测与清分票价 | `planning_prepare(object_kind='revenue_drivers')`、`dr_start` | [lvke-project-planning](../../lvke-project-planning/REFERENCE.md)、[lvke-research](../../lvke-research/REFERENCE.md) |
| 定线路长度/站数/敷设 | 线站位与敷设方案 | `planning_get_industry_constraints`、`planning_solve_build_scale` | [lvke-project-planning](../../lvke-project-planning/REFERENCE.md) |
| 定建设投资 | 投资估算与单位里程强度 | `finance_build_basis_of_estimate`、`finance_prepare_spec` | [lvke-finance](../../lvke-finance/REFERENCE.md) |
| 定运营成本 | 车公里运营成本 | `planning_calculate_cost_drivers` | [lvke-project-planning](../../lvke-project-planning/REFERENCE.md) |
| 定车辆更新 | 车辆更新与大修周期 | `finance_prepare_spec`（更新改造资金） | [lvke-finance](../../lvke-finance/REFERENCE.md) |
| 定人员工资 | 运营定员编制 | `planning_infer_labor_plan` | [lvke-project-planning](../../lvke-project-planning/REFERENCE.md) |
| 出十三表 | 三分收入落表 | `finance_run_model` → `tables_render` → `tables_export_*` | [lvke-finance](../../lvke-finance/REFERENCE.md) |
| 交付门禁 | 尺度一致性与发布 | `feasibility_validate`、`review_prepare` | [lvke-delivery-guardrails](../../lvke-delivery-guardrails/REFERENCE.md)、[lvke-review-release](../../lvke-review-release/REFERENCE.md) |

## 口径速查

- **清分票价** ≠ 闸机票价：跨线换乘按线网清分规则分摊，只有归属本线的部分计入本线票务收入。
- **非票收入**：广告、商业租赁、通信管道等，按票务收入比例情景估算（low 5% / base 10% / high 15%）。
- **财政支持**：运营补贴单列成项，既不摊进票价也不记作保证利润。
- **车公里**：年运营车公里 = 配属列车数 × 日走行公里 × 运营天数，成本按此口径分摊。
