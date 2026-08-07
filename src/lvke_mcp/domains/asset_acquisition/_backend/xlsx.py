"""最小 xlsx 写出与单元格/列原语。"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET




def _xlsx_summary_values(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    values: dict[str, str] = {}
    for row in root.iter():
        if not row.tag.endswith("}row"):
            continue
        cells: list[str] = []
        for cell in row:
            if not cell.tag.endswith("}c"):
                continue
            inline = "".join(node.text or "" for node in cell.iter() if node.tag.endswith("}t"))
            raw = next((node.text or "" for node in cell if node.tag.endswith("}v")), "")
            cells.append(inline if inline else raw)
        if len(cells) >= 2:
            values[cells[0]] = cells[1]
    return values


def _file_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _xlsx_col(index: int) -> str:
    out = ""
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def _xml_cell(row: int, column: int, value: Any) -> str:
    from xml.sax.saxutils import escape

    ref = f"{_xlsx_col(column)}{row}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value or ""))}</t></is></c>'


def _sheet_xml(rows: list[list[Any]]) -> str:
    body = []
    for row_index, values in enumerate(rows, 1):
        cells = "".join(_xml_cell(row_index, col, value) for col, value in enumerate(values, 1))
        body.append(f'<row r="{row_index}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(body)}</sheetData></worksheet>'
    )


def _write_minimal_xlsx(
    path: Path, run: dict[str, Any], *, report_data: dict[str, Any] | None = None,
) -> None:
    result = run.get("result") or {}
    indicators = result.get("indicators") or {}
    report_data = report_data or {}
    asset_type = str(result.get("asset_type") or report_data.get("asset_type") or "hotel_lease")
    is_solar = asset_type == "solar_power"
    max_price_analysis = report_data.get("maximum_acceptable_price") or {}
    max_price_result = max_price_analysis.get("result") or {}
    summary = [
        ["资产收购财务模型", run.get("run_id")],
        ["模型版本", run.get("model_version")],
        ["Spec哈希", run.get("spec_hash")],
        ["证据绑定哈希", run.get("evidence_binding_hash")],
        ["证据绑定版本", run.get("evidence_binding_version")],
        ["收购价格(万元)", result.get("purchase_price_wan")],
        ["总收购成本(万元)", result.get("total_acquisition_cost_wan")],
        ["项目IRR(%)", indicators.get("project_irr_pct")],
        ["资本金IRR(%)", indicators.get("equity_irr_pct")],
        ["NPV(万元)", indicators.get("npv_wan")],
        ["最低DSCR", indicators.get("minimum_dscr")],
        ["最低ICR", indicators.get("minimum_icr")],
        ["维修资本开支覆盖", indicators.get("maintenance_capex_coverage")],
        ["退出价值NPV占比", indicators.get("exit_value_npv_ratio")],
        ["最高可接受收购价(万元)", max_price_result.get("max_acquisition_price_wan")],
        ["最高价目标IRR", (max_price_analysis.get("parameters") or {}).get("target_irr")],
        ["最高价最低DSCR", (max_price_analysis.get("parameters") or {}).get("min_dscr")],
        ["最高价求解哈希", max_price_analysis.get("analysis_hash")],
        ["最高价验证状态", max_price_analysis.get("validation_status")],
    ]
    if is_solar:
        solar = result.get("solar_operation") or {}
        summary[13:13] = [
            ["装机容量(MW)", solar.get("installed_capacity_mw")],
            ["基准发电量(MWh)", solar.get("base_generation_mwh")],
            ["上网电价(元/kWh)", solar.get("tariff_yuan_per_kwh")],
            ["限电率", solar.get("curtailment_rate")],
            ["年衰减率", solar.get("degradation_rate")],
        ]
    else:
        summary[13:13] = [
            ["最低租金覆盖率", indicators.get("minimum_tenant_rent_coverage")],
            ["租约覆盖年限", indicators.get("lease_coverage_years")],
            ["合同收入占比", indicators.get("contract_income_ratio")],
            ["未锁定收入占比", indicators.get("unlocked_income_ratio")],
        ]
    project = result.get("project_cashflows_wan") or []
    equity = result.get("equity_cashflows_wan") or []
    cashflows = [["年度", "项目现金流(万元)", "资本金现金流(万元)"]]
    for index in range(max(len(project), len(equity))):
        cashflows.append([index, project[index] if index < len(project) else "", equity[index] if index < len(equity) else ""])
    def json_text(value: Any) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return str(value or "")

    parties = [["主体ID", "名称", "角色", "状态", "证据"]] + [
        [row.get("entity_id"), row.get("name"), json_text(row.get("roles")), row.get("status"), json_text(row.get("evidence_ids"))]
        for row in report_data.get("party_relationships") or []
    ]
    assets = [["资产ID", "类型", "是否纳入", "面积(㎡)", "状态", "冲突", "裁决", "证据"]] + [
        [row.get("scope_id"), row.get("type"), row.get("included"), row.get("area_sqm"), row.get("status"),
         json_text(row.get("conflicts")), row.get("resolution"), json_text(row.get("evidence_ids"))]
        for row in report_data.get("asset_boundary") or []
    ]
    if is_solar:
        operations = [[
            "年度", "理论发电量(MWh)", "上网电量(MWh)", "上网电价(元/kWh)",
            "售电收入(万元)", "运维费(万元)", "维护性资本开支(万元)",
            "所得税(万元)", "债务服务(万元)", "项目现金流(万元)", "资本金现金流(万元)",
        ]] + [
            [
                row.get("year"), row.get("gross_generation_mwh"), row.get("sold_generation_mwh"),
                row.get("tariff_yuan_per_kwh"), row.get("revenue_wan"), row.get("operating_cost_wan"),
                row.get("maintenance_capex_wan"), row.get("income_tax_wan"), row.get("debt_service_wan"),
                row.get("project_cf_wan"), row.get("equity_cf_wan"),
            ]
            for row in report_data.get("solar_operating_ledger") or []
        ]
    else:
        leases = [["单元ID", "位置", "面积(㎡)", "出租人", "承租人", "起始日", "终止日", "基础租金(万元)", "证据"]] + [
            [row.get("unit_id"), row.get("asset_location"), row.get("area_sqm"), row.get("lessor_id"), row.get("lessee_id"),
             row.get("start_date"), row.get("end_date"), row.get("base_rent_wan"), json_text(row.get("evidence_ids"))]
            for row in report_data.get("lease_ledger") or []
        ]
    history = [["主体", "开始日", "结束日", "报表类型", "来源格式", "数值勾稽", "异常", "来源定位"]] + [
        [row.get("entity_id"), row.get("period_start"), row.get("period_end"), row.get("statement_type"), row.get("source_format"),
         json_text(row.get("reconciliation")), json_text(row.get("anomalies")), json_text(row.get("source_locators"))]
        for row in report_data.get("historical_financial_comparison") or []
    ]
    scenarios = ([
        ["矩阵ID", "场景ID", "变更", "收购价(万元)", "上网电价(元/kWh)", "年发电量(MWh)", "利用小时", "年运维费(万元)", "融资比例", "指标"]
    ] if is_solar else [
        ["矩阵ID", "场景ID", "变更", "收购价(万元)", "市场租金", "入住率", "融资比例", "指标"]
    ])
    for matrix in report_data.get("scenario_matrices") or []:
        for row in matrix.get("rows") or []:
            common = [
                matrix.get("matrix_id"), row.get("scenario_id"), json_text(row.get("changes")),
                row.get("purchase_price_wan"),
            ]
            scenarios.append(common + ([
                row.get("tariff_yuan_per_kwh"), row.get("annual_generation_mwh"),
                row.get("utilization_hours"), row.get("annual_opex_wan"),
                row.get("financing_ratio"), json_text(row.get("indicators")),
            ] if is_solar else [
                json_text(row.get("market_rent")), json_text(row.get("occupancy")),
                row.get("financing_ratio"), json_text(row.get("indicators")),
            ]))
    risks = [["类型", "编码/内容", "状态", "裁决/说明", "证据"]]
    risks.extend([
        ["red_flag", row.get("code"), row.get("status"), row.get("resolution"), json_text(row.get("evidence_ids"))]
        for row in report_data.get("red_flags") or []
    ])
    risks.extend([["closing_condition", item, "", "", ""] for item in report_data.get("closing_conditions") or []])
    risks.extend([["veto_item", item, "", "", ""] for item in report_data.get("veto_items") or []])
    evidence = [["字段", "证据ID", "来源", "绑定状态"]] + [
        [row.get("field"), json_text(row.get("evidence_ids")), json_text(row.get("source")), row.get("validation_status")]
        for row in report_data.get("source_processing_ledger") or []
    ]
    sheets: list[tuple[str, list[list[Any]]]] = [("收购摘要", summary), ("现金流", cashflows)]
    if report_data:
        sheets.extend([("主体关系", parties), ("资产边界", assets)])
        sheets.append(("光伏运营", operations) if is_solar else ("租约台账", leases))
        sheets.extend([
            ("历史财务", history), ("情景矩阵", scenarios),
            ("风险与条件", risks), ("证据台账", evidence),
        ])
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + ''.join(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for index in range(1, len(sheets) + 1)
        )
        + '</Types>'
    )
    from xml.sax.saxutils import escape

    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets>'
        + ''.join(
            f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
            for index, (name, _rows) in enumerate(sheets, 1)
        )
        + '</sheets></workbook>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + ''.join(
            f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
            for index in range(1, len(sheets) + 1)
        )
        + '</Relationships>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        for index, (_name, rows) in enumerate(sheets, 1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _sheet_xml(rows))
