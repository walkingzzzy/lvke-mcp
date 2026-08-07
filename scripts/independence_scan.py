#!/usr/bin/env python3
"""阶段0 独立性依赖扫描：生成 quality/independence_dependency_scan.json。

v2（AST 精确版）：改用 ``ast`` 解析，只统计**真实代码依赖**，不再把
docstring / 注释 / 字符串字面量当作依赖点。

  MCP 项目边界：
      - import 语句根模块 ∈ {hermes_cli, tools, agent}
      - importlib.import_module / __import__ 动态加载上述模块
      - 读取 ``HERMES_*`` 环境变量（os.environ.get / os.getenv / environ[...]）

扫描只证明当前 MCP 项目自身不存在外部宿主依赖、跨 Server 私有导入和禁用
身份/权限语义。仓库外项目不属于本项目的构建或验收输入。

排除目录：build/（构建产物）、*.egg-info、tests、fixtures、scripts、quality、
__pycache__。

文本残留（docstring/注释/字符串中的 ``hermes_cli|HERMES_|keyui_`` 字样）单独
登记在 ``text_residue_entries``，**不参与 conforming 判定**；用于跟踪文案清理
进度。数据兼容标识（如 ``schema_version: "keyui_workspace.v1"``）属于字符串
字面量，既不计入依赖，也在 text_residue 中单独标注 data_identifier=True。

用法：
    python scripts/independence_scan.py [--output quality/independence_dependency_scan.json]
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MCP_ROOT = REPO_ROOT / "mcp_servers"
DEFAULT_OUT = MCP_ROOT / "quality" / "independence_dependency_scan.json"

# 外部宿主模块（MCP 侧禁止 import）。
_FORBIDDEN_ROOTS = frozenset({"hermes_cli", "tools", "agent"})

# 扫描时排除的目录名（相对各自扫描根）。
_EXCLUDED_DIRS = frozenset({
    "build", "__pycache__", "tests", "fixtures", "scripts", "quality",
    "node_modules", ".git", ".venv", "venv", "dist",
})
_EXCLUDED_SUFFIXES = (".egg-info",)

# 文本残留模式（仅跟踪，不判定）。
_TEXT_RESIDUE_RE = re.compile(r"hermes_cli|HERMES_|keyui_|from tools|from agent|import tools|import agent")
_FORBIDDEN_SEMANTIC_RE = re.compile(
    r"\b(?:actor|actor_id|reviewer|reviewed_at|reviewed_on|authenticated|authentication|"
    r"authorization|authorized|permission|rbac|approved_run|review_grade|security_review|"
    r"attest|signoff|release_status|release_condition|formal_delivery_ready|"
    r"formally_deliverable|publish_eligibility|released_by|released_at|approved_by|"
    r"approved_at|accepted_by|confirmed_by|reviewed_by|calculated_by|closed_by|created_by|"
    r"updated_by)\b|职责分离|权限|认证|审批|批准|签审|安全审查",
    re.IGNORECASE,
)

# The compression topology deliberately keeps deterministic delivery quality
# gates and the Tavily provider's bearer header.  These narrowly scoped lines
# mention old scanner keywords without introducing host identity or RBAC
# dependencies.  Keep exemptions path- and context-specific so a new
# identity-bearing field still fails the scan.
_SEMANTIC_EXEMPTION_RULES = (
    ("mcp_servers/src/lvke_mcp/adapters/finance_tables_repository.py", re.compile(r"十三表是需要随仓库留存、复核和签审")),
    ("mcp_servers/src/lvke_mcp/domains/asset_acquisition/backend.py", re.compile(r"不认证项目事实|随仓库留存与签审")),
    ("mcp_servers/src/lvke_mcp/domains/reports/artifacts.py", re.compile(r"随仓库留存、复核与签审")),
    # Wave 2.5 把 confirm_quality 搬到 _service/agent_lifecycle.py，注释随代码保留。
    ("mcp_servers/src/lvke_mcp/domains/research/_service/agent_lifecycle.py", re.compile(r"^\s*#.*认证项目事实")),
    ("mcp_servers/src/lvke_mcp/domains/research/providers/tavily.py", re.compile(r'headers = \{"Authorization": f"Bearer \{token\}"\}')),
    ("mcp_servers/src/lvke_mcp/servers/lvke_asset_acquisition/service.py", re.compile(r"不认证项目事实")),
    # Wave 1.1 把 build_evidence_pack 搬到 _service/evidence_pack.py。豁免按路径登记，
    # 所以搬移后必须跟着改路径；文本与规则完全不变。
    ("mcp_servers/src/lvke_mcp/servers/lvke_data_analysis/_service/evidence_pack.py", re.compile(r"不能认证项目事实")),
    ("mcp_servers/src/lvke_mcp/servers/lvke_deep_research/_server/registration.py", re.compile(r"不认证项目事实")),
    ("mcp_servers/src/lvke_mcp/servers/lvke_feasibility_delivery/service.py", re.compile(r'"released_at": utc_now\(\)')),
    ("mcp_servers/src/lvke_mcp/servers/lvke_finance_model/server.py", re.compile(r"不认证项目")),
    ("mcp_servers/src/lvke_mcp/servers/lvke_knowledge_governance/service.py", re.compile(r'"(?:reviewed_at|released_at)": utc_now\(\)')),
    ("mcp_servers/src/lvke_mcp/testing/source_reconstructed_acceptance.py", re.compile(r"不认证项目事实|不作为项目事实认证")),
)


def _server_package(module: str) -> str | None:
    parts = module.split(".")
    try:
        index = parts.index("servers")
    except ValueError:
        return None
    return parts[index + 1] if len(parts) > index + 1 else None


def _module_for_path(path: Path) -> str:
    try:
        relative = path.relative_to(MCP_ROOT / "src")
    except ValueError:
        return ""
    return ".".join(relative.with_suffix("").parts)


def _architecture_entry(path: Path, rel: str, node: ast.AST, imported: str, reason: str) -> dict:
    return {
        "mcp_file": rel,
        "line": getattr(node, "lineno", 0),
        "direction": "internal_architecture",
        "forbidden_reference": imported,
        "reason": reason,
        "status": "non_conforming",
    }


def _scan_internal_architecture(path: Path, rel: str) -> list[dict]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []
    source_module = _module_for_path(path)
    source_server = _server_package(source_module)
    source_is_domain = source_module.startswith("lvke_mcp.domains.")
    entries: list[dict] = []
    for node in ast.walk(tree):
        modules: list[tuple[str, ast.AST]] = []
        if isinstance(node, ast.Import):
            modules.extend((alias.name, node) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append((node.module, node))
        for imported, import_node in modules:
            target_server = _server_package(imported)
            if source_is_domain and target_server:
                entries.append(_architecture_entry(
                    path, rel, import_node, imported, "domains_must_not_import_servers",
                ))
            elif source_server and target_server and source_server != target_server:
                entries.append(_architecture_entry(
                    path, rel, import_node, imported, "cross_server_python_import",
                ))
            elif source_module.endswith(".service") and imported.endswith(".server"):
                entries.append(_architecture_entry(
                    path, rel, import_node, imported, "service_must_not_import_server",
                ))
    return entries


def _legacy_key_removal_lines(path: Path) -> set[int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "pop" or len(node.args) != 2:
            continue
        key, default = node.args
        if (
            isinstance(key, ast.Constant)
            and isinstance(key.value, str)
            and _FORBIDDEN_SEMANTIC_RE.search(key.value)
            and isinstance(default, ast.Constant)
            and default.value is None
        ):
            lines.add(int(getattr(node, "lineno", 0)))
    return lines


def _scan_forbidden_semantics(path: Path, rel: str) -> list[dict]:
    entries: list[dict] = []
    legacy_removal_lines = _legacy_key_removal_lines(path)
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line_number in legacy_removal_lines:
            continue
        if any(
            rule_path == rel and pattern.search(line)
            for rule_path, pattern in _SEMANTIC_EXEMPTION_RULES
        ):
            continue
        for match in _FORBIDDEN_SEMANTIC_RE.finditer(line):
            entries.append({
                "mcp_file": rel,
                "line": line_number,
                "direction": "forbidden_semantics",
                "forbidden_reference": match.group(0),
                "raw_line": line.strip(),
                "status": "non_conforming",
            })
    return entries

# 每个 MCP 领域 -> (capability, owner_module, contract, golden_fixture, verification_test)。
_DOMAIN = {
    "lvke_archive": ("archive", "lvke_mcp.domains.archive", "ArchiveCase.v1",
                      "tests/fixtures/baseline/research/archive.case.json",
                      "tests/quality/test_archive_independent.py::test_storage_golden"),
    "lvke_templates": ("templates", "lvke_mcp.domains.templates", "TemplateCatalog.v1",
                       "tests/fixtures/baseline/finance-tables/template.catalog.json",
                       "tests/quality/test_templates_independent.py::test_catalog_golden"),
    "lvke_deliverable_review": ("deliverable_review", "lvke_mcp.domains.deliverable_review", "ReviewFinding.v1",
                                "tests/fixtures/baseline/research/review.finding.json",
                                "tests/quality/test_deliverable_review_independent.py::test_finding_golden"),
    "lvke_data_analysis": ("data_analysis", "lvke_mcp.domains.data_analysis", "EvidencePack.v1",
                           "tests/fixtures/baseline/research/EvidencePack.v1.json",
                           "tests/quality/test_data_analysis_independent.py::test_evidence_golden"),
    "lvke_asset_acquisition": ("acquisition", "lvke_mcp.domains.asset_acquisition", "AcquisitionPackage.v1",
                               "tests/fixtures/baseline/finance/acquisition.package.json",
                               "tests/quality/test_acquisition_independent.py::test_package_golden"),
    "policy_search": ("policy_search", "lvke_mcp.domains.policy_search", "PolicyRecord.v1",
                      "tests/fixtures/baseline/research/policy.record.json",
                      "tests/quality/test_policy_independent.py::test_record_golden"),
    "map_geo": ("map_geo", "lvke_mcp.domains.map_geo", "GeoPoint.v1",
                "tests/fixtures/baseline/research/geo.point.json",
                "tests/quality/test_map_geo_independent.py::test_poi_golden"),
    "industry_research": ("industry_research", "lvke_mcp.domains.industry_research", "ResearchReport.v1",
                          "tests/fixtures/baseline/research/industry.report.json",
                          "tests/quality/test_industry_independent.py::test_report_golden"),
}


def _domain_key(mcp_file: str) -> str:
    """从相对仓库根的 src 路径推断领域名。"""
    parts = mcp_file.split("/")
    # src/lvke_mcp/servers/lvke_archive/... -> lvke_archive
    for i, p in enumerate(parts):
        if p in ("mcp_servers", "lvke_mcp"):
            rest = parts[i + 1:]
            for cand in rest:
                if cand in _DOMAIN or cand.startswith("lvke_") or cand in (
                        "policy_search", "map_geo", "industry_research", "_common",
                        "finance_calc", "excel_bridge"):
                    return cand
    return "unknown"


def _root_module(dotted: str) -> str:
    return dotted.split(".")[0]


def _iter_py_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        if _EXCLUDED_DIRS & set(path.parts):
            continue
        if any(part.endswith(_EXCLUDED_SUFFIXES) for part in path.parts):
            continue
        yield path


def _env_key_from_call(node: ast.Call) -> str | None:
    """识别 os.environ.get('HERMES_X') / os.getenv('HERMES_X') / environ['HERMES_X'] 的 key。"""
    func = node.func
    name_chain: list[str] = []
    cur: ast.AST | None = func
    while isinstance(cur, ast.Attribute):
        name_chain.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        name_chain.append(cur.id)
    # 形如 os.environ.get / environ.get / os.getenv
    if name_chain and name_chain[0] == "os":
        name_chain = name_chain[1:]
    if name_chain and name_chain[-1] in ("get", "getenv", "__getitem__") and (
            "environ" in name_chain or name_chain[0] == "getenv"):
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            return node.args[0].value
    return None


def _scan_file_ast(path: Path, rel: str, direction: str) -> tuple[list[dict], list[dict]]:
    """返回 (依赖点列表, 文本残留列表)。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return [], []

    entries: list[dict] = []
    text_lines: list[dict] = []
    raw_lines = path.read_text(encoding="utf-8").splitlines()

    for node in ast.walk(tree):
        hit: tuple[str, str] | None = None  # (root_module, forbidden_reference)

        if isinstance(node, ast.Import):
            for alias in node.names:
                root = _root_module(alias.name)
                if root in _FORBIDDEN_ROOTS:
                    hit = (root, alias.name)
                    break
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = _root_module(node.module)
            if root in _FORBIDDEN_ROOTS:
                hit = (root, node.module)
        elif isinstance(node, ast.Call):
            # importlib.import_module("hermes_cli...") / __import__("...")
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in ("import_module", "__import__") or (
                    isinstance(func, ast.Name) and func.id == "__import__"):
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    root = _root_module(node.args[0].value)
                    if root in _FORBIDDEN_ROOTS:
                        hit = (root, node.args[0].value)
            else:
                key = _env_key_from_call(node)
                if key and key.startswith("HERMES_"):
                    hit = ("HERMES_*", f"os.environ[{key!r}]")

        if hit:
            root, forbidden = hit
            lineno = getattr(node, "lineno", 0)
            entries.append({
                "mcp_file": rel,
                "line": lineno,
                "direction": direction,
                "forbidden_reference": forbidden,
                "raw_line": (raw_lines[lineno - 1].strip() if 0 < lineno <= len(raw_lines) else ""),
                "capability": "unknown",
                "owner_module": "unknown",
                "contract": "Unknown.v1",
                "golden_fixture": "",
                "status": "non_conforming",
                "verification_test": "",
            })

    # 文本残留（仅跟踪）：docstring/注释/字符串中的字样，非 import 语句。
    for i, line in enumerate(raw_lines, 1):
        if _TEXT_RESIDUE_RE.search(line):
            is_data_identifier = "schema_version" in line and ("keyui_" in line)
            text_lines.append({
                "file": rel,
                "line": i,
                "raw_line": line.strip(),
                "data_identifier": is_data_identifier,
            })
    return entries, text_lines


