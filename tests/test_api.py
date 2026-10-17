"""API tests with a fake engine: no OpenSearch, no Redis, no model download."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from hss.cache import Store
from hss.config import reset_settings_cache
from hss.engine import Hit, SearchResult
from hss.query import Understood


@dataclass
class FakeModels:
    embed_name: str = "fake-embedder"
    rerank_name: str = "fake-reranker"

    def embed_query(self, text):
        return [0.0] * 4

    def rerank_scores(self, query, passages):
        return [0.0 for _ in passages]


@dataclass
class FakeQU:
    def understand(self, q: str) -> Understood:
        corrected = q.lower().replace("headphnes", "headphones")
        return Understood(
            q,
            q.lower(),
            corrected,
            [("headphnes", "headphones")] if corrected != q.lower() else [],
            None,
            None,
            corrected,
        )

    def suggest(self, q: str) -> list[str]:
        return [q + " pro", q + " mini"]


@dataclass
class FakeOS:
    class indices:  # noqa: N801
        @staticmethod
        def exists(**_):
            return True

    class cluster:  # noqa: N801
        @staticmethod
        def health(**_):
            return {"status": "green"}

    @staticmethod
    def count(**_):
        return {"count": 1234}


@dataclass
class FakeEngine:
    models: FakeModels = field(default_factory=FakeModels)
    qu: FakeQU = field(default_factory=FakeQU)
    os: FakeOS = field(default_factory=FakeOS)
    index: str = "products"
    calls: int = 0

    def search(self, query, ranker="hybrid", size=10, filters=None, apply_understood_filters=True):
        self.calls += 1
        u = self.qu.understand(query)
        n = 0 if query == "nothing" else size
        offset = {"bm25": 0, "knn": 100, "hybrid": 200, "hybrid_rerank": 300}[ranker]
        hits = [
            Hit(f"p{offset + i}", f"{u.corrected} product {i}", "Acme", "red", 1.0 / (i + 1), i + 1, i + 1)
            for i in range(n)
        ]
        return SearchResult(u, ranker, hits, {"total": 3.0, "bm25": 1.0}, filters or {})


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    reports = tmp_path_factory.mktemp("reports")
    (reports / "eval.json").write_text(
        json.dumps({"queries": 2, "rankers": {"hybrid": {"ndcg10": 0.5}}}), encoding="utf-8"
    )
    env = {
        "HSS_RATE_LIMIT": "8/minute",
        "HSS_ADMIN_KEY": "adm",
        "HSS_REPORTS_DIR": str(reports),
        "HSS_REDIS_URL": "",
        "HSS_CACHE_TTL_S": "60",
    }
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    reset_settings_cache()
    from hss.config import settings
    from hss.serving.app import State, create_app

    state = State(settings(), engine=FakeEngine(), store=Store(None))
    with TestClient(create_app(state)) as c:
        c.engine = state.engine  # type: ignore[attr-defined]
        yield c
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    reset_settings_cache()


def test_health_and_metrics(client):
    h = client.get("/health").json()
    assert (
        h["status"] == "ok"
        and h["index"]["documents"] == 1234
        and h["cache"] == "memory"
        and "hybrid_rerank" in h["rankers"]
    )
    assert "http_request_duration_seconds" in client.get("/metrics").text


def test_search_validates_caches_and_reports_understanding(client):
    assert client.get("/search").status_code == 422
    assert client.get("/search", params={"q": "x", "ranker": "nope"}).status_code == 422
    first = client.get("/search", params={"q": "wireless headphnes", "ranker": "hybrid", "size": 3}).json()
    assert first["cached"] is False and len(first["hits"]) == 3 and first["query"]["corrected"] == "wireless headphones"
    assert first["query"]["corrections"] == [{"from": "headphnes", "to": "headphones"}]
    before = client.engine.calls
    second = client.get("/search", params={"q": "wireless headphnes", "ranker": "hybrid", "size": 3}).json()
    assert second["cached"] is True and client.engine.calls == before and second["hits"] == first["hits"]
    fresh = client.get(
        "/search", params={"q": "wireless headphnes", "ranker": "hybrid", "size": 3, "no_cache": "true"}
    ).json()
    assert fresh["cached"] is False
    empty = client.get("/search", params={"q": "nothing"}).json()
    assert empty["hits"] == []
    text = client.get("/metrics").text
    assert 'search_requests_total{cached="true",ranker="hybrid"}' in text and "search_empty_total" in text


def test_suggest(client):
    s = client.get("/suggest", params={"q": "wireless headphnes"}).json()
    assert s["corrected"] == "wireless headphones" and len(s["completions"]) == 2


def test_interleave_click_and_admin_report(client):
    assert client.get("/interleave", params={"q": "shoes", "a": "hybrid", "b": "hybrid"}).status_code == 422
    page = client.get("/interleave", params={"q": "shoes", "a": "bm25", "b": "knn", "size": 6}).json()
    assert len(page["items"]) == 6 and all("team" not in it for it in page["items"]) and page["impression_id"]
    ids = {it["product_id"] for it in page["items"]}
    assert ids & {f"p{i}" for i in range(6)} and ids & {f"p{100 + i}" for i in range(6)}  # both rankers drafted
    assert client.post("/click", json={"impression_id": "nope", "position": 0}).status_code == 404
    assert client.post("/click", json={"impression_id": page["impression_id"], "position": 99}).status_code == 422
    assert client.post("/click", json={"impression_id": page["impression_id"], "position": 0}).json()["clicks"] == 1
    assert client.get("/interleave/report").status_code == 401
    report = client.get("/interleave/report", headers={"X-Admin-Key": "adm"}).json()
    assert report["impressions_with_clicks"] == 1 and report["live"][0]["queries"] == 1


def test_eval_summary_and_rate_limit(client):
    assert client.get("/eval/summary").json()["eval"]["queries"] == 2
    codes = [client.get("/suggest", params={"q": f"q{i}"}).status_code for i in range(10)]
    assert 429 in codes and codes[0] == 200
