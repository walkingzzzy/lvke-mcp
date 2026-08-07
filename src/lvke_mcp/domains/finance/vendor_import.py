"""Vendor financial-workbook import and cleanup detection.

The imported workbook is a *reference*, never a calculation source of truth.  This
module extracts cached values and formulas, maps heterogeneous vendor sheets to the
13-table business dictionary, and produces deterministic inputs for our own engine.
It never writes back to the vendor workbook.

Wave 3.3 facade: implementation moved to ``_vendor_import/`` sub-modules —
``base`` (version/limit constants, error type, cell-coordinate and number
primitives), ``sheet_read`` (workbook read and business-type inference),
``locate`` (row/column location primitives), ``extract_summary`` (indicator and
sensitivity summaries, trial-rate review), ``reference_pack`` (reference data
extraction and the read-only reference pack), ``cleanup_scan`` (zombie formula and
manual-constant detection), ``project_context`` (loan rate, year sequences,
depreciation classes and cost items) and ``finance_input`` (funding schedules,
construction detail, working capital, staffing, product lines and the two
``build_*`` entry points).
"""

from __future__ import annotations

import hashlib  # noqa: F401
import math  # noqa: F401
import re  # noqa: F401
from collections import Counter, defaultdict  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any, Iterable, Optional  # noqa: F401

from ._vendor_import.base import (  # noqa: F401
    IRR_RESIDUAL_TOL_WAN,
    MAX_COLS,
    MAX_ROWS,
    REFERENCE_PACK_VERSION,
    VendorImportError,
    _CELL_RE,
    _cell_parts,
    _col_index,
    _col_letter,
    _jsonable,
    _norm,
    _to_float,
)
from ._vendor_import.cleanup_scan import (  # noqa: F401
    _CONST_ARITH,
    _constant_formula_frequency,
    _label_locators,
    _orphan_constant_formulas,
    detect_cleanup_issues,
)
from ._vendor_import.extract_summary import (  # noqa: F401
    _extract_indicator_summary,
    _extract_sensitivity_summary,
    _review_trial_rates,
    _sensitivity_factor_code,
)
from ._vendor_import.finance_input import (  # noqa: F401
    _construction_detail_from_items,
    _construction_items_from_investment_sheet,
    _full_working_capital_profile,
    _funding_year_schedules,
    _investment_segment_totals,
    _product_lines_from_revenue_sheet,
    _staff_detail_from_wage_sheet,
    _wc_turnover_from_sheet,
    build_finance_input_from_vendor,
    build_vendor_finance_spec,
)
from ._vendor_import.locate import (  # noqa: F401
    _find_row,
    _first_numeric_on_row,
    _longest_period_sequence,
    _period_headers,
    _row_label,
    _row_value,
    _rows,
    _series_by_label,
    _series_for_row,
    _series_values,
    _total_by_label,
    _total_for_row,
)
from ._vendor_import.project_context import (  # noqa: F401
    _build_period_years,
    _build_years,
    _cost_items,
    _extract_depreciation_classes,
    _extract_depreciation_years,
    _extract_loan_rate,
    infer_vendor_project_context,
)
from ._vendor_import.reference_pack import (  # noqa: F401
    _extract_reference_data,
    build_reference_pack,
)
from ._vendor_import.sheet_read import (  # noqa: F401
    _find_mapped_sheet,
    _header_text,
    _infer_business,
    _read_value_sheets,
    _sheet_is_nonempty,
)
