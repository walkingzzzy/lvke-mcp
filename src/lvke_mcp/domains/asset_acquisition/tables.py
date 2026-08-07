"""Immutable, auditable table packages for acquisition model runs.

Wave 3.2 facade: implementation moved to ``_tables/`` sub-modules — ``columns``
(package store, export root and the thirteen-table column definitions including
the in-place required-column extensions for both the hotel and solar sets),
``rows`` (table contract and PPA/depreciation/equity/scenario row builders),
``build`` (table construction, integrity/tie-out checks and lineage), ``render``
(render and package binding), ``export`` (CSV/XLSX export and its pre-checks) and
``query`` (package read, record read, resource resolution and envelopes).

``PACKAGE_STORE`` lives only in ``columns`` and is re-exported here, so there is
exactly one instance backing the same on-disk state.
"""

from __future__ import annotations

import csv  # noqa: F401
import hashlib  # noqa: F401
import json  # noqa: F401
from datetime import date, timedelta  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any  # noqa: F401

from openpyxl import Workbook  # noqa: F401
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: F401

from lvke_mcp.runtime.workspace import deliverable_dir  # noqa: F401
from lvke_mcp.domains.asset_acquisition import backend as acquisition_service  # noqa: F401
from lvke_mcp.domains.reports import artifacts as report_artifacts  # noqa: F401
from lvke_mcp.runtime.storage import (  # noqa: F401
    JSONArtifactStore,
    require_safe_id,
    sha256_json,
)

from ._tables.build import (  # noqa: F401
    _build_tables,
    _check,
    _integrity,
    _lineage,
)
from ._tables.columns import (  # noqa: F401
    PACKAGE_STORE,
    REQUIRED_COLUMNS,
    SOLAR_REQUIRED_COLUMNS,
    SOLAR_TABLE_COLUMNS,
    SOLAR_TABLE_DEFINITIONS,
    TABLE_COLUMNS,
    TABLE_DEFINITIONS,
    _BOOLEAN_FIELDS,
    _DATE_FIELDS,
    _NUMERIC_FIELDS,
    _export_root,
)
from ._tables.export import (  # noqa: F401
    _ensure_exportable,
    _export_cell,
    export_csv,
    export_xlsx,
)
from ._tables.query import (  # noqa: F401
    _blocked,
    _failure,
    _result,
    get_package,
    get_package_record,
    resolve_resource,
)
from ._tables.render import (  # noqa: F401
    _bind_package,
    _package,
    render,
)
from ._tables.rows import (  # noqa: F401
    _depreciation_rows,
    _equity_rows,
    _join_scalar,
    _ppa_rows,
    _rows,
    _scenario_row,
    _scenario_rows,
    _table_contract,
)
