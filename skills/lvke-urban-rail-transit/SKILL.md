---
name: lvke-urban-rail-transit
description: Plan and model Lvke urban rail transit (metro, light rail, tram) feasibility work. Use for rail ridership and clearing fare, alignment and station siting, per-kilometre investment intensity, car-kilometre operating cost, rolling stock renewal, and operating staffing. Do NOT use the generic public-service route for rail lines.
---

# Lvke Urban Rail Transit

城市轨道交通不是"通用公共服务项目"。它的规模由线路长度、车站数、敷设方式和车辆段决定，
收入由客运量、清分票价、非票比例和财政支持四项分别驱动，成本以车公里为口径。用通用公共
服务口径建模会同时错在规模、收入结构和成本口径三处。

## Routing

按任务读取 [references/catalog.md](references/catalog.md) 中相关行，只加载必要参考。

## 六项核心能力

| 能力 | 何时用 | 关键约束 |
|---|---|---|
| 轨道客流预测 | 定客运量与清分票价 | 必须区分初期/近期/远期；票价是**线网清分后归属本线**的票款，不是闸机票价 |
| 线站位与敷设方案 | 定线路长度、站数、地下/高架比例 | 地下+高架+地面比例合计为 1；站间距须与线路长度、站数一致 |
| 投资估算 | 定建设投资与单位里程强度 | 地下段与高架段单位造价差异显著，不得用单一均价摊平全线 |
| 车公里运营成本 | 定运营成本 | 以车公里为基本口径，由编组、行车间隔、运营里程推导，不用"占收入比例"倒算 |
| 车辆更新与大修 | 定更新改造资金 | 车辆大修/更新周期须单列，不并入年度折旧 |
| 运营定员 | 定人员与工资 | 按线路长度＋车站数＋班制推导，不用单位面积人数 |

## Gates

1. **收入必须三分**：票务、非票、财政支持分别成项。票务=客运量×清分票价×爬坡；
   非票绑定票务比例情景（5%/10%/15%）；财政支持单列。绝不把补贴摊进票价，也不把三者
   合并成单一"营业收入"——否则票价与客流敏感性无法计算。
   FinanceSpec 用 `revenue.model="rail_transit"`，不是 `gov_payment`。
2. **规模参数只能来自证据**：`planning_get_industry_constraints` 对轨道返回的是
   **字段模板与校验规则**（`evidence_eligibility=field_template_only`），零默认取值。
   线路长度、站数、敷设比例必须以工可批复或线网规划 locator 填充。
3. **尺度一致性先于算术**：50 公里线路配通用单体项目投资种子，即使算术自洽也必须
   阻断（`project_scale_inconsistent`），不得创建 FinanceRun。
4. **不套用用地口径**：容积率、建筑密度、厂房占比对线性工程无意义，出现即为错路由。

## Anti-patterns

- 把轨道项目路由到 `public-service` 或住宅开发 → 规模与收入结构双错
- 用 `gov_payment` 单一政府付费建模 → 丢失票价/客流敏感性
- 用"运营成本占收入比例"倒算成本 → 与车公里口径脱节
- 用行业种子投资额直接跑 50 公里线路 → 尺度不一致，须先对账
