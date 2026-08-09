"""Markdown local-link extraction for the Skill delivery link invariant.

此前的守门正则是 ``\\]\\(([^)#]+\\.md)\\)``，只认"以 .md 结尾、且不含 # 的内联链接"，
于是三类真实死链完全落在正则之外从不求值：

- ``foo.md#section``：带锚点，被 ``[^)#]+`` 排除；
- ``[text][ref]`` + ``[ref]: path``：引用式定义，不匹配 ``](...)``；
- ``references/table.csv``、``assets/x.png``：非 .md 本地资源。

不引入第三方 Markdown 解析依赖：这里做的是行级 CommonMark 子集扫描，覆盖上述三类形式，
并跳过代码围栏与行内代码（否则示例代码里的路径会被误判为死链）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# 内联链接/图片：![alt](dest) 与 [text](dest)。dest 可带 <>、标题、锚点。
_INLINE = re.compile(r"!?\[(?:[^\]\\]|\\.)*\]\(\s*(<[^>]*>|[^()\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)")
# 引用式定义：行首 [label]: dest ["title"]
_REFERENCE_DEFINITION = re.compile(
    r"^[ ]{0,3}\[(?:[^\]\\]|\\.)+\]:\s*(<[^>]*>|\S+)", re.MULTILINE
)
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"`[^`\n]*`")

_EXTERNAL_SCHEMES = ("http://", "https://", "mailto:", "ftp://", "data:", "tel:", "lvke://")


@dataclass(frozen=True)
class LocalLink:
    source: Path
    target: str
    path_part: str
    anchor: str

    @property
    def resolved(self) -> Path:
        return (self.source.parent / self.path_part).resolve()


def _strip_code(text: str) -> str:
    """Blank out fenced blocks and inline code so example paths are not linted."""

    lines: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        match = _FENCE.match(line)
        if fence is None and match:
            fence = match.group(1)[0]
            lines.append("")
            continue
        if fence is not None:
            if match and match.group(1)[0] == fence:
                fence = None
            lines.append("")
            continue
        lines.append(_INLINE_CODE.sub("``", line))
    return "\n".join(lines)


def local_links(markdown: Path) -> list[LocalLink]:
    """Return every link in ``markdown`` that points at a local file."""

    text = _strip_code(markdown.read_text(encoding="utf-8"))
    destinations = [
        *_INLINE.findall(text),
        *_REFERENCE_DEFINITION.findall(text),
    ]
    links: list[LocalLink] = []
    for raw in destinations:
        target = raw.strip()
        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1].strip()
        if not target or target.startswith("#"):
            # 纯页内锚点没有文件目标，不在本不变量范围内。
            continue
        if target.lower().startswith(_EXTERNAL_SCHEMES) or target.startswith("//"):
            continue
        path_part, _, anchor = target.partition("#")
        path_part = path_part.strip()
        if not path_part or Path(path_part).is_absolute():
            # 绝对路径由另一条"禁止绝对路径"断言负责，不在这里重复判定。
            continue
        links.append(
            LocalLink(
                source=markdown,
                target=target,
                path_part=path_part,
                anchor=anchor.strip(),
            )
        )
    return links


def broken_local_links(root: Path, *, relative_to: Path | None = None) -> list[str]:
    """Return ``file -> target`` for every local link under ``root`` that misses."""

    base = relative_to or root
    broken: list[str] = []
    for markdown in sorted(root.rglob("*.md")):
        for link in local_links(markdown):
            resolved = link.resolved
            if resolved.is_file() or resolved.is_dir():
                continue
            broken.append(f"{markdown.relative_to(base)} -> {link.target}")
    return broken


def count_local_links(root: Path) -> int:
    return sum(len(local_links(markdown)) for markdown in sorted(root.rglob("*.md")))
