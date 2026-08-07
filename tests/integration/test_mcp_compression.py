from __future__ import annotations

import asyncio
import base64
import copy
import io
import json
import os
import tempfile
import unittest
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator
from mcp import types
from mcp.shared.exceptions import MCPError
from openpyxl import Workbook

from lvke_mcp.runtime import resource_registry
from lvke_mcp.testing.server_manifest import SERVER_SPECS


_VOLATILE_RESPONSE_FIELDS = {
    "trace_id",
    "started_at",
    "finished_at",
    "duration_ms",
    "input_hash",
}


def _stable_response(value: dict) -> dict:
    return {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key not in _VOLATILE_RESPONSE_FIELDS
    }


class McpCompressionTopologyTest(unittest.TestCase):
    def test_solar_power_context_has_an_energy_skill_route(self) -> None:
        manifest = json.loads(
            Path("src/lvke_mcp/config/industry_skill_routes.json").read_text(
                encoding="utf-8"
            )
        )
        energy = next(
            route for route in manifest["routes"] if route["route_id"] == "energy"
        )
        self.assertIn("energy", energy["industry_prefixes"])
        self.assertIn("solar_power", energy["asset_types"])

    def test_migration_manifest_covers_all_85_removed_names(self) -> None:
        manifest = json.loads(
            Path("dev-docs/config/mcp-compression-migration.json").read_text(encoding="utf-8")
        )
        entries = manifest["entries"]
        self.assertEqual(manifest["removed_tool_count"], 85)
        self.assertEqual(len(entries), 85)
        self.assertEqual(len({item["old_tool"] for item in entries}), 85)
        self.assertTrue(
            all(
                item["category"]
                in {
                    "same_handler_alias",
                    "operation_or_dataset_route",
                    "global_resource_route",
                    "cross_service_move",
                }
                for item in entries
            )
        )

    def test_topology_tool_count_and_public_metadata_budget(self) -> None:
        total_tools = 0
        total_chars = 0
        for spec in SERVER_SPECS:
            module = import_module(spec.module)
            server = getattr(module, "SERVER", None) or module.build_server()
            tools = [
                types.Tool(
                    name=tool.name,
                    description=tool.description,
                    inputSchema=server._public_input_schema(  # noqa: SLF001
                        tool.name, tool.input_schema
                    ),
                    outputSchema=None,
                    annotations=tool.annotations,
                    execution=types.ToolExecution(taskSupport=tool.task_support),
                ).model_dump(by_alias=True, exclude_none=True)
                for tool in server.tool_specs
            ]
            total_tools += len(tools)
            total_chars += len(
                json.dumps({"tools": tools}, ensure_ascii=False, separators=(",", ":"))
            )
            self.assertTrue(all("outputSchema" not in item for item in tools))
        self.assertEqual(len(SERVER_SPECS), 14)
        self.assertEqual(total_tools, 169)
        self.assertLess(total_chars, 160_630)

    def test_compact_schema_keeps_top_level_and_full_internal_validation(self) -> None:
        from lvke_mcp.servers.lvke_finance_model import server as finance_server

        server = finance_server.build_server()
        spec = server._tools["finance_validate_spec"]  # noqa: SLF001
        public = server._public_input_schema(spec.name, spec.input_schema)  # noqa: SLF001
        self.assertEqual(public["required"], ["spec"])
        self.assertIn("spec", public["properties"])
        schema_uri = public["properties"]["spec"]["x-lvke-schema-uri"]
        self.assertEqual(schema_uri, "lvke://schemas/finance-spec-v3")

        resources = asyncio.run(server._sdk_list_resources(None, None))  # noqa: SLF001
        self.assertIn(schema_uri, {str(item.uri) for item in resources.resources})
        read = asyncio.run(
            server._sdk_read_resource(  # noqa: SLF001
                None,
                types.ReadResourceRequestParams(uri=schema_uri),
            )
        )
        full = json.loads(read.contents[0].text)
        self.assertEqual(full, spec.input_schema["properties"]["spec"])

        with self.assertRaises(MCPError):
            asyncio.run(
                server._call_tool_async(  # noqa: SLF001
                    "finance_validate_spec", {"spec": {}}, False
                )
            )

        research_server = import_module(
            "lvke_mcp.servers.lvke_deep_research.server"
        ).build_server()
        source_spec = research_server._tools["dr_add_sources"]  # noqa: SLF001
        source_public = research_server._public_input_schema(  # noqa: SLF001
            source_spec.name, source_spec.input_schema
        )
        source_items = source_public["properties"]["sources"]["items"]
        self.assertEqual(source_items["type"], "object")
        self.assertEqual(
            source_items["x-lvke-schema-pointer"],
            "#/properties/sources/items",
        )

    def test_stable_schema_resources_and_compact_aggregate_interfaces(self) -> None:
        expected = {
            "lvke_mcp.servers.lvke_finance_model.server": {
                "lvke://schemas/finance-spec-v3",
            },
            "lvke_mcp.servers.lvke_asset_acquisition.server": {
                "lvke://schemas/asset-acquisition-spec",
            },
            "lvke_mcp.servers.lvke_deliverable_review.server": {
                "lvke://schemas/review-target",
                "lvke://schemas/review-finding-disposition",
            },
            "lvke_mcp.servers.lvke_report_generation.server": {
                "lvke://schemas/report-preparation",
            },
            "lvke_mcp.servers.lvke_project_planning.server": {
                "lvke://schemas/project-planning-candidate",
            },
        }
        for module_name, uris in expected.items():
            with self.subTest(module=module_name):
                server = import_module(module_name).build_server()
                listed = asyncio.run(server._sdk_list_resources(None, None))  # noqa: SLF001
                listed_uris = {str(item.uri) for item in listed.resources}
                self.assertTrue(uris <= listed_uris)
                for uri in uris:
                    read = asyncio.run(
                        server._sdk_read_resource(  # noqa: SLF001
                            None,
                            types.ReadResourceRequestParams(uri=uri),
                        )
                    )
                    Draft202012Validator.check_schema(
                        json.loads(read.contents[0].text)
                    )

        source_server = import_module(
            "lvke_mcp.servers.lvke_source_files.server"
        ).build_server()
        source_properties = source_server._tools[  # noqa: SLF001
            "source_inspect_workbook"
        ].input_schema["properties"]
        self.assertEqual(
            set(source_properties),
            {"workspace_id", "file_id", "operation", "sheet", "range", "options"},
        )
        reference_server = import_module(
            "lvke_mcp.servers.lvke_reference.server"
        ).build_server()
        self.assertIn(
            "limit",
            reference_server._tools["geo_query"].input_schema["properties"],  # noqa: SLF001
        )
        self.assertIn(
            "mode",
            reference_server._tools["geo_distance_matrix"].input_schema[  # noqa: SLF001
                "properties"
            ],
        )


