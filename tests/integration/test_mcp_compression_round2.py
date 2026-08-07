from __future__ import annotations

import asyncio
import copy
import json
import unittest
import uuid
from importlib import import_module
from pathlib import Path

from jsonschema import Draft202012Validator
from mcp import types

from lvke_mcp.testing.server_manifest import SERVER_SPECS


MODULES = {
    "planning": "lvke_mcp.servers.lvke_project_planning.server",
    "finance": "lvke_mcp.servers.lvke_finance_model.server",
    "source": "lvke_mcp.servers.lvke_source_files.server",
    "delivery": "lvke_mcp.servers.lvke_zero_material_delivery.server",
}

TARGET_CASES = [
    ("planning", "planning_validate", "object_kind", "market_case", "planning_validate_market_case", "market_case_id"),
    ("planning", "planning_validate", "object_kind", "revenue_drivers", "planning_validate_revenue_drivers", "revenue_driver_set_id"),
    ("planning", "planning_validate", "object_kind", "build_scale", "planning_validate_build_scale", "build_scale_case_id"),
    ("planning", "planning_validate", "object_kind", "cost_drivers", "planning_validate_cost_drivers", "cost_driver_set_id"),
    ("planning", "planning_validate", "object_kind", "labor_plan", "planning_validate_labor_plan", "labor_plan_id"),
    ("planning", "planning_validate", "object_kind", "option_comparison", "planning_validate_option_comparison", "option_comparison_id"),
    ("planning", "planning_compare", "object_kind", "market_case", "planning_compare_market_cases", "market_case_id"),
    ("planning", "planning_compare", "object_kind", "revenue_drivers", "planning_compare_revenue_candidates", "revenue_driver_set_id"),
    ("finance", "finance_get_analysis", "kind", "balance_sheet", "finance_get_balance_sheet", "balance_sheet_id"),
    ("finance", "finance_get_analysis", "kind", "monte_carlo", "finance_get_monte_carlo", "monte_carlo_id"),
    ("finance", "finance_get_analysis", "kind", "basis_of_estimate", "finance_get_basis_of_estimate", "basis_of_estimate_id"),
    ("finance", "finance_get_analysis", "kind", "fact_pack", "finance_get_fact_pack", "fact_pack_id"),
    ("source", "source_task_status", "task_kind", "parse", "source_parse_status", "job_id"),
    ("source", "source_task_status", "task_kind", "upload", "source_upload_status", "upload_id"),
]

PAYLOAD_CASES = [
    *[("planning", "planning_confirm", "object_kind", kind, old, id_field, "confirm") for kind, old, id_field in [
        ("market_case", "planning_confirm_market_case", "market_case_id"),
        ("revenue_drivers", "planning_confirm_revenue_drivers", "revenue_driver_set_id"),
        ("build_scale", "planning_confirm_build_scale", "build_scale_case_id"),
        ("cost_drivers", "planning_confirm_cost_drivers", "cost_driver_set_id"),
        ("labor_plan", "planning_confirm_labor_plan", "labor_plan_id"),
        ("policy_basis", "planning_confirm_policy_basis", "policy_basis_id"),
        ("option_comparison", "planning_confirm_option_comparison", "option_comparison_id"),
    ]],
    *[("planning", "planning_prepare", "object_kind", kind, old, None, "write") for kind, old in [
        ("market_case", "planning_prepare_market_case"),
        ("revenue_drivers", "planning_prepare_revenue_drivers"),
        ("cost_drivers", "planning_prepare_cost_drivers"),
        ("policy_basis", "planning_prepare_policy_basis"),
        ("option_comparison", "planning_prepare_option_comparison"),
    ]],
    *[("planning", "planning_create", "object_kind", kind, old, None, "write") for kind, old in [
        ("revenue_drivers", "planning_create_revenue_drivers"),
        ("build_scale", "planning_create_build_scale"),
        ("cost_drivers", "planning_create_cost_drivers"),
        ("labor_plan", "planning_create_labor_plan"),
    ]],
]


def _example(schema: dict, field: str = "value"):
    if "default" in schema:
        return copy.deepcopy(schema["default"])
    if "const" in schema:
        return copy.deepcopy(schema["const"])
    if schema.get("enum"):
        return copy.deepcopy(schema["enum"][0])
    if schema.get("oneOf"):
        return _example(schema["oneOf"][0], field)
    value_type = schema.get("type")
    if isinstance(value_type, list):
        value_type = next((item for item in value_type if item != "null"), "string")
    if value_type == "object" or "properties" in schema:
        value = {
            name: _example(schema.get("properties", {}).get(name, {}), name)
            for name in schema.get("required", [])
        }
        if not value and int(schema.get("minProperties") or 0) > 0:
            properties = schema.get("properties", {})
            if properties:
                name, child = next(iter(properties.items()))
            else:
                name, child = "value", schema.get("additionalProperties", {})
            value[name] = _example(child, name)
        return value
    if value_type == "array":
        count = max(1, int(schema.get("minItems") or 0))
        return [_example(schema.get("items", {}), field) for _ in range(count)]
    if value_type == "integer":
        return max(1, int(schema.get("minimum") or 0))
    if value_type == "number":
        floor = schema.get("exclusiveMinimum", schema.get("minimum", 0))
        return float(floor) + 1.0
    if value_type == "boolean":
        return True
    if field == "workspace_id":
        return "round2-ws"
    if field == "job_id":
        return "job_missing"
    if field == "upload_id":
        return "ups_missing"
    if field == "idempotency_key":
        return "round2-key"
    pattern = str(schema.get("pattern") or "")
    if "sha256" in pattern:
        return "sha256:" + "0" * 64
    return "x" * max(1, int(schema.get("minLength") or 0))


