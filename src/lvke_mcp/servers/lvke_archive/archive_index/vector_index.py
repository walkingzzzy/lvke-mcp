"""Phase A.2 · 向量检索运行时（lazy-load 版）。

仅在 storage.py 真正需要时才 import sentence-transformers/chromadb,
避免 MCP server 启动时强制依赖向量栈（即便用户没装 archive-vec extras
也能正常跑 BM25-only 路径）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class VectorHit:
    chunk_id: str
    report_id: str
    chapter_no: int
    score: float


class VectorIndex:
    """Chroma + sentence-transformers 的薄封装。"""

    def __init__(self, encoder: Any, collection: Any) -> None:
        self._encoder = encoder
        self._coll = collection

    def search(self, query: str, top_k: int = 50) -> list[VectorHit]:
        if not query or not query.strip():
            return []
        emb = self._encoder.encode(
            [query], show_progress_bar=False, normalize_embeddings=True
        )
        emb = emb.tolist() if hasattr(emb, "tolist") else list(emb)
        results = self._coll.query(
            query_embeddings=emb,
            n_results=top_k,
            include=["metadatas", "distances"],
        )
        ids = results.get("ids", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]
        out: list[VectorHit] = []
        for cid, meta, dist in zip(ids, metas, dists):
            # chroma cosine distance → similarity in [0, 1]; 1 - d
            score = max(0.0, 1.0 - float(dist))
            out.append(
                VectorHit(
                    chunk_id=cid,
                    report_id=str(meta.get("report_id", "")),
                    chapter_no=int(meta.get("chapter_no", 0)),
                    score=score,
                )
            )
        return out


def load(data_dir: Path, *, model_name: str | None = None) -> VectorIndex | None:
    """Try to load a persisted vector index. Returns None on any failure.

    失败原因（任何一种都安全静默）:
    - chromadb / sentence-transformers 未安装
    - 向量目录不存在或为空
    - encoder 加载失败（无网络且无本地缓存）
    """
    vectors_dir = data_dir / "vectors"
    if not vectors_dir.exists():
        return None

    try:
        import chromadb  # type: ignore
    except Exception:
        return None

    try:
        client = chromadb.PersistentClient(path=str(vectors_dir))
        coll = client.get_collection("lvke_archive_chunks")
        count = coll.count()
        if count == 0:
            return None
    except Exception:
        return None

    # Encoder lazy load
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except Exception:
        return None

    model = model_name or os.environ.get(
        "LVKE_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"
    )
    try:
        encoder = SentenceTransformer(model)
    except Exception:
        return None

    return VectorIndex(encoder, coll)



def probe_vectors(data_dir: Path | str) -> dict[str, Any]:
    """Honest status for archive chromadb vector stack (no silent claim of readiness)."""
    root = Path(data_dir)
    vectors_dir = root / "vectors"
    out: dict[str, Any] = {
        "ok": False,
        "vectors_dir": str(vectors_dir),
        "vectors_dir_exists": vectors_dir.exists(),
        "chromadb_installed": False,
        "collection_loaded": False,
        "reason": "",
    }
    try:
        import chromadb  # type: ignore

        out["chromadb_installed"] = True
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"chromadb_missing:{type(exc).__name__}"
        out["install_hint"] = 'pip install -e ".[archive-vec]"'
        return out
    if not vectors_dir.exists():
        out["reason"] = "vectors_dir_missing"
        return out
    try:
        client = chromadb.PersistentClient(path=str(vectors_dir))
        coll = client.get_collection("lvke_archive_chunks")
        out["collection_loaded"] = True
        try:
            out["count"] = int(coll.count())
        except Exception:
            out["count"] = None
        out["ok"] = True
        out["reason"] = "ok"
        return out
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"load_failed:{type(exc).__name__}"
        return out
