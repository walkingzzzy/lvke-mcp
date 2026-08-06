"""Thin routing facade over the former support MCP handlers."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from lvke_mcp.runtime.responses import err


def _handler(module: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return getattr(import_module(module), name)(arguments)


def _filters(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def search(dataset: str, query: str, filters: dict[str, Any], limit: int) -> dict[str, Any]:
    args = {**_filters(filters), "limit": int(limit)}
    if dataset == "industry_reports":
        args.setdefault("keyword", query)
        return _handler("lvke_mcp.servers.industry_research.server", "_tool_search_report", args)
    if dataset == "clients":
        args.setdefault("keyword", query)
        return _handler("lvke_mcp.servers.lvke_clients.server", "_tool_search_clients", args)
    if dataset == "experts":
        if query and not any(args.get(key) for key in ("industry", "specialty", "role")):
            args["specialty"] = query
        return _handler("lvke_mcp.servers.lvke_experts.server", "_tool_find_experts", args)
    if dataset == "policies":
        args.setdefault("keyword", query)
        return _handler("lvke_mcp.servers.policy_search.server", "_tool_search_policy", args)
    if dataset == "archive":
        args.setdefault("query", query)
        return _handler("lvke_mcp.servers.lvke_archive.server", "_tool_search_archive", args)
    return err("lvke-reference.dataset_invalid", "reference_search 不支持该 dataset")


def get(dataset: str, record_id: str, view: Any = None) -> dict[str, Any]:
    if dataset == "industry_reports":
        return _handler("lvke_mcp.servers.industry_research.server", "_tool_get_report_summary", {"report_id": record_id})
    if dataset == "clients":
        return _handler("lvke_mcp.servers.lvke_clients.server", "_tool_get_client", {"client_id": record_id})
    if dataset == "experts":
        return _handler("lvke_mcp.servers.lvke_experts.server", "_tool_get_expert", {"expert_id": record_id})
    if dataset == "policies":
        return _handler("lvke_mcp.servers.policy_search.server", "_tool_get_policy_full", {"policy_id": record_id})
    if dataset == "templates":
        return _handler("lvke_mcp.servers.lvke_templates.server", "_tool_get_template", {"template_id": record_id})
    if dataset == "archive":
        chapter = view.get("chapter", 1) if isinstance(view, dict) else (view or 1)
        return _handler("lvke_mcp.servers.lvke_archive.server", "_tool_get_chapter", {"report_id": record_id, "chapter": int(chapter)})
    return err("lvke-reference.dataset_invalid", "reference_get 不支持该 dataset")


def list_items(dataset: str, owner_id: str, filters: dict[str, Any]) -> dict[str, Any]:
    args = _filters(filters)
    if dataset == "environment_locations":
        return _handler("lvke_mcp.servers.environmental_data.server", "_tool_list_monitored_locations", {})
    if dataset == "client_projects":
        if owner_id:
            args.setdefault("client_id", owner_id)
        return _handler("lvke_mcp.servers.lvke_clients.server", "_tool_list_projects", args)
    if dataset == "expert_specialties":
        return _handler("lvke_mcp.servers.lvke_experts.server", "_tool_list_specialties", {})
    if dataset == "statistics_dictionaries":
        return _handler("lvke_mcp.servers.statistics_cn.server", "_tool_list_dictionaries", {})
    if dataset == "templates":
        return _handler("lvke_mcp.servers.lvke_templates.server", "_tool_list_templates", args)
    return err("lvke-reference.dataset_invalid", "reference_list 不支持该 dataset")


def observe(dataset: str, subject: str, period: int | None, filters: dict[str, Any]) -> dict[str, Any]:
    args = _filters(filters)
    if period is not None:
        args["year"] = int(period)
    if dataset == "air_quality":
        args["city"] = subject
        return _handler("lvke_mcp.servers.environmental_data.server", "_tool_query_air_quality", args)
    if dataset == "water_quality":
        args["section_or_basin"] = subject
        return _handler("lvke_mcp.servers.environmental_data.server", "_tool_query_water_quality", args)
    if dataset == "statistics":
        args["name"] = subject
        return _handler("lvke_mcp.servers.statistics_cn.server", "_tool_query_indicator", args)
    return err("lvke-reference.dataset_invalid", "reference_observe 不支持该 dataset")


def verify(dataset: str, record_id: str, as_of: str = "") -> dict[str, Any]:
    if dataset != "policy":
        return err("lvke-reference.dataset_invalid", "reference_verify 目前只支持 policy")
    policy = _handler(
        "lvke_mcp.servers.policy_search.server",
        "_tool_get_policy_full",
        {"policy_id": record_id},
    )
    data = policy.get("data") if isinstance(policy, dict) else None
    citation = (
        str(data.get("doc_number") or data.get("title") or record_id)
        if isinstance(data, dict)
        else record_id
    )
    result = _handler(
        "lvke_mcp.servers.policy_search.server",
        "_tool_verify_policy_active",
        {"citation": citation},
    )
    if as_of and isinstance(result, dict):
        result["requested_as_of"] = as_of
    if isinstance(result, dict):
        result["requested_record_id"] = record_id
    return result


def fill_template(
    template_id: str,
    data: dict[str, Any],
    format_name: str = "markdown",
) -> dict[str, Any]:
    if format_name != "markdown":
        return err("lvke-reference.template_format_invalid", "模板填充仅支持 markdown")
    return _handler("lvke_mcp.servers.lvke_templates.server", "_tool_fill_template", {"template_id": template_id, "data": data})


def geo_query(
    operation: str,
    query_or_point: Any,
    radius_km: float,
    category: str,
    limit: int | None = None,
) -> dict[str, Any]:
    if operation == "geocode":
        return _handler("lvke_mcp.servers.map_geo.server", "_tool_geocode", {"address": str(query_or_point or "")})
    if operation == "nearby_pois" and isinstance(query_or_point, dict):
        result = _handler("lvke_mcp.servers.map_geo.server", "_tool_nearby_pois", {
            "lat": query_or_point.get("lat"), "lng": query_or_point.get("lng"),
            "type": category, "radius_km": radius_km,
        })
        data = result.get("data") if isinstance(result, dict) else None
        if limit is not None and isinstance(data, dict) and isinstance(data.get("items"), list):
            data["items"] = data["items"][: int(limit)]
            data["count"] = len(data["items"])
        return result
    return err("lvke-reference.geo_input_invalid", "geo_query 需要 geocode 字符串或 nearby_pois 坐标对象")


def geo_distance_matrix(
    origins: list[Any],
    destinations: list[Any],
    mode: str = "haversine_with_highway_estimate",
) -> dict[str, Any]:
    if mode != "haversine_with_highway_estimate":
        return err(
            "lvke-reference.geo_mode_invalid",
            "当前离线数据仅支持 haversine_with_highway_estimate",
        )
    return _handler("lvke_mcp.servers.map_geo.server", "_tool_distance_matrix", {"origins": origins, "destinations": destinations})


def archive_find_similar(arguments: dict[str, Any]) -> dict[str, Any]:
    return _handler("lvke_mcp.servers.lvke_archive.server", "_tool_find_similar_projects", arguments)


def archive_extract_structure(arguments: dict[str, Any]) -> dict[str, Any]:
    return _handler("lvke_mcp.servers.lvke_archive.server", "_tool_extract_structure", arguments)


def archive_compare_cases(arguments: dict[str, Any]) -> dict[str, Any]:
    return _handler("lvke_mcp.servers.lvke_archive.server", "_tool_compare_cases", arguments)


def archive_get_template_paragraph(arguments: dict[str, Any]) -> dict[str, Any]:
    return _handler("lvke_mcp.servers.lvke_archive.server", "_tool_get_template_paragraph", arguments)