def _required_args(spec) -> dict:
    schema = spec.input_schema
    return {
        field: _example(schema["properties"][field], field)
        for field in schema.get("required", [])
    }


def _stable(value: dict) -> dict:
    volatile = {
        "trace_id", "started_at", "finished_at", "duration_ms", "input_hash",
        "runtime_instance", "build_time", "build_commit",
    }
    return {key: copy.deepcopy(item) for key, item in value.items() if key not in volatile}


class McpCompressionRound2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.servers = {
            key: import_module(module_name).build_server()
            for key, module_name in MODULES.items()
        }

    def test_v2_manifest_has_32_unique_routes_and_eight_contracts(self) -> None:
        manifest = json.loads(
            Path("dev-docs/config/mcp-compression-migration-v2.json").read_text(
                encoding="utf-8"
            )
        )
        entries = manifest["entries"]
        self.assertEqual(manifest["removed_tool_count"], 32)
        self.assertEqual(len(entries), 32)
        self.assertEqual(len({entry["old_tool"] for entry in entries}), 32)
        self.assertEqual(manifest["added_aggregate_tool_count"], 8)
        self.assertEqual(manifest["net_tool_count_change"], -24)
        self.assertEqual(len(manifest["aggregate_contracts"]), 8)

    def test_final_and_transition_topology(self) -> None:
        total = 0
        all_names: set[str] = set()
        for server_spec in SERVER_SPECS:
            module = import_module(server_spec.module)
            server = getattr(module, "SERVER", None) or module.build_server()
            total += len(server.tool_specs)
            all_names.update(tool.name for tool in server.tool_specs)
        self.assertEqual(total, 169)
        self.assertEqual(
            sum(len(server._round2_legacy_specs) for server in self.servers.values()),  # noqa: SLF001
            32,
        )
        self.assertEqual(total + 32, 201)
        aggregates = {
            "planning_validate", "finance_get_analysis", "planning_compare",
            "source_task_status", "delivery_transition", "planning_confirm",
            "planning_prepare", "planning_create",
        }
        self.assertTrue(aggregates <= all_names)
        for server in self.servers.values():
            self.assertFalse(set(server._round2_legacy_specs) & all_names)  # noqa: SLF001

    def test_all_32_dispatch_branches_match_legacy_handlers(self) -> None:
        seen: set[str] = set()
        for server_key, aggregate, discriminator, kind, old_name, id_field in TARGET_CASES:
            with self.subTest(old=old_name):
                server = self.servers[server_key]
                old_spec = server._round2_legacy_specs[old_name]  # noqa: SLF001
                old_args = _required_args(old_spec)
                new_args = {
                    "workspace_id": old_args["workspace_id"],
                    discriminator: kind,
                    "target_id": old_args[id_field],
                }
                self.assertEqual(
                    _stable(server._tools[aggregate].handler(new_args)),  # noqa: SLF001
                    _stable(old_spec.handler(old_args)),
                )
                seen.add(old_name)

        for server_key, aggregate, discriminator, kind, old_name, id_field, mode in PAYLOAD_CASES:
            with self.subTest(old=old_name):
                server = self.servers[server_key]
                old_spec = server._round2_legacy_specs[old_name]  # noqa: SLF001
                old_args = _required_args(old_spec)
                excluded = {"workspace_id", "idempotency_key", "project_context_id"}
                new_args = {
                    "workspace_id": old_args["workspace_id"],
                    discriminator: kind,
                    "idempotency_key": old_args["idempotency_key"],
                }
                if mode == "confirm":
                    excluded.add(str(id_field))
                    new_args["target_id"] = old_args[str(id_field)]
                else:
                    new_args["project_context_id"] = old_args["project_context_id"]
                new_args["payload"] = {
                    key: value for key, value in old_args.items() if key not in excluded
                }
                old_args["idempotency_key"] = "round2-key-legacy"
                new_args["idempotency_key"] = "round2-key-aggregate"
                self.assertFalse(
                    list(Draft202012Validator(server._tools[aggregate].input_schema).iter_errors(new_args))  # noqa: SLF001
                )
                self.assertEqual(
                    _stable(server._tools[aggregate].handler(new_args)),  # noqa: SLF001
                    _stable(old_spec.handler(old_args)),
                )
                seen.add(old_name)

        delivery = self.servers["delivery"]
        for operation, old_name in (("cancel", "delivery_cancel"), ("resume", "delivery_resume")):
            old_spec = delivery._round2_legacy_specs[old_name]  # noqa: SLF001
            old_args = _required_args(old_spec)
            new_args = {**old_args, "operation": operation}
            old_args["idempotency_key"] = f"round2-{operation}-legacy"
            new_args["idempotency_key"] = f"round2-{operation}-aggregate"
            self.assertEqual(
                _stable(delivery._tools["delivery_transition"].handler(new_args)),  # noqa: SLF001
                _stable(old_spec.handler(old_args)),
            )
            seen.add(old_name)
        self.assertEqual(len(seen), 32)

    def test_discriminated_schemas_reject_cross_kind_and_prefix_mismatch(self) -> None:
        planning = self.servers["planning"]
        confirm = planning._tools["planning_confirm"].input_schema  # noqa: SLF001
        bad_confirm = {
            "workspace_id": "round2-ws",
            "object_kind": "labor_plan",
            "target_id": "labor_missing",
            "idempotency_key": "round2-key",
            "payload": {
                "selected_candidate_id": "scale-a",
                "rejected_candidate_ids": ["scale-b"],
                "selection_reason": "明确选择规模候选并舍弃其他方案",
            },
        }
        self.assertTrue(list(Draft202012Validator(confirm).iter_errors(bad_confirm)))

        source = self.servers["source"]
        status_schema = source._tools["source_task_status"].input_schema  # noqa: SLF001
        mismatch = {"workspace_id": "round2-ws", "task_kind": "parse", "target_id": "ups_wrong"}
        self.assertTrue(list(Draft202012Validator(status_schema).iter_errors(mismatch)))

        delivery = self.servers["delivery"]
        transition = delivery._tools["delivery_transition"].input_schema  # noqa: SLF001
        missing_reason = {
            "workspace_id": "round2-ws", "operation": "cancel",
            "delivery_run_id": "run_missing", "idempotency_key": "round2-key",
        }
        self.assertTrue(list(Draft202012Validator(transition).iter_errors(missing_reason)))

    def test_stable_round2_schema_resources_are_readable(self) -> None:
        planning = self.servers["planning"]
        expected = {
            "lvke://schemas/project-planning-validate",
            "lvke://schemas/project-planning-confirm",
            "lvke://schemas/project-planning-prepare",
            "lvke://schemas/project-planning-create",
        }
        listed = asyncio.run(planning._sdk_list_resources(None, None))  # noqa: SLF001
        self.assertTrue(expected <= {str(item.uri) for item in listed.resources})
        for uri in expected:
            result = asyncio.run(
                planning._sdk_read_resource(  # noqa: SLF001
                    None, types.ReadResourceRequestParams(uri=uri)
                )
            )
            Draft202012Validator.check_schema(json.loads(result.contents[0].text))

    def test_option_confirmation_keeps_historical_operation(self) -> None:
        planning_module = import_module(MODULES["planning"])
        self.assertEqual(
            planning_module.CONFIRM_OPERATION_BY_KIND["option_comparison"],
            "planning_confirm_option_selection",
        )

    def test_idempotency_namespaces_and_legacy_replay_are_preserved(self) -> None:
        planning = self.servers["planning"]
        workspace = "round2-" + uuid.uuid4().hex
        shared_key = "same-key-across-kinds"
        market = {
            "workspace_id": workspace,
            "object_kind": "market_case",
            "target_id": "missing-market",
            "idempotency_key": shared_key,
            "payload": {
                "selected_candidate_id": "candidate-a",
                "selection_reason": "明确选择候选并保留完整审计理由",
                "rejected_candidate_ids": [],
            },
        }
        labor = {
            "workspace_id": workspace,
            "object_kind": "labor_plan",
            "target_id": "missing-labor",
            "idempotency_key": shared_key,
            "payload": {"confirmation_reason": "明确确认定员方案及其计算依据"},
        }
        market_result = planning._tools["planning_confirm"].handler(market)  # noqa: SLF001
        labor_result = planning._tools["planning_confirm"].handler(labor)  # noqa: SLF001
        self.assertNotEqual(market_result.get("code"), "idempotency_conflict")
        self.assertNotEqual(labor_result.get("code"), "idempotency_conflict")

        old_spec = planning._round2_legacy_specs[  # noqa: SLF001
            "planning_confirm_option_comparison"
        ]
        old_args = _required_args(old_spec)
        old_args.update(
            workspace_id=workspace,
            option_comparison_id="missing-option",
            idempotency_key="legacy-option-replay",
        )
        legacy_result = old_spec.handler(old_args)
        aggregate_result = planning._tools["planning_confirm"].handler(  # noqa: SLF001
            {
                "workspace_id": workspace,
                "object_kind": "option_comparison",
                "target_id": old_args["option_comparison_id"],
                "idempotency_key": old_args["idempotency_key"],
                "payload": {
                    key: value
                    for key, value in old_args.items()
                    if key
                    not in {"workspace_id", "option_comparison_id", "idempotency_key"}
                },
            }
        )
        self.assertEqual(aggregate_result.get("code"), legacy_result.get("code"))
        self.assertTrue(aggregate_result.get("idempotent_replay"))


if __name__ == "__main__":
    unittest.main()