def _annotate(entry: dict) -> dict:
    key = _domain_key(entry["mcp_file"])
    capability, owner, contract, golden, test = _DOMAIN.get(
        key, ("unknown", "lvke_mcp.domains.unknown", "Unknown.v1", "", "")
    )
    entry["capability"] = capability
    entry["owner_module"] = owner
    entry["contract"] = contract
    entry["golden_fixture"] = golden
    entry["verification_test"] = test
    return entry


def scan_forward() -> tuple[list[dict], list[dict]]:
    """MCP -> 外部：扫描 mcp_servers/ 全部源码（含 src/lvke_mcp 与垫片目录）。"""
    entries: list[dict] = []
    text_residue: list[dict] = []
    for path in _iter_py_files(MCP_ROOT):
        rel = path.relative_to(REPO_ROOT).as_posix()
        hits, texts = _scan_file_ast(path, rel, "mcp_to_external")
        entries.extend(_annotate(h) for h in hits)
        text_residue.extend(texts)
    return entries, text_residue


def scan_internal_boundaries() -> tuple[list[dict], list[dict]]:
    architecture: list[dict] = []
    semantics: list[dict] = []
    for path in _iter_py_files(MCP_ROOT / "src" / "lvke_mcp"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        architecture.extend(_scan_internal_architecture(path, rel))
        semantics.extend(_scan_forbidden_semantics(path, rel))
    return architecture, semantics


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan MCP independence and architecture boundaries")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when project-boundary violations exist",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    out = args.output.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    forward, text_residue = scan_forward()
    architecture, semantics = scan_internal_boundaries()
    violations = forward + architecture + semantics
    scan_status = "conforming" if not violations else "non_conforming"

    doc = {
        "schema": "independence_dependency_scan.v4",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "plan_ref": "MCP_INDEPENDENCE_PLAN.md §19/§26.2/§26.4",
        "strict": bool(args.strict),
        "status_summary": {
            "overall": scan_status,
            "forward": {
                "matches": len(forward),
                "files": len({entry["mcp_file"] for entry in forward}),
                "status": "conforming" if not forward else "non_conforming",
            },
            "internal_architecture": {
                "matches": len(architecture),
                "files": len({entry["mcp_file"] for entry in architecture}),
                "status": "conforming" if not architecture else "non_conforming",
            },
            "forbidden_semantics": {
                "matches": len(semantics),
                "files": len({entry["mcp_file"] for entry in semantics}),
                "status": "conforming" if not semantics else "non_conforming",
            },
            "text_residue": {
                "matches": len(text_residue),
                "files": len({entry["file"] for entry in text_residue}),
                "note": "tracked compatibility text; excluded from conformance",
            },
        },
        "dependencies": forward,
        "internal_architecture_violations": architecture,
        "forbidden_semantic_entries": semantics,
        "text_residue_entries": text_residue,
    }

    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"forward: {len(forward)} matches / {len({entry['mcp_file'] for entry in forward})} files")
    print(f"internal_architecture: {len(architecture)} matches / {len({entry['mcp_file'] for entry in architecture})} files")
    print(f"forbidden_semantics: {len(semantics)} matches / {len({entry['mcp_file'] for entry in semantics})} files")
    print(f"overall status: {scan_status}")
    print(f"written -> {out}")
    return 1 if args.strict and scan_status != "conforming" else 0


if __name__ == "__main__":
    sys.exit(main())
