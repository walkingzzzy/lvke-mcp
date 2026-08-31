"""运行、渲染、读取、整包与甲方导入工具，含 legacy 兼容实现。"""

from __future__ import annotations

import hashlib
import json
import time
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any


from lvke_mcp.runtime.storage import sha256_json
from lvke_mcp.adapters.finance_model_repository import BASIS_OF_ESTIMATE_STORE, IDEMPOTENCY_STORE, SPEC_STORE
from lvke_mcp.runtime.responses import ok

from .analysis_tools import (
    _latest_formal_boe,
)

from .envelope import (
    _active_idempotency_record,
    _blocking_rules,
    _err_env,
    _exception_env,
    _idempotency_ttl_seconds,
    _ok_env,
    _revenue_input_complete,
    _run_uri,
    _str_list,
    _ws,
)

from .schemas import (
    SERVER_NAME,
    _DEPRECATED_PACKAGE_HINT,
    _DEPRECATED_RENDER_HINT,
)


def _tool_run_model(args: dict) -> dict:
    from lvke_mcp.domains.finance.model_application import run_model

    return run_model(args)


def _legacy_tool_run_model(args: dict) -> dict:
    # Legacy imports share the governed application path so gate semantics cannot diverge.
    return _tool_run_model(args)


def _tool_render_tables(args: dict) -> dict:
    run_id = args.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        return _err_env(
            f"{SERVER_NAME}.invalid_argument", "run_id 必填",
            deprecated=True, warnings=[_DEPRECATED_RENDER_HINT],
        )
    wsid = _ws(args)
    if not wsid:
        # 允许只传 run_id 时从审计库反查困难；仍要求 workspace_id
        return _err_env(
            f"{SERVER_NAME}.invalid_argument", "workspace_id 必填",
            deprecated=True, warnings=[_DEPRECATED_RENDER_HINT],
        )
    try:
        from lvke_mcp.domains.finance import run_service

        data = run_service.render_workspace_finance_tables(
            wsid,
            run_id=run_id.strip(),
            format=str(args.get("format") or "structured"),
            include_control_tables=bool(args.get("include_control_tables", True)),
        )
        if not data.get("ok"):
            return _err_env(
                f"{SERVER_NAME}.{data.get('error') or 'render_failed'}",
                data.get("message") or "渲染 13 表失败",
                detail=data,
                status="blocked",
                deprecated=True,
                warnings=[_DEPRECATED_RENDER_HINT],
                next_actions=["迁移到 lvke-finance-tables.tables_render"],
            )
        rid = str(data.get("run_id") or "") or None
        missing_keys = _str_list(data.get("missing_delivery_keys"))
        warnings = [_DEPRECATED_RENDER_HINT]
        if missing_keys:
            warnings.append(f"缺失交付表：{'、'.join(missing_keys)}")
        return _ok_env(
            data,
            source=f"{SERVER_NAME}.finance_render_tables",
            status="partial" if missing_keys else "ok",
            resource_uris=[_run_uri(wsid, rid)] if rid else [],
            warnings=warnings,
            next_actions=["迁移到 lvke-finance-tables.tables_render"],
            deprecated=True,
            run_id=rid,
            missing_delivery_keys=missing_keys,
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_render_tables failed",
            f"{SERVER_NAME}.render_failed",
            "渲染 13 表失败",
            deprecated=True,
            warnings=[_DEPRECATED_RENDER_HINT],
        )


def _tool_get_run(args: dict) -> dict:
    from lvke_mcp.domains.finance.model_application import get_run

    return get_run(args)


def _tool_generate_package(args: dict) -> dict:
    wsid = _ws(args)
    if not wsid:
        return _err_env(
            f"{SERVER_NAME}.invalid_argument", "workspace_id 必填",
            deprecated=True, warnings=[_DEPRECATED_PACKAGE_HINT],
        )
    mode = str(args.get("mode") or "estimate_preview")
    if mode not in {"estimate_preview", "review_candidate"}:
        mode = "estimate_preview"
    try:
        from lvke_mcp.domains.finance import run_service

        data = run_service.generate_workspace_finance_package(
            wsid,
            mode=mode,
            force_refresh_spec=bool(args.get("force_refresh_spec") or False),
            force_recompute=bool(args.get("force_recompute") or False),
            force_flat=bool(args.get("force_flat") or False),
            confirmed_spec=args.get("confirmed_spec") if isinstance(args.get("confirmed_spec"), dict) else None,
            agent_trace_id=str(args.get("agent_trace_id") or ""),
            tool_call_id=str(args.get("tool_call_id") or ""),
            valuation_date=str(args.get("valuation_date") or ""),
            requested_manifest=(
                args.get("requested_manifest")
                if isinstance(args.get("requested_manifest"), dict) else None
            ),
            selected_scenario_id=str(args.get("selected_scenario_id") or "base"),
        )
        run_id = str(data.get("run_id") or "") or None
        uri = _run_uri(wsid, run_id)
        missing = _str_list(data.get("missing_inputs"))
        stage = str(data.get("stage") or "")
        if data.get("ok"):
            status = "ok"
            blockers: list[str] = []
        elif missing:
            status = "missing_inputs"
            blockers = [f"缺少必要输入：{item}" for item in missing]
        else:
            status = "blocked"
            blockers = _blocking_rules(data) or [f"stage={stage or 'unknown'} 未完成"]
        return _ok_env(
            data,
            source=f"{SERVER_NAME}.finance_generate_package",
            status=status,
            resource_uris=[uri] if uri else [],
            warnings=[_DEPRECATED_PACKAGE_HINT, *_str_list(data.get("prepare_warnings"))],
            blockers=blockers,
            next_actions=[
                "迁移：finance_prepare_spec → finance_run_model → lvke-finance-tables.tables_render",
            ],
            deprecated=True,
            run_id=run_id,
            stage=stage or None,
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_generate_package failed",
            f"{SERVER_NAME}.package_failed",
            "生成财务包失败",
            deprecated=True,
            warnings=[_DEPRECATED_PACKAGE_HINT],
        )


