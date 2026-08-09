"""Page-anchored PDF text extraction for the source-files adapter.

Why this lives in ``adapters`` rather than reusing
``domains.research.extractor.extract_pdf_bytes``: the layering rule forbids
``adapters -> domains`` edges (scripts/module_metrics.py ``_FORBIDDEN_LAYER_EDGES``),
and source-file parsing is an adapter concern — the same place
``adapters/spreadsheets`` already parses workbooks.

The research extractor also post-processes text with ``clean_extracted_text``,
which drops every line shorter than 12 characters as suspected navigation
chrome.  That is right for scraped web pages and wrong for a controlled
document: short table cells, headings and numeric rows are exactly the
quotable facts a feasibility study cites.  So this module keeps page text
verbatim apart from whitespace normalization.
"""

from __future__ import annotations

import io
import re
from typing import Any

_SPACE_RE = re.compile(r"[ \t ]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 中文资料的文本层若几乎只剩数字，说明标签以图片形式存在（扫描版，或图文混排
# 导出时只有西文字体带 ToUnicode）。低于此值时数字无法归属到指标名，抽出的候选
# 没有可核对语义 —— 宁可报 needs_ocr，也不让「有数字但无标签」冒充解析成功。
# 阈值取自实测两端：新洲区统计公报（10 页 589 个图片对象，中文标签是图片）整篇
# CJK 占比 0.047、逐页中位 0.000；恒立酒店真文本 PDF 整篇 0.585、逐页最低 0.345。
# 0.10 落在二者之间，对图片 PDF 留 2.1x 余量、对真文本 PDF 留 5.8x 余量。中文
# 统计/可研正文正常在 30%~60%，任何低于 0.10 的文本层都不足以支撑字段归属。
_MIN_CJK_RATIO = 0.10
_MIN_TEXT_FOR_RATIO_CHECK = 200

# OCR 回退参数：2.0 倍渲染在中文小字号上够清晰而不至于让单页耗时失控；0.45 置信度
# 与 research 域实现一致。页数上限防止超长 PDF 把一次导入拖成分钟级。
_OCR_MAX_PAGES = 20
_OCR_ZOOM = 2.0
_OCR_MIN_CONFIDENCE = 0.45


def _normalize_page_text(value: str) -> str:
    """Collapse intra-line whitespace only; never drop short lines."""

    lines = [
        _SPACE_RE.sub(" ", raw.replace(" ", " ")).strip()
        for raw in str(value or "").splitlines()
    ]
    return "\n".join(line for line in lines if line).strip()


def extract_pdf_pages(data: bytes) -> tuple[list[dict[str, Any]], str]:
    """Return ``(pages, degraded_reason)`` for a PDF byte string.

    ``pages`` entries carry ``page`` (1-based), ``text`` and the character
    offsets of that page within the concatenated document text, so a citation
    can be traced back to a page rather than to an opaque blob.

    A non-empty ``degraded_reason`` means no quotable text was recovered and the
    caller must not report the parse as fully succeeded.
    """

    try:
        from pypdf import PdfReader
    except ImportError:
        return [], "pdf_extractor_unavailable"
    try:
        reader = PdfReader(io.BytesIO(data))
        raw_pages = list(reader.pages)
    except Exception as exc:  # noqa: BLE001 - malformed/encrypted PDF is a gap
        return [], f"pdf_open_failed:{type(exc).__name__}"

    pages: list[dict[str, Any]] = []
    offset = 0
    for page_no, page in enumerate(raw_pages, 1):
        try:
            text = _normalize_page_text(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - one bad page must not lose the rest
            continue
        if not text:
            continue
        start = offset
        offset += len(text) + 1
        pages.append(
            {
                "page": page_no,
                "text": text,
                "start_offset": start,
                "end_offset": offset - 1,
            }
        )
    if not pages:
        # Scanned/image-only PDF: honest degradation, never a silent success.
        return [], "pdf_no_text_layer"
    # 标签质量门：文本层存在但几乎只剩数字 → 中文标签是图片，数字无法归属指标名。
    combined = "\n".join(str(item.get("text") or "") for item in pages)
    if len(combined) >= _MIN_TEXT_FOR_RATIO_CHECK:
        cjk_count = len(_CJK_RE.findall(combined))
        if cjk_count / len(combined) < _MIN_CJK_RATIO:
            # 文本层不足以支撑字段归属，先尝试 OCR 把中文标签取回来。
            ocr_pages, ocr_reason = extract_pdf_pages_ocr(data)
            if ocr_pages:
                return ocr_pages, ""
            # OCR 不可用或没认出东西：仍然返回 pages（数字本身可核对，调用方可在
            # 人工复核下引用），但 degraded_reason 会阻止它被当作解析成功而进入
            # 自动字段抽取。附上 OCR 失败原因，否则上层只见「缺标签」无从判断根因。
            return pages, (
                f"pdf_text_layer_lacks_labels:{ocr_reason}"
                if ocr_reason
                else "pdf_text_layer_lacks_labels"
            )
    return pages, ""


def extract_pdf_pages_ocr(
    data: bytes,
    *,
    max_pages: int = _OCR_MAX_PAGES,
) -> tuple[list[dict[str, Any]], str]:
    """OCR 渲染页位图取回文字，返回 ``(pages, failure_reason)``。

    中文政府公报常用 Identity-H 子集化字体且 ToUnicode 往中文字形缺映射，文本层
    只剩数字；标签以图形存在，只有 OCR 能取回。依赖是可选 extras
    （``pip install -e ".[deep-research-ocr]"``），缺失时返回失败原因而非抛错 ——
    调用方据此保留文本层结果，不因为拿不到更好的正文就让整次解析失败。

    与 ``domains.research.extractor.extract_pdf_ocr`` 是同一技术的两份实现：分层
    规则禁止 ``adapters -> domains``，且此处按 ``_normalize_page_text`` 保留短行
    （受控文档的表格单元格与标题正是可引用事实），不做导航裁剪。
    """

    try:
        import fitz
        import numpy as np
        from PIL import Image
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        return [], f"ocr_unavailable:{exc.name or 'dependency'}"

    try:
        engine = RapidOCR()
        document = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - OCR 栈异常不得炸掉整次解析
        return [], f"ocr_init_failed:{type(exc).__name__}"

    pages: list[dict[str, Any]] = []
    offset = 0
    try:
        for page_index in range(min(len(document), max(1, int(max_pages)))):
            try:
                page = document[page_index]
                pixmap = page.get_pixmap(matrix=fitz.Matrix(_OCR_ZOOM, _OCR_ZOOM), alpha=False)
                image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
                result, _ = engine(np.asarray(image))
            except Exception:  # noqa: BLE001 - 单页失败不能丢掉其余页
                continue
            lines = [
                str(item[1]).strip()
                for item in (result or [])
                if len(item) >= 3
                and str(item[1]).strip()
                and float(item[2]) >= _OCR_MIN_CONFIDENCE
            ]
            text = _normalize_page_text("\n".join(lines))
            if not text:
                continue
            start = offset
            offset += len(text) + 1
            pages.append(
                {
                    "page": page_index + 1,
                    "text": text,
                    "start_offset": start,
                    "end_offset": offset - 1,
                    "source": "ocr",
                }
            )
    finally:
        try:
            document.close()
        except Exception:  # noqa: BLE001
            pass
    if not pages:
        return [], "ocr_no_text_recognized"
    return pages, ""
