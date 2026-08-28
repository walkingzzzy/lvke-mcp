#!/usr/bin/env python3
"""G1 live MCP acceptance: 171-tool census + full synthetic golden chain."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import uuid
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(SCRIPTS))

from acceptance_common import call_tool, classify_outcome, object_id_from_payload  # noqa: E402
from g1_golden_chain import ChainStep, run_golden_chain  # noqa: E402
from lvke_mcp.runtime.build_metadata import build_metadata  # noqa: E402
from lvke_mcp.testing.server_manifest import SERVER_SPECS  # noqa: E402

REPORTS = ROOT / "dev-docs" / "reports"
EXPECTED_TOOL_COUNT = 171


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _placeholder(schema: dict[str, Any], name: str, workspace_id: str) -> Any:
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    for branch_key in ("anyOf", "oneOf"):
        branches = schema.get(branch_key)
        if isinstance(branches, list) and branches:
            return _placeholder(branches[0], name, workspace_id)
    declared = schema.get("type")
    if isinstance(declared, list):
        non_null = [item for item in declared if item != "null"]
        declared = non_null[0] if non_null else declared[0]
    if name == "workspace_id":
        return workspace_id
    if declared == "integer":
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        value = 1 if minimum is None else max(int(minimum), 1)
        return min(value, int(maximum)) if maximum is not None else value
    if declared == "number":
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        value = 1.0 if minimum is None else max(float(minimum), 1.0)
        return min(value, float(maximum)) if maximum is not None else value
    if declared == "boolean":
        return False
    if declared == "array":
        item_schema = schema.get("items") or {}
        count = int(schema.get("minItems") or 0)
        return [_placeholder(item_schema, f"{name}_item", workspace_id) for _ in range(count)]
    if declared == "object":
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        if len(required) < int(schema.get("minProperties") or 0):
            required.update(list(props)[: int(schema.get("minProperties") or 0)])
        # Iterate required names rather than properties so schemas that list
        # a required field without an explicit property still receive a
        # syntactically valid placeholder (common in conditional contracts).
        return {
            str(field): _placeholder(props.get(str(field)) or {}, str(field), workspace_id)
            for field in required
        }
    pattern = str(schema.get("pattern") or "")
    if pattern:
        # Hash patterns are common in evidence contracts.  Handle the
        # optional ``sha256:`` prefix before the generic prefix heuristic;
        # otherwise ``(?:sha256:)?`` is incorrectly emitted as a value.
        lower_pattern = pattern.lower()
        if "sha256" in lower_pattern or "[0-9a-f" in lower_pattern:
            return "sha256:" + "0" * 64
        if "lvke://" in lower_pattern:
            if "data-acquisition" in lower_pattern:
                return "lvke://data-acquisition/workspaces/probe/source"
            if "data-analysis" in lower_pattern:
                return "lvke://data-analysis/workspaces/probe/source"
            return "lvke://probe/resource"
        if "drresume" in lower_pattern:
            return "drresume.v1.probe"
        if "^/(?:spec|input_revision)" in lower_pattern:
            return "/spec/input"
        prefix = re.search(r"\^\(([^|)]+)", pattern)
        if prefix:
            return prefix.group(1) + "probe"
    fmt = str(schema.get("format") or "")
    if fmt in {"uri", "uri-reference", "url"}:
        return "https://example.com"
    if fmt == "date":
        return "2026-08-20"
    if "date" in name.lower():
        return "2026-08-20"
    minimum = schema.get("minLength")
    value = "g1-probe-input"
    if minimum is not None:
        value = value.ljust(int(minimum), "x")
    maximum = schema.get("maxLength")
    if maximum is not None:
        value = value[: int(maximum)]
    return value


_SCHEMA_RESOURCE_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


def _read_schema_resource(module: str, uri: str) -> dict[str, Any]:
    """Read a complete schema Resource for compact tools/list projections."""

    key = (module, uri)
    if key in _SCHEMA_RESOURCE_CACHE:
        return _SCHEMA_RESOURCE_CACHE[key]
    from lvke_mcp.testing.protocol_testkit import (
        initialize_message,
        initialized_notification,
        run_raw,
    )

    try:
        responses, _ = run_raw(
            module,
            [
                initialize_message(1, "2025-06-18"),
                initialized_notification(),
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "resources/read",
                    "params": {"uri": uri},
                },
            ],
            timeout=30,
        )
        contents = (responses[-1].get("result") or {}).get("contents") or []
        text = contents[0].get("text") if contents else None
        loaded = json.loads(text) if isinstance(text, str) else {}
        if isinstance(loaded, dict):
            _SCHEMA_RESOURCE_CACHE[key] = loaded
            return loaded
    except Exception:  # noqa: BLE001 - unresolved schema is handled as SKIPPED
        pass
    return {}


def _hydrate_schema(module: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Expand compact x-lvke-schema-uri nodes recursively."""

    if not isinstance(schema, dict):
        return {}
    out = dict(schema)
    uri = str(out.get("x-lvke-schema-uri") or "").strip()
    if uri:
        loaded = _read_schema_resource(module, uri)
        if loaded:
            merged = dict(loaded)
            merged.update({key: value for key, value in out.items() if key != "x-lvke-schema-uri"})
            out = merged
    for key in ("properties",):
        props = out.get(key)
        if isinstance(props, dict):
            out[key] = {
                name: _hydrate_schema(module, value) if isinstance(value, dict) else value
                for name, value in props.items()
            }
    for key in ("items",):
        if isinstance(out.get(key), dict):
            out[key] = _hydrate_schema(module, out[key])
    for key in ("oneOf", "anyOf", "allOf"):
        branches = out.get(key)
        if isinstance(branches, list):
            out[key] = [_hydrate_schema(module, item) if isinstance(item, dict) else item for item in branches]
    return out


