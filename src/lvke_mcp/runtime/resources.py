"""``lvke://`` Resource URI 构造/解析与资源描述 helper（方案 §13）。

URI 形态与 :class:`lvke_mcp.runtime.storage.JSONArtifactStore`
保持同一口径：

    lvke://{domain}/workspaces/{workspace_id}/{segment}/{object_id}

本模块不另建存储，只委托 ``artifact_store`` 的 id 安全校验，供
Server 侧构造 resources/list 描述与解析读请求时复用，避免各
Server 手写字符串切分。多段 artifact URI（如 deep-research 包内
``.../packages/{id}/{artifact}``）由各自 package service 负责，
不在本模块强行统一。
"""

from __future__ import annotations

from dataclasses import dataclass

from mcp import types
from pydantic import AnyUrl

from lvke_mcp.runtime.storage import require_safe_id

URI_SCHEME = "lvke"


@dataclass(frozen=True)
class ParsedResourceURI:
    """一条标准四段 lvke URI 的解析结果。"""

    domain: str
    workspace_id: str
    segment: str
    object_id: str


def build_uri(domain: str, workspace_id: str, segment: str, object_id: str) -> str:
    """构造标准四段 lvke URI（与 JSONArtifactStore.uri 同格式）。"""

    return (
        f"{URI_SCHEME}://{require_safe_id(domain, 'domain')}/workspaces/"
        f"{require_safe_id(workspace_id, 'workspace_id')}/"
        f"{require_safe_id(segment, 'segment')}/"
        f"{require_safe_id(object_id, 'object_id')}"
    )


def parse_uri(uri: str, *, domain: str | None = None) -> ParsedResourceURI | None:
    """解析标准四段 lvke URI；不匹配（含非法 id）返回 None，不抛异常。"""

    text = str(uri or "")
    prefix = f"{URI_SCHEME}://"
    if not text.startswith(prefix):
        return None
    parts = text[len(prefix) :].split("/")
    if len(parts) != 5 or parts[1] != "workspaces":
        return None
    found_domain, _, workspace_id, segment, object_id = parts
    if domain is not None and found_domain != domain:
        return None
    try:
        return ParsedResourceURI(
            domain=require_safe_id(found_domain, "domain"),
            workspace_id=require_safe_id(workspace_id, "workspace_id"),
            segment=require_safe_id(segment, "segment"),
            object_id=require_safe_id(object_id, "object_id"),
        )
    except ValueError:
        return None


def resource_descriptor(
    uri: str,
    name: str,
    *,
    description: str | None = None,
    mime_type: str = "application/json",
) -> types.Resource:
    """构造 resources/list 用的官方 Resource 描述对象。"""

    return types.Resource(
        uri=AnyUrl(uri),
        name=name,
        description=description,
        mimeType=mime_type,
    )
