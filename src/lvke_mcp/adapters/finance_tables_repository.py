"""Persistence and artifact paths for finance-table packages."""

from __future__ import annotations

from pathlib import Path

from lvke_mcp.runtime.storage import JSONArtifactStore, require_safe_id
from lvke_mcp.runtime.workspace import deliverable_dir

PACKAGE_STORE = JSONArtifactStore(
    "finance-tables", "packages", "ftp", "packages"
)
CSV_EXPORT_STORE = JSONArtifactStore(
    "finance-tables", "csv_exports", "ftc", "csv-exports"
)


def export_root(workspace_id: str, kind: str) -> Path:
    """十三表 CSV / XLSX 的落盘根目录。

    交付物落到仓库 ``lvke产出/``（见 :func:`deliverable_dir`），而不是
    ``~/.lvke``：十三表是需要随仓库留存、复核和签审的正式产出，运行时状态
    才留在 data_root。写入与 :func:`xlsx_path_from_uri` 的反查共用本函数，
    因此两侧自动保持一致。
    """

    return deliverable_dir(
        require_safe_id(workspace_id, "workspace_id"),
        "finance-tables",
        require_safe_id(kind, "export_kind"),
    )


def xlsx_path_from_uri(uri: str) -> Path | None:
    prefix = "lvke://finance-tables/workspaces/"
    if not uri.startswith(prefix) or not uri.endswith("/xlsx"):
        return None
    parts = uri[len(prefix) :].split("/")
    if len(parts) != 4 or parts[1] != "packages" or parts[3] != "xlsx":
        return None
    try:
        workspace_id = require_safe_id(parts[0], "workspace_id")
        package_id = require_safe_id(parts[2], "package_id")
    except ValueError:
        return None
    path = export_root(workspace_id, "xlsx") / f"{package_id}.xlsx"
    return path if path.is_file() else None


def get_xlsx_package(
    workspace_id: str,
    package_id: str,
    uri: str,
) -> tuple[dict, Path, str] | None:
    record = PACKAGE_STORE.get(workspace_id, package_id)
    canonical_uri = PACKAGE_STORE.uri(workspace_id, package_id) + "/xlsx"
    if record is None or uri != canonical_uri:
        return None
    path = xlsx_path_from_uri(uri)
    if path is None:
        return None
    return record, path, canonical_uri