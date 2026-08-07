"""lvke-finance-model MCP server 入口(stdio)。

工作区级完整财务模型工具（与 finance-calc 低层计算器分离）：

- finance_prepare_spec
- finance_validate_spec
- finance_run_model
- finance_get_run
- finance_build_balance_sheet / finance_get_balance_sheet
- finance_run_monte_carlo / finance_get_monte_carlo
- finance_list_analyses / finance_read_analysis_resource
- finance_render_tables（DEPRECATED → lvke-finance-tables.tables_render）
- finance_generate_package（DEPRECATED → lvke-finance-authoring 编排 run → tables）
- finance_import_vendor_review

启动方式::

    python -m lvke_mcp.servers.lvke_finance_model.server

契约约定（方案 5.4）：每个工具有专属 outputSchema，公共 envelope 至少含
``status/resource_uris/warnings/blockers/next_actions``；同时保留
``success/data/source``（成功）与 ``code/message``（失败）以兼容既有调用方。
业务缺项返回 ``status=missing_inputs``，阻断返回 ``status=blocked``，
均不与系统错误（``status=failed``）混淆。

Wave 3.5 门面：实现搬到 ``_server/`` 子模块 —— ``schemas``（服务标识、BoE/分布
JSON Schema 与弃用提示）、``envelope``（幂等记录、URI、信封与输入归一化）、
``calc_tools``、``spec_tools``（spec/fact pack，含 legacy 兼容实现）、
``run_tools``（运行/渲染/整包/甲方导入）、``analysis_tools``（BoE、资产负债、
Monte Carlo 与 get_analysis 聚合入口）与 ``registry``（``build_server`` 与
``main``）。

本模块路径同时是 MCP 启动入口（``server_manifest`` 与 ``~/.claude.json`` 都指向
它），因此 ``if __name__ == "__main__"`` 块保留在门面：搬进实现包后模块名不是
``__main__``，那个块永不触发。
"""

from __future__ import annotations

import hashlib  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import time  # noqa: F401
import uuid  # noqa: F401
import zipfile  # noqa: F401
from datetime import datetime, timedelta, timezone  # noqa: F401
from typing import Any  # noqa: F401

from mcp import types  # noqa: F401
from mcp.server.lowlevel.helper_types import ReadResourceContents  # noqa: F401
from jsonschema import Draft202012Validator  # noqa: F401

from lvke_mcp.runtime.storage import (  # noqa: F401
    JSONArtifactStore,
    paginate_resource_entries,
    sha256_json,
)
from lvke_mcp.adapters.finance_model_repository import (  # noqa: F401
    BALANCE_SHEET_STORE,
    BASIS_OF_ESTIMATE_STORE,
    FACT_PACK_STORE,
    IDEMPOTENCY_STORE,
    MONTE_CARLO_STORE,
    SPEC_STORE,
)
from lvke_mcp.runtime.logging import get_logger  # noqa: F401
from lvke_mcp.runtime.transport import OfficialStdioServer  # noqa: F401
from lvke_mcp.runtime.responses import err, ok  # noqa: F401
from lvke_mcp.domains.finance.parameter_resolver import (  # noqa: F401
    finance_input_schema,
    finance_spec_candidate_schema,
)
from lvke_mcp.domains.finance.calculator_service import (  # noqa: F401
    CALCULATOR_INPUT_SCHEMAS,
    calculate as calculate_finance_operation,
)
from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE  # noqa: F401
from lvke_mcp.runtime.source_reconstruction import reconstruction_errors  # noqa: F401

from ._server.analysis_tools import (  # noqa: F401
    _GET_ANALYSIS_BRANCHES,
    _install_get_analysis_aggregate,
    _latest_formal_boe,
    _load_consistent_run,
    _planning_record,
    _required_boe_pointers,
    _resolve_analysis_resource,
    _tool_build_balance_sheet,
    _tool_build_basis_of_estimate,
    _tool_get_analysis,
    _tool_get_balance_sheet,
    _tool_get_basis_of_estimate,
    _tool_get_monte_carlo,
    _tool_list_analyses,
    _tool_read_analysis_resource,
    _tool_run_monte_carlo,
)
from ._server.calc_tools import (  # noqa: F401
    _CALCULATOR_TOOL_BY_OPERATION,
    _tool_finance_calculate,
)
from ._server.envelope import (  # noqa: F401
    _active_idempotency_record,
    _blocking_rules,
    _canonical_candidate_inputs,
    _err_env,
    _exception_env,
    _finalize,
    _idempotency_ttl_seconds,
    _ok_env,
    _revenue_input_complete,
    _run_uri,
    _spec_uri,
    _str_list,
    _unique_strings,
    _ws,
)
from ._server.registry import (  # noqa: F401
    build_server,
    main,
)
from ._server.run_tools import (  # noqa: F401
    _legacy_tool_run_model,
    _tool_generate_package,
    _tool_get_run,
    _tool_import_vendor_review,
    _tool_render_tables,
    _tool_run_model,
)
from ._server.schemas import (  # noqa: F401
    _BOE_ENTRY_SCHEMA,
    _DEPRECATED_PACKAGE_HINT,
    _DEPRECATED_RENDER_HINT,
    _DISTRIBUTION_SCHEMA,
    _FINANCE_SPEC_SCHEMA_URI,
    _STATUS_VALUES,
    SERVER_NAME,
    SERVER_VERSION,
    _output_schema,
    logger,
)
from ._server.spec_tools import (  # noqa: F401
    _legacy_tool_confirm_spec,
    _legacy_tool_prepare_spec,
    _tool_confirm_fact_pack,
    _tool_confirm_spec,
    _tool_get_fact_pack,
    _tool_prepare_fact_pack,
    _tool_prepare_spec,
    _tool_validate_spec,
)

if __name__ == "__main__":
    main()
