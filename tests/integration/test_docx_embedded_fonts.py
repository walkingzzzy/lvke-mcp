from __future__ import annotations

import io
import unittest
import zipfile

from lxml import etree

from lvke_mcp.domains.reports.doc_service import markdown_to_docx
from lvke_mcp.domains.reports.docx_fonts import (
    audit_docx_fonts,
    normalize_docx_fonts,
)


class DocxEmbeddedFontsTest(unittest.TestCase):
    def test_docx_embeds_valid_ofl_fonts_covering_visible_chinese(self) -> None:
        docx = markdown_to_docx(
            "# 武汉市江夏区项目\n\n可行性研究报告包含投资、收入与风险分析。\n"
        )

        audit = audit_docx_fonts(docx)
        self.assertTrue(audit["portable_cjk_fonts"], audit)
        self.assertEqual(audit["embedded_font_count"], 2)
        self.assertEqual(audit["invalid_locale_font_count"], 0)
        for embedded in audit["embedded_fonts"]:
            with self.subTest(alias=embedded["alias"]):
                self.assertTrue(embedded["valid"], embedded)
                self.assertTrue(embedded["ofl_license_metadata"])
                self.assertEqual(embedded["missing_cjk_glyph_count"], 0)
                self.assertLess(embedded["embedded_size_bytes"], 2_000_000)

    def test_short_tables_do_not_request_cross_page_header_repeats(self) -> None:
        docx = markdown_to_docx(
            "| 指标 | 数值 |\n| --- | ---: |\n| 总投资 | 1250 万元 |\n"
        )
        with zipfile.ZipFile(io.BytesIO(docx), "r") as source:
            document_xml = source.read("word/document.xml")
        root = etree.fromstring(document_xml)
        markers = root.xpath(
            ".//w:tbl/w:tr/w:trPr/w:tblHeader",
            namespaces={
                "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            },
        )
        self.assertEqual(markers, [])
        spacing = root.xpath(
            ".//w:tbl/w:tr/w:tc/w:p/w:pPr/w:spacing",
            namespaces={
                "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            },
        )
        self.assertTrue(spacing)
        for item in spacing:
            self.assertEqual(
                item.get(
                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}before"
                ),
                "0",
            )
            self.assertEqual(
                item.get(
                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}after"
                ),
                "0",
            )

    def test_normalization_is_deterministic_and_does_not_duplicate_fonts(self) -> None:
        first = markdown_to_docx("# 中文标题\n\n武汉市江夏区。\n")
        second, second_audit = normalize_docx_fonts(first)
        third, third_audit = normalize_docx_fonts(second)

        self.assertEqual(second, third)
        self.assertEqual(second_audit["embedded_font_count"], 2)
        self.assertTrue(third_audit["portable_cjk_fonts"])

    def test_audit_rejects_corrupted_embedded_font(self) -> None:
        docx = markdown_to_docx("# 中文标题\n\n武汉市江夏区。\n")
        with zipfile.ZipFile(io.BytesIO(docx), "r") as source:
            parts = {name: source.read(name) for name in source.namelist()}
        body_part = "word/fonts/lvke-body-cjk.odttf"
        parts[body_part] = b"corrupt" + parts[body_part][7:40]
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
            for name, data in parts.items():
                target.writestr(name, data)

        audit = audit_docx_fonts(output.getvalue())
        self.assertFalse(audit["portable_cjk_fonts"])
        body = next(
            item for item in audit["embedded_fonts"]
            if item["alias"] == "Songti SC"
        )
        self.assertFalse(body["valid"])
        self.assertEqual(body["error"], "embedded_font_invalid")


if __name__ == "__main__":
    unittest.main()
