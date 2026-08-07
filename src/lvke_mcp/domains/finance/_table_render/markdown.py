"""结构化表到 markdown 的渲染。"""

from __future__ import annotations

from typing import Any, Optional


from .specs import (
    DELIVERY_ORDER,
    _fmt,
    _fmt_cell,
)


def structured_table_to_md(table: dict[str, Any]) -> str:
    """单表 structured → markdown 管道表。"""
    cols_meta = table.get("columns") or []
    col_keys = [
        (c.get("key") if isinstance(c, dict) else (c[0] if isinstance(c, (list, tuple)) else ""))
        for c in cols_meta
    ]
    labels = table.get("column_labels") or [
        (c.get("label", "") if isinstance(c, dict) else (c[1] if isinstance(c, (list, tuple)) and len(c) > 1 else str(c)))
        for c in cols_meta
    ]
    rows = table.get("rows") or []
    if not labels:
        return ""
    lines = [
        "| " + " | ".join(str(x) for x in labels) + " |",
        "| " + " | ".join(["---"] * len(labels)) + " |",
    ]
    for row in rows:
        cells = []
        for i, v in enumerate(row):
            key = col_keys[i] if i < len(col_keys) else ""
            cells.append(_fmt_cell(str(key or ""), v))
        while len(cells) < len(labels):
            cells.append("")
        lines.append("| " + " | ".join(cells[: len(labels)]) + " |")
    footer = table.get("footer") or ""
    if footer:
        lines.append("")
        lines.append(footer)
    return "\n".join(lines)


def render_all_markdown_from_structured(pack: dict[str, Any]) -> dict[str, str]:
    """全部交付表 → {key: md_string}，供 result['tables'] 兼容。"""
    return {
        k: structured_table_to_md(pack[k])
        for k in DELIVERY_ORDER
        if k in pack and isinstance(pack.get(k), dict) and pack[k].get("row_count", 0) >= 0
    }


def finance_tables_markdown_from_structured(pack: dict[str, Any], fin: Optional[dict] = None) -> str:
    """拼完整可读 MD（含标题）。"""
    parts = []
    for key in DELIVERY_ORDER:
        t = pack.get(key)
        if not t:
            continue
        md = structured_table_to_md(t)
        if not md and not t.get("row_count"):
            continue
        title = f"{t.get('delivery_no', '')} {t.get('title', key)}"
        parts.append(f"\n\n**{title}**\n\n{md}")
    # 展示表
    if fin:
        ind_md = (fin.get("tables") or {}).get("indicators")
        if ind_md:
            parts.append(f"\n\n**附表（展示）主要技术经济指标表**\n\n{ind_md}")
        sens = (fin.get("tables") or {}).get("sensitivity")
        if sens:
            parts.append(f"\n\n**附表（展示）单因素敏感性分析表**\n\n{sens}")
        scenarios = fin.get("scenarios") or {}
        if scenarios.get("base"):
            parts.append(
                "\n\n**情景分析**\n\n"
                f"- 基准：IRR {_fmt((scenarios.get('base') or {}).get('irr_pct'))}%\n"
                f"- 乐观：IRR {_fmt((scenarios.get('bull') or {}).get('irr_pct'))}%\n"
                f"- 悲观：IRR {_fmt((scenarios.get('bear') or {}).get('irr_pct'))}%"
            )
    return "".join(parts)
