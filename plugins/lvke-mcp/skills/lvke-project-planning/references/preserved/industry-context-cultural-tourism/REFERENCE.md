---
name: industry-context-cultural-tourism
description: "Apply cultural-tourism, children's amusement, theme-park, scenic-area, and cultural-venue feasibility methods. Use for demand, visitor capacity, ticket and secondary-spend revenue, attraction mix, amusement safety, operations, seasonality, and compliance planning."
---

# 文旅与儿童游乐

适用 `industry_code` 为文化、旅游、儿童游乐、主题公园、景区或文化场馆的项目。不得外推到酒店收购、房地产销售或一般制造业。

## 最低输入与对象

- ProjectContext：建设/改造/运营租赁性质、地区、用地和运营主体。
- MarketSizingCase：常住/流动人口、客群半径、到访频次、竞品和季节性。
- BuildScaleCase：场地、承载量、设施分区、疏散和配套容量。
- RevenueDriverSet：客流爬坡、票价、二次消费、租赁/活动收入分别建模。
- CostDriverSet/LaborPlan：设备维护、检验、保险、营销、能耗、岗位和班次。

## 方法与财务映射

使用 `tourism` 收入模型，不用单点 flat 冒充正式收入。压力场景至少覆盖客流、票价、停运日、维修和营销。大型游乐设施按设备类别、寿命和大修周期建模，不把流动资金计入折旧原值。

## 标准与审查

以 `review_resolve_standards` 锁定当前版本；首批核对 GB 8408-2018、GB/T 42101-2022、GB/T 20050-2020、TSG 71-2023、GB 55036-2022 及《大型游乐设施安全监察规定》。只报告适用性和证据状态，不宣称实质合规。

Rubric 重点：客流证据、规模承载、财务绑定、安全边界、七类风险和决策条件。招商终止、权属或 PPP 资料缺失必须保留为外部缺口。
