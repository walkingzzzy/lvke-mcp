"""Stage 5 · BM25 索引构建与持久化。

优先 ``rank_bm25.BM25Okapi`` + ``jieba.lcut``；
缺 jieba 时用中文 n-gram 回退分词；缺 rank_bm25 时仅允许 load 失败由上层降级。
"""

from __future__ import annotations

import pickle
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_PUNCT_RE = re.compile(r"[\s　，。、；：！？「」『』（）()\[\]【】《》<>\"'`~!@#$%^&*_+=|\\/{}.\-]+")

try:
    import jieba  # type: ignore

    jieba.setLogLevel(20)  # WARNING
    _HAS_JIEBA = True
except Exception:  # pragma: no cover
    jieba = None  # type: ignore
    _HAS_JIEBA = False

try:
    from rank_bm25 import BM25Okapi  # type: ignore

    _HAS_RANK_BM25 = True
except Exception:  # pragma: no cover
    BM25Okapi = None  # type: ignore
    _HAS_RANK_BM25 = False


def tokenize(text: str) -> list[str]:
    """中文 + 英文混合分词；jieba 不可用时回退 CJK n-gram。"""
    if not text:
        return []
    if _HAS_JIEBA:
        raw = jieba.lcut(text)
        out: list[str] = []
        for token in raw:
            token = _PUNCT_RE.sub("", token)
            if token:
                out.append(token.lower())
        return out
    # fallback: ascii words + CJK uni/bi-grams
    text = str(text).lower()
    out = []
    buf: list[str] = []
    chars: list[str] = []
    for ch in text:
        if "一" <= ch <= "鿿":
            if buf:
                out.append("".join(buf))
                buf = []
            chars.append(ch)
            out.append(ch)
        elif ch.isalnum():
            if chars:
                for i in range(len(chars) - 1):
                    out.append(chars[i] + chars[i + 1])
                chars = []
            buf.append(ch)
        else:
            if buf:
                out.append("".join(buf))
                buf = []
            if chars:
                for i in range(len(chars) - 1):
                    out.append(chars[i] + chars[i + 1])
                chars = []
    if buf:
        out.append("".join(buf))
    if chars:
        for i in range(len(chars) - 1):
            out.append(chars[i] + chars[i + 1])
    return [t for t in out if t]


@dataclass(slots=True)
class IndexedDoc:
    chunk_id: str
    report_id: str
    chapter_no: int


class Bm25Index:
    """轻量封装：保存 BM25 模型 + chunk_id 列表。"""

    def __init__(self, bm25: object, docs: list[IndexedDoc]) -> None:
        self._bm25 = bm25
        self._docs = docs

    def search(self, query_tokens: list[str], top_k: int = 50) -> list[tuple[IndexedDoc, float]]:
        if not query_tokens or not self._docs:
            return []
        get_scores = getattr(self._bm25, "get_scores", None)
        if get_scores is None:
            return []
        scores = get_scores(query_tokens)
        ranked = sorted(zip(self._docs, scores), key=lambda x: x[1], reverse=True)
        return [(d, float(s)) for d, s in ranked[:top_k] if s > 0]

    def save(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        with (out_dir / "bm25.pkl").open("wb") as f:
            pickle.dump(self._bm25, f, protocol=pickle.HIGHEST_PROTOCOL)
        with (out_dir / "docs.pkl").open("wb") as f:
            pickle.dump(self._docs, f, protocol=pickle.HIGHEST_PROTOCOL)

    @classmethod
    def load(cls, out_dir: Path) -> "Bm25Index | None":
        bm25_path = out_dir / "bm25.pkl"
        docs_path = out_dir / "docs.pkl"
        if not (bm25_path.exists() and docs_path.exists()):
            return None
        try:
            with bm25_path.open("rb") as f:
                bm25 = pickle.load(f)
            with docs_path.open("rb") as f:
                docs = pickle.load(f)
            return cls(bm25, docs)
        except Exception:
            # pickled BM25Okapi requires rank_bm25 installed; soft-fail for callers
            return None


def build(corpus: Iterable[tuple[str, str, int, str]]) -> Bm25Index:
    """Build BM25 index from (chunk_id, report_id, chapter_no, content) tuples."""
    if not _HAS_RANK_BM25:
        raise ImportError("missing dependency: rank_bm25; run `pip install rank-bm25 jieba`")
    docs: list[IndexedDoc] = []
    tokenized_corpus: list[list[str]] = []
    for chunk_id, report_id, chapter_no, content in corpus:
        tokens = tokenize(content)
        if not tokens:
            continue
        docs.append(IndexedDoc(chunk_id=chunk_id, report_id=report_id, chapter_no=chapter_no))
        tokenized_corpus.append(tokens)
    if not tokenized_corpus:
        raise ValueError("BM25 build received empty corpus")
    bm25 = BM25Okapi(tokenized_corpus)
    return Bm25Index(bm25, docs)


def tokenizer_backend() -> str:
    if _HAS_JIEBA:
        return "jieba"
    return "cjk_ngram"
