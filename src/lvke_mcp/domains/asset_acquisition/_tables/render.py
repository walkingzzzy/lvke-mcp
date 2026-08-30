"""渲染与 package 绑定。"""

from __future__ import annotations

from typing import Any


from lvke_mcp.domains.asset_acquisition import backend as acquisition_service
from lvke_mcp.domains.reports import artifacts as report_artifacts
from lvke_mcp.runtime.storage import sha256_json

from .build import (
    _build_tables,
    _integrity,
    _lineage,
)

from .columns import (
    PACKAGE_STORE,
)

from .query import (
    _blocked,
    _result,
)

from .rows import (
    _table_contract,
)


def render(
    workspace_id: str,
    run_id: str,
) -> dict[str, Any]:
    run = acquisition_service.get_run(
        workspace_id,
        run_id,
    )
    if not run:
        return _blocked("RUN_NOT_FOUND", "资产收购 run 不存在")
    if run.get("status") != "succeeded" or not run.get("available"):
        return _blocked("RUN_NOT_READY", "资产收购 run 尚未固化成功")
    if run.get("model_version") not in {"acquisition_model.v3", "acquisition_model.solar.v1"}:
        return _blocked("ACQUISITION_MODEL_UNSUPPORTED", "收购十三表不支持该模型版本")
    spec_row = acquisition_service.get_spec(
        workspace_id,
        str(run.get("spec_id") or ""),
    )
    spec = spec_row.get("spec") if isinstance(spec_row, dict) else None
    if not isinstance(spec, dict) or spec_row.get("spec_hash") != run.get("spec_hash"):
        return _blocked("RUN_SPEC_MISMATCH", "run 与不可变 Spec 快照不一致")
    asset_type = str(spec.get("asset_type") or "hotel_lease")
    definitions, columns, _required = _table_contract(asset_type)
    tables = _build_tables(run, spec)
    integrity = _integrity(tables, asset_type=asset_type, run_id=run_id)
    manifest = [{
        "index": index, "key": key, "name": name, "row_count": len(tables[key]),
        "column_count": len(columns[key]), "table_hash": sha256_json(tables[key]),
        "missing_required": integrity["column_missing"].get(key, {}),
        "nested_cell_count": integrity["nested_cells"].get(key, 0),
    } for index, (key, name) in enumerate(definitions, 1)]
    basis = {
        "run_id": run_id, "spec_id": run.get("spec_id"), "spec_hash": run.get("spec_hash"),
        "input_hash": run.get("input_hash"), "model_version": run.get("model_version"),
        "asset_type": asset_type,
        "evidence_binding_hash": run.get("evidence_binding_hash"),
    }
    payload = {
        **basis,
        "package_schema": "acquisition_tables_package.v2",
        "table_manifest": manifest,
        "formula_lineage": _lineage(run, tables, asset_type=asset_type),
        "tables": tables,
        "supplemental_table_manifest": [{
            "key": key,
            "row_count": len(tables.get(key) or []),
            "table_hash": sha256_json(tables.get(key) or []),
        } for key in ("monthly_income_statement", "monthly_balance_sheet") if tables.get(key)],
        "monthly_driver_manifest": (run.get("result") or {}).get("monthly_driver_manifest") or {},
        "operating_calendar": (run.get("result") or {}).get("operating_calendar") or {},
        "annual_reconciliation": (run.get("result") or {}).get("annual_reconciliation") or [],
        "integrity": integrity,
        "evidence_policy": str(run.get("evidence_policy") or "formal_evidence"),
        "delivery_mode": str(run.get("delivery_mode") or ""),
        "project_fact_certified": bool(run.get("project_fact_certified", False)),
        "evidence_origin": run.get("evidence_origin"),
        "formal_promotion": run.get("formal_promotion"),
        "lineage": run.get("lineage"),
        "reconstruction_records": list(run.get("reconstruction_records") or []),
        "reconstructed_source_ids": list(run.get("reconstructed_source_ids") or []),
        "unresolved_inputs": list(run.get("unresolved_inputs") or []),
        "release_limitations": list(run.get("release_limitations") or []),
    }
    record = PACKAGE_STORE.put(
        workspace_id, payload,
        producer="lvke-asset-acquisition.acquisition_render_tables",
        status="ok" if integrity["status"] == "passed" else "partial",
        source_ids=[run_id, str(run.get("spec_id") or "")],
        basis=basis, schema_version="acquisition_tables_package.v2",
    )
    if integrity["status"] == "passed":
        _bind_package(
            workspace_id,
            run,
            record,
        )
    return _result(record)


def _bind_package(
    workspace_id: str,
    run: dict[str, Any],
    record: dict[str, Any],
) -> None:
    current = report_artifacts.load(
        workspace_id,
        "finance_binding",
        {},
    ) or {}
    fin = {key: value for key, value in current.items() if key not in {"workspace_id", "finance_run_id", "section", "bound_at"}}
    fin.update({
        "binding_kind": "asset_acquisition",
        "acquisition_tables_package_id": record["object_id"],
        "acquisition_tables_basis_hash": record["basis_hash"],
    })
    report_artifacts.bind_finance_run(
        workspace_id,
        str(run.get("run_id") or ""),
        section="asset_acquisition_tables",
        fin=fin,
    )


def _package(
    workspace_id: str,
    package_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    record = PACKAGE_STORE.get(workspace_id, package_id)
    return record, dict((record or {}).get("payload") or {})
