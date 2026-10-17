"""Team-draft interleaving: two rankers answer the same query, their lists are merged by alternating
drafts, each shown result remembers which team drafted it, and clicks are credited to teams. Over many
queries the ranker that wins more queries is preferred. A click model over the ESCI labels reproduces the
readout offline so the method is demonstrated without real traffic."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa

from hss.engine import Engine
from hss.evaluate import judgments_by_query

# Probability of a click on a result with this label when the user looks at it, times a position bias.
CLICK_MODEL = {"E": 0.6, "S": 0.3, "C": 0.1, "I": 0.02, "unjudged": 0.02}


@dataclass
class Interleaved:
    items: list[dict[str, Any]]  # product with "team" in {"a", "b"}
    a: str
    b: str
    query: str
    impression_id: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def team_draft(list_a: list[str], list_b: list[str], size: int, rng: random.Random) -> list[tuple[str, str]]:
    """Alternating drafts with a coin flip per round for who picks first; a result already shown is skipped."""
    shown: list[tuple[str, str]] = []
    seen: set[str] = set()
    ia = ib = 0
    picks_a = picks_b = 0
    while len(shown) < size and (ia < len(list_a) or ib < len(list_b)):
        a_first = picks_a < picks_b or (picks_a == picks_b and rng.random() < 0.5)
        order = ("a", "b") if a_first else ("b", "a")
        for team in order:
            if len(shown) >= size:
                break
            if team == "a":
                while ia < len(list_a) and list_a[ia] in seen:
                    ia += 1
                if ia < len(list_a):
                    shown.append((list_a[ia], "a"))
                    seen.add(list_a[ia])
                    picks_a += 1
                    ia += 1
            else:
                while ib < len(list_b) and list_b[ib] in seen:
                    ib += 1
                if ib < len(list_b):
                    shown.append((list_b[ib], "b"))
                    seen.add(list_b[ib])
                    picks_b += 1
                    ib += 1
        if ia >= len(list_a) and ib >= len(list_b):
            break
    return shown


def interleave(engine: Engine, query: str, a: str, b: str, size: int, rng: random.Random) -> Interleaved:
    ra = engine.search(query, ranker=a, size=size)
    rb = engine.search(query, ranker=b, size=size)
    by_id = {h.product_id: h for h in [*ra.hits, *rb.hits]}
    drafted = team_draft([h.product_id for h in ra.hits], [h.product_id for h in rb.hits], size, rng)
    items = [{**by_id[pid].to_dict(), "team": team} for pid, team in drafted]
    return Interleaved(
        items, a, b, query, meta={"took_ms_a": ra.took_ms.get("total"), "took_ms_b": rb.took_ms.get("total")}
    )


def credit(items: list[dict[str, Any]], clicked_positions: list[int]) -> str:
    """Winner of one impression from the clicked positions: 'a', 'b' or 'tie'."""
    ca = sum(1 for p in clicked_positions if 0 <= p < len(items) and items[p]["team"] == "a")
    cb = sum(1 for p in clicked_positions if 0 <= p < len(items) and items[p]["team"] == "b")
    return "a" if ca > cb else "b" if cb > ca else "tie"


def readout(wins_a: int, wins_b: int, ties: int, a: str, b: str) -> dict[str, Any]:
    n = wins_a + wins_b
    rate: float | None = None
    ci: list[float] | None = None
    if n:
        rate = wins_b / n
        se = math.sqrt(rate * (1 - rate) / n) if rate not in (0.0, 1.0) else math.sqrt(0.25 / n)
        ci = [round(max(0.0, rate - 1.96 * se), 4), round(min(1.0, rate + 1.96 * se), 4)]
    p = _two_sided_binomial(min(wins_a, wins_b), n) if n else None
    return {
        "a": a,
        "b": b,
        "queries": wins_a + wins_b + ties,
        "wins_a": wins_a,
        "wins_b": wins_b,
        "ties": ties,
        "b_win_rate": None if rate is None else round(rate, 4),
        "b_win_rate_ci95": ci,
        "binomial_p": None if p is None else round(p, 6),
        "verdict": (
            "not enough decided queries"
            if n < 30
            else f"{b} preferred"
            if p is not None and p < 0.05 and rate and rate > 0.5
            else f"{a} preferred"
            if p is not None and p < 0.05
            else "no significant preference"
        ),
        "queries_for_55_45_at_80_power": 783,  # two-sided 5%, detect a 55/45 split
    }


def _two_sided_binomial(k: int, n: int) -> float:
    from math import comb

    tail = sum(comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def simulate(
    engine: Engine, queries: pa.Table, judgments: pa.Table, a: str, b: str, n_queries: int, seed: int, size: int = 10
) -> dict[str, Any]:
    rng = random.Random(seed)
    items = judgments_by_query(queries, judgments)
    rng.shuffle(items)
    wins_a = wins_b = ties = 0
    label_of = {}
    for item in items[:n_queries]:
        label_of = {p: ("E" if g >= 1 else "S" if g >= 0.1 else "C" if g > 0 else "I") for p, g in item.gains.items()}
        shown = interleave(engine, item.query, a, b, size, rng)
        clicks = []
        for pos, it in enumerate(shown.items):
            label = label_of.get(it["product_id"], "unjudged")
            p_click = CLICK_MODEL[label] / math.log2(pos + 2)
            if rng.random() < p_click:
                clicks.append(pos)
        winner = credit(shown.items, clicks)
        wins_a += winner == "a"
        wins_b += winner == "b"
        ties += winner == "tie"
    return {
        "method": "team-draft interleaving, simulated clicks from ESCI labels with position bias",
        "click_model": CLICK_MODEL,
        **readout(wins_a, wins_b, ties, a, b),
    }
