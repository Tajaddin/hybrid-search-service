"""Offline ranking evaluation on the ESCI judgments: NDCG@10 with graded gains, MRR@10 on Exact matches,
recall of Exact and Substitute products in the candidate list, and per-query latency, for each ranker."""

from __future__ import annotations

import json
import math
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa

from hss.engine import RANKERS, Engine


@dataclass
class QueryJudgments:
    query_id: int
    query: str
    gains: dict[str, float]  # product_id -> gain

    @property
    def exact(self) -> set[str]:
        return {p for p, g in self.gains.items() if g >= 1.0}


def judgments_by_query(queries: pa.Table, judgments: pa.Table) -> list[QueryJudgments]:
    text = dict(zip(queries["query_id"].to_pylist(), queries["query"].to_pylist(), strict=True))
    gains: dict[int, dict[str, float]] = {}
    for qid, pid, gain in zip(
        judgments["query_id"].to_pylist(),
        judgments["product_id"].to_pylist(),
        judgments["gain"].to_pylist(),
        strict=True,
    ):
        gains.setdefault(qid, {})[pid] = float(gain)
    return [QueryJudgments(qid, text[qid], g) for qid, g in sorted(gains.items())]


def dcg(gains: list[float]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def ndcg_at_k(ranked: list[str], judged: dict[str, float], k: int = 10) -> float:
    got = dcg([judged.get(p, 0.0) for p in ranked[:k]])
    ideal = dcg(sorted(judged.values(), reverse=True)[:k])
    return got / ideal if ideal > 0 else 0.0


def mrr_at_k(ranked: list[str], exact: set[str], k: int = 10) -> float:
    for i, p in enumerate(ranked[:k], start=1):
        if p in exact:
            return 1.0 / i
    return 0.0


def evaluate(
    engine: Engine,
    queries: pa.Table,
    judgments: pa.Table,
    rankers: tuple[str, ...] = RANKERS,
    limit: int | None = None,
    apply_filters: bool = False,
) -> dict:
    """Runs every ranker on every query. Unjudged products count as irrelevant (gain 0)."""
    items = judgments_by_query(queries, judgments)[:limit]
    out: dict[str, dict] = {}
    for ranker in rankers:
        ndcgs, mrrs, recalls, latencies = [], [], [], []
        per_query = []
        for item in items:
            started = time.perf_counter()
            result = engine.search(
                item.query, ranker=ranker, size=max(10, engine.rerank_top), apply_understood_filters=apply_filters
            )
            latencies.append((time.perf_counter() - started) * 1000)
            ranked = [h.product_id for h in result.hits]
            relevant = {p for p, g in item.gains.items() if g >= 0.1}
            n = ndcg_at_k(ranked, item.gains)
            m = mrr_at_k(ranked, item.exact)
            r = len(relevant & set(ranked[:50])) / len(relevant) if relevant else 0.0
            ndcgs.append(n)
            mrrs.append(m)
            recalls.append(r)
            per_query.append({"query_id": item.query_id, "ndcg10": round(n, 4), "mrr10": round(m, 4)})
        latencies.sort()
        out[ranker] = {
            "queries": len(items),
            "ndcg10": round(statistics.mean(ndcgs), 4),
            "mrr10": round(statistics.mean(mrrs), 4),
            "recall50_es": round(statistics.mean(recalls), 4),
            "ndcg10_ci95": _mean_ci(ndcgs),
            "latency_ms": {
                "p50": round(latencies[len(latencies) // 2], 1),
                "p95": round(latencies[int(0.95 * (len(latencies) - 1))], 1),
                "p99": round(latencies[int(0.99 * (len(latencies) - 1))], 1),
            },
            "per_query": per_query,
        }
    report = {
        "evaluated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "queries": len(items),
        "gains": {"E": 1.0, "S": 0.1, "C": 0.01, "I": 0.0, "unjudged": 0.0},
        "understood_filters_applied": apply_filters,
        "rankers": out,
    }
    return report


def _mean_ci(values: list[float]) -> list[float]:
    if len(values) < 2:
        return [float("nan"), float("nan")]
    m = statistics.mean(values)
    se = statistics.stdev(values) / math.sqrt(len(values))
    return [round(m - 1.96 * se, 4), round(m + 1.96 * se, 4)]


def paired_delta(report: dict, a: str, b: str) -> dict:
    """Mean NDCG@10 difference b - a over the same queries with a 95% interval and a sign test p-value."""
    pa_ = {q["query_id"]: q["ndcg10"] for q in report["rankers"][a]["per_query"]}
    pb_ = {q["query_id"]: q["ndcg10"] for q in report["rankers"][b]["per_query"]}
    deltas = [pb_[q] - pa_[q] for q in pa_ if q in pb_]
    wins = sum(d > 0 for d in deltas)
    losses = sum(d < 0 for d in deltas)
    n = wins + losses
    p = _two_sided_binomial(min(wins, losses), n) if n else 1.0
    return {
        "a": a,
        "b": b,
        "mean_delta": round(statistics.mean(deltas), 4),
        "ci95": _mean_ci(deltas),
        "wins": wins,
        "losses": losses,
        "ties": len(deltas) - n,
        "sign_test_p": round(p, 6),
    }


def _two_sided_binomial(k: int, n: int) -> float:
    from math import comb

    tail = sum(comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def write_report(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slim = {
        **report,
        "rankers": {k: {kk: vv for kk, vv in v.items() if kk != "per_query"} for k, v in report["rankers"].items()},
    }
    comparisons = []
    for a, b in (("bm25", "hybrid"), ("knn", "hybrid"), ("hybrid", "hybrid_rerank")):
        if a in report["rankers"] and b in report["rankers"]:
            comparisons.append(paired_delta(report, a, b))
    slim["comparisons"] = comparisons
    path.write_text(json.dumps(slim, indent=2), encoding="utf-8")
    (path.with_suffix(".per-query.json")).write_text(
        json.dumps({k: v["per_query"] for k, v in report["rankers"].items()}), encoding="utf-8"
    )
