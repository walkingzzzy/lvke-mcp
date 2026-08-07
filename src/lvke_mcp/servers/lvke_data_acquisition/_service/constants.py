"""lvke-data-acquisition service 拆分：共享常量与 Store 单例。

本模块是所有子模块的公共底座。``_SEARCH_SLOTS`` 信号量只能定义在这里，
任何子模块都不得另建副本，否则并发上限会失效。
"""

from __future__ import annotations

import re
import threading

from lvke_mcp.adapters.data_acquisition_repository import (
    COLLECTION_STORE,
    DISCOVERY_STORE,
    RESOURCE_STORES,
    SEARCH_STORE,
    SOURCE_STORE,
    URL_AUDIT_STORE,
    VISUAL_CAPTURE_STORE,
)

_ALLOWED_EXTERNAL_EXTRACT_TOOLS = frozenset({
    ("tavily", "tavily_extract"),
    ("tavily-hikari", "tavily_extract"),
})
_EXTERNAL_RECEIPT_SECRET_ENV = "LVKE_EXTERNAL_EXTRACT_RECEIPT_SECRET"
_SEARCH_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}")
_SEARCH_RELEVANCE_THRESHOLD = 0.25
_SEARCH_SLOTS = threading.BoundedSemaphore(4)
_SEARCH_PROVIDER = "tavily-hikari"