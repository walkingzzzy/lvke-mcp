---
name: industry-context-logistics-transport
description: "Apply feasibility methods for logistics parks, warehouses, freight hubs, passenger terminals, parking, multimodal transport, and supply-chain facilities. Use for cargo or passenger demand, network catchment, throughput, storage and berth capacity, tariffs, equipment, labor, and transport safety."
---

# 物流与交通

适用于物流园、仓储、货运枢纽、客运场站、停车和供应链设施。道路、铁路、港口和机场主体工程还须加载 `industry-context-infrastructure`。

## 最低输入与对象

MarketSizingCase 记录货类/客类、OD、服务半径、现状节点和可转移量；BuildScaleCase 记录年吞吐、峰值日/小时、库容、周转、月台/泊位、停车和集疏运约束；OptionComparison 比较场址、交通组织、设备和运营模式。

## 财务映射

收入按仓储、装卸、运输组织、停车、增值服务分别建模；不得把货值或平台交易额当营业收入。成本覆盖人工班次、车辆/设备、能耗、维修、保险、信息系统和安全投入。场景至少覆盖吞吐、费率、周转率、空置率和燃料/电价。

## 标准与审查

通过标准适用性工具锁定现行交通、仓储、消防、危险货物、环保和安全规范。Rubric 强调需求可转移性、网络衔接、峰值容量、交通影响、跨表财务一致性、危险货物边界和运营韧性。无线路、用地或主管部门意见时保持外部资料缺口。
