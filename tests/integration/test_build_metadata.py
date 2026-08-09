"""构建元数据回归：14 个服务必须输出同一 commit/build_time/plugin_version。

覆盖三条不变量：
1. build_time 是真实 UTC 时刻，不是 ``source-checkout`` 占位串；
2. 缺失字段时显式返回 ``build_metadata_incomplete``，不静默退化；
3. 所有 server 的 envelope 元数据完全一致。
"""

from __future__ import annotations

import re
import unittest
from importlib import import_module

from lvke_mcp.runtime.build_metadata import (
    INCOMPLETE_CODE,
    BuildMetadata,
    build_metadata,
)

from test_mcp_compression_round2 import MODULES

_UTC_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _meta(**overrides: str) -> BuildMetadata:
    payload = {
        "build_commit": "eff16312d54657a114dca1cf57370ad503be854d",
        "build_time": "2026-08-09T01:15:39Z",
        "plugin_version": "0.1.0",
        "source": "test",
    }
    payload.update(overrides)
    return BuildMetadata(**payload)  # type: ignore[arg-type]


class BuildMetadataCompletenessTest(unittest.TestCase):
    def test_complete_metadata_reports_ok(self) -> None:
        meta = _meta()
        self.assertTrue(meta.complete)
        self.assertEqual(meta.missing_fields, ())
        self.assertEqual(meta.startup_report()["status"], "ok")

    def test_placeholder_build_time_is_reported_incomplete(self) -> None:
        meta = _meta(build_time="source-checkout")
        self.assertFalse(meta.complete)
        self.assertIn("build_time", meta.missing_fields)

        report = meta.startup_report()
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["code"], INCOMPLETE_CODE)
        self.assertIn("build_time", report["missing"])
        self.assertTrue(report["next_actions"])

    def test_unknown_commit_is_reported_incomplete(self) -> None:
        meta = _meta(build_commit="unknown")
        self.assertIn("build_commit", meta.missing_fields)

    def test_incomplete_envelope_flags_instead_of_silent_placeholder(self) -> None:
        meta = _meta(build_time="source-checkout", plugin_version="")
        fields = meta.envelope_fields()
        self.assertFalse(fields["build_metadata_complete"])
        self.assertEqual(fields["build_metadata_status"], INCOMPLETE_CODE)
        self.assertEqual(
            sorted(fields["build_metadata_missing"]),
            ["build_time", "plugin_version"],
        )
        # 缺失字段不得伪装成真实值
        self.assertEqual(fields["plugin_version"], INCOMPLETE_CODE)

    def test_complete_envelope_has_no_incomplete_markers(self) -> None:
        fields = _meta().envelope_fields()
        self.assertTrue(fields["build_metadata_complete"])
        self.assertNotIn("build_metadata_status", fields)
        self.assertNotIn("build_metadata_missing", fields)


class BuildMetadataUniformityTest(unittest.TestCase):
    def test_resolved_build_time_is_utc_timestamp_not_placeholder(self) -> None:
        meta = build_metadata()
        self.assertNotEqual(meta.build_time, "source-checkout")
        self.assertRegex(meta.build_time, _UTC_ISO)

    def test_all_servers_emit_identical_build_metadata(self) -> None:
        seen = set()
        for module_name in MODULES.values():
            server = import_module(module_name).build_server()
            envelope = server._attach_runtime_metadata({"status": "ok"})
            seen.add(
                (
                    envelope["build_commit"],
                    envelope["build_time"],
                    envelope["plugin_version"],
                    envelope["build_metadata_complete"],
                )
            )
        self.assertEqual(len(seen), 1, f"servers disagree on build metadata: {seen}")


if __name__ == "__main__":
    unittest.main()
