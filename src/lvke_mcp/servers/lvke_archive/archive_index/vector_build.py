"""Phase A.2 · 向量索引构建（本地 BGE-small-zh）。

设计要点
- 使用 ``sentence-transformers`` 加载 ``BAAI/bge-small-zh-v1.5``（512 维，~95 MB）
- 持久化到 ChromaDB 本地目录（PersistentClient）
- 单批 64 chunks，全 CPU 推理（无 GPU 依赖）
- 仅索引 chapter_no ∈ {1..9} 且 char_len ≥ 80 的 chunks
- 失败时尽量保留已写入的部分（chroma upsert 支持断点续）
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# 离线优先：让 HF 缓存到工作区，下次重启不重新下载
_REPO = Path(__file__).resolve().parents[2]
os.environ.setdefault("HF_HOME", str(_REPO / "data" / "hf_cache"))
os.environ.setdefault("TRANSFORMERS_OFFLINE", "0")  # 首次需联网
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

DEFAULT_MODEL = os.environ.get("LVKE_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
DEFAULT_BATCH = int(os.environ.get("LVKE_EMBEDDING_BATCH", "64"))


@dataclass(slots=True)
class VectorBuildResult:
    indexed: int
    skipped: int
    seconds: float
    model: str


def _load_encoder(model_name: str):
    from sentence_transformers import SentenceTransformer  # type: ignore

    print(f"[vector] loading encoder: {model_name}", flush=True)
    t0 = time.time()
    model = SentenceTransformer(model_name)
    print(f"[vector] encoder ready ({time.time() - t0:.1f}s)", flush=True)
    return model


def _open_chroma(persist_dir: Path):
    import chromadb  # type: ignore

    persist_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(persist_dir))
    coll = client.get_or_create_collection(
        name="lvke_archive_chunks",
        metadata={"hnsw:space": "cosine"},
    )
    return client, coll


def build_vectors(
    db_path: Path,
    out_dir: Path,
    *,
    model_name: str = DEFAULT_MODEL,
    batch: int = DEFAULT_BATCH,
    limit: int = 0,
) -> VectorBuildResult:
    import sqlite3

    print("[stage 6] building vector index ...", flush=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    sql = (
        "SELECT chunk_id, report_id, chapter_no, content, char_len FROM chunks "
        "WHERE chapter_no > 0 AND char_len >= 80"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    conn.close()
    print(f"  candidates: {len(rows)} chunks", flush=True)
    if not rows:
        return VectorBuildResult(indexed=0, skipped=0, seconds=0.0, model=model_name)

    encoder = _load_encoder(model_name)
    persist_dir = out_dir / "vectors"
    _client, coll = _open_chroma(persist_dir)

    indexed = 0
    skipped = 0
    t0 = time.time()
    for i in range(0, len(rows), batch):
        chunk_batch = rows[i : i + batch]
        ids = [r["chunk_id"] for r in chunk_batch]
        texts = []
        metas = []
        for r in chunk_batch:
            c = r["content"]
            if c and len(c) > 2000:
                c = c[:2000]
            texts.append(c or "")
            metas.append({
                "report_id": r["report_id"],
                "chapter_no": int(r["chapter_no"]),
                "char_len": int(r["char_len"]),
            })
        try:
            embs = encoder.encode(
                texts,
                batch_size=min(batch, 32),
                show_progress_bar=False,
                normalize_embeddings=True,
            )
            coll.upsert(
                ids=ids,
                embeddings=embs.tolist() if hasattr(embs, "tolist") else list(embs),
                metadatas=metas,
            )
            indexed += len(ids)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ batch {i//batch} failed: {type(exc).__name__}: {exc}", flush=True)
            skipped += len(ids)
        if (i // batch) % 10 == 0 or i + batch >= len(rows):
            elapsed = time.time() - t0
            done = i + len(chunk_batch)
            rate = done / max(elapsed, 0.01)
            eta = (len(rows) - done) / max(rate, 0.01)
            print(
                f"  [vector] {done}/{len(rows)} embedded "
                f"({elapsed:.0f}s, {rate:.1f} chunk/s, ETA {eta:.0f}s)",
                flush=True,
            )

    seconds = time.time() - t0
    print(
        f"[stage 6] done: indexed={indexed} skipped={skipped} "
        f"in {seconds:.1f}s; persist_dir={persist_dir}",
        flush=True,
    )
    return VectorBuildResult(
        indexed=indexed, skipped=skipped, seconds=seconds, model=model_name
    )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path,
                        default=_REPO / "data" / "archive_index" / "metadata.sqlite")
    parser.add_argument("--out", type=Path, default=_REPO / "data" / "archive_index")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--limit", type=int, default=0,
                        help="抽样测试,只索引前 N 个 chunk")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"ERROR: metadata.sqlite not found at {args.db}", file=sys.stderr)
        return 2

    res = build_vectors(
        args.db, args.out, model_name=args.model, batch=args.batch, limit=args.limit,
    )
    print("\n=== vector build summary ===")
    print(f"  indexed: {res.indexed}")
    print(f"  skipped: {res.skipped}")
    print(f"  seconds: {res.seconds:.1f}")
    print(f"  model:   {res.model}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
