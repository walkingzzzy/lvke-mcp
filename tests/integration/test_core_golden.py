from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CAPTURE_SCRIPT = REPO_ROOT / "scripts" / "capture_samples.py"
BASELINE = REPO_ROOT / "tests" / "fixtures" / "baseline"
CORE_GOLDENS = (
    Path("finance/FinanceRun.roundtrip.v1.json"),
    Path("finance-tables/FinanceTables.roundtrip.v1.json"),
    Path("research/ResearchPackage.roundtrip.v1.json"),
    Path("report/EvidencePack.roundtrip.v1.json"),
    Path("report/ReportRevision.roundtrip.v1.json"),
)


def capture_core(output: Path) -> None:
    subprocess.run(
        [sys.executable, str(CAPTURE_SCRIPT), "--core-only", "--output", str(output)],
        cwd=REPO_ROOT,
        check=True,
        timeout=180,
    )


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


class CoreGoldenTest(unittest.TestCase):
    def test_core_golden_is_reproducible_and_current(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lvke-core-golden-") as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            capture_core(first)
            capture_core(second)

            for relative_path in CORE_GOLDENS:
                expected = read_json(BASELINE / relative_path)
                first_value = read_json(first / relative_path)
                second_value = read_json(second / relative_path)
                self.assertEqual(first_value, second_value)
                self.assertEqual(first_value, expected)

            for output in (first, second):
                manifest = read_json(output / "samples_manifest.json")
                self.assertIsInstance(manifest, dict)
                self.assertEqual(manifest["summary"], {
                    "ok": 5,
                    "defect": 0,
                    "pending": 0,
                    "ok_domains": ["finance", "finance-tables", "report", "research"],
                    "defect_domains": [],
                })


if __name__ == "__main__":
    unittest.main()