def _minimal_payload(
    input_schema: dict[str, Any],
    workspace_id: str,
    *,
    module: str = "",
) -> dict[str, Any]:
    input_schema = _hydrate_schema(module, input_schema) if module else input_schema
    # Public projections may put the actual required fields in a oneOf
    # branch (for example review_disposition_finding).  Select the first
    # concrete branch for a syntactically valid business probe.
    if not input_schema.get("properties") and isinstance(input_schema.get("oneOf"), list):
        for branch in input_schema["oneOf"]:
            if isinstance(branch, dict) and (branch.get("properties") or branch.get("required")):
                input_schema = {**input_schema, **branch}
                break
    properties = input_schema.get("properties") or {}
    payload: dict[str, Any] = {}
    for field_name in input_schema.get("required") or []:
        payload[str(field_name)] = _placeholder(
            properties.get(str(field_name)) or {},
            str(field_name),
            workspace_id,
        )

    # Resolve conditional payload schemas after object_kind is synthesized.
    # This keeps planning probes valid without hard-coding every object type.
    for branch in input_schema.get("allOf") or []:
        if not isinstance(branch, dict):
            continue
        condition = branch.get("if") or {}
        const_kind = (
            ((condition.get("properties") or {}).get("object_kind") or {}).get("const")
        )
        if const_kind and payload.get("object_kind") != const_kind:
            continue
        then = branch.get("then") or {}
        then_props = then.get("properties") or {}
        payload_schema = then_props.get("payload")
        if isinstance(payload_schema, dict) and "payload" in payload:
            payload["payload"] = _placeholder(payload_schema, "payload", workspace_id)
            break
    return payload


def _probe_value_compatible(schema: dict[str, Any], value: Any) -> bool:
    """Avoid injecting manifest fallbacks that violate tool-local enums."""

    enum = schema.get("enum") if isinstance(schema, dict) else None
    return not isinstance(enum, list) or value in enum


