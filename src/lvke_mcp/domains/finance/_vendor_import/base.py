"""版本/上限常量、错误类型与单元格坐标、数值、文本归一化原语。"""

from __future__ import annotations

import math
import re
from typing import Any, Optional


REFERENCE_PACK_VERSION = "vendor_reference.v2"


MAX_ROWS = 500


MAX_COLS = 60


IRR_RESIDUAL_TOL_WAN = 0.1


class VendorImportError(RuntimeError):
    """Raised when a vendor workbook cannot be read as a reference pack."""


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _col_letter(index: int) -> str:
    out = ""
    value = int(index)
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        out = chr(65 + remainder) + out
    return out


_CELL_RE = re.compile(r"^([A-Z]{1,3})(\d+)$")


def _col_index(letter: str) -> int:
    out = 0
    for ch in letter:
        out = out * 26 + ord(ch) - 64
    return out


def _cell_parts(cell: str) -> tuple[int, int]:
    match = _CELL_RE.match(str(cell).replace("$", ""))
    if not match:
        return 0, 0
    return int(match.group(2)), _col_index(match.group(1))


def _to_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _norm(value: Any) -> str:
    return re.sub(r"[\s　]+", "", str(value or "")).replace("：", ":")
