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

from dataclasses import dataclass, field


@dataclass
class FillResult:
    markdown: str
    #: 行/列键不匹配、number 列收到非数值、整表空白等确定性缺口。
    warnings: list[str]
    #: **真的收到数据**的行数。曾经等于模板行数（空 data 也报 15），
    #: 那让"填了多少"无从判断；模板渲染出的行数改用 ``rows_rendered``。
    rows_used: int
    #: 表里实际渲染出的行数（静态模板=模板行数，动态模板=有效传入行数）。
    #: ``init=False``：``FillResult(markdown, warnings, rows_used)`` 的构造签名
    #: 被 API 快照门禁冻结，新增字段不能进 ``__init__``。
    rows_rendered: int = field(default=0, init=False)


def _format_cell(value, col: dict, *, non_numeric: list[tuple[str, object]] | None = None) -> str:
    """格式化单元格；``non_numeric`` 收集"声明 number 却填了非数值"的格子。

    缺陷（R3）：``type:"number"`` 的列此前接受 ``"五千万"``/``"很高"`` 并原样渲染，
    零 warning —— 表面上是一张填好的制式表，实际那两格根本不是数。原实现在
    ``float(value)`` 抛 ``ValueError`` 时静默 ``return str(value)``，把类型冲突
    降级成"照抄原文"。现在照抄仍保留（不猜数、不丢数据），但把冲突登记出来。
    """

    if value is None or value == "":
        return ""
    col_type = col.get("type", "string")
    if col_type == "number":
        # bool 是 int 的子类，float(True)==1.0 会把 True 静默渲染成 1。
        if isinstance(value, bool):
            if non_numeric is not None:
                non_numeric.append((str(col.get("key") or ""), value))
            return str(value)
        try:
            num = float(value)
        except (TypeError, ValueError):
            if non_numeric is not None:
                non_numeric.append((str(col.get("key") or ""), value))
            return str(value)
        if abs(num - round(num)) < 1e-9:
            text = f"{int(round(num)):,}"
        else:
            text = f"{num:,.2f}"
        return text
    return str(value)