def _apply_probe_overrides(tool_name: str, args: dict[str, Any]) -> None:
    """Fill conditional/branch fields that cannot be inferred from compact schemas."""

    zeros = "sha256:" + "0" * 64
    if tool_name == "dr_add_sources" and args.get("sources"):
        args["sources"][0]["resource_uri"] = "lvke://data-acquisition/workspaces/probe/source"
    elif tool_name == "finance_read_analysis_resource":
        args["uri"] = "lvke://finance-model/workspaces/probe/resource"
    elif tool_name == "delivery_transition":
        args["reason"] = "g1 probe cancellation"
    elif tool_name == "planning_prepare":
        args["payload"] = {
            "evidence_pack_id": "g1-probe-input",
            "candidates": [{
                "method": "top_down",
                "market_size": 1.0,
                "unit": "wan",
                "period": "2026",
                "region": "湖北",
                "target_share": 0.1,
                "evidence_bindings": [{
                    "source_id": "g1-probe-input",
                    "source_type": "technical_fixture",
                    "content_hash": zeros,
                    "locator": "probe",
                    "evidence_track": "technical_fixture",
                }],
            }],
        }
    elif tool_name == "planning_create":
        args["payload"] = {
            "market_case_id": "g1-probe-input",
            "revenue_spec": {
                "model": "product_sales",
                "products": [{
                    "name": "probe",
                    "unit": "unit",
                    "price_per_unit": 1.0,
                    "price_unit": "wan",
                    "capacity": 1.0,
                }],
            },
            "op_years": 1,
        }


@dataclass
class ToolRecord:
    server: str
    tool: str
    classification: str
    status: str
    success: bool | None
    system_success: bool | None
    trace_id: str
    code: str
    protocol_error: str
    started_at: str
    finished_at: str
    duration_ms: float
    input_summary: dict[str, Any] = field(default_factory=dict)


def _list_tools(module: str) -> list[dict[str, Any]]:
    from lvke_mcp.testing.protocol_testkit import initialize_message, initialized_notification

    from acceptance_common import PROTOCOL
    from lvke_mcp.testing.protocol_testkit import run_raw

    responses, _ = run_raw(
        module,
        [
            initialize_message(1, PROTOCOL),
            initialized_notification(),
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ],
        timeout=60,
    )
    return responses[1]["result"]["tools"]


def probe_all_tools(workspace_id: str) -> list[ToolRecord]:
    records: list[ToolRecord] = []
    for spec in SERVER_SPECS:
        tools = _list_tools(spec.module)
        for tool in tools:
            name = str(tool.get("name") or "")
            input_schema = tool.get("inputSchema") or {}
            started = _utc_now()
            t0 = time.time()
            args = _minimal_payload(input_schema, workspace_id, module=spec.module)
            if name.endswith("_get") or "missing" in str(spec.probe_arguments):
                # Probe arguments are server-level fallbacks, not a universal
                # tool contract. Inject only fields declared by this tool's
                # input schema; otherwise additionalProperties=false turns a
                # valid business probe into a protocol (-32602) error.
                declared_props = input_schema.get("properties") or {}
                declared = set(declared_props.keys())
                for key, value in spec.probe_arguments.items():
                    if key in declared and _probe_value_compatible(declared_props.get(key) or {}, value):
                        args[key] = value
            _apply_probe_overrides(name, args)
            try:
                payload, protocol_error = call_tool(spec.module, name, args)
            except Exception as exc:  # noqa: BLE001
                payload = {
                    "status": "failed",
                    "success": False,
                    "system_success": False,
                    "code": f"probe_exception.{type(exc).__name__}",
                    "trace_id": f"mcp_{uuid.uuid4().hex}",
                }
                protocol_error = None
            duration = round((time.time() - t0) * 1000, 1)
            records.append(
                ToolRecord(
                    server=spec.name,
                    tool=name,
                    classification=classify_outcome(payload, protocol_error=protocol_error),
                    status=str(payload.get("status") or ""),
                    success=payload.get("success"),
                    system_success=payload.get("system_success"),
                    trace_id=str(payload.get("trace_id") or ""),
                    code=str(payload.get("code") or ""),
                    protocol_error=str(protocol_error or ""),
                    started_at=started,
                    finished_at=_utc_now(),
                    duration_ms=duration,
                    input_summary={k: args[k] for k in list(args)[:4]},
                )
            )
    return records


