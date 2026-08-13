"""Portable OOXML CJK font normalization for every DOCX export path."""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
import uuid
import zipfile
from pathlib import Path
from typing import Any

from fontTools import subset
from fontTools.ttLib import TTFont, TTLibError
from lxml import etree


PROCESSOR_VERSION = "docx-font-normalizer.v2"
BODY_EAST_ASIA_FONT = "Songti SC"
HEADING_EAST_ASIA_FONT = "Heiti SC"
WESTERN_FONT = "Times New Roman"

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_FONT_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/font"
)
_NS = {"w": _W_NS, "r": _R_NS}
_REL_NS = {"pr": _PKG_REL_NS}
_CT_NAMESPACES = {"ct": _CT_NS}
_LOCALE_FONT_RE = re.compile(r"^[a-z]{2,3}(?:[-_][A-Za-z]{2,4})+$")
_FONT_ATTRS = ("ascii", "hAnsi", "eastAsia", "cs")
_FONT_TABLE = "word/fontTable.xml"
_FONT_RELS = "word/_rels/fontTable.xml.rels"
_CONTENT_TYPES = "[Content_Types].xml"
_ASSET_ROOT = Path(__file__).with_name("assets") / "fonts"
_BASELINE_GLYPHS = "中文可行性研究报告"

_EMBEDDED_FONTS = (
    {
        "alias": BODY_EAST_ASIA_FONT,
        "source_name": "NotoSerifSC-Regular.otf",
        "source_sha256": "e8f396decc1f0963a016a989c3d8852e863d1350996f573860a80767c83a1cd3",
        "postscript_name": "NotoSerifSC-Regular",
        "relationship_id": "rIdLvkeBodyCjkFont",
        "target": "fonts/lvke-body-cjk.odttf",
    },
    {
        "alias": HEADING_EAST_ASIA_FONT,
        "source_name": "NotoSansSC-Medium.otf",
        "source_sha256": "7633f5a016d4dd95e685a69633d818aabc4644c4b08e26bd35b1b30c45ed5dda",
        "postscript_name": "NotoSansSC-Medium",
        "relationship_id": "rIdLvkeHeadingCjkFont",
        "target": "fonts/lvke-heading-cjk.odttf",
    },
)


class DocxFontError(ValueError):
    """Raised when a DOCX cannot be normalized without ambiguity."""


def _w(name: str) -> str:
    return f"{{{_W_NS}}}{name}"


def _parse_xml(data: bytes, message: str) -> Any:
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        return etree.fromstring(data, parser=parser)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise DocxFontError(message) from exc


def _serialize_xml(root: Any) -> bytes:
    return etree.tostring(
        root,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )


def _read_parts(docx_bytes: bytes) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as source:
            return {name: source.read(name) for name in source.namelist()}
    except (zipfile.BadZipFile, OSError) as exc:
        raise DocxFontError("DOCX package is invalid") from exc


