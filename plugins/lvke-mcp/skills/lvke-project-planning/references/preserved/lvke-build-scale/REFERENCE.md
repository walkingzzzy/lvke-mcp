---
name: lvke-build-scale
description: "Create deterministic Lvke BuildScaleCase objects constrained by confirmed market demand, target capacity, land, facilities, plot ratio, coverage, and green ratio. Use before project investment, staffing, finance, or construction-scheme drafting."
---

# 建设规模

以 confirmed `MarketSizingCase` 为需求上限，把目标产能、用地和规划约束真实纳入计算。该对象是技术规划输入，不是设计成果。

## 工作流

1. 统一市场需求与目标产能单位。
2. 调用 `planning_get_industry_constraints` 读取版本化技术参数；它不是正式规划证据。
3. 提供用地面积、单位面积产能、设施建筑面积和占地面积。
4. 明确容积率上下限、建筑密度上限、绿地率下限和绿地面积。
5. 调用 `planning_solve_build_scale` 和 `planning_validate_build_scale`，核对可行、边界与不可行方案。
6. Codex 明确选择理由及全部舍弃方案，调用 `planning_confirm_build_scale`；再用 `planning_get_build_scale` 读取 confirmed revision。
7. 将 confirmed `build_scale_case_id` 交给成本和定员工具。

## 门禁

- 目标产能不得超过已选择的同单位市场需求。
- 产能面积不足或任一规划约束失败时保持 blocked。
- `planning_conversion_ledger` 只描述规模转换，不冒充 FinanceSpec 字段。
- 未取得设计依据时，在报告中标记为技术测算，不写成已审批方案。
- `planning_create_build_scale` 仅为兼容入口；新正式流程不得用它绕过方案选择。
