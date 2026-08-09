# 城市轨道交通能力目录

按当前任务选行，只读需要的来源，不预加载。

## 六项核心能力（本 Skill 承载口径）

| 任务 | 能力 | 口径参考 | 主要工具 |
|---|---|---|---|
| 定客运量与票价 | 轨道客流预测与清分票价 | [ridership-and-clearing-fare.md](ridership-and-clearing-fare.md) | `planning_prepare(object_kind='revenue_drivers')`、`dr_start` |
| 定线路长度/站数/敷设 | 线站位与敷设方案 | [alignment-and-stations.md](alignment-and-stations.md) | `planning_get_industry_constraints`、`planning_solve_build_scale` |
| 定建设投资 | 投资估算与单位里程强度 | [investment-estimate.md](investment-estimate.md) | `finance_build_basis_of_estimate`、`finance_prepare_spec` |
| 定运营成本 | 车公里运营成本 | [car-km-operating-cost.md](car-km-operating-cost.md) | `planning_calculate_cost_drivers` |
| 定车辆更新 | 车辆更新与大修周期 | [rolling-stock-renewal.md](rolling-stock-renewal.md) | `finance_prepare_spec` |
| 定人员工资 | 运营定员编制 | [operating-staffing.md](operating-staffing.md) | `planning_infer_labor_plan` |

## 转派到通用流程

| 任务 | 来源 SKILL |
|---|---|
| 出十三表（`finance_run_model` → `tables_render` → `tables_export_xlsx`/`tables_export_csv`） | [lvke-finance](../../lvke-finance/SKILL.md) |
| 交付门禁（`feasibility_validate`、`review_prepare`） | [lvke-delivery-guardrails](../../lvke-delivery-guardrails/SKILL.md)、[lvke-review-release](../../lvke-review-release/SKILL.md) |
| 客流与政策研究 | [lvke-research](../../lvke-research/SKILL.md) |
| 规划对象生命周期 | [lvke-project-planning](../../lvke-project-planning/SKILL.md) |

## 口径速查

- **清分票价** ≠ 闸机票价：跨线换乘按线网清分规则分摊，只有归属本线的部分计入本线票务收入。
- **非票收入**：广告、商业租赁、通信管道等，按票务收入比例情景估算（low 5% / base 10% / high 15%）。
- **财政支持**：运营补贴单列成项，既不摊进票价也不记作保证利润。
- **车公里**：年运营车公里 = 配属列车数 × 编组辆数 × 日走行公里 × 运营天数，成本按此口径分摊。
