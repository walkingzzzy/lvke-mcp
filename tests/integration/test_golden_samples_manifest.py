from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import golden_samples_manifest as golden


class GoldenSamplesManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="lvke-golden-")
        self.root = Path(self.temp.name)
        self.files = []
        for group in sorted(golden.EXPECTED_GROUPS):
            path = self.root / group / "sample.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(group + "\n", encoding="utf-8")
            self.files.append({
                "sample_id": f"gold_{group}",
                "group": group,
                "relative_path": f"{group}/sample.txt",
                "size_bytes": path.stat().st_size,
                "sha256": golden._sha256(path),
                "locator": "whole-file",
            })
        self.manifest = {
            "schema_version": "golden_samples_manifest.v1",
            "p0a": {
                "status": "frozen",
                "sample_count": 3,
                "groups": {group: 1 for group in sorted(golden.EXPECTED_GROUPS)},
                "files": self.files,
            },
            "p0b": {
                "status": "pending_business_approval",
                "expected_results": [],
                "last_passing_build": None,
            },
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _verify_fixture(self, manifest: dict | None = None) -> dict:
        with patch.object(golden, "EXPECTED_SAMPLE_COUNT", 3):
            return golden.verify_manifest(manifest or self.manifest, self.root)

    def test_verify_checks_hash_size_and_groups(self) -> None:
        result = self._verify_fixture()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["p0b_status"], "pending_business_approval")

    def test_tamper_is_rejected(self) -> None:
        (self.root / self.files[0]["relative_path"]).write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(golden.ManifestError, "变化"):
            self._verify_fixture()

    def test_path_escape_is_rejected(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["p0a"]["files"][0]["relative_path"] = "../escape.txt"
        with self.assertRaises(golden.ManifestError) as caught:
            self._verify_fixture(manifest)
        self.assertEqual(caught.exception.code, "P0A_PATH_ESCAPE")

    def test_declared_sample_count_must_match_file_table(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["p0a"]["sample_count"] = 2
        with self.assertRaises(golden.ManifestError) as caught:
            self._verify_fixture(manifest)
        self.assertEqual(caught.exception.code, "P0A_DECLARED_COUNT_MISMATCH")

    def test_symlink_is_rejected(self) -> None:
        target = self.root / "target.txt"
        target.write_text("target\n", encoding="utf-8")
        link = self.root / "link.txt"
        link.symlink_to(target)
        manifest = copy.deepcopy(self.manifest)
        row = manifest["p0a"]["files"][0]
        row.update({
            "relative_path": "link.txt",
            "size_bytes": target.stat().st_size,
            "sha256": golden._sha256(target),
        })
        with self.assertRaises(golden.ManifestError) as caught:
            self._verify_fixture(manifest)
        self.assertEqual(caught.exception.code, "P0A_SYMLINK_FORBIDDEN")

    def test_pending_p0b_cannot_contain_results_or_build(self) -> None:
        p0b = copy.deepcopy(self.manifest["p0b"])
        p0b["expected_results"] = [{}]
        with self.assertRaises(golden.ManifestError) as caught:
            golden.validate_p0b(p0b)
        self.assertEqual(caught.exception.code, "P0B_PENDING_STATE_INVALID")

    def test_frozen_p0b_requires_dual_track_approval_for_all_groups(self) -> None:
        expected = []
        for group in sorted(golden.EXPECTED_GROUPS):
            track = {
                "version": "v1",
                "hash": "sha256:" + "1" * 64,
                "approval_id": f"approval-{group}",
                "approved_by": "business-owner",
                "approved_at": "2026-08-13T12:00:00+08:00",
            }
            expected.append({
                "sample_id": f"result-{group}",
                "group": group,
                "parser": "source_parser.v1",
                "parser_version": "1.0",
                "tolerances": {"amount": 0.01},
                "test_cases": [f"acceptance-{group}"],
                "reference_track": track,
                "corrected_track": {**track, "approval_id": f"corrected-{group}"},
                "difference_decisions": [],
            })
        frozen = {
            "status": "frozen",
            "definition": "approved fixture",
            "expected_results": expected,
            "last_passing_build": None,
        }
        self.assertEqual(golden.validate_p0b(frozen)["status"], "frozen")
        frozen["expected_results"][0]["reference_track"].pop("approved_by")
        with self.assertRaises(golden.ManifestError) as caught:
            golden.validate_p0b(frozen)
        self.assertEqual(caught.exception.code, "P0B_APPROVAL_INVALID")

    def test_build_record_rejects_skips_and_wrong_commit(self) -> None:
        record = {
            "build_id": "build-1",
            "commit_sha": "a" * 40,
            "passed_at": "2026-08-13T12:00:00+08:00",
            "test_report_sha256": "sha256:" + "2" * 64,
            "status": "passed",
            "groups": sorted(golden.EXPECTED_GROUPS),
            "skipped": [],
            "timed_out": [],
            "temporary_dependencies": [],
        }
        self.assertEqual(golden.validate_build_record(record, expected_commit="a" * 40)["status"], "passed")
        skipped = {**record, "skipped": ["golden"]}
        with self.assertRaises(golden.ManifestError) as caught:
            golden.validate_build_record(skipped, expected_commit="a" * 40)
        self.assertEqual(caught.exception.code, "BUILD_SKIP_FORBIDDEN")
        with self.assertRaises(golden.ManifestError) as caught:
            golden.validate_build_record(record, expected_commit="b" * 40)
        self.assertEqual(caught.exception.code, "BUILD_COMMIT_MISMATCH")


if __name__ == "__main__":
    unittest.main()
