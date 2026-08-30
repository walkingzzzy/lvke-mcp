#!/usr/bin/env python3
"""阶段0 基线样本捕获：为独立化版本抓取「可回放成功样本」。

按 `dev-docs/architecture/MCP_INDEPENDENCE_PLAN.md` §29.2 的步骤，走 stdio MCP 线协议调用真实工具，
把外部行为固化为 tests/fixtures/baseline/{finance,finance-tables,report,research}/ 下的
golden fixtures。这些文件是独立化后重新运行时可机器对照的行为基准。

规则：
  - 每个样本以 protocolVersion=2025-11-25 握手（与 freeze_baseline.py 一致）。
  - 核心成功样本从空 MCP 数据目录构建，必须完成写入、对象 ID/哈希校验与资源回读。
  - golden 保存稳定语义投影，剥离随机对象 ID、时间戳和运行实例字段，保证可字节比较。
  - partial/estimate_preview 必须保留其非正式语义；blocked/error 样本不能计入成功 golden。

用法：
    python scripts/capture_samples.py --core-only --output /tmp/lvke-baseline
    python scripts/capture_samples.py --data-dir ~/.lvke
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_ROOT = REPO_ROOT
PYTHON = sys.executable
BASELINE = MCP_ROOT / "tests" / "fixtures" / "baseline"
PROTOCOL_VERSION = "2025-11-25"
FIXTURE_VALUATION_DATE = "2026-01-15"
CORE_FIXTURE_WORKSPACE = "baseline-core-v2"


@dataclass(frozen=True)
class CoreContext:
    workspace_id: str = CORE_FIXTURE_WORKSPACE
    run_id: str = ""
    finance_tables_package_id: str = ""
    research_package_id: str = ""
    evidence_pack_id: str = ""


# 兼容非 core-only 历史样本；核心成功链不读取这些既有对象。
CASE01 = "case01-hubei-pv"
RUN01 = "run_accc8c053bd8"
FTP01 = "ftp_136398d8bf4952051b68ba50"
FTP01_URI = (
    f"lvke://finance-tables/workspaces/{CASE01}/packages/{FTP01}"
)
XLSX01 = (
    Path.home()
    / ".lvke"
    / "workspaces"
    / CASE01
    / "mcp_objects"
    / "finance-tables"
    / "xlsx"
    / f"{FTP01}.xlsx"
)
REPORT_WS = "mcp-stress-20260730-cc01-generic"
DR_WS = "ws-dr-audit-deepassist"


class Sample:
    def __init__(self, sid: str, domain: str, server: str, tool: str | None, params: dict, note: str = ""):
        self.id = sid
        self.domain = domain
        self.server = server
        self.tool = tool
        self.params = params
        self.note = note
        self.status = "pending"
        self.file = None
        self.sha256 = None
        self.detail = ""

    def to_manifest(self) -> dict:
        return {
            "id": self.id,
            "domain": self.domain,
            "server": self.server,
            "tool": self.tool,
            "params": self.params,
            "status": self.status,
            "file": self.file,
            "sha256": self.sha256,
            "detail": self.detail,
            "note": self.note,
        }


class Runner:
    def __init__(self, baseline: Path = BASELINE, data_dir: Path | None = None):
        self.samples: list[Sample] = []
        self.baseline = baseline
        self.data_dir = data_dir

    def _server_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.data_dir is not None:
            env["LVKE_MCP_DATA_DIR"] = str(self.data_dir)
        return env

    # ---- transport 原语 -------------------------------------------------
    def _call(self, proc, payload, timeout: float = 60.0):
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                return None
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == payload.get("id"):
                return msg
        return None

    def open_server(self, server: str):
        proc = subprocess.Popen(
            [PYTHON, "-m", f"lvke_mcp.servers.{server}.server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(REPO_ROOT),
            env=self._server_env(),
        )
        init = self._call(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "capture-samples", "version": "1.0.0"},
            },
        })
        if init is None or "result" not in init:
            proc.kill()
            return None, init
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        proc.stdin.flush()
        return proc, init

    def call_tool_on(self, proc, tool: str, params: dict, request_id: int = 2,
                     timeout: float = 120.0) -> dict:
        resp = self._call(proc, {
            "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
            "params": {"name": tool, "arguments": params},
        }, timeout=timeout)
        if resp is None:
            return {"ok": False, "error": "no tools/call response"}
        if "result" not in resp:
            return {"ok": False, "error": json.dumps(resp, ensure_ascii=False)[:300]}
        return {"ok": True, "result": resp["result"]}

    def call_tool(self, server: str, tool: str, params: dict, timeout: float = 120.0):
        proc, init = self.open_server(server)
        if proc is None:
            return {"ok": False, "error": f"initialize failed: {init}"}
        try:
            return self.call_tool_on(proc, tool, params, timeout=timeout)
        finally:
            proc.kill()

    def call_tool_raw(self, server: str, tool: str, params: dict, timeout: float = 120.0):
        """返回原始 JSON-RPC 响应消息（含 error），用于捕获错误响应基线。"""
        proc, init = self.open_server(server)
        if proc is None:
            return {"jsonrpc": "2.0", "id": 2, "error": {"code": -32000, "message": f"initialize failed: {init}"}}
        try:
            resp = self._call(proc, {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": tool, "arguments": params},
            }, timeout=timeout)
            return resp or {"jsonrpc": "2.0", "id": 2, "error": {"code": -32000, "message": "no response"}}
        finally:
            proc.kill()

    @staticmethod
    def inner_text(resp: dict) -> str:
        """从 tool result 提取 content[].text 拼接文本（无则返回 ""）。"""
        result = resp.get("result") or {}
        content = result.get("content")
        if isinstance(content, list):
            return "".join(c.get("text", "") for c in content)
        return ""

    @staticmethod
    def inner_payload(resp: dict) -> dict | None:
        """解析 content[].text 为 inner transport payload；非 JSON 返回 None。"""
        text = Runner.inner_text(resp).strip()
        if not text.startswith("{"):
            return None
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return None
        return obj if isinstance(obj, dict) else None

    def list_resources(self, server: str, tool: str, params: dict):
        """返回 (ok, items)。items 为 resource 列表（content-text JSON 或 result 直传皆可）。"""
        resp = self.call_tool(server, tool, params)
        if not resp.get("ok"):
            return resp
        # 当前服务统一走 content[].text JSON：优先解析其 resources / data.resources
        inner = self.inner_payload(resp)
        if inner is not None:
            for cand in (inner.get("resources"),
                         (inner.get("data") or {}).get("resources")):
                if isinstance(cand, list):
                    return {"ok": True, "items": cand}
            return {"ok": True, "raw_text": Runner.inner_text(resp), "items": []}
        result = resp.get("result") or {}
        # 兼容直接 result 形态
        if isinstance(result.get("data"), dict) and isinstance(result["data"].get("resources"), list):
            return {"ok": True, "items": result["data"]["resources"]}
        if isinstance(result.get("resources"), list):
            return {"ok": True, "items": result["resources"]}
        return {"ok": True, "items": []}

    # ---- 通用辅助 --------------------------------------------------------
    @staticmethod
    def domain_of(resp: dict):
        """返回 tool-result 的业务载荷：若 result.data 直传则取 data，否则返回完整
        tool-result envelope（业务 payload 在 content[].text 的 inner JSON 中）。"""
        result = resp.get("result") or {}
        if isinstance(result.get("data"), (dict, list)):
            return result["data"]
        return result

    def save_json(self, sample: Sample, obj, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        sample.file = str(path.relative_to(self.baseline))
        sample.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        sample.status = "ok"

    def save_bytes(self, sample: Sample, src: Path, dst: Path):
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        sample.file = str(dst.relative_to(self.baseline))
        sample.sha256 = hashlib.sha256(dst.read_bytes()).hexdigest()
        sample.status = "ok"

    def record_defect(self, sample: Sample, detail: str):
        sample.status = "defect"
        sample.detail = detail


def _status(payload: dict | None) -> str:
    return str((payload or {}).get("status") or "")


def _data(payload: dict | None) -> dict:
    value = (payload or {}).get("data")
    return value if isinstance(value, dict) else {}


def _hash_present(value: object) -> bool:
    text = str(value or "")
    return text.startswith("sha256:") and len(text) == 71


def capture_finance_run(runner: Runner, context: CoreContext) -> CoreContext:
    sample = Sample(
        "finance-run-roundtrip",
        "finance",
        "lvke_finance_model",
        "finance_prepare_spec→finance_confirm_spec→finance_run_model→finance_get_run",
        {"workspace_id": context.workspace_id, "mode": "estimate_preview"},
        note="空 MCP 数据目录中的确定性技术基线；非正式交付样本",
    )
    runner.samples.append(sample)
    spec = {
        "version": "finance_spec.v2",
        "industry": "generic",
        "invest_type": "new_build",
        "selected_scenario_id": "base",
        "revenue": {
            "model": "product_sales",
            "annual_revenue_wan": 3000.0,
            "products": [{
                "name": "技术基线产品",
                "unit": "项",
                "capacity": 30000.0,
                "price_per_unit": 1000.0,
                "price_unit": "yuan",
                "ramp": [1.0] * 12,
                "var_cost_rate": 0.55,
            }],
        },
        "cost": {"total_cost_rate": 0.55, "wage_rate": 0.15, "salvage_rate": 0.05},
        "tax": {
            "income_tax_rate": 0.25,
            "tax_holiday_years": 0,
            "tax_half_years": 0,
            "vat_rate": 0.13,
            "vat_input_rate": 0.10,
            "surtax_rate": 0.01,
        },
        "custom": [],
        "assumptions": ["technical baseline fixture"],
        "source_hint": "user_edited",
        "confirmation_status": "candidate",
        "field_sources": {},
    }
    inputs = {
        "total_investment_wan": 10000.0,
        "annual_revenue_wan": 3000.0,
        "is_operating": True,
        "capital_own_wan": 4000.0,
        "loan_wan": 6000.0,
        "loan_rate": 0.045,
        "loan_years": 8,
        "loan_repay_method": "equal_principal",
        "calc_period_years": 12,
        "build_period_months": 12,
        "depreciation_years": 10,
        "invest_breakdown": {
            "construction_wan": 9700.0,
            "interest_wan": 300.0,
            "construction_items": [
                {
                    "name": "建筑工程",
                    "category": "civil",
                    "unit": "项",
                    "quantity": 1.0,
                    "indicator_yuan": 25_000_000.0,
                    "amount_wan": 2500.0,
                },
                {
                    "name": "设备及工器具购置",
                    "category": "equipment",
                    "unit": "项",
                    "quantity": 1.0,
                    "indicator_yuan": 47_000_000.0,
                    "amount_wan": 4700.0,
                },
                {
                    "name": "安装工程",
                    "category": "installation",
                    "unit": "项",
                    "quantity": 1.0,
                    "indicator_yuan": 10_000_000.0,
                    "amount_wan": 1000.0,
                },
            ],
            "other_wan": 1000.0,
            "reserve_wan": 500.0,
            "working_capital_wan": 0.0,
        },
        "cost_items": {"annual_operating_cost": 1500.0},
    }
    proc, init = runner.open_server("lvke_finance_model")
    if proc is None:
        runner.record_defect(sample, f"initialize failed: {init}")
        return context
    try:
        prepare = runner.inner_payload(runner.call_tool_on(
            proc,
            "finance_prepare_spec",
            {"workspace_id": context.workspace_id, "spec": spec, "input_revision": inputs},
            request_id=2,
        ))
        candidate_id = str((prepare or {}).get("spec_id") or "")
        confirm = runner.inner_payload(runner.call_tool_on(
            proc,
            "finance_confirm_spec",
            {
                "workspace_id": context.workspace_id,
                "spec_id": candidate_id,
                "idempotency_key": "baseline-finance-confirm-v1",
            },
            request_id=3,
        ))
        confirmed_id = str((confirm or {}).get("spec_id") or "")
        run = runner.inner_payload(runner.call_tool_on(
            proc,
            "finance_run_model",
            {
                "workspace_id": context.workspace_id,
                "spec_id": confirmed_id,
                "mode": "estimate_preview",
                "valuation_date": FIXTURE_VALUATION_DATE,
                "idempotency_key": "baseline-finance-run-v1",
            },
            request_id=4,
            timeout=180.0,
        ))
        run_id = str((run or {}).get("run_id") or "")
        read = runner.inner_payload(runner.call_tool_on(
            proc,
            "finance_get_run",
            {"workspace_id": context.workspace_id, "run_id": run_id, "view": "full"},
            request_id=5,
        ))
    finally:
        proc.kill()

    run_data = _data(run)
    read_data = _data(read)
    checks = {
        "prepare_ok": _status(prepare) == "ok" and bool(candidate_id),
        "confirm_ok": _status(confirm) == "ok" and bool(confirmed_id),
        "run_ok": _status(run) == "ok" and bool(run_id) and run_data.get("available") is True,
        "read_ok": _status(read) == "ok" and read_data.get("run_id") == run_id,
        "consistent": read_data.get("consistency_ok") is True,
        "hashes_present": all(_hash_present(value) for value in (
            read_data.get("input_hash"),
            read_data.get("spec_hash"),
            read_data.get("table_bundle_hash"),
        )),
    }
    if not all(checks.values()):
        runner.record_defect(sample, json.dumps({
            "checks": checks,
            "prepare": {key: (prepare or {}).get(key) for key in ("status", "code", "message", "blockers")},
            "confirm": {key: (confirm or {}).get(key) for key in ("status", "code", "message", "blockers")},
            "run": {
                **{key: (run or {}).get(key) for key in ("status", "code", "message", "blockers")},
                "investment": run_data.get("investment"),
                "funding": run_data.get("funding"),
                "failed_checks": [
                    item for item in (run_data.get("checks") or [])
                    if isinstance(item, dict) and item.get("ok") is False
                ],
            },
            "read": {key: (read or {}).get(key) for key in ("status", "code", "message", "blockers")},
        }, ensure_ascii=False))
        return context
    projection = {
        "schema": "finance_run_roundtrip.v1",
        "fixture_kind": "technical_estimate_preview",
        "valuation_date": FIXTURE_VALUATION_DATE,
        "status_chain": ["ok", "ok", "ok", "ok"],
        "resource_roundtrip": True,
        "consistency_ok": True,
        "hash_contract": {
            "input_hash": "sha256",
            "spec_hash": "sha256",
            "table_bundle_hash": "sha256",
        },
        "investment": read_data.get("investment"),
        "funding": read_data.get("funding"),
        "params": read_data.get("params"),
        "indicators": read_data.get("indicators"),
        "assurance_level": read_data.get("assurance_level"),
        "model_version": read_data.get("model_version"),
        "policy_version": read_data.get("policy_version"),
        "table_keys": sorted((read_data.get("tables") or {}).keys()),
    }
    runner.save_json(sample, projection, runner.baseline / "finance" / "FinanceRun.roundtrip.v1.json")
    return replace(context, run_id=run_id)


def capture_research_package(runner: Runner, context: CoreContext) -> CoreContext:
    sample = Sample(
        "research-package-roundtrip",
        "research",
        "lvke_deep_research",
        "dr_start→dr_submit→dr_get_bundle→lvke_read_resource",
        {"workspace_id": context.workspace_id},
        note="可回读 partial ResearchPackage；不冒充独立质量审计完成",
    )
    runner.samples.append(sample)
    source_text = "baseline research source v1"
    source_hash = "sha256:" + hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    imported = runner.inner_payload(runner.call_tool(
        "lvke_source_files",
        "source_import_content",
        {
            "workspace_id": context.workspace_id,
            "original_filename": "baseline-research-source.txt",
            "declared_mime": "text/plain",
            "content_base64": base64.b64encode(source_text.encode("utf-8")).decode("ascii"),
            "idempotency_key": "baseline-research-source-v1",
            "expected_sha256": source_hash,
            "parse_immediately": True,
        },
    ))
    source_id = str((imported or {}).get("file_id") or "")
    if _status(imported) != "ok" or not source_id:
        runner.record_defect(sample, f"source import failed: {imported}")
        return context
    proc, init = runner.open_server("lvke_deep_research")
    if proc is None:
        runner.record_defect(sample, f"initialize failed: {init}")
        return context
    try:
        started = runner.inner_payload(runner.call_tool_on(
            proc,
            "dr_start",
            {
                "workspace_id": context.workspace_id,
                "topic": "MCP 独立化技术基线",
                "industry": "software",
                "region": "local-sandbox",
                "profile": "quick",
                "idempotency_key": "baseline-research-start-v1",
            },
            request_id=2,
        ))
        task_id = str(_data(started).get("task_id") or "")
        submitted = runner.inner_payload(runner.call_tool_on(
            proc,
            "dr_submit",
            {
                "workspace_id": context.workspace_id,
                "task_id": task_id,
                "report_md": "# MCP 独立化技术基线\n\n本样本仅验证 ResearchPackage 的持久化与资源回读。[1]",
                "citations": [{
                    "title": "Technical baseline fixture",
                    "source_id": source_id,
                    "resource_uri": f"lvke://source-files/workspaces/{context.workspace_id}/files/{source_id}",
                    "locator": "document_text",
                    "content_hash": source_hash,
                    "evidence_track": "technical_fixture",
                }],
                "source_snapshot_ids": [source_id],
            },
            request_id=3,
        ))
        package_id = str((submitted or {}).get("research_package_id") or "")
        bundled = runner.inner_payload(runner.call_tool_on(
            proc,
            "dr_get_bundle",
            {"workspace_id": context.workspace_id, "task_id": task_id},
            request_id=4,
        ))
        resources = (bundled or {}).get("resources") or {}
        report_uri = str(resources.get("report") or "")
        read = runner.inner_payload(runner.call_tool(
            "lvke_feasibility_delivery",
            "lvke_read_resource",
            {"workspace_id": context.workspace_id, "uri": report_uri},
        ))
        listed = runner.inner_payload(runner.call_tool(
            "lvke_feasibility_delivery",
            "lvke_list_resources",
            {
                "workspace_id": context.workspace_id,
                "domain": "deep-research",
            },
        ))
    finally:
        proc.kill()

    listed_resources = (listed or {}).get("resources") or []
    checks = {
        "start_created": bool(task_id),
        "submit_partial": _status(submitted) == "partial" and bool(package_id),
        "bundle_partial": _status(bundled) == "partial" and _hash_present((bundled or {}).get("basis_hash")),
        "report_read": _status(read) == "ok" and (read or {}).get("content", "").startswith("# MCP 独立化技术基线"),
        "package_listed": any(package_id in str(item.get("uri") or "") for item in listed_resources),
    }
    if not all(checks.values()):
        runner.record_defect(sample, json.dumps(checks, ensure_ascii=False))
        return context
    projection = {
        "schema": "research_package_roundtrip.v1",
        "status": "partial",
        "fixture_kind": "technical_fixture",
        "resource_roundtrip": True,
        "basis_hash_contract": "sha256",
        "artifact_names": sorted(resources),
        "report": (read or {}).get("content"),
        "mime_type": (read or {}).get("mime_type"),
        "limitations_preserved": bool((bundled or {}).get("warnings")),
    }
    runner.save_json(
        sample,
        projection,
        runner.baseline / "research" / "ResearchPackage.roundtrip.v1.json",
    )
    return replace(context, research_package_id=package_id)


def capture_finance_tables(runner: Runner, context: CoreContext) -> CoreContext:
    sample = Sample(
        "finance-tables-roundtrip",
        "finance-tables",
        "lvke_finance_tables",
        "tables_render→tables_export_csv→tables_export_xlsx→lvke_read_resource",
        {"workspace_id": context.workspace_id, "run_id": context.run_id},
        note="绑定本次 FinanceRun 的十三表、CSV 和 XLSX 技术工件",
    )
    runner.samples.append(sample)
    if not context.run_id:
        runner.record_defect(sample, "finance run_id unavailable")
        return context
    proc, init = runner.open_server("lvke_finance_tables")
    if proc is None:
        runner.record_defect(sample, f"initialize failed: {init}")
        return context
    try:
        rendered = runner.inner_payload(runner.call_tool_on(
            proc,
            "tables_render",
            {"workspace_id": context.workspace_id, "run_id": context.run_id},
            request_id=2,
            timeout=180.0,
        ))
        package_id = str((rendered or {}).get("finance_tables_package_id") or "")
        csv_export = runner.inner_payload(runner.call_tool_on(
            proc,
            "tables_export_csv",
            {
                "workspace_id": context.workspace_id,
                "run_id": context.run_id,
                "finance_tables_package_id": package_id,
                "validation_scope": "technical",
            },
            request_id=3,
            timeout=180.0,
        ))
        xlsx_export = runner.inner_payload(runner.call_tool_on(
            proc,
            "tables_export_xlsx",
            {
                "workspace_id": context.workspace_id,
                "run_id": context.run_id,
                "finance_tables_package_id": package_id,
                "validation_scope": "technical",
            },
            request_id=4,
            timeout=180.0,
        ))
        csv_uris = list((csv_export or {}).get("csv_resource_uris") or [])
        xlsx_uri = str((xlsx_export or {}).get("xlsx_resource") or "")
        csv_read = runner.inner_payload(runner.call_tool(
            "lvke_feasibility_delivery",
            "lvke_read_resource",
            {"workspace_id": context.workspace_id, "uri": csv_uris[0] if csv_uris else "missing"},
        ))
        xlsx_read = runner.inner_payload(runner.call_tool(
            "lvke_feasibility_delivery",
            "lvke_read_resource",
            {"workspace_id": context.workspace_id, "uri": xlsx_uri or "missing"},
        ))
    finally:
        proc.kill()

    manifest = list((rendered or {}).get("table_manifest") or [])
    csv_manifest = list((csv_export or {}).get("csv_manifest") or [])
    csv_bytes = b""
    try:
        csv_bytes = base64.b64decode(str((csv_read or {}).get("content") or ""), validate=True)
    except (ValueError, TypeError):
        pass
    xlsx_bytes = b""
    try:
        xlsx_bytes = base64.b64decode(str((xlsx_read or {}).get("content") or ""), validate=True)
    except (ValueError, TypeError):
        pass
    checks = {
        "rendered": _status(rendered) in {"ok", "partial"} and bool(package_id),
        "bound_run": (rendered or {}).get("run_id") == context.run_id,
        "thirteen_tables": len(manifest) == 13,
        "csv_exported": len(csv_uris) == 13 and len(csv_manifest) == 13,
        "csv_integrity": bool(((csv_export or {}).get("csv_integrity") or {}).get("valid")),
        "csv_read": _status(csv_read) == "ok" and csv_bytes.startswith(b"\xef\xbb\xbf"),
        "csv_technical_scope": (
            (csv_export or {}).get("validation_scope") == "technical"
            and (csv_export or {}).get("release_grade") == "technical_preview"
            and (csv_export or {}).get("source_package_id") == package_id
            and not bool((csv_export or {}).get("validation_complete"))
        ),
        "xlsx_exported": _hash_present((xlsx_export or {}).get("xlsx_hash")),
        "xlsx_read": _status(xlsx_read) == "ok" and xlsx_bytes.startswith(b"PK"),
        "xlsx_technical_scope": (
            (xlsx_export or {}).get("validation_scope") == "technical"
            and (xlsx_export or {}).get("release_grade") == "technical_preview"
            and (xlsx_export or {}).get("source_package_id") == package_id
            and not bool((xlsx_export or {}).get("validation_complete"))
        ),
    }
    if not all(checks.values()):
        runner.record_defect(sample, json.dumps(checks, ensure_ascii=False))
        return context
    projection = {
        "schema": "finance_tables_roundtrip.v1",
        "status": _status(rendered),
        "run_binding": True,
        "table_count": len(manifest),
        "table_ids": [str(item.get("table_id") or item.get("key") or "") for item in manifest],
        "csv_count": len(csv_manifest),
        "csv_integrity": True,
        "csv_hash_contract": all(_hash_present(item.get("content_hash")) for item in csv_manifest),
        "xlsx_hash_contract": True,
        "xlsx_zip_signature": True,
        "resource_roundtrip": True,
        "validation_complete": bool((rendered or {}).get("validation_complete")),
        "delivery_mode": (rendered or {}).get("delivery_mode"),
    }
    runner.save_json(
        sample,
        projection,
        runner.baseline / "finance-tables" / "FinanceTables.roundtrip.v1.json",
    )
    return replace(context, finance_tables_package_id=package_id)


def capture_evidence_pack(runner: Runner, context: CoreContext) -> CoreContext:
    sample = Sample(
        "evidence-pack-roundtrip",
        "report",
        "lvke_source_files+lvke_data_analysis",
        "source_import_content→analysis_ingest→analysis_build_evidence_pack",
        {"workspace_id": context.workspace_id},
        note="Report 上游的本地真实源文件与不可变 EvidencePack",
    )
    runner.samples.append(sample)
    content = (
        "MCP independence technical evidence. "
        "The baseline annual revenue is 3000 wan and total investment is 10000 wan.\n"
    ).encode("utf-8")
    imported = runner.inner_payload(runner.call_tool(
        "lvke_source_files",
        "source_import_content",
        {
            "workspace_id": context.workspace_id,
            "original_filename": "report-evidence.txt",
            "declared_mime": "text/plain",
            "content_base64": base64.b64encode(content).decode("ascii"),
            "expected_sha256": hashlib.sha256(content).hexdigest(),
            "idempotency_key": "baseline-report-evidence-v1",
        },
    ))
    file_id = str((imported or {}).get("file_id") or "")
    ingested = runner.inner_payload(runner.call_tool(
        "lvke_data_analysis",
        "analysis_ingest",
        {"workspace_id": context.workspace_id, "file_ids": [file_id]},
    ))
    task_id = str((ingested or {}).get("analysis_task_id") or "")
    packed = runner.inner_payload(runner.call_tool(
        "lvke_data_analysis",
        "analysis_build_evidence_pack",
        {
            "workspace_id": context.workspace_id,
            "analysis_task_id": task_id,
            "selected_source_ids": [file_id],
            "expected_fields": [],
        },
    ))
    pack_id = str((packed or {}).get("evidence_pack_id") or "")
    checks = {
        "source_ok": _status(imported) == "ok" and bool(file_id),
        "source_hash": (imported or {}).get("lineage", {}).get("source_sha256") == hashlib.sha256(content).hexdigest(),
        "ingest_ok": _status(ingested) == "ok" and bool(task_id),
        "pack_ok": _status(packed) == "ok" and bool(pack_id),
        "basis_hash": _hash_present((packed or {}).get("basis_hash")),
        "source_bound": (packed or {}).get("source_count") == 1,
    }
    if not all(checks.values()):
        runner.record_defect(sample, json.dumps(checks, ensure_ascii=False))
        return context
    projection = {
        "schema": "evidence_pack_roundtrip.v1",
        "status_chain": ["ok", "ok", "ok"],
        "source_sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
        "source_count": 1,
        "basis_hash_contract": "sha256",
        "data_completeness": (packed or {}).get("data_completeness"),
        "evidence_track": (packed or {}).get("evidence_track"),
        "missing_fields": (packed or {}).get("missing_fields"),
    }
    runner.save_json(
        sample,
        projection,
        runner.baseline / "report" / "EvidencePack.roundtrip.v1.json",
    )
    return replace(context, evidence_pack_id=pack_id)


def capture_report_revision(runner: Runner, context: CoreContext) -> CoreContext:
    sample = Sample(
        "report-revision-roundtrip",
        "report",
        "lvke_report_generation",
        "report_prepare→report_start→report_status→report_validate→report_export_docx→lvke_read_resource",
        {"workspace_id": context.workspace_id},
        note="绑定本次 Finance、十三表、Evidence 与 Research 对象的 partial 报告工件",
    )
    runner.samples.append(sample)
    required = all((context.run_id, context.finance_tables_package_id, context.evidence_pack_id, context.research_package_id))
    if not required:
        runner.record_defect(sample, "upstream core context incomplete")
        return context
    proc, init = runner.open_server("lvke_report_generation")
    if proc is None:
        runner.record_defect(sample, f"initialize failed: {init}")
        return context
    report_content = (
        "# MCP 独立化技术报告\n\n"
        "## 投资与收入基线\n\n总投资 10000 万元，年收入 3000 万元。\n\n"
        "## 研究限制\n\nResearchPackage 为 partial，本报告保留该技术限制。\n"
    )
    try:
        prepared = runner.inner_payload(runner.call_tool_on(
            proc,
            "report_prepare",
            {
                "workspace_id": context.workspace_id,
                "evidence_pack_ids": [context.evidence_pack_id],
                "research_package_ids": [context.research_package_id],
                "finance_binding": {
                    "kind": "generic_feasibility",
                    "run_id": context.run_id,
                    "package_id": context.finance_tables_package_id,
                },
                "outline": ["投资与收入基线", "研究限制"],
                "template_version": "baseline-report-v1",
            },
            request_id=2,
        ))
        preparation_id = str((prepared or {}).get("report_preparation_id") or "")
        started = runner.inner_payload(runner.call_tool_on(
            proc,
            "report_start",
            {
                "workspace_id": context.workspace_id,
                "report_preparation_id": preparation_id,
                "document_snapshot": {"report_type": "technical_baseline", "content": report_content},
            },
            request_id=3,
        ))
        task_id = str((started or {}).get("task_id") or "")
        status = runner.inner_payload(runner.call_tool_on(
            proc,
            "report_status",
            {"workspace_id": context.workspace_id, "task_id": task_id},
            request_id=4,
        ))
        revision_id = str((status or {}).get("report_revision_id") or "")
        validated = runner.inner_payload(runner.call_tool_on(
            proc,
            "report_validate",
            {"workspace_id": context.workspace_id, "report_revision_id": revision_id},
            request_id=5,
        ))
        exported = runner.inner_payload(runner.call_tool_on(
            proc,
            "report_export_docx",
            {"workspace_id": context.workspace_id, "report_revision_id": revision_id, "kind": "draft"},
            request_id=6,
            timeout=180.0,
        ))
        revision_uri = next(iter((status or {}).get("resource_uris") or []), "")
        docx_uri = next(
            (uri for uri in ((exported or {}).get("resource_uris") or []) if "/files/" in uri and uri.endswith(".docx")),
            "",
        )
        revision_read = runner.inner_payload(runner.call_tool(
            "lvke_feasibility_delivery",
            "lvke_read_resource",
            {"workspace_id": context.workspace_id, "uri": revision_uri},
        ))
        docx_read = runner.inner_payload(runner.call_tool(
            "lvke_feasibility_delivery",
            "lvke_read_resource",
            {"workspace_id": context.workspace_id, "uri": docx_uri},
        ))
    finally:
        proc.kill()

    revision_record = {}
    try:
        revision_record = json.loads(str((revision_read or {}).get("content") or "{}"))
    except json.JSONDecodeError:
        pass
    docx_bytes = b""
    try:
        docx_bytes = base64.b64decode(str((docx_read or {}).get("content") or ""), validate=True)
    except (ValueError, TypeError):
        pass
    upstream = ((revision_record.get("payload") or {}).get("upstream") or {})
    checks = {
        "prepare_partial": _status(prepared) == "partial" and bool(preparation_id),
        "start_created": _status(started) == "ok" and bool(task_id),
        "revision_created": _status(status) == "ok" and bool(revision_id),
        "validation_ran": isinstance((validated or {}).get("valid"), bool),
        "draft_exported": _status(exported) == "ok" and bool(docx_uri),
        "revision_read": _status(revision_read) == "ok" and revision_record.get("object_id") == revision_id,
        "docx_read": _status(docx_read) == "ok" and docx_bytes.startswith(b"PK"),
        "lineage": (
            upstream.get("run_id") == context.run_id
            and upstream.get("finance_tables_package_id") == context.finance_tables_package_id
            and upstream.get("evidence_pack_ids") == [context.evidence_pack_id]
            and upstream.get("research_package_ids") == [context.research_package_id]
        ),
    }
    if not all(checks.values()):
        runner.record_defect(sample, json.dumps({
            "checks": checks,
            "prepared": {key: (prepared or {}).get(key) for key in ("status", "code", "message", "blockers")},
            "started": {key: (started or {}).get(key) for key in ("status", "code", "message", "blockers")},
            "status": {key: (status or {}).get(key) for key in ("status", "code", "message", "blockers")},
            "validated": {key: (validated or {}).get(key) for key in ("status", "code", "message", "blockers", "valid")},
            "exported": {key: (exported or {}).get(key) for key in ("status", "code", "message", "blockers")},
        }, ensure_ascii=False))
        return context
    projection = {
        "schema": "report_revision_roundtrip.v1",
        "preparation_status": "partial",
        "revision_status": "ok",
        "validation_valid": bool((validated or {}).get("valid")),
        "validation_blockers": sorted(str(item) for item in ((validated or {}).get("blockers") or [])),
        "lineage_bound": True,
        "upstream_hash_contract": all(
            _hash_present(value)
            for values in ((upstream.get("upstream_hashes") or {}).values())
            for value in (values if isinstance(values, list) else [values])
            if value is not None
        ),
        "revision_resource_roundtrip": True,
        "docx_resource_roundtrip": True,
        "docx_zip_signature": True,
        "artifact_kind": (exported or {}).get("artifact_kind"),
        "document_content_hash": "sha256:" + hashlib.sha256(report_content.encode("utf-8")).hexdigest(),
    }
    runner.save_json(
        sample,
        projection,
        runner.baseline / "report" / "ReportRevision.roundtrip.v1.json",
    )
    return context


def run_core_roundtrips(runner: Runner) -> None:
    context = CoreContext()
    context = capture_finance_run(runner, context)
    context = capture_finance_tables(runner, context)
    context = capture_research_package(runner, context)
    context = capture_evidence_pack(runner, context)
    capture_report_revision(runner, context)


def run(runner: Runner) -> None:
    S = runner.samples.append

    capture_finance_run(runner, CoreContext())

    s = Sample("calc-irr", "finance", "lvke_finance_model", "finance_calculate",
               {"cashflows": [-1000, 300, 400, 500, 600]})
    S(s)
    resp = runner.call_tool(
        "lvke_finance_model",
        "finance_calculate",
        {"operation": "irr", "inputs": {"cashflows": [-1000, 300, 400, 500, 600]}},
    )
    if resp.get("ok"):
        runner.save_json(s, runner.domain_of(resp), runner.baseline / "finance" / "calc.irr.json")
    else:
        runner.record_defect(s, resp.get("error", "no-response"))

    s = Sample("finance-error-missing-ws", "finance", "lvke_finance_model",
               "finance_get_run", {"run_id": RUN01, "view": "summary"}, note="缺 workspace_id 的错误响应")
    S(s)
    raw = runner.call_tool_raw("lvke_finance_model", "finance_get_run",
                               {"run_id": RUN01, "view": "summary"})
    runner.save_json(s, raw, runner.baseline / "finance" / "error.missing-workspace.json")

    s = Sample("finance-error-bad-ws", "finance", "lvke_finance_model",
               "finance_get_run", {"workspace_id": "ws-not-exist-000", "view": "summary"},
               note="不存在工作区的错误响应")
    S(s)
    raw = runner.call_tool_raw("lvke_finance_model", "finance_get_run",
                               {"workspace_id": "ws-not-exist-000", "view": "summary"})
    runner.save_json(s, raw, runner.baseline / "finance" / "error.bad-workspace.json")

    # ================= finance-tables =================
    s = Sample("tables-thirteen", "finance-tables", "lvke_feasibility_delivery",
               "lvke_read_resource", {"workspace_id": CASE01, "uri": FTP01_URI})
    S(s)
    resp = runner.call_tool(
        "lvke_feasibility_delivery",
        "lvke_read_resource",
        {"workspace_id": CASE01, "uri": FTP01_URI},
    )
    if resp.get("ok"):
        inner = runner.inner_payload(resp)
        if inner and inner.get("status") == "ok" and inner.get("success") is True:
            runner.save_json(
                s,
                runner.domain_of(resp),
                runner.baseline / "finance-tables" / "thirteen_tables.json",
            )
        else:
            runner.record_defect(
                s,
                "十三表资源可读但未达到成功状态: "
                f"{json.dumps(inner, ensure_ascii=False)[:200]}",
            )
    else:
        runner.record_defect(s, resp.get("error", "no-response"))

    s = Sample("tables-xlsx", "finance-tables", "lvke_finance_tables", "tables_export_xlsx",
               {"workspace_id": CASE01, "run_id": RUN01}, note="字节级拷贝既有 xlsx 产物")
    S(s)
    if XLSX01.is_file():
        runner.save_bytes(s, XLSX01, runner.baseline / "finance-tables" / "export.workbook.xlsx")
    else:
        runner.record_defect(s, f"xlsx 产物缺失: {XLSX01}")

    s = Sample("tables-csv", "finance-tables", "lvke_finance_tables", "tables_export_csv",
               {"workspace_id": CASE01, "run_id": RUN01})
    S(s)
    resp = runner.call_tool("lvke_finance_tables", "tables_export_csv",
                            {"workspace_id": CASE01, "run_id": RUN01})
    if resp.get("ok"):
        inner = runner.inner_payload(resp)
        if inner and inner.get("status") == "ok" and inner.get("success") is True:
            runner.save_json(
                s,
                runner.domain_of(resp),
                runner.baseline / "finance-tables" / "export.csv.manifest.json",
            )
        else:
            runner.record_defect(
                s,
                "CSV 导出未成功: " f"{json.dumps(inner, ensure_ascii=False)[:200]}",
            )
    else:
        runner.record_defect(s, resp.get("error", "no-response"))

    s = Sample("template-catalog", "finance-tables", "lvke-reference", "reference_list", {"dataset": "templates"})
    S(s)
    resp = runner.call_tool("lvke-reference", "reference_list", {"dataset": "templates"})
    if resp.get("ok"):
        runner.save_json(s, runner.domain_of(resp), runner.baseline / "finance-tables" / "template.catalog.json")
    else:
        runner.record_defect(s, resp.get("error", "no-response"))

    # ================= report =================
    s = Sample("report-revision-partial", "report", "lvke_feasibility_delivery",
               "lvke_list_resources", {"workspace_id": REPORT_WS, "domain": "report-generation", "resource_type": "revision"},
               note="既有 revision 经 transport 读取；read 返回 revision 对象")
    S(s)
    listed = runner.list_resources(
        "lvke_feasibility_delivery",
        "lvke_list_resources",
        {"workspace_id": REPORT_WS, "domain": "report-generation", "resource_type": "revision"},
    )
    if listed.get("ok") and listed.get("items"):
        rev = listed["items"][0]
        uri = rev.get("uri") or rev.get("resource_uri")
        if uri:
            resp = runner.call_tool(
                "lvke_feasibility_delivery",
                "lvke_read_resource",
                {"workspace_id": REPORT_WS, "uri": uri},
            )
            if resp.get("ok"):
                runner.save_json(s, runner.domain_of(resp),
                                 runner.baseline / "report" / "ReportRevision.v1.json")
            else:
                runner.record_defect(s, f"read 失败: {resp.get('error')}")
        else:
            runner.record_defect(s, f"list 返回项缺 uri: {json.dumps(rev, ensure_ascii=False)[:200]}")
    else:
        runner.record_defect(s, f"list 无 revision 或失败: {json.dumps(listed, ensure_ascii=False)[:200]}")

    s = Sample("report-error-missing-ws", "report", "lvke_feasibility_delivery",
               "lvke_list_resources", {}, note="缺 workspace_id 的错误响应")
    S(s)
    raw = runner.call_tool_raw("lvke_feasibility_delivery", "lvke_list_resources", {})
    runner.save_json(s, raw, runner.baseline / "report" / "error.missing-workspace.json")

    # ================= research (deep-research) =================
    capture_research_package(runner, CoreContext())

    # research 领域的可回放成功样本：走压缩后的 reference 搜索 + 读取。
    s = Sample("research-industry-report", "research", "lvke-reference", "reference_search",
               {"keyword": "光伏", "limit": 3})
    S(s)
    resp = runner.call_tool(
        "lvke-reference",
        "reference_search",
        {"dataset": "industry_reports", "query": "光伏", "limit": 3},
    )
    if resp.get("ok"):
        inner = runner.inner_payload(resp)
        if inner and inner.get("status") == "ok":
            items = (inner.get("data") or {}).get("items") or []
            rid = items[0].get("report_id") if items else None
            if not rid:
                runner.record_defect(s, f"search 无结果: {json.dumps(inner, ensure_ascii=False)[:200]}")
            else:
                resp2 = runner.call_tool(
                    "lvke-reference",
                    "reference_get",
                    {"dataset": "industry_reports", "record_id": rid},
                )
                if not resp2.get("ok"):
                    runner.record_defect(s, resp2.get("error", "no-response"))
                else:
                    inner2 = runner.inner_payload(resp2)
                    if inner2 and inner2.get("status") == "ok":
                        runner.save_json(s, runner.domain_of(resp2),
                                         runner.baseline / "research" / "industry.report.json")
                    else:
                        runner.record_defect(s, f"get_report_summary 非 ok: "
                                                 f"{json.dumps(inner2, ensure_ascii=False)[:200]}")
        else:
            runner.record_defect(s, f"search_report 非 ok: {json.dumps(inner, ensure_ascii=False)[:200]}")
    else:
        runner.record_defect(s, resp.get("error", "no-response"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="捕获 MCP 可回放 baseline 样本")
    parser.add_argument(
        "--output",
        type=Path,
        default=BASELINE,
        help="baseline 输出目录；验证时应指向临时目录",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="隔离的 LVKE_MCP_DATA_DIR；core-only 省略时自动创建临时目录",
    )
    parser.add_argument(
        "--core-only",
        action="store_true",
        help="仅捕获 FinanceRun 与 ResearchPackage 空工作区回读链",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    baseline = args.output.expanduser().resolve()
    baseline.mkdir(parents=True, exist_ok=True)
    temporary_data = None
    if args.data_dir is None and args.core_only:
        temporary_data = tempfile.TemporaryDirectory(prefix="lvke-mcp-baseline-")
        data_dir = Path(temporary_data.name)
    elif args.data_dir is None:
        data_dir = None
    else:
        data_dir = args.data_dir.expanduser().resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
    runner = Runner(baseline=baseline, data_dir=data_dir)
    if args.core_only:
        run_core_roundtrips(runner)
    else:
        run(runner)

    ok = [x for x in runner.samples if x.status == "ok"]
    defect = [x for x in runner.samples if x.status == "defect"]
    pending = [x for x in runner.samples if x.status == "pending"]

    manifest = {
        "schema": "baseline_samples.v1",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "protocol_version": PROTOCOL_VERSION,
        "plan_ref": "dev-docs/architecture/MCP_INDEPENDENCE_PLAN.md §29.2",
        "domains": ["finance", "finance-tables", "report", "research"],
        "exit_condition": "四个核心领域均有至少一个可回放成功样本；无法取得的样本登记为当前实现缺陷",
        "summary": {
            "ok": len(ok),
            "defect": len(defect),
            "pending": len(pending),
            "ok_domains": sorted({x.domain for x in ok}),
            "defect_domains": sorted({x.domain for x in defect}),
        },
        "samples": [x.to_manifest() for x in runner.samples],
    }
    (runner.baseline / "samples_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    for x in runner.samples:
        print(f"[{x.status.upper():6}] {x.id:28} {x.domain:14} -> {x.file or x.detail[:80]}")
    print(f"\nok={len(ok)} defect={len(defect)} pending={len(pending)}")
    print(f"ok domains: {sorted({x.domain for x in ok})}")
    if defect:
        print(f"defect domains: {sorted({x.domain for x in defect})}")
        print("（缺陷按 §29.2 登记，独立化后重跑应转为可回放）")
    print(f"manifest -> {runner.baseline / 'samples_manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
