"""Persistence and file resolution for zero-material delivery reports.

The technical-report store and its on-disk layout live here rather than in
``servers/lvke_zero_material_delivery`` so that other servers (deliverable
review resolves and audits preview reports) can reach this state through the
shared adapter layer instead of importing another server's internals.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lvke_mcp.runtime.storage import JSONArtifactStore, require_safe_id
from lvke_mcp.runtime.workspace import deliverable_dir

REPORT_STORE = JSONArtifactStore(
    "zero-material-delivery", "technical_reports", "zmrep", "reports"
)

REPORT_FILENAMES = ("report.md", "report.docx")


def artifact_root(workspace_id: str) -> Path:
    """零材料交付研报（MD/DOCX）落盘根，统一到仓库 ``lvke产出/``。"""
    return deliverable_dir(
        require_safe_id(workspace_id, "workspace_id"),
        "zero-material-delivery",
        "artifacts",
    )


def resolve_report_file(
    uri: str,
    *,
    report_store: Any,
) -> tuple[bytes, str] | None:
    marker = "/files/"
    if marker not in uri:
        return None
    base, name = uri.rsplit(marker, 1)
    if name not in set(REPORT_FILENAMES):
        return None
    record = report_store.resolve_uri(base)
    if record is None:
        return None
    path = (
        artifact_root(str(record["workspace_id"]))
        / str(record["object_id"])
        / name
    )
    if not path.is_file():
        return None
    mime = (
        "text/markdown; charset=utf-8"
        if name.endswith(".md")
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return path.read_bytes(), mime


__all__ = ["REPORT_STORE", "REPORT_FILENAMES", "artifact_root", "resolve_report_file"]
