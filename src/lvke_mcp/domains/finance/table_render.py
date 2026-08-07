"""从 catalog 列定义 + annual/investment 结构化数据投影 13 表。

开发期真源：structured rows（非手写 MD）。
MD 仅作为 structured → 管道表 的适配器输出。

Wave 3.1 门面：实现搬到 ``_table_render/`` 子模块 —— ``specs``（格式化原语、
十三表静态规格与交付顺序）、``field_source``（字段取值来源解析）、
``primitives``（期间/序列/求和行列原语）、``normalize``（行归一化、参考行树与
渲染行契约）、``builders``（投资/资金筹措/营运资金三表 builder）、
``reference``（参考矩阵与参考期间表提升）、``orchestrator``（缺项评估、表适用性
与结构化编排入口）与 ``markdown``（结构化表到 markdown）。

纯搬移：``_TABLE_SPECS`` 与 ``DELIVERY_ORDER`` 只在 ``specs`` 有一份，
十三表的列定义、行顺序与数值口径均未改动。
"""

from __future__ import annotations

from typing import Any, Optional  # noqa: F401

from lvke_mcp.domains.finance.reference_schema import (  # noqa: F401
    assess_missing_fields_extended,
    assess_fact_source_coverage,
    assess_structure_coverage,
    merge_missing,
    schema_path,
    validate_reference_sources,
)

from ._table_render.builders import (  # noqa: F401
    _build_funding,
    _build_investment,
    _build_wc,
    _pack_rows,
)
from ._table_render.field_source import (  # noqa: F401
    _approved_direct_rows,
    _confirmed_fact_domains,
    _effective_input_revision,
    _get_field,
    _repay_source_facts,
    _source_kind,
    _source_value,
)
from ._table_render.markdown import (  # noqa: F401
    finance_tables_markdown_from_structured,
    render_all_markdown_from_structured,
    structured_table_to_md,
)
from ._table_render.normalize import (  # noqa: F401
    _attach_reference_row_trees,
    _normalize_rows,
    _renderer_row_contract,
)
from ._table_render.orchestrator import (  # noqa: F401
    _assess_missing_fields,
    _table_applicability,
    build_all_structured,
)
from ._table_render.primitives import (  # noqa: F401
    _column_values,
    _item_row_period_values,
    _last_value,
    _number,
    _period_value,
    _series,
    _sum_values,
)
from ._table_render.reference import (  # noqa: F401
    _canonical_cost_label,
    _promote_reference_period_table,
    _reference_matrix,
)
from ._table_render.specs import (  # noqa: F401
    DELIVERY_ORDER,
    _TABLE_SPECS,
    _fmt,
    _fmt_cell,
    _fmt_rate_pct,
)
