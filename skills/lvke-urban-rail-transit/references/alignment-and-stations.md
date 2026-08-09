# 线站位与敷设方案

定线路长度、车站数与敷设方式比例。承载工具：`planning_get_industry_constraints`
（对轨道返回字段模板，零默认取值）、`planning_solve_build_scale`。

## 字段清单

| 字段 | 单位 | 说明 |
|---|---|---|
| `line_length_km` | 公里 | 正线长度，不含出入段线 |
| `station_count` | 座 | 含换乘站；换乘站按本线计 1 座 |
| `average_station_spacing_km` | 公里 | 须与线路长度、站数一致 |
| `underground_ratio` | 比率 | 地下段占比 |
| `elevated_ratio` | 比率 | 高架段占比 |
| `depot_count` | 座 | 车辆段与停车场 |
| `design_speed_kph` | 公里/小时 | 最高设计速度 |
| `train_formation` | 节 | 编组辆数 |
| `headway_seconds` | 秒 | 最小行车间隔 |

## 校验规则

1. `grade_separation_ratio_sum`：地下 + 高架 + 地面比例合计为 1。三者只给两项时，
   第三项由 1 减得，但不得三项都缺。
2. `station_spacing_consistency`：`line_length_km / station_count` 必须落在
   0.5~8.0 公里区间内（见 `scale_guard._STATION_SPACING_KM`）。50 公里配 3 座车站
   即使算术自洽也阻断。

## 门禁

- 参数取值必须以工可批复或线网规划的 locator 填充，
  `planning_get_industry_constraints` 返回 `evidence_eligibility=field_template_only`，
  不取得正式证据资格。
- 容积率、建筑密度、厂房占比对线性工程无意义；出现即为错路由。

## 反模式

- 用单位面积产能推轨道规模 → 线性工程套用用地口径
- 站间距与线路长度、站数互不自洽 → 后续投资与定员全部错
