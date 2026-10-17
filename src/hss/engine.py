"""Retrieval and ranking: BM25, kNN, reciprocal rank fusion, cross-encoder reranking, one entry point."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from opensearchpy import OpenSearch

from hss.embeddings import Models, document_text
from hss.query import QueryUnderstanding, Understood

Ranker = str
RANKERS: tuple[str, ...] = ("bm25", "knn", "hybrid", "hybrid_rerank")
FIELDS = ["title^3", "brand.text^2", "bullets", "description"]
SOURCE = ["product_id", "title", "brand", "color", "bullets"]


@dataclass
class Hit:
    product_id: str
    title: str
    brand: str | None
    color: str | None
    score: float
    bm25_rank: int | None = None
    knn_rank: int | None = None
    rerank_score: float | None = None
    bullets: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "title": self.title,
            "brand": self.brand,
            "color": self.color,
            "score": round(self.score, 6),
            "bm25_rank": self.bm25_rank,
            "knn_rank": self.knn_rank,
            "rerank_score": None if self.rerank_score is None else round(self.rerank_score, 4),
        }


@dataclass
class SearchResult:
    query: Understood
    ranker: str
    hits: list[Hit]
    took_ms: dict[str, float] = field(default_factory=dict)
    filters: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query.to_dict(),
            "ranker": self.ranker,
            "filters": self.filters,
            "took_ms": {k: round(v, 1) for k, v in self.took_ms.items()},
            "hits": [h.to_dict() for h in self.hits],
        }


def rrf(lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal rank fusion: score(d) = sum over lists of 1 / (k + rank)."""
    scores: dict[str, float] = {}
    for ranked in lists:
        for rank, doc in enumerate(ranked, start=1):
            scores[doc] = scores.get(doc, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


class Engine:
    def __init__(
        self,
        os_client: OpenSearch,
        index: str,
        models: Models,
        qu: QueryUnderstanding,
        rrf_k: int = 60,
        candidates: int = 100,
        rerank_top: int = 50,
        ef_search: int = 256,
    ) -> None:
        self.os = os_client
        self.index = index
        self.models = models
        self.qu = qu
        self.rrf_k = rrf_k
        self.candidates = candidates
        self.rerank_top = rerank_top
        self.ef_search = ef_search

    # ---- retrievers --------------------------------------------------------------------------------------
    @staticmethod
    def _filter_clauses(filters: dict[str, str]) -> list[dict]:
        clauses = []
        if filters.get("brand"):
            clauses.append({"term": {"brand": filters["brand"]}})
        if filters.get("color"):
            clauses.append({"term": {"color": filters["color"].lower()}})
        return clauses

    def _bm25_body(self, text: str, filters: dict[str, str], k: int) -> dict:
        return {
            "size": k,
            "_source": SOURCE,
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": text,
                                "fields": FIELDS,
                                "type": "best_fields",
                                "operator": "or",
                                "tie_breaker": 0.3,
                            }
                        }
                    ],
                    "filter": self._filter_clauses(filters),
                }
            },
        }

    def bm25(self, text: str, filters: dict[str, str], k: int) -> list[dict]:
        return self.os.search(index=self.index, body=self._bm25_body(text, filters, k))["hits"]["hits"]

    def _knn_body(self, vector: list[float], filters: dict[str, str], k: int) -> dict:
        # Lucene HNSW explores ef_search candidates per segment; the catalog is force-merged to one segment, so
        # the default (ef_search = k) explores fewer candidates than the seven-segment index did and recall drops.
        knn: dict[str, Any] = {"vector": vector, "k": k, "method_parameters": {"ef_search": max(k, self.ef_search)}}
        clauses = self._filter_clauses(filters)
        if clauses:
            knn["filter"] = {"bool": {"filter": clauses}}
        return {"size": k, "_source": SOURCE, "query": {"knn": {"embedding": knn}}}

    def knn(self, vector: list[float], filters: dict[str, str], k: int) -> list[dict]:
        return self.os.search(index=self.index, body=self._knn_body(vector, filters, k))["hits"]["hits"]

    def both(self, text: str, vector: list[float], filters: dict[str, str], k: int) -> tuple[dict, dict]:
        """BM25 and k-NN in one round trip: OpenSearch runs the two searches concurrently, so a hybrid query
        costs about the slower of the two instead of their sum."""
        body = [
            {"index": self.index},
            self._bm25_body(text, filters, k),
            {"index": self.index},
            self._knn_body(vector, filters, k),
        ]
        lexical, semantic = self.os.msearch(body=body)["responses"]
        for response in (lexical, semantic):
            if "error" in response:
                raise RuntimeError(f"opensearch: {response['error']}")
        return lexical, semantic

    # ---- the entry point ---------------------------------------------------------------------------------
    def search(
        self,
        query: str,
        ranker: Ranker = "hybrid",
        size: int = 10,
        filters: dict[str, str] | None = None,
        apply_understood_filters: bool = True,
    ) -> SearchResult:
        took: dict[str, float] = {}
        t0 = time.perf_counter()
        understood = self.qu.understand(query)
        effective = dict(filters or {})
        if apply_understood_filters:
            if understood.brand and "brand" not in effective:
                effective["brand"] = understood.brand
            if understood.color and "color" not in effective:
                effective["color"] = understood.color
        text = understood.corrected or understood.normalized or query
        took["understand"] = (time.perf_counter() - t0) * 1000

        docs: dict[str, dict] = {}
        bm25_ranks: dict[str, int] = {}
        knn_ranks: dict[str, int] = {}
        n = (
            self.candidates
            if ranker in ("hybrid", "hybrid_rerank")
            else max(size, self.rerank_top if ranker == "hybrid_rerank" else size)
        )

        def take(hits: list[dict], ranks: dict[str, int]) -> None:
            for rank, doc in enumerate(hits, start=1):
                docs.setdefault(doc["_id"], doc["_source"])
                ranks[doc["_id"]] = rank

        if ranker == "bm25":
            t = time.perf_counter()
            take(self.bm25(text, effective, n), bm25_ranks)
            took["bm25"] = (time.perf_counter() - t) * 1000
        else:
            t = time.perf_counter()
            vector = self.models.embed_query(text)
            took["embed"] = (time.perf_counter() - t) * 1000
            t = time.perf_counter()
            if ranker == "knn":
                take(self.knn(vector, effective, n), knn_ranks)
                took["knn"] = (time.perf_counter() - t) * 1000
            else:
                lexical, semantic = self.both(text, vector, effective, n)
                take(lexical["hits"]["hits"], bm25_ranks)
                take(semantic["hits"]["hits"], knn_ranks)
                took["bm25"] = float(lexical.get("took", 0))  # server-side, the two ran concurrently
                took["knn"] = float(semantic.get("took", 0))
                took["retrieve"] = (time.perf_counter() - t) * 1000

        if ranker == "bm25":
            ordered = [(d, 1.0 / r) for d, r in sorted(bm25_ranks.items(), key=lambda kv: kv[1])]
        elif ranker == "knn":
            ordered = [(d, 1.0 / r) for d, r in sorted(knn_ranks.items(), key=lambda kv: kv[1])]
        else:
            lists = [
                [d for d, _ in sorted(bm25_ranks.items(), key=lambda kv: kv[1])],
                [d for d, _ in sorted(knn_ranks.items(), key=lambda kv: kv[1])],
            ]
            ordered = rrf(lists, self.rrf_k)

        hits = [
            Hit(
                product_id=d,
                title=docs[d].get("title") or "",
                brand=docs[d].get("brand"),
                color=docs[d].get("color"),
                score=s,
                bm25_rank=bm25_ranks.get(d),
                knn_rank=knn_ranks.get(d),
                bullets=docs[d].get("bullets"),
            )
            for d, s in ordered
        ]
        if ranker == "hybrid_rerank" and hits:
            t = time.perf_counter()
            top = hits[: self.rerank_top]
            scores = self.models.rerank_scores(text, [document_text(h.title, h.brand, h.bullets) for h in top])
            for hit, sc in zip(top, scores, strict=True):
                hit.rerank_score = sc
            top.sort(key=lambda h: -(h.rerank_score or -1e9))
            hits = top + hits[self.rerank_top :]
            took["rerank"] = (time.perf_counter() - t) * 1000
        took["total"] = (time.perf_counter() - t0) * 1000
        for hit in hits:
            hit.bullets = None
        return SearchResult(understood, ranker, hits[:size], took, effective)
