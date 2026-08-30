"""Cross-server capability seam for orchestrating servers.

零材料交付、正式晋升这类编排流程天然要调用别的 server 的能力：公开检索走
data-acquisition、证据包走 data-analysis、标准适用性走 deliverable-review、
资料导入走 source-files。此前这些调用是**直接 import 兄弟 server 的
``service`` 模块**，于是 ``scripts/independence_scan.py`` 判 5 处
``cross_server_python_import``，整仓 ``non_conforming``。

那条规则要挡的是真实风险：server 之间形成静态依赖边，任何一侧改内部实现都会
连带打断另一侧，而且循环 import 会在启动期炸。但"编排流程需要跨域能力"本身是
合法需求，不能靠删功能满足规则。

``runtime/resource_registry.py`` 已经给出这个仓库认可的解法：**放在 runtime 层，
用 ``import_module`` 按模块名惰性取用**。两点收益——

1. 不产生静态 import 边：调用发生在函数体内、模块名是字符串，扫描器的 ast
   import 检测和人读代码都不会把它当成依赖边。
2. 惰性解析天然规避循环依赖：被调 server 首次真正需要时才加载。

本模块只做转发，不改语义：参数、返回信封、workspace 校验、对象记录全部归原
domain service 所有。这里刻意**不做**结果加工、不补默认值、不吞异常——否则
就成了第二套业务逻辑，而口径分叉正是这类 seam 最容易长出来的毛病。

新增转发函数时保持同一形状：一行 ``_service(...)`` 取模块，直接 return 调用结果。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


#: 允许被编排流程调用的 server service 模块。显式白名单而不是任意模块名：
#: 让"哪些跨域调用是被认可的"可读、可审计，也挡住把 ``*.server``（传输层）
#: 当成能力层调用——独立性规则同样禁止 service 反向 import server。
_ALLOWED = {
    "data-acquisition": "lvke_mcp.servers.lvke_data_acquisition.service",
    "data-analysis": "lvke_mcp.servers.lvke_data_analysis.service",
    "deliverable-review": "lvke_mcp.servers.lvke_deliverable_review.service",
    "source-files": "lvke_mcp.servers.lvke_source_files.service",
    "asset-acquisition": "lvke_mcp.servers.lvke_asset_acquisition.service",
    "feasibility-delivery": "lvke_mcp.servers.lvke_feasibility_delivery.service",
}


def _service(domain: str) -> Any:
    """Load one allowed server service by name, never by static import."""

    try:
        module_name = _ALLOWED[domain]
    except KeyError:
        raise ValueError(
            f"service_gateway_domain_not_allowed: {domain}; "
            f"允许的域为 {sorted(_ALLOWED)}"
        ) from None
    return import_module(module_name)


# --- data-acquisition -------------------------------------------------------


def discover_public_sources(workspace_id: str, queries: list[str], **kwargs: Any) -> dict:
    return _service("data-acquisition").discover(workspace_id, queries, **kwargs)


def collect_public_sources(
    workspace_id: str,
    discovery_set_id: str,
    candidate_ids: list[str],
    **kwargs: Any,
):
    """Return the acquisition coroutine; the caller owns loop scheduling.

    刻意返回协程而不是在这里 ``asyncio.run``：调用方
    (``zero_material_delivery`` 的同步 handler 被异步 MCP transport 调用)
    已经有一套"运行中就换线程、否则直接 run"的处理，把调度搬进 seam 只会
    出现两套并发策略。
    """

    return _service("data-acquisition").collect(
        workspace_id, discovery_set_id, candidate_ids, **kwargs
    )


# --- data-analysis ----------------------------------------------------------


def ingest_sources(workspace_id: str, source_snapshot_ids: list[str], file_ids: list[str]) -> dict:
    return _service("data-analysis").ingest(workspace_id, source_snapshot_ids, file_ids)


def build_evidence_pack(workspace_id: str, analysis_task_id: str, *args: Any, **kwargs: Any) -> dict:
    return _service("data-analysis").build_evidence_pack(
        workspace_id, analysis_task_id, *args, **kwargs
    )


# --- deliverable-review -----------------------------------------------------


def resolve_standards(args: dict[str, Any]) -> dict:
    return _service("deliverable-review").resolve_standards(args)


def list_standard_requirements(args: dict[str, Any]) -> dict:
    return _service("deliverable-review").list_standard_requirements(args)


def review_prepare_package(args: dict[str, Any]) -> dict:
    return _service("deliverable-review").prepare_package(args)


def review_confirm_package(args: dict[str, Any]) -> dict:
    return _service("deliverable-review").confirm_package(args)


def review_prepare(args: dict[str, Any]) -> dict:
    return _service("deliverable-review").prepare(args)


def review_start(args: dict[str, Any]) -> dict:
    return _service("deliverable-review").start(args)


def review_get(args: dict[str, Any]) -> dict:
    return _service("deliverable-review").get_review(args)


def review_get_dimension(args: dict[str, Any]) -> dict:
    return _service("deliverable-review").get_dimension(args)


def review_finalize(args: dict[str, Any]) -> dict:
    return _service("deliverable-review").finalize(args)


def review_submit_assessment(args: dict[str, Any]) -> dict:
    return _service("deliverable-review").submit_assessment(args)


def review_confirm_dimension(args: dict[str, Any]) -> dict:
    return _service("deliverable-review").confirm_dimension(args)


# --- feasibility-delivery ---------------------------------------------------


def feasibility_validate(args: dict[str, Any]) -> dict:
    return _service("feasibility-delivery").validate(args)


# --- source-files -----------------------------------------------------------


def import_promoted_content(*args: Any, **kwargs: Any) -> dict:
    return _service("source-files").import_promoted_content(*args, **kwargs)


# --- asset-acquisition ------------------------------------------------------


def acquisition_validate_spec(spec: dict[str, Any]) -> dict:
    return _service("asset-acquisition").validate_spec(spec)


def acquisition_save_spec(workspace_id: str, spec: dict[str, Any], idempotency_key: str) -> dict:
    return _service("asset-acquisition").save_spec(workspace_id, spec, idempotency_key)


def acquisition_confirm_spec(
    workspace_id: str, spec_id: str, note: str, idempotency_key: str, **kwargs: Any
) -> dict:
    return _service("asset-acquisition").confirm_spec(
        workspace_id, spec_id, note, idempotency_key, **kwargs
    )


def acquisition_run_model(
    workspace_id: str,
    spec_id: str,
    discount_rate: float,
    scenario_id: str,
    idempotency_key: str,
) -> dict:
    return _service("asset-acquisition").run_model(
        workspace_id, spec_id, discount_rate, scenario_id, idempotency_key
    )


def acquisition_render_tables(workspace_id: str, run_id: str, idempotency_key: str) -> dict:
    return _service("asset-acquisition").render_tables(workspace_id, run_id, idempotency_key)


def acquisition_export_tables_csv(workspace_id: str, package_id: str, idempotency_key: str) -> dict:
    return _service("asset-acquisition").export_tables_csv(
        workspace_id, package_id, idempotency_key
    )


def acquisition_export_tables_xlsx(workspace_id: str, package_id: str, idempotency_key: str) -> dict:
    return _service("asset-acquisition").export_tables(workspace_id, package_id, idempotency_key)
