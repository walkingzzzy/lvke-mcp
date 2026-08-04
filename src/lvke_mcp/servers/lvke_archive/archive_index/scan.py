"""Stage 1 · 扫描 统一模板库 下的 .md 文件并生成稳定 report_id。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

# 顶层目录 → corpus_origin
CORPUS_MAP = {
    "三案例库": "public",
    "绿科案例库": "lvke",
    "6.29新增案例": "lvke",
}


@dataclass(slots=True)
class RawFile:
    path: Path
    relative: Path
    corpus_origin: str
    report_id: str


def stable_id(rel_path: Path) -> str:
    """Stable, deterministic id derived from the relative path."""
    h = hashlib.sha1(str(rel_path).replace("\\", "/").encode("utf-8")).hexdigest()
    return f"r-{h[:12]}"


def detect_corpus(rel_path: Path) -> str:
    parts = rel_path.parts
    if not parts:
        return "unknown"
    return CORPUS_MAP.get(parts[0], "unknown")


def iter_archive(root: Path) -> list[RawFile]:
    """Walk the unified template library and yield .md files.

    Skips legacy/auxiliary files (README.md, conversion_log.csv 等),只看 md/ 下的内容。
    """
    files: list[RawFile] = []
    for md in sorted(root.rglob("*.md")):
        rel = md.relative_to(root)
        # 跳过 legacy / README 等元数据
        parts = {p.lower() for p in rel.parts}
        if any(name in parts for name in ("legacy", "readme.md")):
            continue
        if rel.name.lower() == "readme.md":
            continue
        corpus = detect_corpus(rel)
        if corpus == "unknown":
            continue
        files.append(
            RawFile(
                path=md,
                relative=rel,
                corpus_origin=corpus,
                report_id=stable_id(rel),
            )
        )
    return files
