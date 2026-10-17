"""OpenSearch index: BM25 fields and an HNSW vector field in one index; bulk loading with cached embeddings."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pyarrow as pa
from opensearchpy import OpenSearch, helpers

from hss.embeddings import Models, document_text
from hss.logging_setup import get_logger

log = get_logger("index")


def client(url: str) -> OpenSearch:
    return OpenSearch(hosts=[url], http_compress=True, timeout=60, max_retries=3, retry_on_timeout=True)


def mapping(dimension: int) -> dict:
    return {
        "settings": {
            "index": {"number_of_shards": 1, "number_of_replicas": 0, "knn": True, "refresh_interval": "-1"},
            "analysis": {
                "analyzer": {
                    "product_text": {
                        "type": "custom",
                        "tokenizer": "standard",
                        "filter": ["lowercase", "asciifolding", "english_stop", "english_stemmer"],
                    }
                },
                "filter": {
                    "english_stop": {"type": "stop", "stopwords": "_english_"},
                    "english_stemmer": {"type": "stemmer", "language": "english"},
                },
            },
        },
        "mappings": {
            "properties": {
                "product_id": {"type": "keyword"},
                "title": {
                    "type": "text",
                    "analyzer": "product_text",
                    "fields": {"raw": {"type": "keyword", "ignore_above": 256}},
                },
                "brand": {"type": "keyword", "fields": {"text": {"type": "text", "analyzer": "product_text"}}},
                "color": {"type": "keyword"},
                "bullets": {"type": "text", "analyzer": "product_text"},
                "description": {"type": "text", "analyzer": "product_text"},
                "embedding": {
                    "type": "knn_vector",
                    "dimension": dimension,
                    "method": {
                        "name": "hnsw",
                        "space_type": "cosinesimil",
                        "engine": "lucene",
                        "parameters": {"ef_construction": 128, "m": 16},
                    },
                },
            }
        },
    }


def ensure_index(os_client: OpenSearch, name: str, dimension: int, recreate: bool = False) -> None:
    if recreate and os_client.indices.exists(index=name):
        os_client.indices.delete(index=name)
    if not os_client.indices.exists(index=name):
        os_client.indices.create(index=name, body=mapping(dimension))


def embeddings_for(catalog: pa.Table, models: Models, cache: Path, batch_size: int = 256) -> np.ndarray:
    """Embeddings aligned with the catalog rows, cached as .npy next to the catalog."""
    if cache.exists():
        arr = np.load(cache)
        if arr.shape[0] == catalog.num_rows:
            return arr
    texts = [
        document_text(t, b, bl)
        for t, b, bl in zip(
            catalog["title"].to_pylist(), catalog["brand"].to_pylist(), catalog["bullets"].to_pylist(), strict=True
        )
    ]
    chunks = []
    started = time.perf_counter()
    for start in range(0, len(texts), 4096):
        chunks.append(models.embed(texts[start : start + 4096], batch_size=batch_size))
        done = min(start + 4096, len(texts))
        log.info("embedding", done=done, total=len(texts), rate_per_s=round(done / (time.perf_counter() - started), 1))
    arr = np.vstack(chunks).astype(np.float32)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache, arr)
    return arr


def _actions(index: str, catalog: pa.Table, vectors: np.ndarray) -> Iterator[dict]:
    cols = {c: catalog[c].to_pylist() for c in ("product_id", "title", "brand", "color", "bullets", "description")}
    for i in range(catalog.num_rows):
        yield {
            "_index": index,
            "_id": cols["product_id"][i],
            "_source": {
                "product_id": cols["product_id"][i],
                "title": cols["title"][i],
                "brand": cols["brand"][i],
                "color": (cols["color"][i] or "").lower() or None,
                "bullets": cols["bullets"][i],
                "description": cols["description"][i],
                "embedding": vectors[i].tolist(),
            },
        }


def load(os_client: OpenSearch, index: str, catalog: pa.Table, vectors: np.ndarray, chunk: int = 1000) -> dict:
    started = time.perf_counter()
    ok, errors = helpers.bulk(
        os_client,
        _actions(index, catalog, vectors),
        chunk_size=chunk,
        request_timeout=120,
        raise_on_error=False,
        stats_only=False,
    )
    os_client.indices.put_settings(index=index, body={"index": {"refresh_interval": "1s"}})
    os_client.indices.refresh(index=index)
    # One segment: a k-NN query walks every segment's HNSW graph, so a freshly bulk-loaded index with
    # several segments answers several times slower than the same data merged. Static catalog, so merge once.
    os_client.indices.forcemerge(index=index, max_num_segments=1, request_timeout=1800)
    os_client.indices.refresh(index=index)
    count = os_client.count(index=index)["count"]
    return {
        "indexed": ok,
        "errors": len(errors) if isinstance(errors, list) else errors,
        "count": count,
        "segments": len(os_client.cat.segments(index=index, format="json")),
        "seconds": round(time.perf_counter() - started, 1),
    }
