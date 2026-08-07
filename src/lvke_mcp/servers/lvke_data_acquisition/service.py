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