"""报告域就绪度评分 —— MCP 自有实现（零外部依赖）。

为既有 ``build_readiness`` 逻辑的域内复刻（PT-5 四维评分：
结构/数据/论证/表达 + 阻断/警告），仅改 import 路径与证据源，不重写业务逻辑：

- evidence 源重指 ``lvke_data_analysis.EVIDENCE_STORE``（MCP 自有证据包存储；
  惰性 import，域内独立可 import，存储不可用时降级为空证据）
- 财务审计语义改读 MCP 自有 run 存储（``domains.finance.run_store``），
  不再依赖历史 sqlite 审计库
- 删 tenant 形参；``cross_check`` / ``finance_narrative_verification`` 为
  hermes 侧工件，MCP 域内无对应持久化，直接降级为空
"""

from __future__ import annotations

import logging
from typing import Any

from lvke_mcp.domains.reports import doc_service as svc

logger = logging.getLogger(__name__)


def _latest_evidence_pack(workspace_id: str) -> dict[str, Any]:
    """读取工作区最新 evidence pack（record 的 payload 缺省空 dict）。"""
    try:
        # 惰性 import：EVIDENCE_STORE 属 MCP 数据链 server，域内不静态依赖。
        from lvke_mcp.servers.lvke_data_analysis.service import EVIDENCE_STORE
    except Exception:  # noqa: BLE001 - 数据链不可用时降级为空证据
        return {}
    try:
        records = EVIDENCE_STORE.list(workspace_id) or []
    except Exception:  # noqa: BLE001
        return {}
    if not records:
        return {}
    latest = sorted(
        (r for r in records if isinstance(r, dict)),
        key=lambda r: str(r.get("created_at") or ""),
        reverse=True,
    )[0]
    payload = latest.get("payload")
    return payload if isinstance(payload, dict) else {}


def _evidence_items(ev: dict[str, Any]) -> list[dict[str, Any]]:
    """把 MCP evidence payload 归一化为评分用证据项。

    MCP 证据包结构为 ``sources``（来源文档）+ ``fact_candidates``（候选事实）；
    hermes 版的 ``items``/``chapters_used``/``evidence_level`` 在此无对应，
    评分按 MCP 结构降级（论证分按事实候选覆盖的字段集合计）。
    """
    items = []
    for src in ev.get("sources") or []:
        if isinstance(src, dict):
            items.append({
                "source_type": src.get("source_type") or "",
                "field": src.get("field") or "",
            })
    for cand in ev.get("fact_candidates") or []:
        if isinstance(cand, dict):
            items.append({
                "source_type": cand.get("source_type") or "fact_candidate",
                "field": cand.get("field") or "",
                "evidence_level": cand.get("evidence_level") or "",
            })
    return items


def _persist_readiness(workspace_id: str, readiness: dict[str, Any]) -> None:
    try:
        svc._write_json(  # noqa: SLF001 - 同域边界
            svc._workspace_root(workspace_id) / "publish_readiness.json",  # noqa: SLF001
            readiness,
        )
    except Exception:  # noqa: BLE001 - 缓存写失败不阻断
        pass


