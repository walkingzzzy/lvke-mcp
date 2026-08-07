"""封印：密钥、账本记录与 MAC 计算/校验。"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any

from .base import (
    LEGACY_SEAL_VERSION,
    SEAL_VERSION,
    VERSION,
    _canonical,
    _now_iso,
    _workspace_root,
    compute_fact_pack_hash,
)


_SEAL_LOCK = threading.Lock()


def _seal_secret(workspace_id: str) -> bytes:
    """Return a durable per-workspace HMAC secret (create if missing)."""
    env_secret = (
        os.environ.get("LVKE_FACT_PACK_SEAL_SECRET")
        or ""
    ).strip()
    if env_secret:
        return env_secret.encode("utf-8")
    path = _workspace_root(workspace_id) / ".fact_pack_seal_secret"
    with _SEAL_LOCK:
        if path.is_file():
            raw = path.read_bytes().strip()
            if raw:
                return raw
        secret = secrets.token_bytes(32)
        path.write_bytes(secret)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return secret


def _seal_ledger_path(workspace_id: str) -> Path:
    return _workspace_root(workspace_id) / "fact_pack_seal_ledger.jsonl"


def _record_seal_ledger(
    workspace_id: str,
    *,
    seal_id: str,
    content_hash: str,
    seal_mac: str,
    ceiling: str,
) -> None:
    entry = {
        "seal_id": seal_id,
        "content_hash": content_hash,
        "seal_mac": seal_mac,
        "ceiling": ceiling,
        "sealed_at": _now_iso(),
        "workspace_id": workspace_id,
    }
    path = _seal_ledger_path(workspace_id)
    with _SEAL_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical(entry) + "\n")


def _ledger_has_seal(
    workspace_id: str,
    *,
    seal_id: str,
    content_hash: str,
    seal_mac: str,
) -> bool:
    path = _seal_ledger_path(workspace_id)
    if not path.is_file():
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            str(row.get("seal_id") or "") == seal_id
            and str(row.get("content_hash") or "") == content_hash
            and str(row.get("seal_mac") or "") == seal_mac
        ):
            return True
    return False


def compute_fact_pack_mac(
    pack: dict[str, Any],
    *,
    workspace_id: str,
    content_hash: str | None = None,
) -> str:
    digest = content_hash or compute_fact_pack_hash(pack)
    material = f"{digest}|{workspace_id}|{pack.get('seal_id') or ''}|{pack.get('sealed_at') or ''}"
    secret = _seal_secret(workspace_id)
    return "hmac-sha256:" + hmac.new(
        secret, material.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_fact_pack_seal(
    pack: Any,
    *,
    workspace_id: str | None = None,
    require_ledger: bool = True,
) -> dict[str, Any]:
    issues: list[str] = []
    if not isinstance(pack, dict):
        return {"ok": False, "issues": ["finance_fact_pack 缺失或不是对象"]}
    if pack.get("version") != VERSION:
        issues.append("finance_fact_pack version 非 v1")
    if str(pack.get("confirmation_status") or "") != "confirmed":
        issues.append("finance_fact_pack 未 confirmed")
    seal_version = str(pack.get("seal_version") or "")
    if seal_version not in {SEAL_VERSION, LEGACY_SEAL_VERSION}:
        issues.append("finance_fact_pack 未由服务端 seal")
    if not str(pack.get("sealed_at") or "").strip():
        issues.append("finance_fact_pack 缺 sealed_at")

    # Content hash excludes mac/id so re-verify is stable.
    expected = compute_fact_pack_hash(pack)
    if str(pack.get("fact_pack_hash") or "") != expected:
        issues.append("finance_fact_pack hash 不匹配")

    # Legacy v1 seals (hash-only) are no longer accepted for formal use.
    if seal_version == LEGACY_SEAL_VERSION:
        issues.append("finance_fact_pack seal 为 v1（仅公开 hash），须重新服务端确认")
    elif seal_version == SEAL_VERSION:
        seal_id = str(pack.get("seal_id") or "").strip()
        seal_mac = str(pack.get("seal_mac") or "").strip()
        if not seal_id:
            issues.append("finance_fact_pack 缺 seal_id")
        if not seal_mac:
            issues.append("finance_fact_pack 缺 seal_mac")
        ws = str(
            workspace_id
            or pack.get("seal_workspace_id")
            or pack.get("project_id")
            or ""
        ).strip()
        if not ws:
            issues.append("finance_fact_pack 缺 seal workspace 上下文")
        elif seal_mac and seal_id:
            expected_mac = compute_fact_pack_mac(
                pack, workspace_id=ws, content_hash=expected,
            )
            if not hmac.compare_digest(seal_mac, expected_mac):
                issues.append("finance_fact_pack seal_mac 不匹配")
            elif require_ledger and not _ledger_has_seal(
                ws,
                seal_id=seal_id,
                content_hash=expected,
                seal_mac=seal_mac,
            ):
                issues.append("finance_fact_pack 不在服务端 seal ledger")

    return {"ok": not issues, "issues": issues, "expected_hash": expected}
