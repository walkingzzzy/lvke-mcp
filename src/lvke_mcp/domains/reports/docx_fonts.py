"""Deterministic OOXML font normalization for every DOCX export path."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from typing import Any

from lxml import etree


PROCESSOR_VERSION = "docx-font-normalizer.v1"
BODY_EAST_ASIA_FONT = "Songti SC"
HEADING_EAST_ASIA_FONT = "Heiti SC"
WESTERN_FONT = "Times New Roman"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = {"w": _W_NS}
_LOCALE_FONT_RE = re.compile(r"^[a-z]{2,3}(?:[-_][A-Za-z]{2,4})+$")
_FONT_ATTRS = ("ascii", "hAnsi", "eastAsia", "cs")


class DocxFontError(ValueError):
    """Raised when a DOCX cannot be normalized without ambiguity."""


def _w(name: str) -> str:
    return f"{{{_W_NS}}}{name}"


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
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        root = etree.fromstring(data, parser=parser)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise DocxFontError("DOCX OOXML font part is malformed") from exc
    if styles:
        _ensure_style_fonts(root)
    replacements = 0
    for rfonts in root.xpath(".//w:rFonts", namespaces=_NS):
        east_asia = HEADING_EAST_ASIA_FONT if _is_heading_context(rfonts) else BODY_EAST_ASIA_FONT
        for attr in _FONT_ATTRS:
            key = _w(attr)
            value = str(rfonts.get(key) or "").strip()
            if value and _LOCALE_FONT_RE.fullmatch(value):
                replacements += 1
                rfonts.set(key, east_asia if attr == "eastAsia" else WESTERN_FONT)
        rfonts.set(_w("eastAsia"), east_asia)
    return (
        etree.tostring(
            root,
            xml_declaration=True,
            encoding="UTF-8",
            standalone=True,
        ),
        replacements,
    )


def _audit_parts(parts: dict[str, bytes]) -> dict[str, Any]:
    locale_fonts: list[dict[str, str]] = []
    east_asia_fonts: set[str] = set()
    for name, data in parts.items():
        if not name.startswith("word/") or not name.endswith(".xml"):
            continue
        try:
            root = etree.fromstring(data)
        except etree.XMLSyntaxError as exc:
            raise DocxFontError("DOCX OOXML part is malformed") from exc
        for rfonts in root.xpath(".//w:rFonts", namespaces=_NS):
            for attr in _FONT_ATTRS:
                value = str(rfonts.get(_w(attr)) or "").strip()
                if attr == "eastAsia" and value:
                    east_asia_fonts.add(value)
                if value and _LOCALE_FONT_RE.fullmatch(value):
                    locale_fonts.append({"part": name, "attribute": attr, "value": value})
    return {
        "processor_version": PROCESSOR_VERSION,
        "east_asia_fonts": sorted(east_asia_fonts),
        "body_east_asia_font": BODY_EAST_ASIA_FONT,
        "heading_east_asia_font": HEADING_EAST_ASIA_FONT,
        "invalid_locale_font_count": len(locale_fonts),
        "invalid_locale_fonts": locale_fonts,
    }


def normalize_docx_fonts(docx_bytes: bytes) -> tuple[bytes, dict[str, Any]]:
    """Normalize East Asian fonts and return the immutable post-process audit."""

    try:
        with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as source:
            parts = {name: source.read(name) for name in source.namelist()}
    except (zipfile.BadZipFile, OSError) as exc:
        raise DocxFontError("DOCX package is invalid") from exc
    replacements = 0
    for name in list(parts):
        if not name.startswith("word/") or not name.endswith(".xml"):
            continue
        parts[name], count = _normalize_xml(
            parts[name],
            styles=name == "word/styles.xml",
        )
        replacements += count
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, data in parts.items():
            target.writestr(name, data)
    normalized = output.getvalue()
    audit = _audit_parts(parts)
    audit["replaced_locale_font_count"] = replacements
    audit["content_hash"] = "sha256:" + hashlib.sha256(normalized).hexdigest()
    if audit["invalid_locale_font_count"]:
        raise DocxFontError("DOCX still contains locale-like font names")
    if BODY_EAST_ASIA_FONT not in audit["east_asia_fonts"]:
        raise DocxFontError("DOCX body East Asian font was not normalized")
    return normalized, audit


def audit_docx_fonts(docx_bytes: bytes) -> dict[str, Any]:
    """Audit an already normalized DOCX without changing it."""

    try:
        with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as source:
            parts = {name: source.read(name) for name in source.namelist()}
    except (zipfile.BadZipFile, OSError) as exc:
        raise DocxFontError("DOCX package is invalid") from exc
    audit = _audit_parts(parts)
    audit["content_hash"] = "sha256:" + hashlib.sha256(docx_bytes).hexdigest()
    return audit
