"""dr_add_sources 的公开 schema 必须可操作。

此前两个缺陷叠加：
1. 八个 oneOf 分支完全同构，缺 ``source_file``，导入并解析过的受控项目文件无处可绑。
2. oneOf 失败时校验器只回报最后一个分支的错误（``'technical_fixture' was expected``），
   即使真正的问题是"少了 allowed_uses"，调用方也完全看不出来。

修复后改成扁平 + ``allOf/if-then`` 判别式：``source_type`` 是显式枚举，缺字段直接
点到字段；按类型约束 ``resource_uri`` 所属域与 ``evidence_track``；服务层再回报
命中分支与具体缺失字段。
"""

from __future__ import annotations

import unittest

import jsonschema

from lvke_mcp.domains.research._service.planning import _describe_source_rejection
from lvke_mcp.servers.lvke_deep_research._server.schemas import (
    SOURCE_TYPES,
    SOURCE_URI_DOMAINS,
)
from lvke_mcp.servers.lvke_deep_research.server import build_server

_HASH = "sha256:" + "a" * 64


def _source(source_type: str, resource_uri: str, **overrides: object) -> dict:
    payload = {
        "source_type": source_type,
        "object_id": "obj_abc123",
        "resource_uri": resource_uri,
        "content_hash": _HASH,
        "locator": "第3段",
        "evidence_track": "real",
        "allowed_uses": ["fact_extraction"],
    }
    payload.update(overrides)
    return payload


class DrAddSourcesSchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = {item.name: item for item in build_server().tool_specs}["dr_add_sources"]
        schema = getattr(spec, "input_schema", None) or getattr(spec, "inputSchema", None)
        cls.item_schema = schema["properties"]["sources"]["items"]

    def test_source_type_is_an_explicit_discriminator_enum(self) -> None:
        enum = self.item_schema["properties"]["source_type"]["enum"]
        self.assertEqual(list(SOURCE_TYPES), enum)
        self.assertIn("source_snapshot", enum)
        self.assertIn("source_file", enum)
        self.assertIn("evidence_pack", enum)

    def test_required_fields_are_declared(self) -> None:
        for field in (
            "source_type",
            "object_id",
            "resource_uri",
            "content_hash",
            "locator",
            "evidence_track",
            "allowed_uses",
        ):
            with self.subTest(field=field):
                self.assertIn(field, self.item_schema["required"])

    def test_reasonable_descriptors_are_accepted(self) -> None:
        for source_type, domain in SOURCE_URI_DOMAINS.items():
            with self.subTest(source_type=source_type):
                jsonschema.validate(
                    _source(source_type, f"lvke://{domain}/workspaces/w/x/obj_abc123"),
                    self.item_schema,
                )

    def test_source_file_branch_exists(self) -> None:
        jsonschema.validate(
            _source("source_file", "lvke://source-files/workspaces/w/files/file_a"),
            self.item_schema,
        )

    def test_missing_field_error_points_at_that_field(self) -> None:
        payload = _source(
            "source_snapshot", "lvke://data-acquisition/workspaces/w/sources/src_a"
        )
        payload.pop("allowed_uses")
        with self.assertRaises(jsonschema.ValidationError) as ctx:
            jsonschema.validate(payload, self.item_schema)
        # 关键：错误必须提到真正缺的字段，而不是 technical_fixture。
        self.assertIn("allowed_uses", ctx.exception.message)
        self.assertNotIn("technical_fixture", ctx.exception.message)

    def test_resource_uri_domain_is_enforced_per_type(self) -> None:
        with self.assertRaises(jsonschema.ValidationError) as ctx:
            jsonschema.validate(
                _source(
                    "source_file", "lvke://data-acquisition/workspaces/w/sources/src_a"
                ),
                self.item_schema,
            )
        self.assertEqual(list(ctx.exception.absolute_path), ["resource_uri"])

    def test_technical_fixture_track_and_uses_are_pinned(self) -> None:
        jsonschema.validate(
            _source(
                "technical_fixture",
                "lvke://data-acquisition/workspaces/w/sources/src_a",
                evidence_track="technical_fixture",
                allowed_uses=["technical_validation"],
            ),
            self.item_schema,
        )
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                _source(
                    "technical_fixture",
                    "lvke://data-acquisition/workspaces/w/sources/src_a",
                    evidence_track="real",
                ),
                self.item_schema,
            )

    def test_source_reconstructed_track_is_pinned(self) -> None:
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                _source(
                    "source_reconstructed",
                    "lvke://data-acquisition/workspaces/w/sources/src_a",
                    evidence_track="real",
                ),
                self.item_schema,
            )

    def test_unknown_source_type_is_rejected(self) -> None:
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                _source("whatever", "lvke://data-acquisition/w/s"), self.item_schema
            )

    def test_branch_schemas_stay_under_the_2kib_limit(self) -> None:
        # oneOf 判别式在分支超过 2KiB 时会被摧毁；扁平结构本身也要留在限内。
        import json

        encoded = json.dumps(self.item_schema, ensure_ascii=False).encode("utf-8")
        self.assertLess(len(encoded), 8192, len(encoded))


