"""模板填充器:把业务数据填入预定义模板,输出 markdown 表 + 校验报告。

输入:
- ``template``: ``catalog.TEMPLATES`` 中的模板对象
- ``data``: 业务数据字典。两种形态:
  1. **静态行模板**(如 investment-estimation):``{"<row.key>": {"<col.key>": value, ...}, ...}``
  2. **动态行模板**(如 sensitivity / risk-matrix):``{"rows": [{"<col.key>": value, ...}, ...]}``

输出:
- ``markdown``: 渲染好的 markdown 表
- ``warnings``: 字段缺失 / 单位异常等告警
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FillResult:
    markdown: str
    warnings: list[str]
    rows_used: int


def _format_cell(value, col: dict) -> str:
    if value is None or value == "":
        return ""
    col_type = col.get("type", "string")
    if col_type == "number":
        try:
            num = float(value)
        except (TypeError, ValueError):
            return str(value)
        if abs(num - round(num)) < 1e-9:
            text = f"{int(round(num)):,}"
        else:
            text = f"{num:,.2f}"
        return text
    return str(value)


def fill_template(template: dict, data: dict) -> FillResult:
    """渲染模板。"""

    warnings: list[str] = []
    cols = template["columns"]
    header_cells = []
    for col in cols:
        label = col["label"]
        if col.get("unit"):
            label = f"{label}({col['unit']})"
        header_cells.append(label)
    header = "| " + " | ".join(header_cells) + " |"
    divider = "| " + " | ".join(["---"] * len(cols)) + " |"

    body_lines: list[str] = []

    predefined_rows = template.get("rows")
    if predefined_rows:
        # 静态行模板
        for row in predefined_rows:
            row_key = row["key"]
            row_data = data.get(row_key, {})
            if not isinstance(row_data, dict):
                warnings.append(f"行 '{row_key}' 的数据应是对象,跳过")
                continue
            cells: list[str] = []
            for col in cols:
                if col["key"] in ("category", "indicator", "label", "source", "factor", "task", "risk"):
                    # 第一列默认填行 label,除非业务方显式覆盖
                    value = row_data.get(col["key"], row["label"])
                else:
                    value = row_data.get(col["key"], "")
                cells.append(_format_cell(value, col))
            body_lines.append("| " + " | ".join(cells) + " |")
        rows_used = len(predefined_rows)
    else:
        # 动态行模板
        dyn_rows = data.get("rows") or []
        if not isinstance(dyn_rows, list):
            warnings.append("data.rows 必须是列表,跳过填充")
            dyn_rows = []
        for idx, row in enumerate(dyn_rows):
            if not isinstance(row, dict):
                warnings.append(f"rows[{idx}] 应是对象,跳过")
                continue
            cells = [_format_cell(row.get(col["key"], ""), col) for col in cols]
            body_lines.append("| " + " | ".join(cells) + " |")
        rows_used = len(dyn_rows)

    notes = template.get("notes") or []
    notes_md = ""
    if notes:
        notes_md = "\n\n**填写口径**：\n" + "\n".join(f"- {n}" for n in notes)

    markdown = "\n".join(
        [
            f"### {template['name']}",
            "",
            header,
            divider,
            *body_lines,
        ]
    ) + notes_md

    return FillResult(markdown=markdown, warnings=warnings, rows_used=rows_used)
