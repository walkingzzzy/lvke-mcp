"""lvke-data-acquisition service 拆分：URL 审计与视觉来源捕获。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from lvke_mcp.adapters.data_acquisition_repository import (
    SOURCE_STORE,
    URL_AUDIT_STORE,
    VISUAL_CAPTURE_STORE,
)
from lvke_mcp.runtime.storage import utc_now

from .resources import _resource_failure
from .urls import _audit_display_url, _canonical_discovery_url, _secret_block_reason


def audit_urls(
    workspace_id: str,
    urls: list[str],
    *,
    audit_mode: str = "safety",
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Persist a bounded URL safety/live audit without collecting page content."""

    if audit_mode not in {"safety", "live"}:
        return _resource_failure("url_audit_mode_invalid", "audit_mode 必须为 safety 或 live")
    if not urls:
        return _resource_failure("url_audit_urls_required", "至少需要一个待审计 URL")
    from lvke_mcp.domains.research.extractor import _safe_public_url, _verify_public_url

    bounded_timeout = max(0.1, min(float(timeout_seconds), 30.0))
    checked_at = utc_now()
    results: list[dict[str, Any]] = []
    for index, raw_url in enumerate(urls):
        url = str(raw_url or "").strip()
        display_url = _audit_display_url(url)
        secret_reason = _secret_block_reason(url)
        if secret_reason is not None:
            results.append(
                {
                    "url_index": index,
                    "url": display_url,
                    "canonical_url": None,
                    "url_status": "BLOCKED",
                    "safe_public_target": False,
                    "reason_code": "url_contains_sensitive_value",
                    "checked_at": checked_at,
                }
            )
            continue
        safe = _safe_public_url(url)
        canonical = _canonical_discovery_url(url)
        if not safe:
            results.append(
                {
                    "url_index": index,
                    "url": display_url,
                    "canonical_url": canonical,
                    "url_status": "BLOCKED",
                    "safe_public_target": False,
                    "reason_code": "unsafe_public_url",
                    "checked_at": checked_at,
                }
            )
            continue
        verification: dict[str, Any] = {
            "url_status": "SAFE",
            "url_checked_at": checked_at,
        }
        if audit_mode == "live":
            verification = _verify_public_url(url, timeout=bounded_timeout)
        results.append(
            {
                "url_index": index,
                "url": display_url,
                "canonical_url": canonical,
                "url_status": str(verification.get("url_status") or "UNKNOWN"),
                "safe_public_target": True,
                "reason_code": "",
                "checked_at": str(verification.get("url_checked_at") or checked_at),
                "final_url": str(verification.get("final_url") or ""),
                "peer_ip": str(verification.get("peer_ip") or ""),
            }
        )
    unknown_count = sum(item["url_status"] == "UNKNOWN" for item in results)
    blocked_count = sum(item["url_status"] == "BLOCKED" for item in results)
    status = "partial" if unknown_count else "ok"
    payload = {
        "object_type": "UrlAudit",
        "audit_mode": audit_mode,
        "checked_at": checked_at,
        "timeout_seconds": bounded_timeout,
        "results": results,
        "blocked_count": blocked_count,
        "unknown_count": unknown_count,
        "evidence_boundary": "URL 审计只证明检查时点的安全性/可达性，不固化网页正文，也不授予证据资格。",
    }
    record = URL_AUDIT_STORE.put(
        workspace_id,
        payload,
        producer="lvke-data-acquisition.data_audit_urls",
        status=status,
        basis={
            "audit_mode": audit_mode,
            "urls": [item["url"] for item in results],
            "results": results,
        },
    )
    return {
        "success": status == "ok",
        "business_success": status == "ok",
        "system_success": True,
        "transport_success": True,
        "status": status,
        "url_audit_id": record["object_id"],
        "results": results,
        "blocked_count": blocked_count,
        "unknown_count": unknown_count,
        "resource_uris": [record["resource_uri"]],
        "warnings": (["部分 URL 可达性无法判定"] if unknown_count else []),
        "blockers": [],
        "next_actions": [
            "只对 safe_public_target=true 的 URL 执行浏览器查看或正文采集",
            "需要证据时调用 data_fetch/data_import_external_snapshot 固化正文",
        ],
    }


def get_url_audit(
    workspace_id: str,
    url_audit_id: str,
) -> dict[str, Any]:
    record = URL_AUDIT_STORE.get(workspace_id, url_audit_id)
    if record is None:
        return _resource_failure("url_audit_not_found", "URL 审计不存在或不属于当前作用域")
    return {
        "success": True,
        "business_success": True,
        "system_success": True,
        "transport_success": True,
        "status": "ok",
        "url_audit_id": url_audit_id,
        "url_audit": record["payload"],
        "basis_hash": record["basis_hash"],
        "content_hash": record["content_hash"],
        "resource_uris": [record["resource_uri"]],
        "warnings": [],
        "blockers": [],
        "next_actions": [],
    }


