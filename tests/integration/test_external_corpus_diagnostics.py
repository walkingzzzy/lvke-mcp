"""External corpus failure diagnostics: reason -> envelope detail mapping.

`external_corpus_unavailable` covers six distinct failure causes. The error code
stays stable for backward compatibility, so the machine-readable cause is carried
in `detail` and the remediation in `next_actions`. Without these assertions the
mapping silently regresses and operators lose the ability to tell "env var not
set" apart from "project not registered".
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lvke_mcp.servers.lvke_source_files._service.imports import resolve_external_corpus
from lvke_mcp.servers.lvke_source_files.external_corpora import (
    ExternalCorpusError,
    configured_import_root_diagnostics,
    configured_import_roots,
)

_MARKER = "registered/marker.md"


def _write_corpus(root: Path) -> Path:
    """Build a minimal valid corpus tree and manifest; return the manifest path."""

    marker = root / _MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("# marker\n", encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "external-corpora.v1",
                "corpora": [
                    {
                        "corpus_id": "registered_corpus",
                        "relative_path": "registered",
                        "marker_files": ["marker.md"],
                        "allowed_evidence_roles": ["client_report"],
                        "preferred_extensions": [".md"],
                    }
                ],
                "projects": [
                    {
                        "project_id": "registered-project",
                        "aliases": ["已登记项目"],
                        "finance_route": "generic_feasibility",
                        "report_type": "feasibility_study",
                        "route_markers": ["source_reconstructed"],
                        "corpus_ids": ["registered_corpus"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest


class TestExternalCorpusErrorReason(unittest.TestCase):
    """Unit: ExternalCorpusError carries reason without breaking existing callers."""

    def test_default_reason_is_empty(self) -> None:
        e = ExternalCorpusError("some message")
        self.assertEqual(e.reason, "")
        self.assertEqual(str(e), "some message")

    def test_custom_reason_is_preserved(self) -> None:
        e = ExternalCorpusError("not found", reason="project_not_registered")
        self.assertEqual(e.reason, "project_not_registered")
        self.assertEqual(str(e), "not found")

    def test_positional_construction_still_works(self) -> None:
        """Old callers that pass only a message string continue to work."""
        e = ExternalCorpusError("legacy message")
        self.assertIsInstance(e, RuntimeError)

    def test_valid_import_root_survives_invalid_siblings(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lvke-valid-root-") as directory:
            missing = str(Path(directory) / "missing")
            configured = os.pathsep.join([missing, directory])
            with mock.patch.dict(
                os.environ, {"LVKE_SOURCE_IMPORT_ROOTS": configured}, clear=False
            ):
                self.assertEqual(configured_import_roots(), (Path(directory).resolve(),))
                diagnostics = configured_import_root_diagnostics()
            self.assertEqual(len(diagnostics["invalid_roots"]), 1)
            self.assertEqual(diagnostics["invalid_roots"][0]["reason"], "root_not_found")


class TestResolveExternalCorpusDiagnostics(unittest.TestCase):
    """Integration: detail and next_actions match reason for each failure class."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="lvke-corpus-diag-")
        self._root = Path(self._tmp.name)
        self._manifest = _write_corpus(self._root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _env(self, **extra: str) -> dict[str, str]:
        base = {
            "LVKE_EXTERNAL_CORPUS_ROOT": str(self._root),
            "LVKE_EXTERNAL_CORPUS_MANIFEST": str(self._manifest),
            "LVKE_SOURCE_IMPORT_ROOTS": "",
        }
        base.update(extra)
        return base

    # ── root_not_configured ──────────────────────────────────────────────────

    def test_root_not_configured(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"LVKE_EXTERNAL_CORPUS_ROOT": "", "LVKE_SOURCE_IMPORT_ROOTS": ""},
            clear=False,
        ):
            result = resolve_external_corpus("any project")
        self.assertEqual(result["code"], "external_corpus_unavailable")
        self.assertEqual(result["detail"], "root_not_configured")
        self.assertFalse(result["success"])
        self.assertTrue(any("LVKE_EXTERNAL_CORPUS_ROOT" in a for a in result["next_actions"]))

    # ── project_not_registered ───────────────────────────────────────────────

    def test_project_not_registered(self) -> None:
        with mock.patch.dict(os.environ, self._env(), clear=False):
            result = resolve_external_corpus("黄鹰岩旅游项目")
        self.assertEqual(result["code"], "external_corpus_unavailable")
        self.assertEqual(result["detail"], "project_not_registered")
        self.assertFalse(result["success"])
        # Must tell callers they can use source_import_local_path instead
        self.assertTrue(
            any("source_import_local_path" in a for a in result["next_actions"]),
            "next_actions must mention source_import_local_path for project_not_registered",
        )
        self.assertTrue(
            any("LVKE_SOURCE_IMPORT_ROOTS" in a for a in result["next_actions"]),
            "next_actions must mention LVKE_SOURCE_IMPORT_ROOTS for project_not_registered",
        )

    # ── project_ambiguous ────────────────────────────────────────────────────

    def test_project_ambiguous(self) -> None:
        """Two projects whose aliases overlap produce project_ambiguous, not not_registered."""
        manifest_ambiguous = self._root / "manifest_ambiguous.json"
        manifest_ambiguous.write_text(
            json.dumps(
                {
                    "schema_version": "external-corpora.v1",
                    "corpora": [
                        {
                            "corpus_id": "registered_corpus",
                            "relative_path": "registered",
                            "marker_files": ["marker.md"],
                            "allowed_evidence_roles": ["client_report"],
                            "preferred_extensions": [".md"],
                        }
                    ],
                    "projects": [
                        {
                            "project_id": "proj-a",
                            "aliases": ["项目A"],
                            "finance_route": "generic_feasibility",
                            "report_type": "feasibility_study",
                            "route_markers": ["source_reconstructed"],
                            "corpus_ids": ["registered_corpus"],
                        },
                        {
                            "project_id": "proj-b",
                            "aliases": ["项目A二期"],
                            "finance_route": "generic_feasibility",
                            "report_type": "feasibility_study",
                            "route_markers": ["source_reconstructed"],
                            "corpus_ids": ["registered_corpus"],
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with mock.patch.dict(
            os.environ,
            self._env(LVKE_EXTERNAL_CORPUS_MANIFEST=str(manifest_ambiguous)),
            clear=False,
        ):
            result = resolve_external_corpus("项目A")
        self.assertEqual(result["code"], "external_corpus_unavailable")
        self.assertEqual(result["detail"], "project_ambiguous")

    # ── registered project: happy path ──────────────────────────────────────

    def test_registered_project_returns_ok(self) -> None:
        with mock.patch.dict(os.environ, self._env(), clear=False):
            result = resolve_external_corpus("已登记项目")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["success"])
        self.assertNotIn("detail", result)  # no error detail on success

    # ── manifest_invalid ─────────────────────────────────────────────────────

    def test_manifest_invalid_bad_json(self) -> None:
        bad = self._root / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        with mock.patch.dict(
            os.environ,
            self._env(LVKE_EXTERNAL_CORPUS_MANIFEST=str(bad)),
            clear=False,
        ):
            result = resolve_external_corpus("任何项目")
        self.assertEqual(result["code"], "external_corpus_unavailable")
        self.assertEqual(result["detail"], "manifest_invalid")


if __name__ == "__main__":
    unittest.main()
