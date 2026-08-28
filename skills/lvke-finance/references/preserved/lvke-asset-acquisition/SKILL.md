---
name: lvke-asset-acquisition
description: 资产收购（酒店租赁、光伏电站）的 12 个 acquisition_* 工具：规范校验、确认、建模、情景矩阵、最高可接受价、十三表与出件的完整调用顺序与门禁。
platforms: [linux, macos, windows]
metadata:
  conditions:
    tools_any:
      - acquisition_validate_spec
      - acquisition_run_model
      - acquisition_render_tables
---

# 资产收购建模与出件

资产收购走**独立于通用可研的模型路线**：酒店租赁用月度模型，光伏用年度运营模型。
不要用 `finance_run_model` 算收购项目，也不要用 `acquisition_run_model` 算新建项目。

判别字段是 `finance_kind="asset_acquisition"` + `asset_type`（`hotel_lease` / `solar_power`）。
`invest_type` 是投资属性，**不能替代** `finance_kind`；缺 `finance_kind` 时模型只能注入
执行默认值，`validation_status` 会停在 `calculated` 而非 `passed`。

## 标准调用顺序

```text
1. acquisition_validate_spec(spec)                                  # 只校验，不落库
2. acquisition_save_spec(workspace_id, spec, idempotency_key)        # 固化候选
3. acquisition_confirm_spec(workspace_id, spec_id, idempotency_key)  # 产出不可变确认修订
4. acquisition_run_model(workspace_id, spec_id, idempotency_key)     # 建 run
5. acquisition_render_tables(workspace_id, run_id, idempotency_key)  # 十三表 package
6. acquisition_export_tables_csv(workspace_id,
       acquisition_tables_package_id, idempotency_key)               # 落盘 CSV
   acquisition_export_tables_xlsx(workspace_id,
       acquisition_tables_package_id, idempotency_key)               # 落盘 XLSX
```

只读与分析入口，可在 4 之后任意时点调用：

| 工具 | 必填 | 用途 |
|---|---|---|
| `acquisition_get_run` | `workspace_id`, `run_id` | 读 run，`view` 支持 summary/result/governance/full |
| `acquisition_get_artifact` | `workspace_id`, `artifact_id` | 读已固化工件及内容/数值一致性状态 |
| `acquisition_create_scenario_matrix` | `+ dimensions` | 按独立维度做最多 64 组笛卡尔积 |
| `acquisition_solve_max_price` | `+ target_irr` 或 `min_dscr` | 反解最高可接受收购价 |
| `acquisition_generate_artifact` | `+ idempotency_key` | 出 Word/Excel/report-data，**有正式资格门禁** |

## 门禁

**`acquisition_confirm_spec` 结构校验失败即拒绝，不产出确认修订。**
确认修订是不可变基准，run / tables / artifact 全部以它为准；让一个结构非法的 spec
拿到确认修订等于把非法基准固化进整条链。受控假设预览路线只放宽"正式证据"，
不放宽"controlled_assumptions 七项必填齐全"（field / value / unit / basis /
impact / sensitivity / validation_condition）。

**`acquisition_generate_artifact` 正式资格不足时直接拒绝**，返回
`FORMAL_ARTIFACT_QUALIFICATION_REQUIRED`，不是"照出并附一段限制说明"。
原因：收购工件一旦生成就会被当交付物流转，而限制说明只留在 MCP 响应里、
不进文件，收件人看不到。资格项包括 `delivery_mode=formal_candidate`、
`validation_status=passed`、`formal_spec_valid`、证据正式合格、无未关闭阻断项。

**需要过程验收件时改走十三表导出**：`acquisition_render_tables` +
`acquisition_export_tables_csv/xlsx`。这条路径的等级从 package 已固化字段派生
（`evidence_policy` / `delivery_mode` / `project_fact_certified` /
`release_limitations`），**不接受调用方传参**，因此无法通过省略参数把预览件提级：

- CSV 每个文件第一行写入「技术预览·不可正式使用」整行声明（CSV 无注释语法）
- XLSX 每个 sheet 的 A1 写入预览横幅，文件名带 `.technical.xlsx` 后缀
- 响应带 `release_grade=technical_preview`、`formal_usable=false`、`status=partial`

`acquisition_render_tables` 与 `acquisition_get_run` 等只读入口同样携带上述等级字段
（四个入口共用一个结果构造器），所以绑定 table package 前先看 `formal_usable`。

## 只消费固化对象

`acquisition_render_tables` 只消费已固化 run，绝不重算财务；
`acquisition_export_tables_*` 只消费已通过列级完整性与勾稽门禁的 package。
收购价与经营参数在情景矩阵里是**独立维度**，不联动——不要假设调价会自动带动 ADR
或利用小时数。

## 反例

❌ 用 `finance_run_model` 算酒店收购 → 路线错，走的是通用新建模型
❌ spec 缺 `finance_kind` 就直接 run → `validation_status` 停在 `calculated`
❌ 拿 `acquisition_generate_artifact` 的拒绝当"工具坏了" → 那是资格门禁，改走十三表导出
❌ 看到 `render_tables` 返回 `success=true` 就当正式件 → 要看 `formal_usable`
