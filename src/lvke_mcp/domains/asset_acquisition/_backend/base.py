"""全局锁、日志、恢复池、失败文案常量与数值/百分比格式化原语。"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any




_LOCK = threading.RLock()


_LOG = logging.getLogger(__name__)


_RECOVERY_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="acquisition-recovery")


_DEFAULT_IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60


_UNTRUSTED_EVIDENCE_ASSERTION_KEYS = {
    "binding_hash",
    "source_sha256",
    "source_size_bytes",
    "parse_job",
    "parse_job_id",
    "attempt",
    "evidence_content_hash",
    "integrity_status",
}


_SOURCE_EVIDENCE_FAILURE_MESSAGE = "资料证据状态当前不可验证"


_RUN_VALIDATION_FAILURE_MESSAGE = "资产收购财务输入未通过模型校验"


_RUN_EXECUTION_FAILURE_MESSAGE = "资产收购财务测算执行失败"


_ARTIFACT_GENERATION_FAILURE_MESSAGE = "资产收购正式工件生成失败"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _idempotency_ttl_seconds() -> int:
    try:
        value = int(os.environ.get("LVKE_MCP_IDEMPOTENCY_TTL_SECONDS", ""))
    except (TypeError, ValueError):
        value = _DEFAULT_IDEMPOTENCY_TTL_SECONDS
    return max(1, value)


def _hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _same_optional_number(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    try:
        return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)
    except (TypeError, ValueError):
        return False


def _same_number(actual: str, expected: Any) -> bool:
    try:
        return math.isclose(float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-9)
    except (TypeError, ValueError):
        return False


def _num(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _pct(value: Any) -> str:
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "—"


def _pct_ratio(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "—"
