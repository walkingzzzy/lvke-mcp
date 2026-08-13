"""构建元数据回归：14 个服务必须输出同一 commit/build_time/plugin_version。

覆盖三条不变量：
1. build_time 是真实 UTC 时刻，不是 ``source-checkout`` 占位串；
2. 缺失字段时显式返回 ``build_metadata_incomplete``，不静默退化；
3. 所有 server 的 envelope 元数据完全一致。
"""

from __future__ import annotations

import re
import tempfile
import unittest
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

from lvke_mcp.runtime import build_metadata as metadata_module
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
        "plugin_version": "0.1.0+codex.20260809085642",
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
    def test_current_checkout_reports_honest_metadata_state(self) -> None:
        meta = build_metadata()
        self.assertNotEqual(meta.build_time, "source-checkout")
        if meta.complete:
            self.assertRegex(meta.build_time, _UTC_ISO)
            self.assertTrue(meta.plugin_version.startswith("0.1.0+codex."))
        else:
            self.assertEqual(meta.build_time, "")
            self.assertEqual(meta.startup_report()["code"], INCOMPLETE_CODE)

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


class BuildMetadataSourceCheckoutTest(unittest.TestCase):
    def _resolve(
        self,
        *,
        metadata_commit: str = "a" * 40,
        head: str = "a" * 40,
        clean: bool = True,
    ) -> BuildMetadata:
        fixture = {
            "build_commit": metadata_commit,
            "build_time": "2026-08-13T12:00:00Z",
            "plugin_version": "0.1.0+codex.20260813120000",
        }
        with patch.object(metadata_module, "_load_metadata_file", return_value=fixture), patch.object(
            metadata_module, "_metadata_file", return_value=Path("/fixture/build_metadata.json")
        ), patch.object(metadata_module, "_repo_root", return_value=Path("/fixture/repo")), patch.object(
            metadata_module, "_git_head", return_value=head
        ), patch.object(metadata_module, "_tracked_worktree_clean", return_value=clean):
            return metadata_module._resolve()

    def test_clean_matching_checkout_is_complete(self) -> None:
        meta = self._resolve()
        self.assertTrue(meta.complete)
        self.assertEqual(meta.build_time, "2026-08-13T12:00:00Z")
        self.assertEqual(meta.build_commit, "a" * 40)

    def test_stale_metadata_commit_rejects_old_build_time(self) -> None:
        meta = self._resolve(metadata_commit="b" * 40)
        self.assertFalse(meta.complete)
        self.assertEqual(meta.build_commit, "a" * 40)
        self.assertEqual(meta.build_time, "")
        self.assertIn("stale_build_commit", meta.source)

    def test_dirty_tracked_checkout_rejects_build_time(self) -> None:
        meta = self._resolve(clean=False)
        self.assertFalse(meta.complete)
        self.assertEqual(meta.build_time, "")
        self.assertIn("tracked_worktree_dirty", meta.source)

    def test_untracked_files_do_not_make_checkout_dirty(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lvke-build-metadata-") as directory:
            root = Path(directory)
            import subprocess

            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "lvke@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Lvke Test"], cwd=root, check=True)
            tracked = root / "tracked.txt"
            tracked.write_text("tracked\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
            (root / "untracked-deliverable.txt").write_text("output\n", encoding="utf-8")
            self.assertTrue(metadata_module._tracked_worktree_clean(root))


class BuildMetadataWriterTest(unittest.TestCase):
    def test_writer_uses_package_local_target_and_full_plugin_version(self) -> None:
        from scripts import write_build_metadata as writer

        with tempfile.TemporaryDirectory(prefix="lvke-build-writer-") as directory:
            root = Path(directory)
            target = root / "src" / "lvke_mcp" / "runtime" / "build_metadata.json"
            root_copy = root / "build_metadata.json"
            root_copy.write_text("sentinel\n", encoding="utf-8")
            with patch.object(writer, "TARGET", target), patch.object(
                writer, "_plugin_version", return_value="0.1.0+codex.20260813123456"
            ):
                payload = writer.write_build_metadata(
                    commit="c" * 40,
                    build_time="2026-08-13T12:34:56Z",
                )
            self.assertEqual(payload["plugin_version"], "0.1.0+codex.20260813123456")
            self.assertTrue(target.is_file())
            self.assertEqual(root_copy.read_text(encoding="utf-8"), "sentinel\n")


if __name__ == "__main__":
    unittest.main()