def _write_parts(parts: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name in sorted(parts):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            target.writestr(info, parts[name])
    return output.getvalue()


def _is_heading_context(node: Any) -> bool:
    for ancestor in [node, *node.iterancestors()]:
        if ancestor.tag == _w("style"):
            style_id = str(ancestor.get(_w("styleId")) or "").casefold()
            names = ancestor.xpath("./w:name/@w:val", namespaces=_NS)
            style_name = str(names[0] if names else "").casefold()
            if (
                style_id.startswith(("heading", "title"))
                or style_name.startswith(("heading", "title"))
                or "标题" in style_name
            ):
                return True
        if ancestor.tag == _w("p"):
            styles = ancestor.xpath("./w:pPr/w:pStyle/@w:val", namespaces=_NS)
            paragraph_style = str(styles[0] if styles else "").casefold()
            if paragraph_style.startswith(("heading", "title")) or "标题" in paragraph_style:
                return True
    return False


def _ensure_style_fonts(root: Any) -> None:
    for style in root.xpath(".//w:style", namespaces=_NS):
        style_id = str(style.get(_w("styleId")) or "").casefold()
        names = style.xpath("./w:name/@w:val", namespaces=_NS)
        style_name = str(names[0] if names else "").casefold()
        if not (
            style_id in {"normal", "bodytext"}
            or style_id.startswith(("heading", "title"))
            or style_name.startswith(("normal", "heading", "title"))
            or "正文" in style_name
            or "标题" in style_name
        ):
            continue
        rpr = style.find(_w("rPr"))
        if rpr is None:
            rpr = etree.SubElement(style, _w("rPr"))
        rfonts = rpr.find(_w("rFonts"))
        if rfonts is None:
            rfonts = etree.SubElement(rpr, _w("rFonts"))
        rfonts.set(
            _w("eastAsia"),
            HEADING_EAST_ASIA_FONT if _is_heading_context(style) else BODY_EAST_ASIA_FONT,
        )
        if not rfonts.get(_w("ascii")):
            rfonts.set(_w("ascii"), WESTERN_FONT)
        if not rfonts.get(_w("hAnsi")):
            rfonts.set(_w("hAnsi"), WESTERN_FONT)
        lang = rpr.find(_w("lang"))
        if lang is None:
            lang = etree.SubElement(rpr, _w("lang"))
        lang.set(_w("eastAsia"), "zh-CN")


def _normalize_xml(data: bytes, *, styles: bool) -> tuple[bytes, int]:
    root = _parse_xml(data, "DOCX OOXML font part is malformed")
    if styles:
        _ensure_style_fonts(root)
    replacements = 0
    for rfonts in root.xpath(".//w:rFonts", namespaces=_NS):
        east_asia = (
            HEADING_EAST_ASIA_FONT
            if _is_heading_context(rfonts)
            else BODY_EAST_ASIA_FONT
        )
        for attr in _FONT_ATTRS:
            key = _w(attr)
            value = str(rfonts.get(key) or "").strip()
            if value and _LOCALE_FONT_RE.fullmatch(value):
                replacements += 1
                rfonts.set(key, east_asia if attr == "eastAsia" else WESTERN_FONT)
        rfonts.set(_w("eastAsia"), east_asia)
    return _serialize_xml(root), replacements


def _document_codepoints(parts: dict[str, bytes]) -> set[int]:
    characters = set(_BASELINE_GLYPHS)
    for name, data in parts.items():
        if not name.startswith("word/") or not name.endswith(".xml"):
            continue
        root = _parse_xml(data, "DOCX OOXML text part is malformed")
        for value in root.xpath(".//w:t/text()", namespaces=_NS):
            characters.update(str(value))
    return {ord(character) for character in characters}


def _is_cjk(codepoint: int) -> bool:
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x3134F
    )


def _font_source(spec: dict[str, str]) -> bytes:
    path = _ASSET_ROOT / spec["source_name"]
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise DocxFontError(f"packaged CJK font is unavailable: {spec['source_name']}") from exc
    if hashlib.sha256(data).hexdigest() != spec["source_sha256"]:
        raise DocxFontError(f"packaged CJK font hash mismatch: {spec['source_name']}")
    return data


def _subset_font(spec: dict[str, str], codepoints: set[int]) -> bytes:
    options = subset.Options()
    options.recalc_timestamp = False
    options.canonical_order = True
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.layout_features = ["*"]
    options.notdef_glyph = True
    options.notdef_outline = True
    options.recommended_glyphs = True
    font = TTFont(io.BytesIO(_font_source(spec)), recalcTimestamp=False, lazy=False)
    try:
        subsetter = subset.Subsetter(options=options)
        subsetter.populate(unicodes=codepoints)
        subsetter.subset(font)
        output = io.BytesIO()
        font.save(output, reorderTables=True)
        return output.getvalue()
    except Exception as exc:  # noqa: BLE001
        raise DocxFontError(f"CJK font subsetting failed: {spec['alias']}") from exc
    finally:
        font.close()


def _font_key(spec: dict[str, str], font_data: bytes) -> uuid.UUID:
    digest = hashlib.sha256(
        spec["alias"].encode("utf-8") + b"\0" + font_data
    ).digest()
    return uuid.UUID(bytes=digest[:16])


def _obfuscate_font(font_data: bytes, key: uuid.UUID) -> bytes:
    result = bytearray(font_data)
    key_bytes = key.bytes[::-1]
    for index in range(min(32, len(result))):
        result[index] ^= key_bytes[index % 16]
    return bytes(result)


