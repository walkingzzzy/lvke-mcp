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
        # 静态行模板。data 必须是 {row_key: {col_key: value}}，row_key 形如
        # ``construction.civil``。传成 {"rows": [...]} 或用中文行名做键时，
        # 每一行都 data.get(row_key, {}) 落空 —— 此前整表渲染成空白却零
        # warning，调用方只能靠肉眼发现数据没进去。这里显式报缺口。
        expected_keys = [str(row["key"]) for row in predefined_rows]
        supplied_keys = [str(key) for key in data] if isinstance(data, dict) else []
        matched_keys = [key for key in supplied_keys if key in set(expected_keys)]
        if supplied_keys and not matched_keys:
            warnings.append(
                "data 的键与模板行键完全不匹配，整表将渲染为空白。"
                f"传入键={supplied_keys[:6]}；期望形如 {{row_key: {{col_key: value}}}}，"
                f"row_key 取值 {expected_keys[:6]}"
                f"{'…' if len(expected_keys) > 6 else ''}；"
                f"col_key 取值 {[col['key'] for col in cols]}"
            )
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
