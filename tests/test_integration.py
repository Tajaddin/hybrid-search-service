"""Against a live OpenSearch and the real models: a 400-product synthetic catalog is indexed into a throwaway
index and the retrievers, fusion, filters and reranker are exercised end to end. Marked integration."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    from ci_fixture import main as build_fixture

    from hss.data import load_catalog
    from hss.embeddings import Models
    from hss.engine import Engine
    from hss.index import client, embeddings_for, ensure_index, load
    from hss.query import QueryUnderstanding

    target = tmp_path_factory.mktemp("ci")
    build_fixture(target)
    prepared = target / "prepared"
    url = os.environ.get("HSS_OPENSEARCH_URL", "http://localhost:9201")
    os_client = client(url)
    try:
        os_client.cluster.health(request_timeout=5)
    except Exception as exc:  # pragma: no cover - depends on the environment
        pytest.skip(f"OpenSearch not reachable at {url}: {exc}")
    models = Models(
        "sentence-transformers/all-MiniLM-L6-v2",
        "cross-encoder/ms-marco-MiniLM-L-6-v2",
        Path(os.environ.get("HSS_MODELS_DIR", "data/models")),
    )
    catalog = load_catalog(prepared)
    vectors = embeddings_for(catalog, models, prepared / "embeddings.npy")
    index = "products-test"
    ensure_index(os_client, index, vectors.shape[1], recreate=True)
    stats = load(os_client, index, catalog, vectors)
    assert stats["count"] == catalog.num_rows
    yield Engine(os_client, index, models, QueryUnderstanding(prepared), rrf_k=60, candidates=50, rerank_top=20)
    os_client.indices.delete(index=index)


def test_bm25_knn_hybrid_and_rerank_return_relevant_products(engine):
    for ranker in ("bm25", "knn", "hybrid", "hybrid_rerank"):
        result = engine.search("wireless headphones", ranker=ranker, size=10)
        assert len(result.hits) == 10, ranker
        titles = " ".join(h.title.lower() for h in result.hits)
        assert "headphones" in titles, ranker
        assert result.took_ms["total"] > 0
    hybrid = engine.search("wireless headphones", ranker="hybrid", size=10)
    assert all(h.bm25_rank or h.knn_rank for h in hybrid.hits)
    reranked = engine.search("wireless headphones", ranker="hybrid_rerank", size=5)
    assert all(h.rerank_score is not None for h in reranked.hits)
    assert reranked.took_ms["rerank"] > 0


def test_spelling_and_attribute_filters_apply(engine):
    result = engine.search("Sony wireles headphnes red", ranker="hybrid", size=10)
    assert result.query.corrected == "sony wireless headphones red"
    assert result.filters == {"brand": "Sony", "color": "red"}
    assert result.hits and all(h.brand == "Sony" and h.color == "red" for h in result.hits)
    explicit = engine.search("running shoes", ranker="bm25", size=10, filters={"brand": "Nike"})
    assert explicit.hits and all(h.brand == "Nike" for h in explicit.hits)
    off = engine.search("Sony headphones", ranker="bm25", size=10, apply_understood_filters=False)
    assert off.filters == {}


def test_knn_finds_semantic_neighbours_without_shared_words(engine):
    # "earphones" is not in any title; the vector side still lands on the headphones.
    result = engine.search("earphones for music", ranker="knn", size=5)
    assert any("headphones" in h.title.lower() or "speaker" in h.title.lower() for h in result.hits)


def test_offline_eval_runs_on_the_fixture(engine, tmp_path):
    from hss.data import load_eval
    from hss.evaluate import evaluate, write_report

    queries, judgments = load_eval(engine.qu.prepared_dir)
    report = evaluate(engine, queries, judgments, ("bm25", "hybrid"), limit=4)
    assert set(report["rankers"]) == {"bm25", "hybrid"}
    assert 0.0 <= report["rankers"]["hybrid"]["ndcg10"] <= 1.0
    write_report(report, tmp_path / "eval.json")
    assert (tmp_path / "eval.json").exists() and (tmp_path / "eval.per-query.json").exists()