def build_readiness(
    workspace_id: str,
    *,
    persist: bool = True,
    revision_id: str | None = None,
    document_snapshot: dict[str, Any] | None = None,
    expected_chapters: list[str] | None = None,
) -> dict[str, Any]:
    """PT-5：按 4 维（结构/数据/论证/表达）评分 + 阻断/警告（对齐 16.4）。

    ``persist=False`` returns a fresh, read-only snapshot.  Review/status
    services use that mode so inspecting a gate never rewrites the cached
    ``publish_readiness.json`` artifact.
    """
    try:
        meta = svc.ensure_workspace(workspace_id)
        rt = svc.resolve_report_type(meta)
        document = (
            dict(document_snapshot)
            if isinstance(document_snapshot, dict)
            else (
                svc.read_document(workspace_id, revision_id=revision_id)
                if revision_id
                else svc.read_document(workspace_id)
            )
        )
        doc = document["content"]
    except Exception as exc:  # noqa: BLE001
        return {"workspace_id": workspace_id, "score": 0, "error": str(exc)[:200]}

    ev = _latest_evidence_pack(workspace_id)
    ev_items = _evidence_items(ev)
    grounding = str(ev.get("grounding_state") or "ungrounded")
    placeholders = doc.count(svc.MISSING_MARKER)
    struct = svc.report_structure(rt)
    total_ch = len(expected_chapters or struct["chapters"])

    # 结构分
    try:
        vres = svc.validate_report_structure(
            doc,
            rt,
            expected_chapters=expected_chapters,
        )
        structure_score = 100 if vres.get("ok") else max(0, 100 - 20 * len(vres.get("issues", [])))
    except Exception:  # noqa: BLE001
        structure_score = 60

    # 数据分：有财务候选事实 + 财务无占位
    has_finance = any(
        i.get("source_type") in {"finance_calc", "finance_model", "fact_candidate"}
        for i in ev_items
    )
    fin_placeholder = "详见第六章测算" in doc
    data_score = 100 if (has_finance and not fin_placeholder) else (60 if has_finance else 0)

    # 论证分：事实候选覆盖的字段占比（粗略：字段去重 / 总章数）
    grounded_themes = set()
    for i in ev_items:
        field = i.get("field")
        if field:
            grounded_themes.add(field)
    argument_score = min(100, round(len(grounded_themes) / max(total_ch, 1) * 100))

    # 表达分：占位清零
    expression_score = max(0, 100 - 5 * placeholders) if placeholders else 100

    weights = {"structure": 0.20, "data": 0.30, "argument": 0.30, "expression": 0.20}
    dims = {"structure": structure_score, "data": data_score, "argument": argument_score, "expression": expression_score}
    score = round(sum(dims[k] * w for k, w in weights.items()))

    blockers: list[dict] = []
    warnings: list[dict] = []
    if placeholders > 0:
        blockers.append({"code": "placeholder_remaining", "message": f"正文残留 {placeholders} 处“（待补充）”占位"})
    if fin_placeholder:
        blockers.append({"code": "finance_ungrounded", "message": "财务仍为“详见第六章测算”占位，未接入 finance-calc"})
    if grounding == "ungrounded" and not ev_items:
        blockers.append({"code": "need_grounding", "message": "文档未接地，无任何证据，需运行专业化增强"})
    c_level = [i for i in ev_items if i.get("evidence_level") == "C"]
    if c_level:
        warnings.append({"code": "c_level_evidence", "message": f"使用了 {len(c_level)} 条 C 级(未审核)证据，关键结论建议人工复核"})
    owner = (meta.get("requirement") or {}).get("owner") or {}
    if not owner.get("name"):
        warnings.append({"code": "owner_missing", "message": "建设单位工商信息缺失，正文以占位表述，发布前须补充"})

    # PG4（方案 §11.3 G5）：财务审计完整性门禁——正文关键数字须可追溯到 run。
    # 仅当文档有财务内容时才校验。strict_audit=true（LVKE_STRICT_AUDIT）下升为 blocker。
    if has_finance:
        import os

        strict = str(os.environ.get("LVKE_STRICT_AUDIT", "")).lower() in ("1", "true", "yes")
        try:
            from lvke_mcp.domains.finance import run_store

            au = {
                "has_run": bool(run_store.latest_run(workspace_id)),
                "has_approved": bool(run_store.get_approved_run(workspace_id)),
            }
        except Exception:  # noqa: BLE001 - run 存储不可用不阻断评分
            au = {}
        if not au.get("has_run"):
            item = {"code": "audit_no_run",
                    "message": "财务已接地但无测算留痕（未落 calculation_run），正文数字不可追溯"}
            (blockers if strict else warnings).append(item)
        else:
            # 绑定 run 必须等于 approved run（MCP 版门禁：绑定由调用方显式传入）
            try:
                from lvke_mcp.domains.finance import gate as finance_gate

                bind_chk = finance_gate.assert_publish_finance_binding(
                    workspace_id,
                    strict=True,
                )
                for b in bind_chk.get("blockers") or []:
                    if b.get("code") == "finance_run_not_approved":
                        continue
                    blockers.append(b)
                for w in bind_chk.get("warnings") or []:
                    warnings.append(w)
            except Exception:  # noqa: BLE001
                pass
            if not au.get("has_approved"):
                blockers.append({
                    "code": "finance_run_not_approved",
                    "message": "财务测算尚未批准，终稿正文不得引用未批准运行的结果",
                })

    # 【P0-6 / 方案 §9.4】财务发布门禁：投资口径歧义未确认、勾稽失败 → 阻断终稿发布。
    if has_finance:
        try:
            from lvke_mcp.domains.finance import run_service

            fm_r = run_service.get_workspace_finance_run(workspace_id, view="full")
            if not fm_r.get("available"):
                fm_r = {}
        except Exception:  # noqa: BLE001
            fm_r = {}
        if not fm_r:
            try:
                fm_r = svc.workspace_finance_model(workspace_id)
            except Exception:  # noqa: BLE001
                fm_r = {}
        if fm_r.get("available"):
            scope = (fm_r.get("investment") or {}).get("scope_status") or {}
            if scope.get("status") == "ambiguous":
                blockers.append({
                    "code": "investment_scope_ambiguous",
                    "message": f"投资口径歧义未确认，仅可匡算预览：{scope.get('reason') or '总投资分项口径不自洽'}",
                })
            try:
                from lvke_mcp.domains.finance import finance_model

                recon = finance_model.check_consistency(fm_r)
            except Exception:  # noqa: BLE001
                recon = []
            failed = [c for c in recon if not c.get("ok")]
            for chk in failed:
                blockers.append({
                    "code": "finance_reconciliation_failed",
                    "message": f"财务勾稽不一致：{chk.get('rule')}（{chk.get('detail')}）",
                })

    # 【P0-6】发布可否：任一 blocker 存在即不可发布终稿（匡算预览不受影响）。
    publishable = not blockers
    blocking_issues = [b.get("code") for b in blockers]

    readiness = {
        "publishable": publishable,
        "blocking_issues": blocking_issues,
        "workspace_id": workspace_id, "score": score, "dimension_scores": dims,
        "grounding_state": grounding, "blockers": blockers, "warnings": warnings,
        "manual_review_required": score < 85 or bool(c_level),
        "computed_at": svc._now_iso(),  # noqa: SLF001 - 同域边界
    }
    if persist:
        _persist_readiness(workspace_id, readiness)
    return readiness