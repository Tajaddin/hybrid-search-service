"""Sentence embeddings and the cross-encoder, on CPU, loaded once per process."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np


class Models:
    """Lazy holders so importing the API does not load PyTorch until the first query."""

    def __init__(self, embed_model: str, rerank_model: str, cache_dir: Path) -> None:
        self.embed_name = embed_model
        self.rerank_name = rerank_model
        self.cache_dir = cache_dir
        self._embedder: Any = None
        self._reranker: Any = None
        self._lock = threading.Lock()
        # Request threads must not run the models concurrently: each call already uses the process's
        # OMP threads, so parallel calls only thrash. Throughput scales with uvicorn workers instead.
        self._embed_lock = threading.Lock()
        self._rerank_lock = threading.Lock()

    @property
    def embedder(self):
        if self._embedder is None:
            with self._lock:
                if self._embedder is None:
                    from sentence_transformers import SentenceTransformer

                    try:
                        self._embedder = SentenceTransformer(
                            self.embed_name, device="cpu", cache_folder=str(self.cache_dir), local_files_only=True
                        )
                    except Exception:
                        self._embedder = SentenceTransformer(
                            self.embed_name, device="cpu", cache_folder=str(self.cache_dir)
                        )
        return self._embedder

    @property
    def reranker(self):
        if self._reranker is None:
            with self._lock:
                if self._reranker is None:
                    from sentence_transformers import CrossEncoder

                    try:
                        self._reranker = CrossEncoder(
                            self.rerank_name, device="cpu", max_length=256, local_files_only=True
                        )
                    except Exception:
                        self._reranker = CrossEncoder(self.rerank_name, device="cpu", max_length=256)
        return self._reranker

    @property
    def dimension(self) -> int:
        return int(self.embedder.get_sentence_embedding_dimension() or 384)

    def embed(self, texts: list[str], batch_size: int = 128) -> np.ndarray:
        with self._embed_lock:
            vectors = self.embedder.encode(
                texts, batch_size=batch_size, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False
            )
        return np.asarray(vectors, dtype=np.float32)

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0].tolist()

    def rerank_scores(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        with self._rerank_lock:
            scores = self.reranker.predict([(query, p) for p in passages], batch_size=32, show_progress_bar=False)
        return [float(s) for s in scores]


def document_text(title: str, brand: str | None, bullets: str | None) -> str:
    """What gets embedded and reranked: the title, the brand and the first bullet points."""
    parts = [title]
    if brand:
        parts.append(brand)
    if bullets:
        parts.append(bullets[:400])
    return ". ".join(parts)
