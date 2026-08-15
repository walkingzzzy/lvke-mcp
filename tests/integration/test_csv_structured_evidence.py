from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lvke_mcp.adapters.source_files_repository import _parse_bytes
from lvke_mcp.servers.lvke_data_analysis._service.candidate_extract import extract_candidates


class CsvStructuredEvidenceTests(unittest.TestCase):
    def _analysis(self, content: bytes) -> dict:
        with tempfile.TemporaryDirectory(prefix="lvke-csv-evidence-") as directory:
            path = Path(directory) / "jiangxia.csv"
            path.write_bytes(content)
            return _parse_bytes(path, "text/csv")

    def test_csv_cells_keep_header_row_value_unit_and_locator(self) -> None:
        analysis = self._analysis(
            b"field,value,unit\n"
            b"purchase_price,5200,wan\n"
            b"installed_capacity,10,MW\n"
            b"annual_generation,11500,MWh\n"
            b"tariff,0.42,yuan/kWh\n"
            b"target_return,8,percent\n"
            b"minimum_dscr,1.2,ratio\n"
        )
        self.assertEqual(analysis["parser"], "mcp-csv-parser.v1")
        self.assertNotIn("degraded_reason", analysis)
        value = next(row for row in analysis["locators"] if row.get("cell") == "B2")
        self.assertEqual(value["locator"], "csv:2:2")
        self.assertEqual(value["header_name"], "value")
        self.assertEqual(value["original_value"], "5200")
        self.assertEqual(value["cached_value"], 5200)
        self.assertTrue(str(value["content_hash"]).startswith("sha256:"))

    def test_candidate_extraction_uses_same_row_value_and_unit(self) -> None:
        analysis = self._analysis(
            b"field,value,unit\n"
            b"purchase_price,5200,wan\n"
            b"installed_capacity,10,MW\n"
            b"annual_generation,11500,MWh\n"
            b"tariff,0.42,yuan/kWh\n"
            b"target_return,8,percent\n"
            b"minimum_dscr,1.2,ratio\n"
        )
        document = {
            "source_id": "src-jiangxia",
            "source_type": "controlled_file",
            "formal_use_allowed": False,
            "locators": analysis["locators"],
        }
        specs = [
            {"field": "purchase_price", "expected_unit": "万元"},
            {"field": "installed_capacity", "expected_unit": "MW"},
            {"field": "annual_generation", "expected_unit": "MWh"},
            {"field": "tariff", "expected_unit": "元/kWh"},
            {"field": "target_return", "expected_unit": "%"},
            {"field": "minimum_dscr", "expected_unit": "倍"},
        ]
        with patch(
            "lvke_mcp.servers.lvke_data_analysis._service.candidate_extract._documents_from_task",
            return_value=[document],
        ), patch(
            "lvke_mcp.servers.lvke_data_analysis._service.candidate_extract.CANDIDATE_STORE.put",
            return_value={"object_id": "cand-1", "resource_uri": "lvke://candidate/cand-1"},
        ):
            result = extract_candidates("ws", "task", specs)
        values = {row["field"]: row["numeric_value"] for row in result["fact_candidates"]}
        self.assertEqual(values, {
            "purchase_price": 5200,
            "installed_capacity": 10,
            "annual_generation": 11500,
            "tariff": 0.42,
            "target_return": 8,
            "minimum_dscr": 1.2,
        })
        self.assertFalse(result["missing_fields"])
        self.assertTrue(all(row["candidate_kind"] == "structured_csv_row" for row in result["fact_candidates"]))

    def test_malformed_or_ragged_csv_is_partial_not_prose(self) -> None:
        malformed = self._analysis(b'field,value\npurchase_price,"5200\n')
        self.assertEqual(malformed["degraded_reason"], "csv_structure_invalid")
        self.assertFalse(malformed["locators"])
        ragged = self._analysis(b"field,value,unit\npurchase_price,5200\n")
        self.assertEqual(ragged["degraded_reason"], "csv_ragged_rows")
        self.assertTrue(ragged["locators"])
        self.assertFalse(any(row.get("kind") == "document_text" for row in ragged["locators"]))


if __name__ == "__main__":
    unittest.main()
