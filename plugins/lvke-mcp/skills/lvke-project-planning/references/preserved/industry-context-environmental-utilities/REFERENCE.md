---
name: industry-context-environmental-utilities
description: "Apply feasibility methods for water, wastewater, solid waste, environmental facilities, heating, gas, and other public utilities. Use for demand and design capacity, inflow or waste composition, tariff and government-payment revenue, process options, environmental compliance, and lifecycle cost."
---

# 环保与公用事业

适用于水务、污水、固废、环保设施、供热、燃气和公用事业。危险废物、燃气等高风险项目必须加载专门安全与许可要求。

## 最低输入与对象

以服务区域、现状负荷、增长、峰值系数和收集率建立 MarketSizingCase；BuildScaleCase 同时满足设计流量/处理量、用地、冗余和排放约束；OptionComparison 比较工艺的 CAPEX、OPEX、能耗、药耗、污泥/残渣和达标风险。

## 财务映射

区分处理费、使用者付费、政府可用性付费、补贴和资源化收入；明确调价机制、最低量、回款周期和绩效扣款。成本覆盖电耗、药剂、人工、维修、检测、残渣处置和周期大修。

## 标准与审查

通过 `review_resolve_standards` 锁定现行环境质量、污染物排放、工程设计、消防和安全版本，并绑定许可/环评/监测证据。Rubric 强调物料平衡、规模峰值、达标边界、全生命周期成本、应急冗余和公众环境风险。技术 fixture 不得形成达标承诺。
