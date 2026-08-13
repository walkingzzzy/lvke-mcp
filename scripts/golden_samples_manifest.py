#!/usr/bin/env python3
"""Verify the frozen Lvke golden corpus and govern P0B approval state."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config" / "golden_samples_manifest.json"
EXPECTED_GROUPS = frozenset({"huangyingyan", "finance_templates", "hengli_hotel"})
EXPECTED_SAMPLE_COUNT = 46
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")


class ManifestError(ValueError):
    """A stable, user-actionable golden manifest validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError("FILE_NOT_FOUND", f"文件不存在: {path}") from exc
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise ManifestError("JSON_INVALID", f"JSON 无法读取: {path}") from exc
    if not isinstance(payload, dict):
        raise ManifestError("JSON_OBJECT_REQUIRED", f"JSON 顶层必须是对象: {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _iso_timestamp(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ManifestError("APPROVAL_FIELD_REQUIRED", f"{field} 不能为空")
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManifestError("TIMESTAMP_INVALID", f"{field} 必须是 ISO-8601 时间") from exc
    return text


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_source_path(data_root: Path, relative_path: Any) -> Path:
    text = str(relative_path or "").strip()
    candidate = Path(text)
    if not text or candidate.is_absolute() or ".." in candidate.parts:
        raise ManifestError("P0A_PATH_ESCAPE", f"P0A 路径必须是安全相对路径: {text}")
    root = data_root.resolve()
    unresolved = root / candidate
    current = root
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise ManifestError("P0A_SYMLINK_FORBIDDEN", f"P0A 路径不得包含符号链接: {text}")
    resolved = unresolved.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ManifestError("P0A_PATH_ESCAPE", f"P0A 路径逃逸数据根: {text}") from exc
    return resolved


def validate_p0b(p0b: Any) -> dict[str, Any]:
    if not isinstance(p0b, dict):
        raise ManifestError("P0B_INVALID", "p0b 必须是对象")
    status = str(p0b.get("status") or "")
    expected_results = p0b.get("expected_results")
    last_build = p0b.get("last_passing_build")
    if status == "pending_business_approval":
        if expected_results != [] or last_build is not None:
            raise ManifestError(
                "P0B_PENDING_STATE_INVALID",
                "pending_business_approval 必须保持 expected_results=[] 且 last_passing_build=null",
            )
        return copy.deepcopy(p0b)
    if status != "frozen":
        raise ManifestError("P0B_STATUS_INVALID", "p0b.status 只能是 pending_business_approval 或 frozen")
    if not str(p0b.get("definition") or "").strip():
        raise ManifestError("P0B_DEFINITION_REQUIRED", "冻结 P0B 必须提供 definition")
    if not isinstance(expected_results, list) or not expected_results:
        raise ManifestError("P0B_EXPECTED_RESULTS_REQUIRED", "冻结 P0B 必须提供 expected_results")
    groups: set[str] = set()
    required = {
        "sample_id", "group", "parser", "parser_version", "tolerances",
        "test_cases", "reference_track", "corrected_track", "difference_decisions",
    }
    for index, row in enumerate(expected_results):
        if not isinstance(row, dict) or not required <= set(row):
            raise ManifestError("P0B_RESULT_INVALID", f"expected_results[{index}] 字段不完整")
        for field in ("sample_id", "group", "parser", "parser_version"):
            if not str(row.get(field) or "").strip():
                raise ManifestError("P0B_RESULT_INVALID", f"expected_results[{index}].{field} 不能为空")
        group = str(row["group"])
        groups.add(group)
        if group not in EXPECTED_GROUPS:
            raise ManifestError("P0B_GROUP_INVALID", f"未知 P0B group: {group}")
        if not isinstance(row.get("tolerances"), dict) or not row["tolerances"]:
            raise ManifestError("P0B_RESULT_INVALID", f"expected_results[{index}].tolerances 必须是非空对象")
        if not isinstance(row.get("test_cases"), list) or not row["test_cases"]:
            raise ManifestError("P0B_RESULT_INVALID", f"expected_results[{index}].test_cases 必须是非空数组")
        if not isinstance(row.get("difference_decisions"), list):
            raise ManifestError("P0B_RESULT_INVALID", f"expected_results[{index}].difference_decisions 必须是数组")
        for track_name in ("reference_track", "corrected_track"):
            track = row.get(track_name)
            fields = ("version", "hash", "approval_id", "approved_by", "approved_at")
            if not isinstance(track, dict) or any(not str(track.get(field) or "").strip() for field in fields):
                raise ManifestError("P0B_APPROVAL_INVALID", f"expected_results[{index}].{track_name} 批准字段不完整")
            if not SHA256_RE.fullmatch(str(track["hash"])):
                raise ManifestError("P0B_APPROVAL_HASH_INVALID", f"expected_results[{index}].{track_name}.hash 无效")
            _iso_timestamp(track["approved_at"], f"expected_results[{index}].{track_name}.approved_at")
    if groups != EXPECTED_GROUPS:
        raise ManifestError("P0B_GROUP_COVERAGE_INVALID", "expected_results 必须完整覆盖三组金标")
    if last_build is not None:
        validate_build_record(last_build)
    return copy.deepcopy(p0b)


def _is_empty_skip_value(value: Any) -> bool:
    return value in (None, False, 0, "", [], {})


def validate_build_record(record: Any, *, expected_commit: str = "") -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ManifestError("BUILD_RECORD_INVALID", "构建记录必须是对象")
    for field in ("build_id", "commit_sha", "passed_at", "test_report_sha256"):
        if not str(record.get(field) or "").strip():
            raise ManifestError("BUILD_FIELD_REQUIRED", f"{field} 不能为空")
    commit = str(record["commit_sha"])
    if not COMMIT_RE.fullmatch(commit):
        raise ManifestError("BUILD_COMMIT_INVALID", "commit_sha 必须是完整 Git SHA")
    if expected_commit and commit.lower() != expected_commit.lower():
        raise ManifestError("BUILD_COMMIT_MISMATCH", "构建记录 commit_sha 必须等于当前 Git HEAD")
    _iso_timestamp(record["passed_at"], "passed_at")
    if not SHA256_RE.fullmatch(str(record["test_report_sha256"])):
        raise ManifestError("BUILD_REPORT_HASH_INVALID", "test_report_sha256 必须是 sha256:<64 hex>")
    if record.get("status") != "passed":
        raise ManifestError("BUILD_STATUS_INVALID", "构建记录 status 必须是 passed")
    groups = record.get("groups")
    if not isinstance(groups, list) or set(str(item) for item in groups) != EXPECTED_GROUPS:
        raise ManifestError("BUILD_GROUP_COVERAGE_INVALID", "构建记录必须完整覆盖三组金标")
    for field in ("skipped", "timed_out", "temporary_dependencies"):
        if not _is_empty_skip_value(record.get(field)):
            raise ManifestError("BUILD_SKIP_FORBIDDEN", f"{field} 非空时禁止记录通过构建")
    return copy.deepcopy(record)


def verify_manifest(manifest: dict[str, Any], data_root: Path) -> dict[str, Any]:
    if manifest.get("schema_version") != "golden_samples_manifest.v1":
        raise ManifestError("MANIFEST_VERSION_INVALID", "不支持的 golden manifest 版本")
    p0a = manifest.get("p0a")
    if not isinstance(p0a, dict) or p0a.get("status") != "frozen":
        raise ManifestError("P0A_NOT_FROZEN", "p0a.status 必须是 frozen")
    files = p0a.get("files")
    if not isinstance(files, list) or len(files) != EXPECTED_SAMPLE_COUNT:
        raise ManifestError("P0A_COUNT_INVALID", f"P0A 必须冻结 {EXPECTED_SAMPLE_COUNT} 份原件")
    if p0a.get("sample_count") != len(files):
        raise ManifestError("P0A_DECLARED_COUNT_MISMATCH", "P0A sample_count 与文件表长度不一致")
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    groups: dict[str, int] = {}
    for index, row in enumerate(files):
        if not isinstance(row, dict):
            raise ManifestError("P0A_ENTRY_INVALID", f"p0a.files[{index}] 必须是对象")
        sample_id = str(row.get("sample_id") or "")
        relative_path = str(row.get("relative_path") or "")
        group = str(row.get("group") or "")
        locator = str(row.get("locator") or "")
        if not sample_id or sample_id in seen_ids:
            raise ManifestError("P0A_SAMPLE_ID_INVALID", f"P0A sample_id 缺失或重复: {sample_id}")
        if not relative_path or relative_path in seen_paths:
            raise ManifestError("P0A_PATH_DUPLICATE", f"P0A relative_path 缺失或重复: {relative_path}")
        if group not in EXPECTED_GROUPS:
            raise ManifestError("P0A_GROUP_INVALID", f"未知 P0A group: {group}")
        if not locator:
            raise ManifestError("P0A_LOCATOR_REQUIRED", f"{sample_id} 缺少 locator")
        if not SHA256_RE.fullmatch(str(row.get("sha256") or "")):
            raise ManifestError("P0A_HASH_INVALID", f"{sample_id} sha256 格式无效")
        path = _safe_source_path(data_root, relative_path)
        if not path.is_file():
            raise ManifestError("P0A_FILE_MISSING", f"P0A 原件不存在: {relative_path}")
        size = path.stat().st_size
        if size != row.get("size_bytes"):
            raise ManifestError("P0A_SIZE_MISMATCH", f"P0A 大小变化: {relative_path}")
        if _sha256(path) != row.get("sha256"):
            raise ManifestError("P0A_HASH_MISMATCH", f"P0A 内容变化: {relative_path}")
        seen_ids.add(sample_id)
        seen_paths.add(relative_path)
        groups[group] = groups.get(group, 0) + 1
    if set(groups) != EXPECTED_GROUPS:
        raise ManifestError("P0A_GROUP_COVERAGE_INVALID", "P0A 必须完整覆盖三组金标")
    declared_groups = p0a.get("groups")
    if declared_groups != groups:
        raise ManifestError("P0A_GROUP_COUNT_MISMATCH", "P0A group 计数与文件表不一致")
    validate_p0b(manifest.get("p0b"))
    return {
        "status": "passed",
        "p0a_status": "frozen",
        "p0a_sample_count": len(files),
        "groups": groups,
        "p0b_status": str((manifest.get("p0b") or {}).get("status") or ""),
        "skipped": [],
    }


def _git_head() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, check=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--data-root", type=Path,
        default=Path(os.getenv("LVKE_GOLDEN_DATA_ROOT") or ROOT),
    )
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--verify", action="store_true")
    actions.add_argument("--freeze-p0b", type=Path, metavar="APPROVED.json")
    actions.add_argument("--record-build", type=Path, metavar="PASSED_BUILD.json")
    args = parser.parse_args(argv)

    try:
        manifest = _load_json(args.manifest)
        verification = verify_manifest(manifest, args.data_root)
        action = "verify"
        if args.freeze_p0b:
            approved = _load_json(args.freeze_p0b)
            approved["status"] = "frozen"
            approved.setdefault("last_passing_build", None)
            validate_p0b(approved)
            updated = copy.deepcopy(manifest)
            updated["p0b"] = approved
            _atomic_write(args.manifest, updated)
            verification = verify_manifest(updated, args.data_root)
            action = "freeze-p0b"
        elif args.record_build:
            if str((manifest.get("p0b") or {}).get("status") or "") != "frozen":
                raise ManifestError("P0B_APPROVAL_REQUIRED", "P0B 未冻结，禁止记录通过构建")
            head = _git_head()
            if not head:
                raise ManifestError("GIT_HEAD_UNAVAILABLE", "无法解析当前 Git HEAD")
            record = validate_build_record(_load_json(args.record_build), expected_commit=head)
            updated = copy.deepcopy(manifest)
            updated["p0b"]["last_passing_build"] = record
            _atomic_write(args.manifest, updated)
            verification = verify_manifest(updated, args.data_root)
            action = "record-build"
        print(json.dumps({"success": True, "action": action, **verification}, ensure_ascii=False))
        return 0
    except ManifestError as exc:
        print(json.dumps({"success": False, "code": exc.code, "message": exc.message}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
