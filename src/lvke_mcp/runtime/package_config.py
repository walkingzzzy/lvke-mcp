"""Single packaged-configuration root with deterministic versioned loading.

在此之前，"随 wheel 打包的配置目录"在仓库里有五处各自的锚点，深度还不一样：

- ``servers/lvke_deliverable_review/_service/base.py`` 的 ``parents[3] / "config"``
- ``servers/lvke_deliverable_review/rules.py`` 的 ``parents[2] / "config"``
- ``domains/project_planning/_service/context.py`` 的 ``parents[3] / "config"``
- ``servers/lvke_project_planning/_lifecycle/build_scale.py`` 的 ``parents[3]``
- ``servers/lvke_zero_material_delivery/_service/promotion.py`` 的 ``parents[3]``

各写一套的直接后果已经发生过一次：其中一处 ``parents[N]`` 数错，配置在开发树上
能读到、装成 wheel 后指向包外（见记忆 runtime-gap-fixes-20260808）。因此这里给出
唯一入口，并允许部署期用 ``LVKE_MCP_PACKAGE_CONFIG_DIR`` 整体改指向。

注意与 ``runtime/config.py`` 的 ``Config.config_dir`` 的分工：那个是**运行时可写**
的用户配置目录（``LVKE_MCP_CONFIG_DIR``，默认 ``<data_dir>/config``）；本模块是
**随包分发的只读**配置根（``src/lvke_mcp/config``）。两者曾被混为一谈，
``external_corpora.py`` 至今同时持有两个常量——不要把它们合并。

``load_versioned_config`` 只做三件确定性的事：读、校验声明的 ``schema_version``、
按规范 JSON 算 ``content_hash``。它刻意**不**补默认值、不吞损坏文件：配置不可信时
抛 ``PackageConfigError``，由调用方决定是阻断还是降级——静默回退到"某个默认模板"
正是"报告内容其实由代码决定"的老毛病。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from lvke_mcp.runtime.storage import sha256_json

#: 部署期整体改指向打包配置根。与 ``LVKE_MCP_CONFIG_DIR``（运行时可写目录）
#: 是两个不同的东西，不要互相顶替。
PACKAGE_CONFIG_DIR_ENV = "LVKE_MCP_PACKAGE_CONFIG_DIR"

#: ``src/lvke_mcp/config``：本文件在 ``src/lvke_mcp/runtime/`` 下，故上溯一级。
_PACKAGED_ROOT = Path(__file__).resolve().parents[1] / "config"


class PackageConfigError(RuntimeError):
    """打包配置缺失、不可解析或 schema_version 不符。

    Attributes:
        code: 机器可读的失败类别，直接用作阻断码。
        message: 面向人的说明。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def package_config_dir() -> Path:
    """Return the read-only packaged configuration root."""

    configured = str(os.getenv(PACKAGE_CONFIG_DIR_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser()
    return _PACKAGED_ROOT


def package_config_path(*parts: str) -> Path:
    """Resolve one packaged configuration path, refusing to escape the root.

    ``..`` 与绝对路径段都会被拒绝：配置名有时来自请求参数（``template_set_id``），
    不做这道检查就等于把打包配置目录变成任意文件读取入口。
    """

    root = package_config_dir().resolve()
    for part in parts:
        text = str(part)
        if not text or text in {".", ".."} or "/" in text or "\\" in text:
            raise PackageConfigError(
                "package_config_path_invalid",
                f"打包配置路径段非法: {part!r}",
            )
    candidate = root.joinpath(*[str(part) for part in parts])
    try:
        resolved = candidate.resolve()
    except OSError as exc:  # pragma: no cover - 平台相关的解析失败
        raise PackageConfigError(
            "package_config_path_invalid", f"打包配置路径无法解析: {exc}"
        ) from None
    if resolved != root and root not in resolved.parents:
        raise PackageConfigError(
            "package_config_path_escapes_root",
            "打包配置路径逃出配置根",
        )
    return resolved


def load_versioned_config(
    *parts: str,
    expected_schema_version: str = "",
) -> dict[str, Any]:
    """Load one JSON config and stamp it with its canonical ``content_hash``.

    Args:
        parts: 相对打包配置根的路径段。
        expected_schema_version: 非空时必须与文件声明的 ``schema_version`` 精确
            相等。版本不符按失败处理而不是"尽力解析"——旧结构静默半解析出来的
            报告，看起来正常而实际缺章节。

    Returns:
        原文档加上 ``content_hash``（对**去掉该键后的**文档体算，故可重算）。

    Raises:
        PackageConfigError: 文件缺失、非 JSON 对象或 schema_version 不符。
    """

    path = package_config_path(*parts)
    name = "/".join(str(part) for part in parts)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PackageConfigError(
            "package_config_not_found", f"打包配置缺失: {name}"
        ) from None
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageConfigError(
            "package_config_invalid", f"打包配置不可解析: {name} ({exc})"
        ) from None
    if not isinstance(document, dict):
        raise PackageConfigError(
            "package_config_invalid", f"打包配置必须是 JSON 对象: {name}"
        )
    declared = str(document.get("schema_version") or "")
    if expected_schema_version and declared != expected_schema_version:
        raise PackageConfigError(
            "package_config_schema_version_mismatch",
            f"打包配置 schema_version 不符: {name} 声明 {declared!r}，"
            f"要求 {expected_schema_version!r}",
        )
    body = {key: value for key, value in document.items() if key != "content_hash"}
    return {**body, "content_hash": sha256_json(body)}


__all__ = [
    "PACKAGE_CONFIG_DIR_ENV",
    "PackageConfigError",
    "load_versioned_config",
    "package_config_dir",
    "package_config_path",
]