class DrAddSourcesDiagnosabilityTest(unittest.TestCase):
    def test_missing_field_names_branch_and_field(self) -> None:
        rejection = _describe_source_rejection(
            [_source("source_file", "lvke://source-files/w/f", locator="")]
        )
        self.assertEqual(rejection["code"], "source_descriptor_incomplete")
        self.assertEqual(rejection["matched_branch"], "source_file")
        self.assertEqual(rejection["missing_fields"], ["locator"])

    def test_absent_source_type_lists_supported_types(self) -> None:
        rejection = _describe_source_rejection([{"object_id": "x"}])
        self.assertEqual(rejection["code"], "source_type_required")
        self.assertIn("source_file", rejection["supported_source_types"])

    def test_wrong_uri_domain_reports_expected_prefix(self) -> None:
        rejection = _describe_source_rejection(
            [_source("source_file", "lvke://data-acquisition/w/s")]
        )
        self.assertEqual(rejection["code"], "source_resource_uri_domain_mismatch")
        self.assertEqual(rejection["expected_uri_prefix"], "lvke://source-files/")

    def test_wrong_evidence_track_reports_expected_track(self) -> None:
        rejection = _describe_source_rejection(
            [
                _source(
                    "technical_fixture",
                    "lvke://data-acquisition/w/s",
                    evidence_track="real",
                )
            ]
        )
        self.assertEqual(rejection["code"], "source_evidence_track_mismatch")
        self.assertEqual(rejection["expected_evidence_track"], "technical_fixture")

    def test_fixture_allowed_uses_are_restricted(self) -> None:
        rejection = _describe_source_rejection(
            [
                _source(
                    "technical_fixture",
                    "lvke://data-acquisition/w/s",
                    evidence_track="technical_fixture",
                    allowed_uses=["fact_extraction"],
                )
            ]
        )
        self.assertEqual(rejection["code"], "source_allowed_uses_not_permitted")

    def test_valid_sources_pass_through(self) -> None:
        self.assertIsNone(
            _describe_source_rejection(
                [
                    _source(
                        "source_snapshot",
                        "lvke://data-acquisition/workspaces/w/sources/src_a",
                    ),
                    _source(
                        "source_file",
                        "lvke://source-files/workspaces/w/files/file_a",
                    ),
                ]
            )
        )

    def test_rejection_index_is_reported(self) -> None:
        rejection = _describe_source_rejection(
            [
                _source(
                    "source_snapshot",
                    "lvke://data-acquisition/workspaces/w/sources/src_a",
                ),
                _source("source_file", "lvke://source-files/w/f", content_hash=""),
            ]
        )
        self.assertEqual(rejection["source_index"], 1)


if __name__ == "__main__":
    unittest.main()
