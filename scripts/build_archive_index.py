#!/usr/bin/env python3
"""Build the lvke-archive SQLite/BM25 index from this repository's docs."""

from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from lvke_mcp.servers.lvke_archive.archive_index import bm25_build, chunker, indicators, metadata
from lvke_mcp.servers.lvke_archive.archive_index.schema import open_db


def _report_id(relative_path: Path) -> str:
    digest = hashlib.sha256(relative_path.as_posix().encode("utf-8")).hexdigest()
    return f"r-{digest[:16]}"


def _origin(relative_path: Path) -> str:
    first = relative_path.parts[0] if relative_path.parts else ""
    if first in {"client-materials", "恒立酒店资产收购", "项目流程"}:
        return "client"
    if first in {"财务测算表格及文字说明（带公式）(3)", "研报资料库"}:
        return "method"
    return "project"


def build(docs_root: Path, output_dir: Path) -> dict[str, int | str]:
    docs_root = docs_root.resolve(strict=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_files = sorted(path for path in docs_root.rglob("*.md") if path.is_file())
    if not markdown_files:
        raise ValueError("docs root contains no Markdown files")

    fd, temporary_name = tempfile.mkstemp(prefix="metadata-", suffix=".sqlite", dir=output_dir)
    os.close(fd)
    temporary_db = Path(temporary_name)
    reports_count = chunks_count = 0
    bm25_rows: list[tuple[str, str, int, str]] = []
    try:
        conn = open_db(temporary_db)
        for path in markdown_files:
            relative = path.relative_to(docs_root)
            text = path.read_text(encoding="utf-8", errors="replace")
            report_id = _report_id(relative)
            report_meta = metadata.extract(
                report_id=report_id,
                corpus_origin=_origin(relative),
                relative_path=relative.as_posix(),
                full_text=text,
                haystack_hint=f"{relative.as_posix()}\n{text[:2000]}",
            )
            conn.execute(
                """INSERT INTO reports (
                    report_id,title,corpus_origin,industry,project_type,report_type,
                    year,region,scale_bucket,source_path,char_len,quality_flag,brief,indexed_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    report_meta.report_id, report_meta.title, report_meta.corpus_origin,
                    report_meta.industry, report_meta.project_type, report_meta.report_type,
                    report_meta.year, report_meta.region, "", report_meta.source_path,
                    report_meta.char_len, report_meta.quality_flag, report_meta.brief,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            report_chunks = chunker.split(report_id, text)
            for row in report_chunks:
                conn.execute(
                    """INSERT INTO chunks (
                        chunk_id,report_id,chapter_no,chapter_title,level,content,
                        content_redacted,char_len,parent_chunk_id
                    ) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        row.chunk_id, row.report_id, row.chapter_no, row.chapter_title,
                        row.level, row.content, row.content, row.char_len, row.parent_chunk_id,
                    ),
                )
                bm25_rows.append((row.chunk_id, row.report_id, row.chapter_no, row.content))
            indicator = indicators.extract_for_report(report_id, (row.content for row in report_chunks))
            values = asdict(indicator)
            conn.execute(
                """INSERT INTO indicators (
                    report_id,total_investment,construction_invest,working_capital,
                    equity_ratio,project_irr,capital_irr,payback_years,scale_metric,confidence
                ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                tuple(values[key] for key in (
                    "report_id", "total_investment", "construction_invest", "working_capital",
                    "equity_ratio", "project_irr", "capital_irr", "payback_years",
                    "scale_metric", "confidence",
                )),
            )
            reports_count += 1
            chunks_count += len(report_chunks)
        conn.commit()
        conn.close()
        with sqlite3.connect(temporary_db) as check:
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("archive SQLite integrity check failed")
        os.replace(temporary_db, output_dir / "metadata.sqlite")
    finally:
        temporary_db.unlink(missing_ok=True)

    try:
        bm25_build.build(bm25_rows).save(output_dir / "bm25")
        bm25_status = "built"
    except (ImportError, ValueError):
        bm25_status = "unavailable"
    return {
        "reports": reports_count,
        "chunks": chunks_count,
        "bm25": bm25_status,
        "metadata_sqlite": str(output_dir / "metadata.sqlite"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs", type=Path, default=Path(__file__).resolve().parents[1] / "docs")
    parser.add_argument("--out", type=Path, default=Path.home() / ".lvke" / "archive_index")
    args = parser.parse_args()
    print(build(args.docs, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