def _tool_import_vendor_review(args: dict) -> dict:
    wsid = _ws(args)
    if not wsid:
        return _err_env(f"{SERVER_NAME}.invalid_argument", "workspace_id 必填")
    xlsx_path = args.get("xlsx_path") or args.get("path")
    if not isinstance(xlsx_path, str) or not xlsx_path.strip():
        return _err_env(f"{SERVER_NAME}.invalid_argument", "xlsx_path 必填")
    cohort = args.get("cohort_xlsx_paths")
    if cohort is not None and not (
        isinstance(cohort, list) and all(isinstance(item, str) for item in cohort)
    ):
        return _err_env(
            f"{SERVER_NAME}.invalid_argument",
            "cohort_xlsx_paths 必须是字符串数组",
        )
    try:
        from lvke_mcp.domains.finance.vendor_review import import_vendor_workbook_review

        data = import_vendor_workbook_review(
            wsid,
            xlsx_path.strip(),
            valuation_date=str(args.get("valuation_date") or ""),
            force_recompute=bool(args.get("force_recompute") or False),
            cohort_xlsx_paths=cohort or None,
        )
        run_id = str(data.get("run_id") or "") or None
        uri = _run_uri(wsid, run_id)
        missing = _str_list(data.get("missing_inputs"))
        blocking = [
            str(issue.get("rule") or issue.get("code") or "blocking_issue")
            for issue in (data.get("blocking_issues") or [])
            if isinstance(issue, dict)
        ]
        if missing:
            status = "missing_inputs"
        elif not data.get("available"):
            status = "blocked"
        elif blocking:
            # 复核完成但存在阻断预警：不冒充复核通过。
            status = "blocked"
        else:
            status = "ok"
        return _ok_env(
            data,
            source=f"{SERVER_NAME}.finance_import_vendor_review",
            status=status,
            resource_uris=[uri] if uri else [],
            warnings=_str_list(((data.get("reference") or {}).get("warnings"))),
            blockers=blocking or (
                [f"缺少必要输入：{item}" for item in missing] if missing else []
            ),
            next_actions=(
                ["修复阻断预警并重新运行确定性校验"] if blocking else []
            ),
            reference_id=data.get("reference_id"),
            review_passed=bool(data.get("review_passed")),
            run_id=run_id,
            missing_inputs=missing,
        )
    except FileNotFoundError:
        return _exception_env(
            "finance_import_vendor_review workbook missing",
            f"{SERVER_NAME}.vendor_workbook_not_found",
            "甲方工作簿不存在",
        )
    except ImportError:  # pragma: no cover - environment dependent
        return _exception_env(
            "finance_import_vendor_review parser unavailable",
            f"{SERVER_NAME}.vendor_workbook_parser_unavailable",
            "甲方工作簿解析依赖不可用",
        )
    except (ValueError, OSError, zipfile.BadZipFile):
        return _exception_env(
            "finance_import_vendor_review parse failed",
            f"{SERVER_NAME}.vendor_workbook_parse_failed",
            "甲方工作簿格式无效或解析失败",
        )
    except Exception:  # noqa: BLE001
        return _exception_env(
            "finance_import_vendor_review failed",
            f"{SERVER_NAME}.vendor_review_failed",
            "导入并复核甲方计算表失败",
        )

# 门面模块的公开面。显式声明而不是靠"碰巧 import 了"——API 快照门禁
# (tests/integration/test_refactor_guardrails.py) 要求这些 re-export 保持
# 可达,而 ruff F401 会把它们判成未使用。写成 __all__ 让两个门禁同时成立,
# 也让"哪些名字是刻意对外的"可读。
__all__ = [
    "Any",
    "BASIS_OF_ESTIMATE_STORE",
    "IDEMPOTENCY_STORE",
    "SERVER_NAME",
    "SPEC_STORE",
    "_DEPRECATED_PACKAGE_HINT",
    "_DEPRECATED_RENDER_HINT",
    "_active_idempotency_record",
    "_blocking_rules",
    "_err_env",
    "_exception_env",
    "_idempotency_ttl_seconds",
    "_latest_formal_boe",
    "_legacy_tool_run_model",
    "_ok_env",
    "_revenue_input_complete",
    "_run_uri",
    "_str_list",
    "_tool_generate_package",
    "_tool_get_run",
    "_tool_import_vendor_review",
    "_tool_render_tables",
    "_tool_run_model",
    "_ws",
    "datetime",
    "hashlib",
    "json",
    "ok",
    "sha256_json",
    "time",
    "timedelta",
    "timezone",
    "zipfile",
]
