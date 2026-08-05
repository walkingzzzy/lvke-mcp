"""Server-side binding of finance-spec evidence to immutable source facts (MCP 版)。

裁剪自 hermes ``finance/evidence_binding.py``：引用收集与绑定判定逻辑原样保留，
仅替换存储根（MCP 自有 source-files 存储与 evidence-pack store）并删除作用域维度、
golden 清单与跨工作区探测（MCP 域内不存在这些概念，相关路径 fail-closed 为
``missing``，与 hermes 在无 manifest 时的公开结果一致）。

finance spec 是断言文档而非证据完整性权威：本模块只接受 spec 中的证据*引用*，
并从当前工作区 source 索引与解析结果重建全部绑定；客户端自带的哈希、解析任务、
复核状态或预构建绑定一律忽略。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from lvke_mcp.adapters.source_files_repository import (
    _load_analysis,
    _load_state,
    _root,
)
from lvke_mcp.runtime.source_reconstruction import (
    SOURCE_RECONSTRUCTED,
    normalize_reconstruction,
    validate_reconstruction_records,
)

BINDING_VERSION = "finance_evidence_binding.v3"

_EV_ID = re.compile(r"^ev_[0-9a-f]{24}$")
_EVP_ID = re.compile(r"^evp_[0-9a-f]{24}$")
_GOLD_ID = re.compile(r"^gold_[0-9a-f]{8,64}$")
_FILE_ID = re.compile(r"^src_[A-Za-z0-9_.-]{1,156}$")
_TERMINAL_PARSE_STATES = {"succeeded", "partial"}
_SUCCESS_EXTRACT_STATES = {"success", "partial"}
_MAX_REFERENCE_LENGTH = 512


@dataclass(frozen=True)
class _Reference:
    source_path: str
    evidence_id: str = ""
    file_id: str = ""
    locator: str = ""
    reference_kind: str = "evidence_id"

    def public(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "source_path": self.source_path,
            "reference_kind": self.reference_kind,
        }
        if self.evidence_id:
            value["evidence_id"] = self.evidence_id
        if self.file_id:
            value["file_id"] = self.file_id
        if self.locator:
            value["locator"] = self.locator
        return value


@dataclass
class _LocalContext:
    state: dict[str, Any]
    analyses: dict[str, dict[str, Any]]
    evidence_index: dict[str, list[tuple[str, dict[str, Any]]]]
    locator_index: dict[tuple[str, str], list[dict[str, Any]]]
    analysis_errors: list[str]


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256_path(path: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            size += len(chunk)
            hasher.update(chunk)
    return hasher.hexdigest(), size


def _issue(
    source_path: str,
    code: str,
    message: str,
    *,
    evidence_id: str = "",
    file_id: str = "",
    locator: str = "",
) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "source_path": source_path,
        "code": code,
        "message": message,
    }
    if evidence_id:
        issue["evidence_id"] = evidence_id
    if file_id:
        issue["file_id"] = file_id
    if locator:
        issue["locator"] = locator
    return issue


def _safe_locator(value: Any) -> str:
    locator = str(value or "").strip()
    if (
        not locator
        or len(locator) > _MAX_REFERENCE_LENGTH
        or any(ord(char) < 32 for char in locator)
    ):
        return ""
    return locator


def _split_evidence_reference(value: str) -> tuple[str, str]:
    base, separator, fragment = value.partition("#")
    return base.strip(), _safe_locator(fragment) if separator else ""


def _json_key_path(base: str, key: Any) -> str:
    encoded = json.dumps(str(key), ensure_ascii=False)
    return f"{base}[{encoded}]"


def _cell_locator(sheet: Any, cell: Any) -> str:
    sheet_text = str(sheet or "").strip()
    cell_text = str(cell or "").strip().upper()
    if not sheet_text or not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]*", cell_text):
        return ""
    return _safe_locator(f"{sheet_text}!{cell_text}")


def _page_locator(value: Any) -> str:
    try:
        page = int(value)
    except (TypeError, ValueError):
        return ""
    return f"page:{page}" if page > 0 else ""


def _identifier_reference(
    value: Any,
    source_path: str,
    *,
    locator: str = "",
    file_id: str = "",
    reconstructed_ids: set[str] | None = None,
) -> tuple[_Reference | None, dict[str, Any] | None]:
    raw = str(value or "").strip()
    if not raw or len(raw) > _MAX_REFERENCE_LENGTH:
        return None, _issue(
            source_path,
            "INVALID_EVIDENCE_REFERENCE",
            "证据引用为空或长度非法",
        )
    evidence_id, fragment = _split_evidence_reference(raw)
    if evidence_id in (reconstructed_ids or set()):
        # The reconstruction record already binds URI, hash, locator and
        # method. Domain objects may reference that record without presenting
        # it as a reviewed ev_* fact.
        return None, None
    if "#" in raw and not fragment:
        return None, _issue(
            source_path,
            "INVALID_EVIDENCE_LOCATOR",
            "证据引用的定位片段非法",
            evidence_id=evidence_id,
        )
    if not (
        _EV_ID.fullmatch(evidence_id)
        or _EVP_ID.fullmatch(evidence_id)
        or _GOLD_ID.fullmatch(evidence_id)
    ):
        return None, _issue(
            source_path,
            "INVALID_EVIDENCE_REFERENCE",
            "证据引用必须是服务端签发的 ev_*、evp_*#candidate_id 或清单中的 gold_* 标识",
        )
    if evidence_id.startswith("evp_") and not re.fullmatch(r"candidate_[0-9]{3,}", fragment):
        return None, _issue(
            source_path,
            "EVIDENCE_CANDIDATE_REQUIRED",
            "evp_* 正式引用必须包含 #candidate_id",
            evidence_id=evidence_id,
        )
    hinted = _safe_locator(locator)
    if locator and not hinted:
        return None, _issue(
            source_path,
            "INVALID_EVIDENCE_LOCATOR",
            "证据定位格式非法",
            evidence_id=evidence_id,
        )
    if fragment and hinted and fragment != hinted:
        return None, _issue(
            source_path,
            "EVIDENCE_LOCATOR_CONFLICT",
            "同一证据引用包含相互冲突的定位",
            evidence_id=evidence_id,
        )
    if file_id and not _FILE_ID.fullmatch(file_id):
        return None, _issue(
            source_path,
            "INVALID_SOURCE_FILE_REFERENCE",
            "原始文件引用格式非法",
            evidence_id=evidence_id,
        )
    return _Reference(
        source_path=source_path,
        evidence_id=evidence_id,
        file_id=file_id,
        locator=fragment or hinted,
        reference_kind=(
            "golden_sample"
            if evidence_id.startswith("gold_")
            else "evidence_pack_candidate"
            if evidence_id.startswith("evp_")
            else "evidence_id"
        ),
    ), None


def _collect_identifier_sequence(
    value: Any,
    source_path: str,
    references: list[_Reference],
    invalid: list[dict[str, Any]],
    reconstructed_ids: set[str] | None = None,
) -> None:
    if not isinstance(value, (list, tuple)):
        invalid.append(_issue(
            source_path,
            "INVALID_EVIDENCE_REFERENCE_LIST",
            "证据引用集合必须是数组",
        ))
        return
    for index, item in enumerate(value):
        item_path = f"{source_path}[{index}]"
        if isinstance(item, Mapping):
            _collect_source_locator(
                item,
                item_path,
                references,
                invalid,
                reconstructed_ids=reconstructed_ids,
            )
            continue
        reference, error = _identifier_reference(
            item,
            item_path,
            reconstructed_ids=reconstructed_ids,
        )
        if error:
            invalid.append(error)
        elif reference:
            references.append(reference)


def _locator_hints(
    value: Mapping[str, Any],
    source_path: str,
    invalid: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    """Return ``(locator, source_path)`` pairs from one source-locator object."""

    explicit = value.get("locator")
    if explicit not in (None, ""):
        locator = _safe_locator(explicit)
        if not locator:
            invalid.append(_issue(
                f"{source_path}.locator",
                "INVALID_EVIDENCE_LOCATOR",
                "证据定位格式非法",
            ))
            return []
        return [(locator, f"{source_path}.locator")]

    cells = value.get("cells")
    if cells is not None:
        if not isinstance(cells, (list, tuple)) or not cells:
            invalid.append(_issue(
                f"{source_path}.cells",
                "INVALID_EVIDENCE_LOCATOR",
                "单元格定位必须是非空数组",
            ))
            return []
        result: list[tuple[str, str]] = []
        for index, cell in enumerate(cells):
            locator = _cell_locator(value.get("sheet"), cell)
            cell_path = f"{source_path}.cells[{index}]"
            if not locator:
                invalid.append(_issue(
                    cell_path,
                    "INVALID_EVIDENCE_LOCATOR",
                    "工作表和单元格定位非法",
                ))
            else:
                result.append((locator, cell_path))
        return result

    if value.get("cell") not in (None, ""):
        locator = _cell_locator(value.get("sheet"), value.get("cell"))
        if not locator:
            invalid.append(_issue(
                f"{source_path}.cell",
                "INVALID_EVIDENCE_LOCATOR",
                "工作表和单元格定位非法",
            ))
            return []
        return [(locator, f"{source_path}.cell")]

    page_value = value.get("page_no", value.get("page"))
    if page_value not in (None, ""):
        locator = _page_locator(page_value)
        if not locator:
            invalid.append(_issue(
                f"{source_path}.page_no",
                "INVALID_EVIDENCE_LOCATOR",
                "页码定位非法",
            ))
            return []
        return [(locator, f"{source_path}.page_no")]

    return []


def _collect_source_locator(
    value: Any,
    source_path: str,
    references: list[_Reference],
    invalid: list[dict[str, Any]],
    reconstructed_ids: set[str] | None = None,
) -> None:
    if not isinstance(value, Mapping):
        invalid.append(_issue(
            source_path,
            "INVALID_SOURCE_LOCATOR",
            "source_locator 必须是对象",
        ))
        return

    hints = _locator_hints(value, source_path, invalid)
    identifier_key = next(
        (key for key in ("evidence_id", "sample_id") if value.get(key)),
        "",
    )
    identifiers: list[tuple[Any, str]] = []
    if identifier_key:
        identifiers.append((value.get(identifier_key), f"{source_path}.{identifier_key}"))
    elif value.get("evidence_ids") is not None:
        evidence_ids = value.get("evidence_ids")
        if not isinstance(evidence_ids, (list, tuple)) or not evidence_ids:
            invalid.append(_issue(
                f"{source_path}.evidence_ids",
                "INVALID_EVIDENCE_REFERENCE_LIST",
                "source_locator.evidence_ids 必须是非空数组",
            ))
            return
        identifiers.extend(
            (item, f"{source_path}.evidence_ids[{index}]")
            for index, item in enumerate(evidence_ids)
        )

    file_id = str(value.get("file_id") or "").strip()
    if file_id and not _FILE_ID.fullmatch(file_id):
        invalid.append(_issue(
            f"{source_path}.file_id",
            "INVALID_SOURCE_FILE_REFERENCE",
            "原始文件引用格式非法",
        ))
        return

    if identifiers:
        # One ev_* identifies one locator.  A gold sample may intentionally be
        # expanded to several approved cells within the same immutable file.
        if len(hints) > 1 and any(str(item[0]).startswith("ev_") for item in identifiers):
            invalid.append(_issue(
                source_path,
                "AMBIGUOUS_EVIDENCE_LOCATOR",
                "单个 ev_* 引用不能同时绑定多个定位",
            ))
            return
        pairs = hints or [("", source_path)]
        for identifier, identifier_path in identifiers:
            for locator, locator_path in pairs:
                reference, error = _identifier_reference(
                    identifier,
                    locator_path if hints else identifier_path,
                    locator=locator,
                    file_id=file_id,
                    reconstructed_ids=reconstructed_ids,
                )
                if error:
                    invalid.append(error)
                elif reference:
                    references.append(reference)
        return

    if file_id:
        if not hints:
            invalid.append(_issue(
                source_path,
                "SOURCE_LOCATOR_REQUIRED",
                "仅按 file_id 引用时必须提供精确页码或单元格定位",
                file_id=file_id,
            ))
            return
        for locator, locator_path in hints:
            references.append(_Reference(
                source_path=locator_path,
                file_id=file_id,
                locator=locator,
                reference_kind="file_locator",
            ))
        return

    invalid.append(_issue(
        source_path,
        "SOURCE_EVIDENCE_ID_REQUIRED",
        "source_locator 缺 evidence_id、sample_id 或 file_id",
    ))


def _collect_references(
    spec: Mapping[str, Any],
    *,
    reconstructed_ids: set[str] | None = None,
) -> tuple[list[_Reference], list[dict[str, Any]]]:
    references: list[_Reference] = []
    invalid: list[dict[str, Any]] = []

    def collect_source_locators(value: Any, base: str) -> None:
        if not isinstance(value, list):
            invalid.append(_issue(base, "INVALID_SOURCE_LOCATOR", "source_locators 必须是数组"))
            return
        for locator_index, locator in enumerate(value):
            _collect_source_locator(
                locator,
                f"{base}[{locator_index}]",
                references,
                invalid,
                reconstructed_ids=reconstructed_ids,
            )

    def collect_row_evidence(row: Mapping[str, Any], row_path: str) -> None:
        if "evidence_ids" in row:
            _collect_identifier_sequence(
                row.get("evidence_ids"),
                f"{row_path}.evidence_ids",
                references,
                invalid,
                reconstructed_ids,
            )
        if "source_locators" in row:
            collect_source_locators(row.get("source_locators"), f"{row_path}.source_locators")

    def collect_rows(container: Any, base: str) -> None:
        if container in (None, ""):
            return
        if not isinstance(container, list):
            invalid.append(_issue(base, "INVALID_EVIDENCE_CONTAINER", "证据所属对象集合必须是数组"))
            return
        for index, row in enumerate(container):
            row_path = f"{base}[{index}]"
            if not isinstance(row, Mapping):
                invalid.append(_issue(row_path, "INVALID_EVIDENCE_CONTAINER", "证据所属项必须是对象"))
                continue
            collect_row_evidence(row, row_path)

    collect_rows(spec.get("project_parties"), "project_parties")

    hotel = spec.get("hotel_operation")
    if isinstance(hotel, Mapping):
        collect_row_evidence(hotel, "hotel_operation")
    elif hotel not in (None, "") and not isinstance(hotel, Mapping):
        invalid.append(_issue("hotel_operation", "INVALID_EVIDENCE_CONTAINER", "hotel_operation 必须是对象"))

    solar = spec.get("solar_operation")
    if isinstance(solar, Mapping):
        collect_row_evidence(solar, "solar_operation")
    elif solar not in (None, "") and not isinstance(solar, Mapping):
        invalid.append(_issue("solar_operation", "INVALID_EVIDENCE_CONTAINER", "solar_operation 必须是对象"))

    lease = spec.get("lease_portfolio")
    if isinstance(lease, Mapping):
        collect_row_evidence(lease, "lease_portfolio")
        collect_rows(lease.get("units"), "lease_portfolio.units")
    elif lease not in (None, ""):
        invalid.append(_issue("lease_portfolio", "INVALID_EVIDENCE_CONTAINER", "lease_portfolio 必须是对象"))

    transaction = spec.get("transaction")
    if isinstance(transaction, Mapping):
        collect_rows(transaction.get("asset_scope"), "transaction.asset_scope")
        collect_rows(transaction.get("red_flags"), "transaction.red_flags")
    elif transaction not in (None, ""):
        invalid.append(_issue("transaction", "INVALID_EVIDENCE_CONTAINER", "transaction 必须是对象"))

    links = spec.get("evidence_links")
    if links not in (None, ""):
        if not isinstance(links, Mapping):
            invalid.append(_issue("evidence_links", "INVALID_EVIDENCE_CONTAINER", "evidence_links 必须是对象"))
        else:
            for field, values in links.items():
                path = _json_key_path("evidence_links", field)
                if isinstance(values, (list, tuple)):
                    _collect_identifier_sequence(
                        values,
                        path,
                        references,
                        invalid,
                        reconstructed_ids,
                    )
                elif isinstance(values, (str, Mapping)):
                    # Be liberal when reading a single reference, but still
                    # record its exact source path.
                    if isinstance(values, Mapping):
                        _collect_source_locator(
                            values,
                            path,
                            references,
                            invalid,
                            reconstructed_ids=reconstructed_ids,
                        )
                    else:
                        reference, error = _identifier_reference(
                            values,
                            path,
                            reconstructed_ids=reconstructed_ids,
                        )
                        if error:
                            invalid.append(error)
                        elif reference:
                            references.append(reference)
                else:
                    invalid.append(_issue(path, "INVALID_EVIDENCE_REFERENCE_LIST", "字段证据必须是引用或引用数组"))

    statements = spec.get("historical_statements")
    if statements not in (None, ""):
        if not isinstance(statements, list):
            invalid.append(_issue(
                "historical_statements",
                "INVALID_EVIDENCE_CONTAINER",
                "historical_statements 必须是数组",
            ))
        else:
            for statement_index, statement in enumerate(statements):
                base = f"historical_statements[{statement_index}]"
                if not isinstance(statement, Mapping):
                    invalid.append(_issue(base, "INVALID_EVIDENCE_CONTAINER", "历史报表项必须是对象"))
                    continue
                if "source_locators" not in statement:
                    continue
                locators = statement.get("source_locators")
                path = f"{base}.source_locators"
                collect_source_locators(locators, path)

    return references, invalid


def _load_local_context(workspace_id: str) -> _LocalContext:
    analyses: dict[str, dict[str, Any]] = {}
    evidence_index: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    locator_index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    analysis_errors: list[str] = []
    state = _load_state(workspace_id)
    for file_id in state.get("files", {}):
        try:
            analysis = _load_analysis(workspace_id, file_id)
        except (OSError, RuntimeError, TypeError, ValueError):
            analysis_errors.append(file_id)
            continue
        if not isinstance(analysis, dict) or not analysis:
            continue
        analyses[file_id] = analysis
        for item in analysis.get("locators") or []:
            if not isinstance(item, dict):
                continue
            evidence_id = str(item.get("evidence_id") or "")
            locator = str(item.get("locator") or "")
            if evidence_id:
                evidence_index.setdefault(evidence_id, []).append((file_id, item))
            if locator:
                locator_index.setdefault((file_id, locator), []).append(item)
    return _LocalContext(
        state=state,
        analyses=analyses,
        evidence_index=evidence_index,
        locator_index=locator_index,
        analysis_errors=analysis_errors,
    )


def _resolve_local_reference(
    context: _LocalContext,
    reference: _Reference,
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    """Return ``(file_id, locator item, error)`` within the target workspace."""

    matches: list[tuple[str, dict[str, Any]]] = []
    if reference.evidence_id:
        matches = list(context.evidence_index.get(reference.evidence_id) or [])
        if reference.file_id:
            matches = [item for item in matches if item[0] == reference.file_id]
        if reference.locator:
            matches = [item for item in matches if item[1].get("locator") == reference.locator]
    elif reference.file_id and reference.locator:
        matches = [
            (reference.file_id, item)
            for item in context.locator_index.get((reference.file_id, reference.locator)) or []
        ]

    if not matches:
        if context.analysis_errors:
            return "", None, _issue(
                reference.source_path,
                "EVIDENCE_INDEX_INVALID",
                "当前工作区证据索引不可完整读取",
                evidence_id=reference.evidence_id,
                file_id=reference.file_id,
                locator=reference.locator,
            )
        return "", None, None
    if len(matches) != 1:
        return "", None, _issue(
            reference.source_path,
            "AMBIGUOUS_EVIDENCE_REFERENCE",
            "证据引用在当前解析结果中不唯一",
            evidence_id=reference.evidence_id,
            file_id=reference.file_id,
            locator=reference.locator,
        )
    file_id, item = matches[0]
    if item.get("evidence_id") and not _EV_ID.fullmatch(str(item.get("evidence_id"))):
        return "", None, _issue(
            reference.source_path,
            "INVALID_EVIDENCE_RECORD",
            "服务端证据记录标识非法",
            file_id=file_id,
        )
    return file_id, item, None


def _validate_local_file_chain(
    workspace_id: str,
    context: _LocalContext,
    file_id: str,
    source_path: str,
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    """Validate source bytes and the current parse attempt (MCP 存储版)。

    Status is one of ``ready``, ``pending`` or ``invalid``.
    """

    record = context.state.get("files", {}).get(file_id)
    if not isinstance(record, dict) or str(record.get("workspace_id") or "") != workspace_id:
        return "invalid", None, _issue(
            source_path, "EVIDENCE_NOT_FOUND", "当前工作区未找到该证据",
        )
    try:
        source_path_on_disk = Path(str(record.get("path") or ""))
    except (KeyError, TypeError, ValueError):
        return "invalid", None, _issue(
            source_path,
            "INVALID_SOURCE_FILE_RECORD",
            "原始文件记录非法",
            file_id=file_id,
        )
    if not source_path_on_disk.exists():
        return "invalid", None, _issue(
            source_path,
            "SOURCE_FILE_MISSING",
            "已登记的原始文件缺失",
            file_id=file_id,
        )
    try:
        source_root = (_root(workspace_id) / "files" / file_id).resolve()
        resolved_source = source_path_on_disk.resolve(strict=True)
        resolved_source.relative_to(source_root)
    except (OSError, RuntimeError, ValueError):
        return "invalid", None, _issue(
            source_path,
            "INVALID_SOURCE_FILE_RECORD",
            "原始文件记录路径非法",
            file_id=file_id,
        )
    if (
        not Path(str(record.get("original_filename") or "")).name
        or source_path_on_disk.is_symlink()
        or not resolved_source.is_file()
    ):
        return "invalid", None, _issue(
            source_path,
            "INVALID_SOURCE_FILE_RECORD",
            "原始文件记录路径非法",
            file_id=file_id,
        )
    try:
        actual_sha256, actual_size = _sha256_path(resolved_source)
        recorded_size = int(record.get("size_bytes"))
    except (OSError, TypeError, ValueError):
        return "invalid", None, _issue(
            source_path,
            "SOURCE_FILE_INTEGRITY_INVALID",
            "无法验证原始文件完整性",
            file_id=file_id,
        )
    recorded_sha256 = str(record.get("sha256") or "")
    if (
        not re.fullmatch(r"[0-9a-f]{64}", recorded_sha256)
        or actual_sha256 != recorded_sha256
        or actual_size != recorded_size
    ):
        return "invalid", None, _issue(
            source_path,
            "SOURCE_FILE_INTEGRITY_MISMATCH",
            "原始文件哈希或大小与登记值不一致",
            file_id=file_id,
        )

    job_id = str(record.get("parse_job_id") or "")
    job = context.state.get("jobs", {}).get(job_id)
    if not job_id or not isinstance(job, dict):
        return "invalid", None, _issue(
            source_path,
            "CURRENT_PARSE_ATTEMPT_INVALID",
            "原始文件缺当前解析任务",
            file_id=file_id,
        )
    if (
        str(job.get("job_id") or "") != job_id
        or str(job.get("file_id") or "") != file_id
        or str(job.get("workspace_id") or "") != workspace_id
    ):
        return "invalid", None, _issue(
            source_path,
            "CURRENT_PARSE_ATTEMPT_INVALID",
            "当前解析任务与原始文件不一致",
            file_id=file_id,
        )
    job_status = str(job.get("status") or "")
    if job_status in {"queued", "running"}:
        return "pending", None, _issue(
            source_path,
            "CURRENT_PARSE_ATTEMPT_PENDING",
            "当前解析任务尚未完成",
            file_id=file_id,
        )
    if job_status not in _TERMINAL_PARSE_STATES:
        return "invalid", None, _issue(
            source_path,
            "CURRENT_PARSE_ATTEMPT_FAILED",
            "当前解析任务未成功完成",
            file_id=file_id,
        )
    try:
        attempt = int(job.get("attempt"))
    except (TypeError, ValueError):
        attempt = 0
    analysis = context.analyses.get(file_id)
    if not isinstance(analysis, dict) or attempt <= 0:
        return "invalid", None, _issue(
            source_path,
            "CURRENT_PARSE_ATTEMPT_INVALID",
            "当前解析结果缺失或尝试号非法",
            file_id=file_id,
        )
    try:
        analysis_size = int(analysis.get("size_bytes"))
    except (TypeError, ValueError):
        analysis_size = -1
    analysis_sha256 = str(analysis.get("sha256") or "")
    verified_sha256 = str(analysis.get("source_verified_sha256") or "")
    try:
        verified_size = int(analysis.get("source_verified_size_bytes"))
    except (TypeError, ValueError):
        verified_size = -1
    if (
        (str(analysis.get("file_id") or "") != file_id if analysis.get("file_id") else False)
        or (analysis_sha256 not in (None, "") and analysis_sha256 != actual_sha256)
        or (analysis_size != -1 and analysis_size != actual_size)
        or (verified_sha256 not in (None, "") and verified_sha256 != actual_sha256)
        or (verified_size != -1 and verified_size != actual_size)
        or (
            str(analysis.get("extract_status") or "") not in _SUCCESS_EXTRACT_STATES
            if analysis.get("extract_status")
            else False
        )
    ):
        return "invalid", None, _issue(
            source_path,
            "CURRENT_PARSE_ATTEMPT_MISMATCH",
            "当前解析结果与任务或原始文件不一致",
            file_id=file_id,
        )
    return "ready", {
        "file_id": file_id,
        "source_sha256": actual_sha256,
        "source_size_bytes": actual_size,
        "parse_job": job_id,
        "attempt": attempt,
    }, None


def _evidence_content_hash(item: Mapping[str, Any]) -> str:
    content = dict(item)
    content.pop("manual_review_status", None)
    content.pop("review_revisions", None)
    content.pop("reviewed_value", None)
    content.pop("reviewer", None)
    content.pop("reviewed_at", None)
    return _canonical_hash(content)


def _bind_local_reference(
    workspace_id: str,
    context: _LocalContext,
    reference: _Reference,
    file_cache: dict[str, tuple[str, dict[str, Any] | None, dict[str, Any] | None]],
) -> tuple[str, dict[str, Any]]:
    file_id, item, resolution_error = _resolve_local_reference(context, reference)
    if resolution_error:
        return "invalid", resolution_error
    if not item:
        # MCP 域内无跨工作区探测：缺失证据统一收敛为 missing（与 hermes 公开结果一致）。
        return "missing", _issue(
            reference.source_path,
            "EVIDENCE_NOT_FOUND",
            "当前工作区未找到该证据",
            evidence_id=reference.evidence_id,
            file_id=reference.file_id,
            locator=reference.locator,
        )

    if file_id not in file_cache:
        file_cache[file_id] = _validate_local_file_chain(
            workspace_id, context, file_id, reference.source_path,
        )
    chain_status, chain, chain_error = file_cache[file_id]
    if chain_status != "ready" or not chain:
        assert chain_error is not None
        # Preserve the current reference's path even when the cached check was
        # first made for another occurrence of the same file.
        error = {**chain_error, "source_path": reference.source_path}
        return chain_status, error

    evidence_id = str(item.get("evidence_id") or "")
    locator = str(item.get("locator") or "")
    if evidence_id != reference.evidence_id and reference.evidence_id:
        return "invalid", _issue(
            reference.source_path,
            "EVIDENCE_ID_MISMATCH",
            "证据定位解析出的标识与引用不一致",
            evidence_id=reference.evidence_id,
            file_id=file_id,
            locator=reference.locator,
        )
    if not _safe_locator(locator) or (reference.locator and locator != reference.locator):
        return "invalid", _issue(
            reference.source_path,
            "EVIDENCE_LOCATOR_MISMATCH",
            "证据定位与当前解析结果不一致",
            evidence_id=evidence_id,
            file_id=file_id,
            locator=reference.locator,
        )
    payload = {
        "binding_version": BINDING_VERSION,
        "source_type": "workspace_source",
        "file_id": file_id,
        "evidence_id": evidence_id,
        "locator": locator,
        "source_sha256": chain["source_sha256"],
        "source_size_bytes": chain["source_size_bytes"],
        "parse_job": chain["parse_job"],
        "attempt": chain["attempt"],
        "evidence_content_hash": _evidence_content_hash(item),
    }
    return "bound", {
        "source_path": reference.source_path,
        **payload,
        "binding_hash": _canonical_hash(payload),
    }


def _assessment_hash(
    bindings: Sequence[Mapping[str, Any]],
    missing: Sequence[Mapping[str, Any]],
    pending: Sequence[Mapping[str, Any]],
    invalid: Sequence[Mapping[str, Any]],
) -> str:
    normalized = {
        "binding_version": BINDING_VERSION,
        "bindings": sorted(
            (
                {
                    "source_path": str(row.get("source_path") or ""),
                    "binding_hash": str(row.get("binding_hash") or ""),
                }
                for row in bindings
            ),
            key=lambda row: (row["source_path"], row["binding_hash"]),
        ),
        "missing": sorted(
            ({key: row.get(key) for key in ("source_path", "code", "evidence_id", "file_id", "locator") if row.get(key)} for row in missing),
            key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True),
        ),
        "pending": sorted(
            ({key: row.get(key) for key in ("source_path", "code", "evidence_id", "file_id", "locator") if row.get(key)} for row in pending),
            key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True),
        ),
        "invalid": sorted(
            ({key: row.get(key) for key in ("source_path", "code", "evidence_id", "file_id", "locator") if row.get(key)} for row in invalid),
            key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True),
        ),
    }
    return _canonical_hash(normalized)


def _bind_evidence_pack_reference(
    workspace_id: str,
    reference: _Reference,
) -> tuple[str, dict[str, Any]]:
    """Bind one server-signed evidence-pack candidate in the same workspace."""

    from lvke_mcp.adapters.data_analysis_repository import EVIDENCE_STORE

    try:
        record = EVIDENCE_STORE.get(workspace_id, reference.evidence_id)
    except ValueError:
        record = None
    if record is None:
        return "missing", _issue(
            reference.source_path,
            "EVIDENCE_NOT_FOUND",
            "当前工作区未找到该证据",
            evidence_id=reference.evidence_id,
            locator=reference.locator,
        )
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    if (
        str(record.get("status") or "") != "ok"
        or not payload.get("server_signed_candidates")
        or not payload.get("formal_evidence_candidate")
    ):
        return "pending", _issue(
            reference.source_path,
            "EVIDENCE_PACK_NOT_FORMAL",
            "证据包尚未达到正式使用资格",
            evidence_id=reference.evidence_id,
            locator=reference.locator,
        )
    matches = [
        item
        for item in payload.get("fact_candidates") or []
        if isinstance(item, dict)
        and str(item.get("candidate_id") or "") == reference.locator
    ]
    if len(matches) != 1:
        return "invalid", _issue(
            reference.source_path,
            "EVIDENCE_CANDIDATE_NOT_FOUND",
            "证据包中未找到唯一候选事实",
            evidence_id=reference.evidence_id,
            locator=reference.locator,
        )
    candidate = matches[0]
    source_id = str(candidate.get("source_id") or "")
    sources = [
        item
        for item in payload.get("sources") or []
        if isinstance(item, dict) and str(item.get("source_id") or "") == source_id
    ]
    locator = candidate.get("locator")
    if (
        len(sources) != 1
        or sources[0].get("formal_use_allowed") is not True
        or candidate.get("formal_use_allowed") is not True
        or not isinstance(locator, dict)
        or not locator
    ):
        return "invalid", _issue(
            reference.source_path,
            "EVIDENCE_CANDIDATE_INVALID",
            "候选事实的来源资格或定位无效",
            evidence_id=reference.evidence_id,
            locator=reference.locator,
        )
    bound_payload = {
        "binding_version": BINDING_VERSION,
        "source_type": "evidence_pack_candidate",
        "file_id": source_id,
        "evidence_id": reference.evidence_id,
        "candidate_id": reference.locator,
        "locator": locator,
        "source_sha256": str(sources[0].get("content_hash") or ""),
        "evidence_pack_content_hash": str(record.get("content_hash") or ""),
        "candidate_hash": _canonical_hash(candidate),
    }
    return "bound", {
        "source_path": reference.source_path,
        **bound_payload,
        "binding_hash": _canonical_hash(bound_payload),
    }


def bind_finance_spec_evidence(
    workspace_id: str,
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    """Reconstruct formal evidence bindings for one finance spec (MCP 版)。

    ``ok`` means every reference was structurally and physically resolvable;
    it may still be awaiting review.  ``formal_ok`` additionally requires all
    exact locators to have an authoritative approval revision.

    MCP 域内不存在 golden 清单与作用域维度：``gold_*`` 引用 fail-closed 为
    ``EVIDENCE_NOT_FOUND``（与 hermes 在无 manifest 时的公开结果一致）。
    """

    bindings: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []

    # A reconstructed source is a first-class process-acceptance input.  It is
    # bound by its immutable URI/hash/locator record, while remaining clearly
    # distinct from certified project evidence.
    reconstructed = []
    if isinstance(spec, Mapping) and str(spec.get("evidence_policy") or "") == SOURCE_RECONSTRUCTED:
        raw_records = spec.get("reconstruction_records")
        reconstruction_errors = validate_reconstruction_records(raw_records)
        if reconstruction_errors:
            invalid.extend({
                "source_path": f"/reconstruction_records/{item.get('index') if item.get('index') is not None else ''}",
                "code": f"SOURCE_RECONSTRUCTION_{item.get('code')}",
                "message": "source_reconstructed 记录缺少 hash、locator、method 或限制字段",
            } for item in reconstruction_errors)
        else:
            reconstructed = [normalize_reconstruction(item) for item in raw_records]
            for item in reconstructed:
                binding = {
                    "binding_version": BINDING_VERSION,
                    "source_type": SOURCE_RECONSTRUCTED,
                    "source_uri": item["source_uri"],
                    "reconstruction_id": item["reconstruction_id"],
                    "locator": item["locator"],
                    "source_sha256": item["content_hash"],
                    "method": item["method"],
                    "original_formula_available": item["original_formula_available"],
                    "limitations": item["limitations"],
                }
                binding["binding_hash"] = _canonical_hash(binding)
                bindings.append(binding)

    if not isinstance(spec, Mapping):
        invalid.append(_issue("$", "INVALID_FINANCE_SPEC", "finance spec 必须是对象"))
        references: list[_Reference] = []
    else:
        references, collection_invalid = _collect_references(
            spec,
            reconstructed_ids={
                str(item.get("reconstruction_id") or "")
                for item in reconstructed
                if item.get("reconstruction_id")
            },
        )
        invalid.extend(collection_invalid)
    if not references and not invalid and not reconstructed:
        missing.append(_issue(
            "$",
            "NO_EVIDENCE_REFERENCES",
            "finance spec 未包含可绑定的证据引用",
        ))

    local_context: _LocalContext | None = None
    local_context_error = False
    if any(reference.evidence_id.startswith("ev_") or reference.file_id for reference in references):
        try:
            local_context = _load_local_context(workspace_id)
        except (OSError, RuntimeError, TypeError, ValueError):
            local_context_error = True

    file_cache: dict[str, tuple[str, dict[str, Any] | None, dict[str, Any] | None]] = {}
    for reference in references:
        if reference.evidence_id.startswith("gold_"):
            # MCP 域内无 golden 数据根：与 hermes 无 manifest 时一致，视为未找到。
            status, result = "missing", _issue(
                reference.source_path,
                "EVIDENCE_NOT_FOUND",
                "当前工作区未找到该证据",
                evidence_id=reference.evidence_id,
                locator=reference.locator,
            )
        elif reference.evidence_id.startswith("evp_"):
            status, result = _bind_evidence_pack_reference(
                workspace_id,
                reference,
            )
        else:
            if local_context is None:
                if local_context_error:
                    invalid.append(_issue(
                        reference.source_path,
                        "SOURCE_EVIDENCE_STATE_INVALID",
                        "当前工作区证据状态不可读取",
                        evidence_id=reference.evidence_id,
                        file_id=reference.file_id,
                        locator=reference.locator,
                    ))
                continue
            status, result = _bind_local_reference(
                workspace_id,
                local_context,
                reference,
                file_cache,
            )
        if status == "bound":
            bindings.append(result)
        elif status in {"missing", "cross_workspace"}:
            missing.append(result)
        elif status == "pending":
            pending.append(result)
        else:
            invalid.append(result)

    bindings.sort(key=lambda row: (
        str(row.get("source_path") or ""),
        str(row.get("evidence_id") or ""),
        str(row.get("locator") or ""),
    ))
    for issues in (missing, pending, invalid):
        issues.sort(key=lambda row: (
            str(row.get("source_path") or ""),
            str(row.get("code") or ""),
        ))

    if invalid:
        status = "invalid"
    elif missing:
        status = "missing"
    elif pending:
        status = "pending"
    else:
        status = "bound"
    ok = not missing and not invalid
    formal_ok = status == "bound" and bool(bindings)
    assessment_hash = _assessment_hash(bindings, missing, pending, invalid)
    return {
        "ok": ok,
        "formal_ok": formal_ok,
        "status": status,
        "binding_version": BINDING_VERSION,
        "bindings": bindings,
        "missing": missing,
        "pending": pending,
        "invalid": invalid,
        "binding_hash": assessment_hash,
        "evidence_policy": SOURCE_RECONSTRUCTED if reconstructed else "formal_evidence",
        "project_fact_certified": not bool(reconstructed),
        "reconstruction_ids": [item.get("reconstruction_id") for item in reconstructed],
    }


__all__ = [
    "BINDING_VERSION",
    "bind_finance_spec_evidence",
]
