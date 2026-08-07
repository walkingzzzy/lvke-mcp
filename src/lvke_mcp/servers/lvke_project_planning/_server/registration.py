"""``build_server`` assembly for the project-planning MCP server.

Registration order is load-bearing: ``_install_round2_aggregates`` reads the
legacy tool specs out of ``server._tools`` and then pops their public names, so
every legacy tool must already be registered before it runs.
"""

from __future__ import annotations

from mcp import types

from lvke_mcp.runtime.logging import get_logger
from lvke_mcp.runtime.transport import OfficialStdioServer

from .register_context_market import (
    _register_context,
    _register_market,
    _register_revenue,
)
from .register_labor_option import (
    _register_labor,
    _register_policy_option,
    _register_query,
)
from .register_scale_cost import (
    _register_build_scale,
    _register_cost,
    _register_direct_create,
)
from .round2 import _install_round2_aggregates
from .schema_parts import (
    _PROJECT_PLANNING_CANDIDATE_SCHEMA,
    _PROJECT_PLANNING_CANDIDATE_SCHEMA_URI,
)

SERVER_NAME = "lvke-project-planning"
SERVER_VERSION = "0.1.0"
_ROUND2_SCHEMA_URIS = {
    "planning_validate": "lvke://schemas/project-planning-validate",
    "planning_confirm": "lvke://schemas/project-planning-confirm",
    "planning_prepare": "lvke://schemas/project-planning-prepare",
    "planning_create": "lvke://schemas/project-planning-create",
}
logger = get_logger(SERVER_NAME)


def build_server() -> OfficialStdioServer:
    server = OfficialStdioServer(SERVER_NAME, SERVER_VERSION, logger)
    read = types.ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    write = types.ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    _register_context(server, read, write)
    _register_market(server, read, write)
    _register_revenue(server, read, write)
    _register_build_scale(server, read, write)
    _register_direct_create(server, read, write)
    _register_cost(server, read, write)
    _register_labor(server, read, write)
    _register_policy_option(server, read, write)
    _register_query(server, read, write)
    round2_schemas = _install_round2_aggregates(server, read, write)
    server.register_schema_resource(
        _PROJECT_PLANNING_CANDIDATE_SCHEMA_URI,
        _PROJECT_PLANNING_CANDIDATE_SCHEMA,
        name="project-planning-candidate",
        title="Project Planning Candidate",
        description="规划领域所有候选类型的完整联合 Schema；服务端各工具仍执行其精确子 Schema。",
    )
    for tool_name, schema in round2_schemas.items():
        server.register_schema_resource(
            _ROUND2_SCHEMA_URIS[tool_name],
            schema,
            name=tool_name.replace("_", "-"),
            title=tool_name.replace("_", " ").title(),
            description=f"{tool_name} 服务端执行的完整判别式 JSON Schema。",
        )
    # Protocol-level resources carry no explicit workspace assertion. Dynamic
    # access is centralized in lvke-feasibility-delivery.
    server.register_resource_provider(lambda: [], lambda _uri: None)
    return server
