from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1] / "src" / "lvke_mcp" / "standards" / "ndrc_feasibility_2023"
MANIFEST_PATH = ROOT / "standard_manifest.json"
MAIN_HEADING = re.compile(r"^([一二三四五六七八九十]+)、(.+)$")
SUB_HEADING = re.compile(r"^（([一二三四五六七八九十]+)）(.+)$")
PAGE_NUMBER = re.compile(r"^(?:-\s*)?\d+(?:\s*-)?$")
CN_NUMBERS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "十一": 11, "十二": 12,
}


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clean_line(value: str) -> str:
    return re.sub(r"\s+", "", value).strip()


def _flush_clause(clause: dict[str, Any] | None, target: list[dict[str, Any]]) -> None:
    if clause is None:
        return
    body = "".join(clause.pop("_body", [])).strip()
    clause["body"] = body
    clause["text_hash"] = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
    target.append(clause)


def extract_document(document: dict[str, Any]) -> dict[str, Any]:
    path = ROOT / str(document["path"])
    reader = PdfReader(path)
    clauses: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    main_number = 0
    sub_number = 0

    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for raw_line in text.splitlines():
            line = _clean_line(raw_line)
            if not line or PAGE_NUMBER.fullmatch(line):
                continue
            main_match = MAIN_HEADING.fullmatch(line)
            sub_match = SUB_HEADING.fullmatch(line)
            if main_match:
                _flush_clause(current, clauses)
                main_number = CN_NUMBERS[main_match.group(1)]
                sub_number = 0
                current = {
                    "clause_id": f"{document['id']}.{main_number}",
                    "level": 1,
                    "number": main_match.group(1),
                    "title": main_match.group(2),
                    "page_start": page_number,
                    "page_end": page_number,
                    "_body": [],
                }
                continue
            if sub_match and main_number:
                _flush_clause(current, clauses)
                sub_number = CN_NUMBERS[sub_match.group(1)]
                current = {
                    "clause_id": f"{document['id']}.{main_number}.{sub_number}",
                    "parent_id": f"{document['id']}.{main_number}",
                    "level": 2,
                    "number": sub_match.group(1),
                    "title": sub_match.group(2),
                    "page_start": page_number,
                    "page_end": page_number,
                    "_body": [],
                }
                continue
            if current is not None:
                current["page_end"] = page_number
                current["_body"].append(line)

    _flush_clause(current, clauses)
    return {
        "document_id": document["id"],
        "title": document["title"],
        "path": document["path"],
        "sha256": document["sha256"],
        "page_count": len(reader.pages),
        "clause_count": len(clauses),
        "clauses": clauses,
    }


def build(root: Path = ROOT) -> dict[str, Any]:
    manifest = json.loads((root / "standard_manifest.json").read_text(encoding="utf-8"))
    source_issues: list[str] = []
    documents: list[dict[str, Any]] = []
    for document in manifest["documents"]:
        path = root / document["path"]
        if not path.is_file():
            source_issues.append(f"missing:{document['id']}")
            continue
        actual_hash = _sha256(path)
        if actual_hash != document["sha256"]:
            source_issues.append(f"sha256_mismatch:{document['id']}")
            continue
        parsed = extract_document(document)
        if parsed["page_count"] != document["pages"]:
            source_issues.append(f"page_count_mismatch:{document['id']}")
        documents.append(parsed)

    notice = root / manifest["notice"]["path"]
    if not notice.is_file():
        source_issues.append("missing:notice")
    elif _sha256(notice) != manifest["notice"]["sha256"]:
        source_issues.append("sha256_mismatch:notice")

    source_fingerprint = "sha256:" + hashlib.sha256(
        _stable_json({
            "notice": manifest["notice"]["sha256"],
            "documents": [item["sha256"] for item in manifest["documents"]],
        }).encode("utf-8")
    ).hexdigest()
    result = {
        "schema_version": "ndrc-feasibility-clauses.v1",
        "standard_id": manifest["standard_id"],
        "document_no": manifest["document_no"],
        "effective_from": manifest["effective_from"],
        "source_fingerprint": source_fingerprint,
        "source_valid": not source_issues,
        "source_issues": source_issues,
        "documents": documents,
    }
    output_dir = root / "parsed"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "clauses.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the pinned NDRC 2023 clause tree")
    parser.add_argument("--check", action="store_true", help="verify generated output is current")
    args = parser.parse_args()
    output_path = ROOT / "parsed" / "clauses.json"
    previous = output_path.read_text(encoding="utf-8") if output_path.is_file() else ""
    result = build()
    current = output_path.read_text(encoding="utf-8")
    if args.check and previous != current:
        print("generated clause tree is stale")
        return 1
    print(json.dumps({
        "source_valid": result["source_valid"],
        "source_issues": result["source_issues"],
        "source_fingerprint": result["source_fingerprint"],
        "documents": [
            {"id": item["document_id"], "clauses": item["clause_count"]}
            for item in result["documents"]
        ],
    }, ensure_ascii=False))
    return 0 if result["source_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())