"""Thirteen-table views that consume immutable finance run IDs only.

Wave 3.1 facade: implementation moved to ``_tables_service/`` sub-modules —
``base`` (run/manifest/quality aliases, template-version and delivery-assessment
primitives, result envelopes), ``render`` (render and whole-package validate),
``export`` (XLSX/CSV export plus the CSV export gate), ``query`` (package read,
table registry, single-table read/validate) and ``resources`` (resource listing
and resolution).
"""

from __future__ import annotations

import csv  # noqa: F401
import hashlib  # noqa: F401
import json  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any  # noqa: F401

from lvke_mcp.adapters.finance_tables_repository import (  # noqa: F401
    CSV_EXPORT_STORE,
    PACKAGE_STORE,
    export_root as _export_root,
    xlsx_path_from_uri,
)
from lvke_mcp.runtime.storage import (  # noqa: F401
    paginate_resource_entries,
    require_safe_id,
    sha256_json,
)
from lvke_mcp.domains.finance import tables_application  # noqa: F401

from ._tables_service.base import (  # noqa: F401
    _check_template_version,
    _delivery_assessment,
    _delivery_keys,
    _failure,
    _load_run,
    _package_result,
    _require_run_id,
    _scalar_csv_rows,
    _structured_delivery_tables,
    _structured_table_manifest,
    _structured_table_quality,
)
from ._tables_service.export import (  # noqa: F401
    _validate_csv_export,
    csv_path_from_uri,
    export_csv,
    export_xlsx,
)
from ._tables_service.query import (  # noqa: F401
    _package_for_table,
    get_package,
    get_table,
    list_tables,
    table_registry,
    validate_table,
)
from ._tables_service.render import (  # noqa: F401
    render as _render_impl,
    validate,
)


def render(
    workspace_id: str,
    run_id: str,
    format_name: str = "structured",
    template_version: str = "",
) -> dict[str, Any]:
    """渲染十三表并固化 package（签名与拆分前一致）。

    把**本模块**的三个薄委托属性注入实现，使
    ``patch.object(tables_service, "_load_run", ...)`` 一类替换继续生效，
    同时实现包不需要反向 import 门面。见 ``_tables_service.render.render``。
    """
    return _render_impl(
        workspace_id,
        run_id,
        format_name,
        template_version,
        load_run=_load_run,
        delivery_assessment=_delivery_assessment,
        structured_delivery_tables=_structured_delivery_tables,
    )
from ._tables_service.resources import (  # noqa: F401
    list_resources,
    resolve_resource,
)