def write_reports(
    *,
    workspace_id: str,
    tool_records: list[ToolRecord],
    chain_steps: list[ChainStep],
    meta: dict[str, Any],
) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    json_path = REPORTS / f"G1_LIVE_ACCEPTANCE_{stamp}.json"
    md_path = REPORTS / "G1_GOLDEN_CHAIN_ACCEPTANCE.md"

    counts: dict[str, int] = {}
    for rec in tool_records:
        counts[rec.classification] = counts.get(rec.classification, 0) + 1

    chain_counts: dict[str, int] = {}
    for step in chain_steps:
        chain_counts[step.classification] = chain_counts.get(step.classification, 0) + 1

    upstream_tools = [
        f"{rec.server}.{rec.tool}" for rec in tool_records if rec.classification == "UPSTREAM_FAILURE"
    ]
    protocol_tools = [
        f"{rec.server}.{rec.tool}" for rec in tool_records if rec.protocol_error
    ]

    payload = {
        "generated_at": _utc_now(),
        "workspace_id": workspace_id,
        "build_metadata": meta,
        "tool_denominator": len(tool_records),
        "expected_tool_denominator": EXPECTED_TOOL_COUNT,
        "classification_counts": counts,
        "chain_classification_counts": chain_counts,
        "upstream_failure_tools": upstream_tools,
        "protocol_error_tools": protocol_tools,
        "tool_records": [asdict(r) for r in tool_records],
        "golden_chain": [asdict(s) for s in chain_steps],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    chain_pass = chain_counts.get("PASS", 0)
    chain_protocol = sum(1 for step in chain_steps if step.protocol_error)
    chain_upstream = sum(1 for step in chain_steps if step.classification == "UPSTREAM_FAILURE")
    probe_protocol_count = sum(1 for rec in tool_records if rec.protocol_error)
    network_only = (
        counts.get("UPSTREAM_FAILURE", 0) > 0
        and all("data_search" in item for item in upstream_tools)
        and len(protocol_tools) == 0
    )

    lines = [
        "# G1 技术金标链验收报告",
        "",
        f"- **生成时间（UTC）**：{payload['generated_at']}",
        f"- **工作区**：`{workspace_id}`",
        f"- **工具分母**：{len(tool_records)}（预期 {EXPECTED_TOOL_COUNT}，14 服务 live `tools/list`）",
        f"- **金标链步骤数**：{len(chain_steps)}",
        f"- **build_metadata_complete**：{meta.get('build_metadata_complete')}",
        "",
        "## 工具覆盖分类",
        "",
        "| 分类 | 数量 |",
        "|------|------|",
    ]
    for key in ("PASS", "EXPECTED_REJECTION", "UPSTREAM_FAILURE", "SKIPPED"):
        if counts.get(key, 0):
            lines.append(f"| {key} | {counts.get(key, 0)} |")

    lines += [
        "",
        "## 金标链步骤分类",
        "",
        "| 分类 | 数量 |",
        "|------|------|",
    ]
    for key in ("PASS", "EXPECTED_REJECTION", "UPSTREAM_FAILURE", "SKIPPED"):
        if chain_counts.get(key, 0):
            lines.append(f"| {key} | {chain_counts.get(key, 0)} |")

    if upstream_tools or protocol_tools:
        lines += ["", "## 探测异常明细", ""]
        for item in upstream_tools:
            lines.append(f"- UPSTREAM_FAILURE: `{item}`")
        for item in protocol_tools:
            lines.append(f"- PROTOCOL_ERROR: `{item}`")

    lines += [
        "",
        "## Synthetic 金标链（stdio 脚本）",
        "",
        "| 步骤 | 工具 | 分类 | 状态 | object_id | trace_id | 备注 |",
        "|------|------|------|------|-----------|----------|------|",
    ]
    for step in chain_steps:
        tid = step.trace_id[:20] + "…" if step.trace_id else ("—" if not step.protocol_error else step.protocol_error)
        lines.append(
            f"| {step.step} | `{step.tool}` | {step.classification} | {step.status or '—'} | "
            f"{step.object_id or '—'} | `{tid}` | {step.notes or '—'} |"
        )

    lines += [
        "",
        "## G1 退出条件核对",
        "",
        f"- [{'x' if len(tool_records) == EXPECTED_TOOL_COUNT else ' '}] {EXPECTED_TOOL_COUNT} 工具实时调用",
        f"- [{'x' if len(chain_steps) >= 20 else ' '}] 金标链 ≥20 步（含 evidence/planning/report/review）",
        f"- [{'x' if probe_protocol_count == 0 else ' '}] 工具探测无协议错误（-32602）：{probe_protocol_count} 项",
        f"- [{'x' if chain_protocol == 0 and chain_upstream == 0 else ' '}] 金标链无 PROTOCOL_ERROR/UPSTREAM_FAILURE（{chain_pass}/{len(chain_steps)} PASS）",
        f"- [{'x' if counts.get('UPSTREAM_FAILURE', 0) == 0 else ' '}] 工具探测无 UPSTREAM_FAILURE"
        + ("（仅 data_search 网络类可接受）" if network_only else ""),
        f"- [{'x' if meta.get('build_metadata_complete') else ' '}] `build_metadata_complete=true`（须 clean checkout + `--release` 构建）",
        "",
        f"详细 trace：`{json_path.relative_to(ROOT)}`",
        "",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default="", help="workspace_id（默认自动生成）")
    parser.add_argument("--skip-tool-probe", action="store_true", help="仅调试金标链时使用")
    parser.add_argument("--skip-chain", action="store_true")
    parser.add_argument(
        "--allow-network-upstream",
        action="store_true",
        help="data_search 等网络 UPSTREAM_FAILURE 不计入退出失败",
    )
    args = parser.parse_args()

    if args.skip_tool_probe:
        print("warning: --skip-tool-probe 仅用于链调试，不能作为 G1 正式验收", file=sys.stderr)

    workspace_id = args.workspace.strip() or f"g1-golden-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    meta = build_metadata().envelope_fields()

    with tempfile.TemporaryDirectory(prefix="lvke-g1-acceptance-") as tmp:
        data_dir = Path(tmp)
        os.environ["LVKE_MCP_DATA_DIR"] = str(data_dir)
        tool_records = [] if args.skip_tool_probe else probe_all_tools(workspace_id)
        chain_steps = [] if args.skip_chain else run_golden_chain(workspace_id, data_dir)

    write_reports(
        workspace_id=workspace_id,
        tool_records=tool_records,
        chain_steps=chain_steps,
        meta=meta,
    )

    bad_upstream = [r for r in tool_records if r.classification == "UPSTREAM_FAILURE"]
    if args.allow_network_upstream:
        bad_upstream = [r for r in bad_upstream if r.tool != "data_search"]
    chain_bad = [
        s for s in chain_steps
        if s.protocol_error or (s.classification == "UPSTREAM_FAILURE" and s.tool != "data_discover")
    ]
    probe_protocol_count = sum(1 for r in tool_records if r.protocol_error)

    ok = (
        len(tool_records) == EXPECTED_TOOL_COUNT
        and not chain_bad
        and not bad_upstream
        and len(chain_steps) >= 20
        and not args.skip_tool_probe
    )
    print(
        f"tools={len(tool_records)} chain_steps={len(chain_steps)} "
        f"protocol_errors={probe_protocol_count} "
        f"upstream={len(bad_upstream)} chain_bad={len(chain_bad)}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
