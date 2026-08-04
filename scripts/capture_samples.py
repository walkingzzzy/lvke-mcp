#!/usr/bin/env python3
"""阶段0 基线样本捕获：为独立化版本抓取「可回放成功样本」。

按 MCP_INDEPENDENCE_PLAN.md §29.2 的步骤，走 stdio MCP 线协议调用真实工具，
把外部行为固化为 tests/fixtures/baseline/{finance,finance-tables,report,research}/ 下的
golden fixtures。这些文件是独立化后重新运行时可机器对照的行为基准。

规则：
  - 每个样本以 protocolVersion=2025-11-25 握手（与 freeze_baseline.py 一致）。
  - 走 transport 抓取（list/read/get）的真实对象为成功样本；成功样本保存**完整 tool-result
    envelope**（content[].text + structuredContent + isError）。这些服务在内部已剥离
    trace_id/duration_ms 等不可回放噪声，故完整 envelope 是确定性的、可直接机器对照。
  - 无法确定性取得的样本（如需要 agent 生命周期才能终结的研究包）按 §29.2 退出条件
    如实登记为当前实现缺陷（status=defect），不伪造。

用法：
    .venv/bin/python mcp_servers/scripts/capture_samples.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MCP_ROOT = REPO_ROOT / "mcp_servers"
PYTHON = str(REPO_ROOT / ".venv" / "bin" / "python")
BASELINE = MCP_ROOT / "tests" / "fixtures" / "baseline"
PROTOCOL_VERSION = "2025-11-25"

# 捕获用真实工作区（既有数据，勿改）。
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
    def __init__(self):
        self.samples: list[Sample] = []

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

    def call_tool(self, server: str, tool: str, params: dict, timeout: float = 120.0):
        proc, init = self.open_server(server)
        if proc is None:
            return {"ok": False, "error": f"initialize failed: {init}"}
        try:
            resp = self._call(proc, {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": tool, "arguments": params},
            }, timeout=timeout)
            if resp is None:
                return {"ok": False, "error": "no tools/call response"}
            if "result" not in resp:
                return {"ok": False, "error": json.dumps(resp, ensure_ascii=False)[:300]}
            return {"ok": True, "result": resp["result"]}
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
        sample.file = str(path.relative_to(BASELINE))
        sample.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        sample.status = "ok"

    def save_bytes(self, sample: Sample, src: Path, dst: Path):
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        sample.file = str(dst.relative_to(BASELINE))
        sample.sha256 = hashlib.sha256(dst.read_bytes()).hexdigest()
        sample.status = "ok"

    def record_defect(self, sample: Sample, detail: str):
        sample.status = "defect"
        sample.detail = detail


def run(runner: Runner) -> None:
    S = runner.samples.append

    # ================= finance =================
    s = Sample("finance-run-full", "finance", "lvke_finance_model",
               "finance_get_run", {"workspace_id": CASE01, "run_id": RUN01, "view": "full"})
    S(s)
    resp = runner.call_tool("lvke_finance_model", "finance_get_run",
                            {"workspace_id": CASE01, "run_id": RUN01, "view": "full"})
    if resp.get("ok"):
        inner = runner.inner_payload(resp)
        status = inner.get("status") if inner else None
        data = runner.domain_of(resp)
        if status == "ok":
            runner.save_json(s, data, BASELINE / "finance" / "FinanceRun.v1.json")
        else:
            body = data if inner is None else inner.get("data")
            runner.record_defect(s, f"run 非终态(status={status}): {json.dumps(body, ensure_ascii=False)[:200]}")
    else:
        runner.record_defect(s, resp.get("error", "no-response"))

    s = Sample("calc-irr", "finance", "finance_calc", "calc_irr",
               {"cashflows": [-1000, 300, 400, 500, 600]})
    S(s)
    resp = runner.call_tool("finance_calc", "calc_irr", {"cashflows": [-1000, 300, 400, 500, 600]})
    if resp.get("ok"):
        runner.save_json(s, runner.domain_of(resp), BASELINE / "finance" / "calc.irr.json")
    else:
        runner.record_defect(s, resp.get("error", "no-response"))

    s = Sample("finance-error-missing-ws", "finance", "lvke_finance_model",
               "finance_get_run", {"run_id": RUN01, "view": "summary"}, note="缺 workspace_id 的错误响应")
    S(s)
    raw = runner.call_tool_raw("lvke_finance_model", "finance_get_run",
                               {"run_id": RUN01, "view": "summary"})
    runner.save_json(s, raw, BASELINE / "finance" / "error.missing-workspace.json")

    s = Sample("finance-error-bad-ws", "finance", "lvke_finance_model",
               "finance_get_run", {"workspace_id": "ws-not-exist-000", "view": "summary"},
               note="不存在工作区的错误响应")
    S(s)
    raw = runner.call_tool_raw("lvke_finance_model", "finance_get_run",
                               {"workspace_id": "ws-not-exist-000", "view": "summary"})
    runner.save_json(s, raw, BASELINE / "finance" / "error.bad-workspace.json")

    # ================= finance-tables =================
    s = Sample("tables-thirteen", "finance-tables", "lvke_finance_tables",
               "tables_read_resource", {"workspace_id": CASE01, "uri": FTP01_URI})
    S(s)
    resp = runner.call_tool("lvke_finance_tables", "tables_read_resource",
                            {"workspace_id": CASE01, "uri": FTP01_URI})
    if resp.get("ok"):
        data = runner.domain_of(resp)
        runner.save_json(s, data, BASELINE / "finance-tables" / "thirteen_tables.json")
    else:
        runner.record_defect(s, resp.get("error", "no-response"))

    s = Sample("tables-xlsx", "finance-tables", "lvke_finance_tables", "tables_export_xlsx",
               {"workspace_id": CASE01, "run_id": RUN01}, note="字节级拷贝既有 xlsx 产物")
    S(s)
    if XLSX01.is_file():
        runner.save_bytes(s, XLSX01, BASELINE / "finance-tables" / "export.workbook.xlsx")
    else:
        runner.record_defect(s, f"xlsx 产物缺失: {XLSX01}")

    s = Sample("tables-csv", "finance-tables", "lvke_finance_tables", "tables_export_csv",
               {"workspace_id": CASE01, "run_id": RUN01})
    S(s)
    resp = runner.call_tool("lvke_finance_tables", "tables_export_csv",
                            {"workspace_id": CASE01, "run_id": RUN01})
    if resp.get("ok"):
        data = runner.domain_of(resp)
        runner.save_json(s, data, BASELINE / "finance-tables" / "export.csv.manifest.json")
    else:
        runner.record_defect(s, resp.get("error", "no-response"))

    s = Sample("template-catalog", "finance-tables", "lvke_templates", "list_templates", {})
    S(s)
    resp = runner.call_tool("lvke_templates", "list_templates", {})
    if resp.get("ok"):
        runner.save_json(s, runner.domain_of(resp), BASELINE / "finance-tables" / "template.catalog.json")
    else:
        runner.record_defect(s, resp.get("error", "no-response"))

    # ================= report =================
    s = Sample("report-revision-partial", "report", "lvke_report_generation",
               "report_list_resources", {"workspace_id": REPORT_WS, "resource_type": "revision"},
               note="既有 revision 经 transport 读取；read 返回 revision 对象")
    S(s)
    listed = runner.list_resources("lvke_report_generation", "report_list_resources",
                                   {"workspace_id": REPORT_WS, "resource_type": "revision"})
    if listed.get("ok") and listed.get("items"):
        rev = listed["items"][0]
        uri = rev.get("uri") or rev.get("resource_uri")
        if uri:
            resp = runner.call_tool("lvke_report_generation", "report_read_resource",
                                    {"workspace_id": REPORT_WS, "uri": uri})
            if resp.get("ok"):
                runner.save_json(s, runner.domain_of(resp),
                                 BASELINE / "report" / "ReportRevision.v1.json")
            else:
                runner.record_defect(s, f"read 失败: {resp.get('error')}")
        else:
            runner.record_defect(s, f"list 返回项缺 uri: {json.dumps(rev, ensure_ascii=False)[:200]}")
    else:
        runner.record_defect(s, f"list 无 revision 或失败: {json.dumps(listed, ensure_ascii=False)[:200]}")

    s = Sample("report-error-missing-ws", "report", "lvke_report_generation",
               "report_list_resources", {}, note="缺 workspace_id 的错误响应")
    S(s)
    raw = runner.call_tool_raw("lvke_report_generation", "report_list_resources", {})
    runner.save_json(s, raw, BASELINE / "report" / "error.missing-workspace.json")

    # ================= research (deep-research) =================
    s = Sample("research-package", "research", "lvke_deep_research", "dr_list_resources",
               {"workspace_id": DR_WS},
               note="当前布局(非旧版路径)研究包需 agent 生命周期(dr_start→submit→get_bundle)终结；"
                    "若当前工作区无可读包则登记为缺陷")
    S(s)
    listed = runner.list_resources("lvke_deep_research", "dr_list_resources", {"workspace_id": DR_WS})
    if listed.get("ok") and listed.get("items"):
        runner.save_json(s, {"resources": listed["items"]},
                         BASELINE / "research" / "ResearchPackage.list.json")
    else:
        runner.record_defect(
            s,
            f"dr_list_resources 无当前布局包（既有包在旧版路径,当前代码不可读）: "
            f"{json.dumps(listed, ensure_ascii=False)[:200]}",
        )

    # research 领域的可回放成功样本：走 industry_research 研报检索 + 摘要。
    s = Sample("research-industry-report", "research", "industry_research", "search_report",
               {"keyword": "光伏", "limit": 3})
    S(s)
    resp = runner.call_tool("industry_research", "search_report", {"keyword": "光伏", "limit": 3})
    if resp.get("ok"):
        inner = runner.inner_payload(resp)
        if inner and inner.get("status") == "ok":
            items = (inner.get("data") or {}).get("items") or []
            rid = items[0].get("report_id") if items else None
            if not rid:
                runner.record_defect(s, f"search 无结果: {json.dumps(inner, ensure_ascii=False)[:200]}")
            else:
                resp2 = runner.call_tool("industry_research", "get_report_summary", {"report_id": rid})
                if not resp2.get("ok"):
                    runner.record_defect(s, resp2.get("error", "no-response"))
                else:
                    inner2 = runner.inner_payload(resp2)
                    if inner2 and inner2.get("status") == "ok":
                        runner.save_json(s, runner.domain_of(resp2),
                                         BASELINE / "research" / "industry.report.json")
                    else:
                        runner.record_defect(s, f"get_report_summary 非 ok: "
                                                 f"{json.dumps(inner2, ensure_ascii=False)[:200]}")
        else:
            runner.record_defect(s, f"search_report 非 ok: {json.dumps(inner, ensure_ascii=False)[:200]}")
    else:
        runner.record_defect(s, resp.get("error", "no-response"))


def main() -> int:
    runner = Runner()
    run(runner)

    ok = [x for x in runner.samples if x.status == "ok"]
    defect = [x for x in runner.samples if x.status == "defect"]
    pending = [x for x in runner.samples if x.status == "pending"]

    manifest = {
        "schema": "baseline_samples.v1",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "protocol_version": PROTOCOL_VERSION,
        "plan_ref": "mcp_servers/MCP_INDEPENDENCE_PLAN.md §29.2",
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
    (BASELINE / "samples_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    for x in runner.samples:
        print(f"[{x.status.upper():6}] {x.id:28} {x.domain:14} -> {x.file or x.detail[:80]}")
    print(f"\nok={len(ok)} defect={len(defect)} pending={len(pending)}")
    print(f"ok domains: {sorted({x.domain for x in ok})}")
    if defect:
        print(f"defect domains: {sorted({x.domain for x in defect})}")
        print("（缺陷按 §29.2 登记，独立化后重跑应转为可回放）")
    print(f"manifest -> {BASELINE / 'samples_manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
