#!/usr/bin/env python3
"""Inventory historical acquisition staging directories without mutating them."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _directory_size(path: Path) -> tuple[int, int]:
    files = 0
    size = 0
    for candidate in path.rglob("*"):
        if candidate.is_file() and not candidate.is_symlink():
            files += 1
            size += candidate.stat().st_size
    return files, size


def audit(deliverable_root: Path) -> dict[str, Any]:
    root = deliverable_root.resolve()
    rows: list[dict[str, Any]] = []
    if root.is_dir():
        for path in sorted(root.rglob(".*.staging-*")):
            if not path.is_dir() or path.is_symlink():
                continue
            relative = path.resolve().relative_to(root).as_posix()
            parts = Path(relative).parts
            workspace_id = parts[0] if parts else ""
            file_count, size_bytes = _directory_size(path)
            rows.append({
                "relative_path": relative,
                "workspace_id": workspace_id,
                "file_count": file_count,
                "size_bytes": size_bytes,
                "classification": "historical_preexisting_staging",
                "acceptance_eligible": False,
                "action": "retained_not_deleted",
            })
    return {
        "schema_version": "asset-staging-audit.v1",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        ),
        "deliverable_root": str(root),
        "historical_staging_count": len(rows),
        "deleted_count": 0,
        "acceptance_artifact_count": 0,
        "directories": rows,
    }


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deliverable-root", type=Path, default=ROOT / "lvke产出")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "quality" / "historical_asset_staging_audit.json",
    )
    args = parser.parse_args()
    payload = audit(args.deliverable_root)
    _write_atomic(args.output, payload)
    print(json.dumps({
        "success": True,
        "historical_staging_count": payload["historical_staging_count"],
        "deleted_count": 0,
        "output": str(args.output.resolve()),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