class McpCompressionParityTest(unittest.TestCase):
    def test_all_finance_calculator_operations_match_legacy_handlers(self) -> None:
        from lvke_mcp.servers.finance_calc import server as legacy
        from lvke_mcp.servers.lvke_finance_model import server as aggregated

        cases = {
            "irr": {"cashflows": [-1000, 600, 600]},
            "npv": {"cashflows": [-1000, 600, 600], "rate": 0.08},
            "xirr": {
                "cashflows": [-1000, 600, 600],
                "dates": ["2024-01-01", "2025-01-01", "2026-01-01"],
            },
            "xnpv": {
                "cashflows": [-1000, 600, 600],
                "dates": ["2024-01-01", "2025-01-01", "2026-01-01"],
                "rate": 0.08,
            },
            "break_even": {
                "fixed_cost_wan": 100,
                "unit_price_yuan": 20,
                "unit_variable_cost_yuan": 8,
                "expected_volume": 150_000,
            },
            "payback_period": {"cashflows": [-1000, 300, 400, 500], "rate": 0.08},
            "sensitivity": {
                "cashflows": [-1000, 400, 450, 500],
                "factors": {"revenue": {"years": [1, 2, 3], "value_per_year": 300}},
                "deltas": [-0.1, 0, 0.1],
            },
        }
        legacy_server = legacy.build_server()
        for operation, inputs in cases.items():
            with self.subTest(operation=operation):
                legacy_name = aggregated._CALCULATOR_TOOL_BY_OPERATION[operation]
                expected = legacy_server._tools[legacy_name].handler(inputs)  # noqa: SLF001
                actual = aggregated._tool_finance_calculate(  # noqa: SLF001
                    {"operation": operation, "inputs": inputs}
                )
                self.assertEqual(_stable_response(actual), _stable_response(expected))

    def test_reference_routes_match_legacy_handlers(self) -> None:
        from lvke_mcp.servers.lvke_reference import service

        cases = [
            (
                service.search("industry_reports", "能源", {"year": 2024}, 3),
                "lvke_mcp.servers.industry_research.server",
                "_tool_search_report",
                {"keyword": "能源", "year": 2024, "limit": 3},
            ),
            (
                service.search("clients", "光伏", {"region": "湖北"}, 3),
                "lvke_mcp.servers.lvke_clients.server",
                "_tool_search_clients",
                {"keyword": "光伏", "region": "湖北", "limit": 3},
            ),
            (
                service.search("experts", "财务", {}, 3),
                "lvke_mcp.servers.lvke_experts.server",
                "_tool_find_experts",
                {"specialty": "财务", "limit": 3},
            ),
            (
                service.search("policies", "长江", {}, 3),
                "lvke_mcp.servers.policy_search.server",
                "_tool_search_policy",
                {"keyword": "长江", "limit": 3},
            ),
            (
                service.search("archive", "光伏", {}, 3),
                "lvke_mcp.servers.lvke_archive.server",
                "_tool_search_archive",
                {"query": "光伏", "limit": 3},
            ),
            (
                service.list_items("environment_locations", "", {}),
                "lvke_mcp.servers.environmental_data.server",
                "_tool_list_monitored_locations",
                {},
            ),
            (
                service.list_items("expert_specialties", "", {}),
                "lvke_mcp.servers.lvke_experts.server",
                "_tool_list_specialties",
                {},
            ),
            (
                service.list_items("statistics_dictionaries", "", {}),
                "lvke_mcp.servers.statistics_cn.server",
                "_tool_list_dictionaries",
                {},
            ),
            (
                service.observe("air_quality", "武汉市", 2023, {}),
                "lvke_mcp.servers.environmental_data.server",
                "_tool_query_air_quality",
                {"city": "武汉市", "year": 2023},
            ),
            (
                service.observe("statistics", "GDP", 2023, {"region": "湖北省"}),
                "lvke_mcp.servers.statistics_cn.server",
                "_tool_query_indicator",
                {"name": "GDP", "year": 2023, "region": "湖北省"},
            ),
            (
                service.geo_query("geocode", "武汉天河国际机场", 5, ""),
                "lvke_mcp.servers.map_geo.server",
                "_tool_geocode",
                {"address": "武汉天河国际机场"},
            ),
            (
                service.geo_distance_matrix(
                    ["武汉天河国际机场"], ["武汉站"]
                ),
                "lvke_mcp.servers.map_geo.server",
                "_tool_distance_matrix",
                {"origins": ["武汉天河国际机场"], "destinations": ["武汉站"]},
            ),
        ]
        for actual, module_name, handler_name, arguments in cases:
            with self.subTest(handler=handler_name):
                expected = getattr(import_module(module_name), handler_name)(arguments)
                self.assertEqual(_stable_response(actual), _stable_response(expected))

    def test_all_workbook_operations_match_excel_bridge(self) -> None:
        from lvke_mcp.adapters import source_files_repository as source_api
        from lvke_mcp.servers.excel_bridge import server as legacy
        from lvke_mcp.servers.lvke_source_files import service

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Input"
        sheet["A1"] = 10
        sheet["B1"] = "=A1*2"
        output = workbook.create_sheet("Output")
        output["A1"] = "=Input!B1"
        buffer = io.BytesIO()
        workbook.save(buffer)
        raw = buffer.getvalue()

        with tempfile.TemporaryDirectory(prefix="lvke-compression-xlsx-") as root:
            with patch.dict(os.environ, {"LVKE_MCP_DATA_DIR": root}, clear=False):
                imported = service.import_content(
                    "ws-xlsx",
                    original_filename="parity.xlsx",
                    declared_mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    content_base64=base64.b64encode(raw).decode("ascii"),
                    idempotency_key="xlsx-parity-0001",
                    parse_immediately=False,
                )
                self.assertTrue(imported["success"], imported)
                file_id = imported["file_id"]
                resolved = source_api.resolve_source_workbook_for_review("ws-xlsx", file_id)
                self.assertTrue(resolved["ok"], resolved)
                path = str(resolved["path"])
                cases = {
                    "list_sheets": {},
                    "read_cells": {"sheet": "Input", "max_rows": 10, "max_cols": 10},
                    "read_formulas": {"sheet": "Input", "max_rows": 10, "max_cols": 10},
                    "cross_sheet_refs": {},
                    "dependency_tree": {"sheet": "Output", "cell": "A1", "max_depth": 6},
                }
                for operation, options in cases.items():
                    with self.subTest(operation=operation):
                        handler_name = service._WORKBOOK_OPERATION_TO_HANDLER[operation]  # noqa: SLF001
                        expected = getattr(legacy, handler_name)({"path": path, **options})
                        range_ref = options.get("cell", "") if operation == "dependency_tree" else ""
                        compact_options = {
                            key: value
                            for key, value in options.items()
                            if key in {"max_rows", "max_cols", "max_depth"}
                        }
                        actual = service.inspect_workbook(
                            "ws-xlsx",
                            file_id,
                            operation,
                            sheet=options.get("sheet", ""),
                            range_ref=range_ref,
                            options=compact_options,
                        )
                        comparable = copy.deepcopy(actual)
                        for key in (
                            "workspace_id",
                            "source_file_id",
                            "source_sha256",
                            "source_version",
                        ):
                            comparable.pop(key, None)
                        self.assertEqual(
                            _stable_response(comparable),
                            _stable_response(expected),
                        )
                ranged_cells = service.inspect_workbook(
                    "ws-xlsx",
                    file_id,
                    "read_cells",
                    sheet="Input",
                    range_ref="A1:A1",
                )
                self.assertEqual(ranged_cells["data"]["rows"], [[10]])
                self.assertEqual(ranged_cells["data"]["selected_range"], "A1:A1")
                ranged_formulas = service.inspect_workbook(
                    "ws-xlsx",
                    file_id,
                    "read_formulas",
                    sheet="Input",
                    range_ref="B1:B1",
                )
                self.assertEqual(ranged_formulas["data"]["formula_count"], 1)
                self.assertEqual(ranged_formulas["data"]["cells"][0]["cell"], "B1")

    def test_planning_get_object_and_global_resource_read_preserve_records(self) -> None:
        from lvke_mcp.adapters.project_planning_repository import PROJECT_CONTEXT_STORE
        from lvke_mcp.domains.project_planning import application

        with tempfile.TemporaryDirectory(prefix="lvke-compression-resource-") as root:
            with patch.dict(os.environ, {"LVKE_MCP_DATA_DIR": root}, clear=False):
                record = PROJECT_CONTEXT_STORE.put(
                    "ws-a",
                    {
                        "object_type": "ProjectContext",
                        "project_name": "压缩等价测试",
                        "industry_code": "C39",
                        "project_type": "new_build",
                        "region": {"province": "湖北省"},
                        "objective": "验证统一读取入口",
                        "report_type": "feasibility_study",
                        "evidence_track": "real",
                    },
                    producer="test",
                    status="ok",
                )
                object_id = record["object_id"]
                self.assertEqual(
                    application.get_planning_object("ws-a", "ProjectContext", object_id),
                    application.get_project_context("ws-a", object_id),
                )
                direct = application.read_resource("ws-a", record["resource_uri"])
                routed = resource_registry.read_resource("ws-a", record["resource_uri"])
                self.assertEqual(routed, direct)
                denied = resource_registry.read_resource("ws-b", record["resource_uri"])
                self.assertFalse(denied["success"])
                self.assertIn("resource_not_found", denied["blockers"])

    def test_removed_table_aliases_are_exact_registry_routes(self) -> None:
        from lvke_mcp.domains.finance import tables_service
        from lvke_mcp.servers.lvke_finance_tables import server

        expected = {
            "tables_get_investment": "investment",
            "tables_get_construction_interest": "construction_interest",
            "tables_get_working_capital": "working_capital",
            "tables_get_funding": "funding",
            "tables_get_income_statement": "income_statement",
            "tables_get_total_cost": "total_cost",
            "tables_get_wage": "wage",
            "tables_get_depreciation": "depreciation",
            "tables_get_amortization": "amortization",
            "tables_get_profit_distribution": "profit_distribution",
            "tables_get_debt_service": "debt_service",
            "tables_get_cashflow": "cashflow",
            "tables_get_capital_cashflow": "capital_cashflow",
        }
        canonical = {
            item["alias_tool"]: item["table_id"]
            for item in tables_service.table_registry()
        }
        self.assertEqual(
            {
                alias: server._canonical_table_id(table_id)  # noqa: SLF001
                for alias, table_id in expected.items()
            },
            canonical,
        )
        public_names = {item.name for item in server.SERVER.tool_specs}
        self.assertIn("tables_get_table", public_names)
        self.assertTrue(expected.keys().isdisjoint(public_names))

    def test_search_implementation_has_no_non_tavily_provider(self) -> None:
        provider_files = list(Path("src/lvke_mcp/domains/research/providers").glob("*.py"))
        names = {path.stem for path in provider_files if path.stem != "__init__"}
        self.assertEqual(names, {"tavily"})
        acquisition = Path(
            "src/lvke_mcp/servers/lvke_data_acquisition/service.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("fathom", acquisition.lower())
        self.assertNotIn("context7", acquisition.lower())
        self.assertNotIn("mcp-web-search", acquisition.lower())


if __name__ == "__main__":
    unittest.main()
