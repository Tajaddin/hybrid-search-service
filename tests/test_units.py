from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from hss.cache import Store
from hss.engine import rrf
from hss.evaluate import _two_sided_binomial, dcg, mrr_at_k, ndcg_at_k, paired_delta
from hss.interleave import credit, readout, team_draft
from hss.query import QueryUnderstanding


def test_rrf_rewards_agreement_and_is_deterministic():
    fused = rrf([["a", "b", "c"], ["b", "a", "d"]], k=60)
    order = [d for d, _ in fused]
    assert order[:2] == ["a", "b"] or order[:2] == ["b", "a"]
    assert set(order) == {"a", "b", "c", "d"} and order[2:] == ["c", "d"]
    only_one = rrf([["x"], []], k=60)
    assert only_one == [("x", 1 / 61)]
    assert rrf([["a", "b"], ["b", "a"]]) == rrf([["a", "b"], ["b", "a"]])


def test_team_draft_alternates_without_duplicates():
    rng = random.Random(1)
    shown = team_draft(["a1", "x", "a3", "a4"], ["b1", "x", "b3", "b4"], 6, rng)
    ids = [d for d, _ in shown]
    teams = [t for _, t in shown]
    assert len(ids) == len(set(ids)) == 6
    assert abs(teams.count("a") - teams.count("b")) <= 1
    assert "x" in ids
    short = team_draft(["a1"], ["b1"], 10, rng)
    assert sorted(short) == [("a1", "a"), ("b1", "b")]


def test_credit_and_readout():
    items = [{"team": "a"}, {"team": "b"}, {"team": "a"}]
    assert (
        credit(items, [0, 2]) == "a"
        and credit(items, [1]) == "b"
        and credit(items, [0, 1]) == "tie"
        and credit(items, []) == "tie"
    )
    r = readout(30, 70, 10, "hybrid", "hybrid_rerank")
    assert (
        r["queries"] == 110
        and r["b_win_rate"] == 0.7
        and r["binomial_p"] < 0.001
        and r["verdict"] == "hybrid_rerank preferred"
    )
    assert readout(10, 12, 0, "a", "b")["verdict"] == "not enough decided queries"
    assert readout(50, 50, 0, "a", "b")["verdict"] == "no significant preference"
    assert _two_sided_binomial(0, 10) == pytest.approx(2 / 1024)


def test_ndcg_and_mrr_with_esci_gains():
    judged = {"e": 1.0, "s": 0.1, "c": 0.01, "i": 0.0}
    assert ndcg_at_k(["e", "s", "c"], judged) == pytest.approx(1.0)
    assert ndcg_at_k(["i", "unjudged", "e"], judged) == pytest.approx(dcg([0, 0, 1.0]) / dcg([1.0, 0.1, 0.01]))
    assert ndcg_at_k([], judged) == 0.0 and ndcg_at_k(["e"], {"i": 0.0}) == 0.0
    assert mrr_at_k(["s", "e"], {"e"}) == 0.5 and mrr_at_k(["s"], {"e"}) == 0.0


def test_paired_delta_reports_wins_and_interval():
    report = {
        "rankers": {
            "a": {"per_query": [{"query_id": i, "ndcg10": 0.5} for i in range(40)]},
            "b": {"per_query": [{"query_id": i, "ndcg10": 0.6 if i % 4 else 0.5} for i in range(40)]},
        }
    }
    d = paired_delta(report, "a", "b")
    assert d["wins"] == 30 and d["losses"] == 0 and d["ties"] == 10 and d["mean_delta"] == pytest.approx(0.075)
    assert d["sign_test_p"] < 0.001 and d["ci95"][0] > 0


@pytest.fixture
def qu(tmp_path: Path) -> QueryUnderstanding:
    (tmp_path / "vocabulary.txt").write_text(
        "\n".join(
            f"{t} {n}"
            for t, n in [
                ("wireless", 500),
                ("headphones", 400),
                ("bluetooth", 300),
                ("nike", 200),
                ("running", 150),
                ("shoes", 900),
                ("red", 800),
                ("sony", 120),
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "attributes.json").write_text(
        json.dumps(
            {
                "brands": {
                    "nike": {"value": "Nike", "count": 50},
                    "sony": {"value": "Sony", "count": 30},
                    "under armour": {"value": "Under Armour", "count": 25},
                },
                "colors": {"red": 100, "black": 400},
            }
        ),
        encoding="utf-8",
    )
    return QueryUnderstanding(tmp_path)


def test_query_understanding_corrects_spelling_and_finds_attributes(qu: QueryUnderstanding):
    u = qu.understand("Wireles Headphnes")
    assert u.corrected == "wireless headphones" and [c[1] for c in u.corrections] == ["wireless", "headphones"]
    u = qu.understand("red nike running shoes")
    assert u.brand == "Nike" and u.color == "red" and u.residual == "running shoes"
    u = qu.understand("under armour shoes")
    assert u.brand == "Under Armour" and u.residual == "shoes"
    assert qu.understand("12 pack").corrections == []  # numbers and short tokens are left alone
    assert "wireless" in qu.suggest("wire") and qu.suggest("") == []


def test_store_memory_backend_roundtrip_and_ttl(monkeypatch):
    store = Store(None)
    assert store.backend == "memory" and store.ping()
    store.set_json("k", {"a": 1}, ttl_s=60)
    assert store.get_json("k") == {"a": 1} and store.get_json("missing") is None
    store.push("l", "x")
    store.push("l", "y")
    assert store.items("l") == ["y", "x"]
    import time as _t

    far_future = _t.time() + 10**6
    monkeypatch.setattr(_t, "time", lambda: far_future)
    assert store.get_json("k") is None  # expired
    assert Store("redis://127.0.0.1:1/0").backend == "memory"  # unreachable Redis degrades to memory