def _ensure_embedded_font_parts(parts: dict[str, bytes]) -> None:
    if _FONT_TABLE not in parts or _CONTENT_TYPES not in parts:
        raise DocxFontError("DOCX font table or content types part is missing")
    font_table = _parse_xml(parts[_FONT_TABLE], "DOCX font table is malformed")
    if _FONT_RELS in parts:
        relationships = _parse_xml(
            parts[_FONT_RELS],
            "DOCX font relationships are malformed",
        )
    else:
        relationships = etree.Element(
            f"{{{_PKG_REL_NS}}}Relationships",
            nsmap={None: _PKG_REL_NS},
        )
    content_types = _parse_xml(
        parts[_CONTENT_TYPES],
        "DOCX content types are malformed",
    )
    if not content_types.xpath(
        './ct:Default[@Extension="odttf"]',
        namespaces=_CT_NAMESPACES,
    ):
        default = etree.SubElement(content_types, f"{{{_CT_NS}}}Default")
        default.set("Extension", "odttf")
        default.set(
            "ContentType",
            "application/vnd.openxmlformats-officedocument.obfuscatedFont",
        )

    codepoints = _document_codepoints(parts)
    for spec in _EMBEDDED_FONTS:
        subset_data = _subset_font(spec, codepoints)
        key = _font_key(spec, subset_data)
        matches = font_table.xpath(
            "./w:font[@w:name=$name]",
            namespaces=_NS,
            name=spec["alias"],
        )
        font_node = matches[0] if matches else etree.SubElement(font_table, _w("font"))
        font_node.set(_w("name"), spec["alias"])
        for duplicate in matches[1:]:
            font_table.remove(duplicate)
        for child in list(font_node):
            if child.tag in {
                _w("embedRegular"),
                _w("embedBold"),
                _w("embedItalic"),
                _w("embedBoldItalic"),
            }:
                font_node.remove(child)
        embedded = etree.SubElement(font_node, _w("embedRegular"))
        embedded.set(f"{{{_R_NS}}}id", spec["relationship_id"])
        embedded.set(_w("fontKey"), "{" + str(key).upper() + "}")

        for relation in list(relationships):
            if (
                relation.get("Id") == spec["relationship_id"]
                or relation.get("Target") == spec["target"]
            ):
                relationships.remove(relation)
        relation = etree.SubElement(
            relationships,
            f"{{{_PKG_REL_NS}}}Relationship",
        )
        relation.set("Id", spec["relationship_id"])
        relation.set("Type", _FONT_REL_TYPE)
        relation.set("Target", spec["target"])
        parts["word/" + spec["target"]] = _obfuscate_font(subset_data, key)

    parts[_FONT_TABLE] = _serialize_xml(font_table)
    parts[_FONT_RELS] = _serialize_xml(relationships)
    parts[_CONTENT_TYPES] = _serialize_xml(content_types)


def _font_name(font: TTFont, name_id: int) -> str:
    values: list[str] = []
    for record in font["name"].names:
        if record.nameID != name_id:
            continue
        try:
            value = record.toUnicode().strip()
        except (UnicodeDecodeError, AttributeError):
            continue
        if value:
            values.append(value)
    return values[0] if values else ""


def _embedded_font_audit(
    parts: dict[str, bytes],
    codepoints: set[int],
) -> list[dict[str, Any]]:
    if _FONT_TABLE not in parts or _FONT_RELS not in parts:
        return []
    font_table = _parse_xml(parts[_FONT_TABLE], "DOCX font table is malformed")
    relationships = _parse_xml(
        parts[_FONT_RELS],
        "DOCX font relationships are malformed",
    )
    relations = {
        str(item.get("Id") or ""): str(item.get("Target") or "")
        for item in relationships.xpath("./pr:Relationship", namespaces=_REL_NS)
        if item.get("Type") == _FONT_REL_TYPE
    }
    cjk_codepoints = {value for value in codepoints if _is_cjk(value)}
    audits: list[dict[str, Any]] = []
    for spec in _EMBEDDED_FONTS:
        result: dict[str, Any] = {
            "alias": spec["alias"],
            "expected_postscript_name": spec["postscript_name"],
            "valid": False,
            "missing_cjk_glyph_count": len(cjk_codepoints),
        }
        nodes = font_table.xpath(
            "./w:font[@w:name=$name]/w:embedRegular",
            namespaces=_NS,
            name=spec["alias"],
        )
        if not nodes:
            result["error"] = "embed_regular_missing"
            audits.append(result)
            continue
        relationship_id = str(nodes[0].get(f"{{{_R_NS}}}id") or "")
        key_text = str(nodes[0].get(_w("fontKey")) or "").strip()
        target = relations.get(relationship_id, "")
        normalized = posixpath.normpath(posixpath.join("word", target))
        if not target or normalized.startswith("../") or normalized not in parts:
            result["error"] = "embedded_font_part_missing"
            audits.append(result)
            continue
        try:
            key = uuid.UUID(key_text.strip("{}"))
            font_data = _obfuscate_font(parts[normalized], key)
            font = TTFont(io.BytesIO(font_data), recalcTimestamp=False, lazy=False)
            try:
                postscript_name = _font_name(font, 6)
                license_text = _font_name(font, 13)
                cmap = set((font.getBestCmap() or {}).keys())
            finally:
                font.close()
        except (ValueError, TTLibError, KeyError, OSError):
            result["error"] = "embedded_font_invalid"
            audits.append(result)
            continue
        missing = sorted(cjk_codepoints - cmap)
        license_ok = "Open Font License" in license_text
        result.update({
            "relationship_id": relationship_id,
            "part": normalized,
            "font_key": "{" + str(key).upper() + "}",
            "postscript_name": postscript_name,
            "font_sha256": "sha256:" + hashlib.sha256(font_data).hexdigest(),
            "embedded_size_bytes": len(parts[normalized]),
            "missing_cjk_glyph_count": len(missing),
            "missing_cjk_codepoints": [f"U+{value:04X}" for value in missing[:20]],
            "ofl_license_metadata": license_ok,
            "valid": (
                postscript_name == spec["postscript_name"]
                and license_ok
                and not missing
            ),
        })
        if not result["valid"]:
            result["error"] = "embedded_font_identity_or_coverage_invalid"
        audits.append(result)
    return audits