def _has_payload(row_data: dict) -> bool:
    """该行是否真的收到了数据（至少一个非空值）。"""

    return any(value not in (None, "") for value in row_data.values())


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

    col_keys = [str(col["key"]) for col in cols]
    # 声明 number 却填了非数值的格子：(row_key, col_key, 原值)。
    non_numeric_cells: list[tuple[str, str, object]] = []

    predefined_rows = template.get("rows")
    if predefined_rows:
        # 静态行模板。data 必须是 {row_key: {col_key: value}}，row_key 形如
        # ``construction.civil``。传成 {"rows": [...]} 或用中文行名做键时，
        # 每一行都 data.get(row_key, {}) 落空 —— 此前整表渲染成空白却零
        # warning，调用方只能靠肉眼发现数据没进去。这里显式报缺口。
        expected_keys = [str(row["key"]) for row in predefined_rows]
        supplied_keys = [str(key) for key in data] if isinstance(data, dict) else []
        expected_set = set(expected_keys)
        matched_keys = [key for key in supplied_keys if key in expected_set]
        # 缺陷（R3）：门禁原本是 ``if supplied_keys and not matched_keys`` —— 只有
        # **全部**键都不匹配才报。传 {"typo.rowkey":…, "construction.civil":…} 时
        # typo 那行被静默丢弃，warnings 仍是 []。部分拼错恰恰比全错更危险：表里有
        # 数、看着填成了，少的那几行只能靠肉眼发现。现在按"未匹配键集合"报。
        unmatched_keys = [key for key in supplied_keys if key not in expected_set]
        if unmatched_keys:
            scope = (
                f"全部 {len(unmatched_keys)} 个"
                if not matched_keys
                else f"{len(unmatched_keys)}/{len(supplied_keys)} 个"
            )
            warnings.append(
                f"data 有 {scope}键不是模板行键，这些数据已被丢弃且不会出现在表里："
                f"{unmatched_keys[:6]}{'…' if len(unmatched_keys) > 6 else ''}。"
                f"期望形如 {{row_key: {{col_key: value}}}}，"
                f"row_key 取值 {expected_keys[:6]}"
                f"{'…' if len(expected_keys) > 6 else ''}；"
                f"col_key 取值 {col_keys}"
            )
        filled_rows = 0
        for row in predefined_rows:
            row_key = str(row["key"])
            row_data = data.get(row_key, {})
            if not isinstance(row_data, dict):
                warnings.append(f"行 '{row_key}' 的数据应是对象,跳过")
                continue
            unknown_cols = [key for key in row_data if str(key) not in set(col_keys)]
            if unknown_cols:
                # 列键拼错与行键拼错同理：值进不了任何格，必须说出来。
                warnings.append(
                    f"行 '{row_key}' 有列键不属于本模板，对应值已被丢弃："
                    f"{sorted(str(key) for key in unknown_cols)[:6]}；col_key 取值 {col_keys}"
                )
            cells: list[str] = []
            row_non_numeric: list[tuple[str, object]] = []
            for col in cols:
                if col["key"] in ("category", "indicator", "label", "source", "factor", "task", "risk"):
                    # 第一列默认填行 label,除非业务方显式覆盖
                    value = row_data.get(col["key"], row["label"])
                else:
                    value = row_data.get(col["key"], "")
                cells.append(_format_cell(value, col, non_numeric=row_non_numeric))
            non_numeric_cells.extend((row_key, col_key, raw) for col_key, raw in row_non_numeric)
            body_lines.append("| " + " | ".join(cells) + " |")
            if _has_payload(row_data):
                filled_rows += 1
        # 缺陷（R3）：``rows_used`` 原本 ``= len(predefined_rows)``，是模板自身的行数，
        # 与"填了多少"完全无关 —— 空 data 也报 15。改成真实收到数据的行数；
        # 模板行数另用 ``rows_rendered`` 表达，两者语义不再混为一谈。
        rows_used = filled_rows
        rows_rendered = len(predefined_rows)
        if rows_rendered and not filled_rows:
            warnings.append(
                f"整表 {rows_rendered} 行全部渲染为空白：data 未提供任何匹配行键的数据"
            )
    else:
        # 动态行模板
        dyn_rows = data.get("rows") or []
        if not isinstance(dyn_rows, list):
            warnings.append("data.rows 必须是列表,跳过填充")
            dyn_rows = []
        rendered = 0
        for idx, row in enumerate(dyn_rows):
            if not isinstance(row, dict):
                warnings.append(f"rows[{idx}] 应是对象,跳过")
                continue
            unknown_cols = [key for key in row if str(key) not in set(col_keys)]
            if unknown_cols:
                warnings.append(
                    f"rows[{idx}] 有列键不属于本模板，对应值已被丢弃："
                    f"{sorted(str(key) for key in unknown_cols)[:6]}；col_key 取值 {col_keys}"
                )
            row_non_numeric: list[tuple[str, object]] = []
            cells = [
                _format_cell(row.get(col["key"], ""), col, non_numeric=row_non_numeric)
                for col in cols
            ]
            non_numeric_cells.extend((f"rows[{idx}]", col_key, raw) for col_key, raw in row_non_numeric)
            body_lines.append("| " + " | ".join(cells) + " |")
            rendered += 1
        # 动态模板下"传了几行"就是"用了几行"；跳过的非对象行不计入。
        rows_used = rendered
        rows_rendered = rendered

    if non_numeric_cells:
        detail = "、".join(
            f"{row_key}.{col_key}={raw!r}" for row_key, col_key, raw in non_numeric_cells[:6]
        )
        warnings.append(
            f"{len(non_numeric_cells)} 个声明为 number 的单元格收到非数值并按原文渲染，"
            f"这些格子不可参与计算或勾稽：{detail}"
            f"{'…' if len(non_numeric_cells) > 6 else ''}"
        )

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

    result = FillResult(markdown=markdown, warnings=warnings, rows_used=rows_used)
    result.rows_rendered = rows_rendered
    return result
