"""Governed staging limits and the source-files Resource domain.

Single home for the four module constants so no sub-module redefines them.
"""

from __future__ import annotations

from datetime import timedelta


DIRECT_CONTENT_LIMIT = 8 * 1024 * 1024
CHUNK_LIMIT = 4 * 1024 * 1024
SESSION_TTL = timedelta(hours=24)
DOMAIN = "source-files"
