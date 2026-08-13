from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.audit_asset_staging import audit


class AssetStagingAuditTest(unittest.TestCase):
    def test_audit_records_without_deleting_historical_staging(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lvke-staging-audit-") as directory:
            root = Path(directory)
            staging = root / "workspace-a" / "asset-acquisition" / "artifacts" / ".artifact_1.staging-old"
            staging.mkdir(parents=True)
            (staging / "partial.docx").write_bytes(b"fixture")

            result = audit(root)

            self.assertEqual(result["historical_staging_count"], 1)
            self.assertEqual(result["deleted_count"], 0)
            self.assertEqual(result["acceptance_artifact_count"], 0)
            self.assertTrue(staging.is_dir())
            row = result["directories"][0]
            self.assertFalse(row["acceptance_eligible"])
            self.assertEqual(row["action"], "retained_not_deleted")


if __name__ == "__main__":
    unittest.main()
