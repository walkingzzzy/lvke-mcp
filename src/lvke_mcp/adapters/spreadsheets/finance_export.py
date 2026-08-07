"""Finance workbook export with formulas (P2-2).

Exports a review-friendly xlsx with the 13 delivery sheets, cross-sheet formulas,
cell lineage, template-gap disclosure, Inputs, Checks and Meta.

Wave 3.7 facade: implementation moved to ``_finance_export/`` sub-modules —
``base`` (error type, required formula families, delivery-sheet mapping, openpyxl
dependency check and soffice recalculate), ``sheets`` (input, year-table,
indicators and checks pages), ``delivery_tables`` (the thirteen-table writer; a
single transaction boundary kept as one 1,500-line function per §4) and
``workbook`` (delivery-quality assessment and the export entry point).
"""

from __future__ import annotations

import hashlib  # noqa: F401
import logging  # noqa: F401
import re  # noqa: F401
import os  # noqa: F401
import shutil  # noqa: F401
import subprocess  # noqa: F401
import tempfile  # noqa: F401
import uuid  # noqa: F401
from datetime import datetime, timezone  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any, Optional, Union  # noqa: F401

from filelock import FileLock  # noqa: F401

from ._finance_export.base import (  # noqa: F401
    _DELIVERY_SHEETS,
    _REQUIRED_FORMULA_FAMILIES,
    FinanceExportError,
    _recalculate_with_soffice,
    _require_openpyxl,
    logger,
)
from ._finance_export.delivery_tables import _write_delivery_tables  # noqa: F401
from ._finance_export.sheets import (  # noqa: F401
    _input_rows,
    _write_checks,
    _write_indicators,
    _write_inputs,
    _write_year_table,
)
from ._finance_export.workbook import (  # noqa: F401
    assess_finance_delivery_quality,
    export_finance_workbook,
)
