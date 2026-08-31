"""policy-search MCP server 入口(stdio)。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any


from lvke_mcp.runtime.coordination import build_coordination  # noqa: E402
from lvke_mcp.runtime.logging import get_logger  # noqa: E402
from lvke_mcp.runtime.responses import err, ok  # noqa: E402
from lvke_mcp.runtime.stdio import StdioServer  # noqa: E402

SERVER_NAME = "policy-search"
SERVER_VERSION = "0.1.0"
logger = get_logger(SERVER_NAME)

#: 只认 ISO 日历日 ``YYYY-MM-DD``。见 ``_parse_iso_date`` 的"宽进即失效"说明。
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

_QUERY_ALIASES = {
    "光伏": {"光伏", "太阳能", "可再生能源", "新能源", "新型电力系统"},
    "太阳能": {"太阳能", "光伏", "可再生能源"},
    "新能源": {"新能源", "可再生能源", "新型电力系统", "光伏", "储能"},
    "补贴": {"补贴", "支持", "电价", "市场", "示范"},
}


def _query_groups(keyword: str) -> list[set[str]]:
    tokens = [token for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", keyword) if token]
    expanded: list[set[str]] = []
    for token in tokens:
        # “政策/文件/要求”是检索意图词，不应要求政策正文逐字包含。
        token = re.sub(r"(?:政策|文件|要求)$", "", token)
        if not token:
            continue
        aliases = set(_QUERY_ALIASES.get(token, {token}))
        # 连续中文查询中识别关键行业词，例如“湖北光伏补贴”。
        for key, values in _QUERY_ALIASES.items():
            if key in token:
                aliases.update(values)
        expanded.append(aliases)
    return expanded


def _normalize_region(value: str) -> str:
    return str(value or "").strip().removesuffix("省").removesuffix("市")


def _region_matches(query: str, value: str) -> bool:
    q = _normalize_region(query)
    v = _normalize_region(value)
    return value == "全国" or not q or q == v or q in v or v in q


def _topic_matches(query: str, topics: list[str]) -> bool:
    hay = " ".join(str(item) for item in topics)
    groups = _query_groups(str(query or ""))
    return not groups or all(any(alias in hay for alias in group) for group in groups)


@dataclass
class PolicyStorage:
    data_dir: Path
    _records: list[dict[str, Any]] = field(default_factory=list, init=False)
    _loaded: bool = field(default=False, init=False)

    def _load(self) -> None:
        if self._loaded:
            return
        path = self.data_dir / "policies.json"
        if path.exists():
            self._records = json.loads(path.read_text(encoding="utf-8"))
        else:
            self._records = []
        self._loaded = True

    def list_all(self) -> list[dict[str, Any]]:
        self._load()
        return list(self._records)

    def search(
        self,
        keyword: str | None = None,
        year: int | None = None,
        region: str | None = None,
        level: str | None = None,
        topic: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self._load()
        kw = (keyword or "").strip().lower()
        out: list[dict[str, Any]] = []
        for rec in self._records:
            if region and not _region_matches(region, str(rec.get("region") or "")):
                continue
            if level and rec.get("level") != level:
                continue
            if topic and not _topic_matches(topic, list(rec.get("topics") or [])):
                continue
            if year:
                ed = rec.get("effective_date") or rec.get("issue_date") or ""
                if not ed.startswith(str(year)):
                    continue
            if kw:
                hay_parts = [
                    rec.get("title", ""),
                    rec.get("summary", ""),
                    rec.get("doc_number", ""),
                    rec.get("issuer", ""),
                    " ".join(rec.get("topics") or []),
                    " ".join(rec.get("key_articles") or []),
                ]
                hay = " ".join(hay_parts).lower()
                groups = _query_groups(kw)
                if groups and not all(any(alias.lower() in hay for alias in group) for group in groups):
                    continue
            out.append(rec)
            if len(out) >= max(1, limit):
                break
        return out

    def get(self, policy_id: str) -> dict[str, Any] | None:
        self._load()
        for rec in self._records:
            if rec.get("policy_id") == policy_id:
                return rec
        return None

    def find_by_citation(self, citation: str) -> dict[str, Any] | None:
        """按文号或标题模糊查询。"""

        self._load()
        c = (citation or "").strip()
        if not c:
            return None
        # 优先按文号(如 国发〔2021〕23 号)
        # 把全角中括号和数字都规约化
        normalized = re.sub(r"[\s〔〕\[\]【】()]", "", c).lower()
        for rec in self._records:
            doc_num = re.sub(
                r"[\s〔〕\[\]【】()]", "", rec.get("doc_number", "")
            ).lower()
            if doc_num and normalized in doc_num:
                return rec
        # 回退按 title 包含
        for rec in self._records:
            title = rec.get("title", "").lower()
            if c.lower() in title:
                return rec
        return None


def resolve_data_dir() -> Path:
    env_dir = os.environ.get("LVKE_POLICY_DATA_DIR", "").strip()
    if env_dir:
        p = Path(env_dir).expanduser()
        if p.exists():
            return p
    base = Path(__file__).resolve().parent
    data_dir = base / "data"
    if (data_dir / "policies.json").exists():
        return data_dir
    return base / "seed"


_storage: PolicyStorage | None = None


def _get_storage() -> PolicyStorage:
    global _storage
    if _storage is None:
        d = resolve_data_dir()
        logger.info("使用政策数据目录:%s", d)
        _storage = PolicyStorage(data_dir=d)
    return _storage


def _tool_search_policy(args: dict) -> dict:
    year = args.get("year")
    if year is not None and not isinstance(year, int):
        return err(f"{SERVER_NAME}.invalid_argument", "year 必须是整数")
    out = _get_storage().search(
        keyword=args.get("keyword"),
        year=year,
        region=args.get("region"),
        level=args.get("level"),
        topic=args.get("topic"),
        limit=int(args.get("limit") or 20),
    )
    return ok(
        {"count": len(out), "items": out},
        source=f"{SERVER_NAME}.search_policy",
    )


def _tool_get_policy_full(args: dict) -> dict:
    policy_id = args.get("policy_id")
    if not isinstance(policy_id, str) or not policy_id:
        return err(f"{SERVER_NAME}.invalid_argument", "policy_id 必须是非空字符串")
    rec = _get_storage().get(policy_id)
    if rec is None:
        return err(f"{SERVER_NAME}.not_found", f"未找到 policy_id={policy_id}")
    return ok(rec, source=f"{SERVER_NAME}.get_policy_full")


def _parse_iso_date(value: Any) -> date | None:
    """严格解析 ``YYYY-MM-DD``；解析不了就返回 None，绝不猜。

    只接受 ISO 日历日期。``date.fromisoformat`` 还会吃下 ``20000101`` 和
    ``2000-W01-1`` 这类写法，但那不是本域的输入约定；宽进会把"调用方写错格式"
    伪装成"时点已被正确采纳"，正是 R1 的失效模式。
    """

    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _ISO_DATE_RE.fullmatch(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _as_of_verdict(as_of: str, effective_date: Any) -> dict[str, Any]:
    """按查询时点判定政策在该时点是否已生效。

    缺陷（R1）：``as_of`` 此前只被回显进响应，判定完全走 ``status == "active"``。
    于是 ``as_of=1990-01-01`` 查生效日 2000-01-01 的招标投标法会返回
    ``active:true`` 并同时回显 ``requested_as_of``/``effective_date`` —— 回显字段
    让"时点已被采纳"看起来成立，实际是把「当前有效」冒充成「查询时点有效」。

    这里只判定"时点相对生效日"这一件确定的事，三种结局都显式区分：
    - ``not_yet_effective``：as_of 严格早于 effective_date，此时不得报 active；
    - ``in_effect_at_as_of``：as_of 不早于 effective_date，可沿用库内 status；
    - ``undeterminable``：as_of 或 effective_date 缺失/格式不可解析 —— 诚实说
      不可判定（``active=None``），不拿"当前 status"顶替时点判定。
    """

    verdict: dict[str, Any] = {"requested_as_of": as_of}
    as_of_date = _parse_iso_date(as_of)
    effective = _parse_iso_date(effective_date)
    if as_of_date is None:
        verdict["as_of_assessment"] = "undeterminable"
        verdict["as_of_reason"] = (
            "as_of 不是可解析的 YYYY-MM-DD 日期，无法按时点判定生效状态"
        )
        return verdict
    if effective is None:
        verdict["as_of_assessment"] = "undeterminable"
        verdict["as_of_reason"] = (
            "本地政策记录缺少可解析的 effective_date，无法按时点判定生效状态；"
            "不得据此断言该政策在查询时点有效"
        )
        return verdict
    verdict["effective_date_parsed"] = effective.isoformat()
    if as_of_date < effective:
        verdict["as_of_assessment"] = "not_yet_effective"
        verdict["as_of_reason"] = (
            f"查询时点 {as_of_date.isoformat()} 早于生效日 {effective.isoformat()}，"
            "该政策在查询时点尚未生效"
        )
    else:
        verdict["as_of_assessment"] = "in_effect_at_as_of"
        verdict["as_of_reason"] = (
            f"查询时点 {as_of_date.isoformat()} 不早于生效日 {effective.isoformat()}"
        )
    return verdict


def _tool_verify_policy_active(args: dict) -> dict:
    citation = args.get("citation")
    if not isinstance(citation, str) or not citation.strip():
        return err(f"{SERVER_NAME}.invalid_argument", "citation 必须是非空字符串")
    as_of = args.get("as_of")
    as_of = as_of.strip() if isinstance(as_of, str) else ""
    rec = _get_storage().find_by_citation(citation)
    if rec is None:
        payload: dict[str, Any] = {
            "citation": citation,
            "matched": False,
            "active": None,
            "message": "未在本地政策库中找到匹配项;无法验证。建议人工复核或扩充数据库。",
        }
        if as_of:
            payload["requested_as_of"] = as_of
            payload["as_of_assessment"] = "undeterminable"
            payload["as_of_reason"] = "未匹配到政策记录，无生效日可比对"
        return ok(payload, source=f"{SERVER_NAME}.verify_policy_active")
    status = rec.get("status", "unknown")
    payload = {
        "citation": citation,
        "matched": True,
        "active": status == "active",
        "policy_id": rec.get("policy_id"),
        "title": rec.get("title"),
        "doc_number": rec.get("doc_number"),
        "status": status,
        "effective_date": rec.get("effective_date"),
        "issuer": rec.get("issuer"),
    }
    if not as_of:
        # 未传 as_of：语义仍是"按库内 status 判定当前是否有效"，行为不变。
        payload["as_of_assessment"] = "not_requested"
        return ok(payload, source=f"{SERVER_NAME}.verify_policy_active")

    verdict = _as_of_verdict(as_of, rec.get("effective_date"))
    payload.update(verdict)
    assessment = verdict["as_of_assessment"]
    warnings: list[str] = []
    if assessment == "not_yet_effective":
        # fail-closed：时点早于生效日，active 必须为 False，与库内 status 无关。
        payload["active"] = False
        warnings.append(verdict["as_of_reason"])
    elif assessment == "undeterminable":
        # 不可判定不等于有效：把 active 收回 None，不让"当前 active"冒充时点结论。
        payload["active"] = None
        warnings.append(verdict["as_of_reason"])
    result = ok(payload, source=f"{SERVER_NAME}.verify_policy_active")
    if warnings:
        result["warnings"] = warnings
        result["next_actions"] = [
            "按查询时点复核政策适用性；引用前确认该时点的有效版本"
        ]
        result["coordination"] = build_coordination(result, server_name=SERVER_NAME)
    return result


def build_server() -> StdioServer:
    server = StdioServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        logger=logger,
    )
    server.register_tool(
        name="search_policy",
        description="检索政策文件。支持关键词 / 年份 / 地区 / 层级 / 主题过滤。",
        input_schema={
            "type": "object",
            "properties": {
                "keyword": {"type": "string"},
                "year": {"type": "integer"},
                "region": {"type": "string"},
                "level": {"type": "string", "description": "国家级 / 省级 / 市级"},
                "topic": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
        },
        handler=_tool_search_policy,
    )
    server.register_tool(
        name="get_policy_full",
        description="按 policy_id 拿政策全文摘要、生效日期、关键条款。",
        input_schema={
            "type": "object",
            "properties": {"policy_id": {"type": "string"}},
            "required": ["policy_id"],
        },
        handler=_tool_get_policy_full,
    )
    server.register_tool(
        name="verify_policy_active",
        description=(
            "按文号或标题校验报告引用的政策是否仍生效。"
            "传 as_of 时按该时点与 effective_date 比对：时点早于生效日返回 active=false;"
            "时点或生效日不可解析返回 active=null(不可判定)。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "citation": {
                    "type": "string",
                    "description": "政策文号(如 国发〔2021〕23 号)或标题",
                },
                "as_of": {
                    "type": "string",
                    "description": "查询时点 YYYY-MM-DD;省略时按库内 status 判定当前是否有效",
                },
            },
            "required": ["citation"],
        },
        handler=_tool_verify_policy_active,
    )
    return server


def main() -> None:
    server = build_server()
    logger.info("%s server v%s 启动(stdio)", SERVER_NAME, SERVER_VERSION)
    server.serve_forever()


if __name__ == "__main__":
    main()