def capture_source_view(
    workspace_id: str,
    *,
    source_snapshot_id: str,
    image_file_id: str,
    url: str,
    viewport: dict[str, Any],
    captured_at: str,
    image_content_hash: str = "",
    page_title: str = "",
) -> dict[str, Any]:
    """Bind a governed screenshot to a source snapshot without operating a browser."""

    snapshot = SOURCE_STORE.get(
        workspace_id, source_snapshot_id
    )
    if snapshot is None:
        return _resource_failure(
            "source_snapshot_not_found", "来源快照不存在或不属于当前作用域"
        )
    snapshot_payload = snapshot.get("payload") or {}
    if _canonical_discovery_url(str(snapshot_payload.get("url") or "")) != _canonical_discovery_url(url):
        return _resource_failure(
            "visual_capture_url_mismatch", "截图 URL 与来源快照 URL 不一致"
        )
    try:
        parsed_time = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00"))
    except ValueError:
        return _resource_failure("visual_capture_time_invalid", "captured_at 不是合法 ISO-8601 时间")
    if parsed_time.tzinfo is None:
        return _resource_failure("visual_capture_time_invalid", "captured_at 必须包含时区")
    width = viewport.get("width")
    height = viewport.get("height")
    scale = viewport.get("device_scale_factor", 1)
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or not 1 <= width <= 10000
        or not 1 <= height <= 10000
        or isinstance(scale, bool)
        or not isinstance(scale, (int, float))
        or not 0.1 <= float(scale) <= 10
    ):
        return _resource_failure("visual_capture_viewport_invalid", "viewport 尺寸或缩放比例无效")
    from lvke_mcp.adapters import source_files_repository

    try:
        _state, file_record = source_files_repository._require_source_record(
            workspace_id, image_file_id
        )
    except source_files_repository.SourceFileError:
        return _resource_failure(
            "visual_capture_file_not_found", "截图文件不存在或不属于当前作用域"
        )
    filename = str(file_record.get("original_filename") or "")
    file_format = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    scan = file_record.get("security_scan") or {}
    if file_format not in {"png", "jpg", "jpeg"} or not scan.get("type_verified"):
        return _resource_failure(
            "visual_capture_file_type_invalid", "截图必须是已通过类型校验的 PNG/JPEG 原始资料"
        )
    actual_hash = "sha256:" + str(file_record.get("sha256") or "").lower().removeprefix("sha256:")
    supplied_hash = str(image_content_hash or actual_hash).lower()
    if not supplied_hash.startswith("sha256:"):
        supplied_hash = "sha256:" + supplied_hash
    if supplied_hash != actual_hash:
        return _resource_failure("visual_capture_hash_mismatch", "截图内容哈希与已固化文件不一致")
    normalized_viewport = {
        "width": width,
        "height": height,
        "device_scale_factor": float(scale),
    }
    payload = {
        "object_type": "VisualSourceCapture",
        "source_snapshot_id": source_snapshot_id,
        "source_snapshot_content_hash": snapshot["content_hash"],
        "image_file_id": image_file_id,
        "image_content_hash": actual_hash,
        "url": _audit_display_url(url),
        "page_title": str(page_title or ""),
        "viewport": normalized_viewport,
        "captured_at": parsed_time.isoformat(),
        "evidence_track": "candidate",
        "formal_use_allowed": False,
        "capture_method": "external_browser_supplied",
        "evidence_boundary": "截图只辅助复核页面状态；正式事实仍须绑定正文快照 locator 和内容哈希。",
    }
    record = VISUAL_CAPTURE_STORE.put(
        workspace_id,
        payload,
        producer="lvke-data-acquisition.data_capture_source_view",
        source_ids=[source_snapshot_id, image_file_id],
        basis={
            "source_snapshot_content_hash": snapshot["content_hash"],
            "image_content_hash": actual_hash,
            "url": payload["url"],
            "viewport": normalized_viewport,
            "captured_at": payload["captured_at"],
        },
    )
    return {
        "success": True,
        "business_success": True,
        "system_success": True,
        "transport_success": True,
        "status": "ok",
        "visual_capture_id": record["object_id"],
        "visual_capture": payload,
        "basis_hash": record["basis_hash"],
        "content_hash": record["content_hash"],
        "formal_use_allowed": False,
        "resource_uris": [
            record["resource_uri"],
            snapshot["resource_uri"],
            f"lvke://source-files/workspaces/{workspace_id}/files/{image_file_id}",
        ],
        "warnings": ["视觉捕获不会自动升级证据资格"],
        "blockers": [],
        "next_actions": ["使用正文 snapshot locator 核对截图中的事实陈述"],
    }


def get_visual_capture(
    workspace_id: str,
    visual_capture_id: str,
) -> dict[str, Any]:
    record = VISUAL_CAPTURE_STORE.get(
        workspace_id, visual_capture_id
    )
    if record is None:
        return _resource_failure(
            "visual_capture_not_found", "视觉来源捕获不存在或不属于当前作用域"
        )
    return {
        "success": True,
        "business_success": True,
        "system_success": True,
        "transport_success": True,
        "status": "ok",
        "visual_capture_id": visual_capture_id,
        "visual_capture": record["payload"],
        "basis_hash": record["basis_hash"],
        "content_hash": record["content_hash"],
        "formal_use_allowed": False,
        "resource_uris": [record["resource_uri"]],
        "warnings": [],
        "blockers": [],
        "next_actions": [],
    }