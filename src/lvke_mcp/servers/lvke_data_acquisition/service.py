"""Shared acquisition service used by the MCP adapter and DR internals.

门面模块：实现已按职责拆分到 ``_service/`` 子包，这里统一 re-export，
保持原导入路径与符号（含私有名）稳定可用。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import queue
import re
import threading
import time
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit
from typing import Any

from lvke_mcp.adapters.data_acquisition_repository import (
    COLLECTION_STORE,
    DISCOVERY_STORE,
    RESOURCE_STORES,
    SEARCH_STORE,
    SOURCE_STORE,
    URL_AUDIT_STORE,
    VISUAL_CAPTURE_STORE,
    resolve_resource as resolve_repository_resource,
)
from lvke_mcp.runtime.storage import (
    paginate_resource_entries,
    utc_now,
)

from ._service.audit_capture import (
    audit_urls,
    capture_source_view,
    get_url_audit,
    get_visual_capture,
)
from ._service.constants import (
    _ALLOWED_EXTERNAL_EXTRACT_TOOLS,
    _EXTERNAL_RECEIPT_SECRET_ENV,
    _SEARCH_PROVIDER,
    _SEARCH_RELEVANCE_THRESHOLD,
    _SEARCH_SLOTS,
    _SEARCH_TOKEN_RE,
)
from ._service.discovery import (
    _expand_queries,
    discover,
)
from ._service.resources import (
    _resource_failure,
    list_resources,
    provider_status,
    resolve_resource,
)
from ._service.searching import (
    _bounded_web_search,
    _canonical_search_provider,
    _search_relevance,
    _search_tokens,
    _search_timeout_seconds,
    search,
)
from ._service.snapshots import (
    _collection_failure,
    _external_receipt_message,
    _external_snapshot_url_block_reason,
    _network_safety_decision,
    _trusted_tavily_extract,
    collect,
    fetch,
    import_external_snapshot,
)
from ._service.urls import (
    _audit_display_url,
    _candidate_domain,
    _canonical_discovery_url,
    _matches_domain_rule,
    _secret_block_reason,
)

# 门面模块的公开面。显式声明而不是靠"碰巧 import 了"——API 快照门禁
# (tests/integration/test_refactor_guardrails.py) 要求这些 re-export 保持
# 可达,而 ruff F401 会把它们判成未使用。写成 __all__ 让两个门禁同时成立,
# 也让"哪些名字是刻意对外的"可读。
__all__ = [
    "Any",
    "COLLECTION_STORE",
    "DISCOVERY_STORE",
    "RESOURCE_STORES",
    "SEARCH_STORE",
    "SOURCE_STORE",
    "URL_AUDIT_STORE",
    "VISUAL_CAPTURE_STORE",
    "_ALLOWED_EXTERNAL_EXTRACT_TOOLS",
    "_EXTERNAL_RECEIPT_SECRET_ENV",
    "_SEARCH_PROVIDER",
    "_SEARCH_RELEVANCE_THRESHOLD",
    "_SEARCH_SLOTS",
    "_SEARCH_TOKEN_RE",
    "_audit_display_url",
    "_bounded_web_search",
    "_candidate_domain",
    "_canonical_discovery_url",
    "_canonical_search_provider",
    "_collection_failure",
    "_expand_queries",
    "_external_receipt_message",
    "_external_snapshot_url_block_reason",
    "_matches_domain_rule",
    "_network_safety_decision",
    "_resource_failure",
    "_search_relevance",
    "_search_timeout_seconds",
    "_search_tokens",
    "_secret_block_reason",
    "_trusted_tavily_extract",
    "asyncio",
    "audit_urls",
    "capture_source_view",
    "collect",
    "datetime",
    "discover",
    "fetch",
    "get_url_audit",
    "get_visual_capture",
    "hashlib",
    "hmac",
    "import_external_snapshot",
    "ipaddress",
    "json",
    "list_resources",
    "os",
    "paginate_resource_entries",
    "provider_status",
    "queue",
    "re",
    "resolve_repository_resource",
    "resolve_resource",
    "search",
    "threading",
    "time",
    "urlsplit",
    "urlunsplit",
    "utc_now",
]
