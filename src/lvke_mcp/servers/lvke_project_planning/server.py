"""Official-SDK MCP server for immutable project planning objects.

Wave 2.3 facade: implementation moved to ``_server/`` sub-modules —
``schema_parts`` (JSON Schema fragments), ``dispatch_tables`` (order-sensitive
round-two branch maps), ``round2`` (aggregate route installation) and
``registration`` (``build_server`` grouped into ``_register_*`` helpers).

``SERVER`` stays here and is evaluated exactly once: ``_install_round2_aggregates``
mutates ``server._tools`` by popping the legacy public names, so a second
evaluation would observe a different tool surface.
"""

from __future__ import annotations

import copy  # noqa: F401

from mcp import types  # noqa: F401

from lvke_mcp.runtime.logging import get_logger  # noqa: F401
from lvke_mcp.runtime.transport import OfficialStdioServer  # noqa: F401
from lvke_mcp.domains.project_planning import application as service  # noqa: F401
from lvke_mcp.servers.lvke_project_planning import lifecycle  # noqa: F401

from ._server.dispatch_tables import (  # noqa: F401
    _COMPARE_BRANCHES,
    _CONFIRM_BRANCHES,
    _CREATE_BRANCHES,
    _PREPARE_BRANCHES,
    _VALIDATE_BRANCHES,
    CONFIRM_OPERATION_BY_KIND,
    CREATE_OPERATION_BY_KIND,
    PREPARE_OPERATION_BY_KIND,
)
from ._server.registration import (  # noqa: F401
    SERVER_NAME,
    SERVER_VERSION,
    _ROUND2_SCHEMA_URIS,
    build_server,
    logger,
)
from ._server.round2 import (  # noqa: F401
    _branch_payload_schema,
    _discriminated_payload_schema,
    _install_round2_aggregates,
)
from ._server.schema_parts import (  # noqa: F401
    _BUILD_ALTERNATIVE,
    _BUILD_CONSTRAINTS,
    _CONTEXT,
    _CONTEXT_PROPERTIES,
    _COST_CANDIDATE_ITEM,
    _EVIDENCE_BINDING,
    _FACILITY,
    _INVEST_BREAKDOWN,
    _KEY,
    _LABOR_REQUIREMENT,
    _MARKET_CANDIDATE,
    _OPERATING_COST_ITEM,
    _OPTION,
    _OPTION_CONSTRAINT,
    _OPTION_CRITERION,
    _OUTPUT,
    _POLICY_CANDIDATE,
    _POSITION,
    _PROJECT_PLANNING_CANDIDATE_SCHEMA,
    _PROJECT_PLANNING_CANDIDATE_SCHEMA_URI,
    _RATE_SERIES,
    _REGION,
    _REVENUE_CANDIDATE,
    _REVENUE_SPEC,
    _STRING,
    _TARGET_CAPACITY,
    _WS,
    _schema,
)

SERVER = build_server()


def main() -> None:
    SERVER.serve_forever()


if __name__ == "__main__":
    main()