def _audit_parts(parts: dict[str, bytes]) -> dict[str, Any]:
    locale_fonts: list[dict[str, str]] = []
    east_asia_fonts: set[str] = set()
    for name, data in parts.items():
        if not name.startswith("word/") or not name.endswith(".xml"):
            continue
        root = _parse_xml(data, "DOCX OOXML part is malformed")
        for rfonts in root.xpath(".//w:rFonts", namespaces=_NS):
            for attr in _FONT_ATTRS:
                value = str(rfonts.get(_w(attr)) or "").strip()
                if attr == "eastAsia" and value:
                    east_asia_fonts.add(value)
                if value and _LOCALE_FONT_RE.fullmatch(value):
                    locale_fonts.append({
                        "part": name,
                        "attribute": attr,
                        "value": value,
                    })
    embedded_fonts = _embedded_font_audit(parts, _document_codepoints(parts))
    required_aliases = {BODY_EAST_ASIA_FONT, HEADING_EAST_ASIA_FONT}
    valid_aliases = {
        str(item.get("alias") or "")
        for item in embedded_fonts
        if item.get("valid")
    }
    return {
        "processor_version": PROCESSOR_VERSION,
        "east_asia_fonts": sorted(east_asia_fonts),
        "body_east_asia_font": BODY_EAST_ASIA_FONT,
        "heading_east_asia_font": HEADING_EAST_ASIA_FONT,
        "invalid_locale_font_count": len(locale_fonts),
        "invalid_locale_fonts": locale_fonts,
        "embedded_font_count": len(embedded_fonts),
        "embedded_fonts": embedded_fonts,
        "portable_cjk_fonts": (
            not locale_fonts
            and required_aliases.issubset(valid_aliases)
        ),
    }


def normalize_docx_fonts(docx_bytes: bytes) -> tuple[bytes, dict[str, Any]]:
    """Normalize aliases, embed document-specific OFL subsets, and audit them."""

    parts = _read_parts(docx_bytes)
    replacements = 0
    for name in list(parts):
        if not name.startswith("word/") or not name.endswith(".xml"):
            continue
        parts[name], count = _normalize_xml(
            parts[name],
            styles=name == "word/styles.xml",
        )
        replacements += count
    _ensure_embedded_font_parts(parts)
    normalized = _write_parts(parts)
    audit = _audit_parts(parts)
    audit["replaced_locale_font_count"] = replacements
    audit["content_hash"] = "sha256:" + hashlib.sha256(normalized).hexdigest()
    if audit["invalid_locale_font_count"]:
        raise DocxFontError("DOCX still contains locale-like font names")
    if BODY_EAST_ASIA_FONT not in audit["east_asia_fonts"]:
        raise DocxFontError("DOCX body East Asian font was not normalized")
    if not audit["portable_cjk_fonts"]:
        raise DocxFontError("DOCX portable CJK font embedding failed")
    return normalized, audit


def audit_docx_fonts(docx_bytes: bytes) -> dict[str, Any]:
    """Audit aliases, embedded font identity, licensing, and CJK coverage."""

    parts = _read_parts(docx_bytes)
    audit = _audit_parts(parts)
    audit["content_hash"] = "sha256:" + hashlib.sha256(docx_bytes).hexdigest()
    return audit
