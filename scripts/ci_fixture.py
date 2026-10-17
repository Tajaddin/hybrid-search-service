"""A small catalog for CI and quick local runs, built without the ESCI download: 400 synthetic products across
a few brands and colours, a vocabulary, attributes, and an evaluation sample with judgments, under data/ci.
Also writes placeholder reports so the UI's evaluation tab has something to show."""

from __future__ import annotations

import json
import random
import re
import shutil
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
BRANDS = ["Nike", "Sony", "Anker", "Lego", "Oster", "Columbia", "Logitech", "Pyrex"]
COLORS = ["black", "red", "blue", "white", "green"]
KINDS = [
    ("wireless headphones", "bluetooth over-ear headphones with noise cancelling and 30 hour battery"),
    ("running shoes", "lightweight running shoes with breathable mesh upper and cushioned sole"),
    ("bluetooth speaker", "portable waterproof bluetooth speaker with deep bass and 12 hour playtime"),
    ("coffee maker", "12 cup programmable coffee maker with glass carafe and auto shut off"),
    ("building blocks set", "creative building blocks set with 500 pieces for ages 6 and up"),
    ("rain jacket", "waterproof rain jacket with hood and sealed seams"),
    ("wireless mouse", "ergonomic wireless mouse with silent clicks and 18 month battery"),
    ("glass storage containers", "glass food storage containers with airtight lids, set of 10"),
]


def main(target: Path = ROOT / "data" / "ci") -> None:
    shutil.rmtree(target, ignore_errors=True)
    prepared = target / "prepared"
    prepared.mkdir(parents=True)
    rng = random.Random(7)
    rows = []
    for i in range(400):
        kind, bullets = KINDS[i % len(KINDS)]
        brand = BRANDS[(i // len(KINDS)) % len(BRANDS)]
        color = COLORS[i % len(COLORS)]
        rows.append(
            {
                "product_id": f"CI{i:05d}",
                "title": f"{brand} {kind} {color} model {i}",
                "brand": brand,
                "color": color,
                "bullets": bullets,
                "description": f"{bullets}. Model {i} by {brand} in {color}.",
            }
        )
    catalog = pa.Table.from_pylist(rows)
    pq.write_table(catalog, prepared / "catalog.parquet")
    vocab: Counter[str] = Counter()
    for r in rows:
        vocab.update(re.findall(r"[a-z0-9][a-z0-9'\-]+", (r["title"] + " " + r["brand"]).lower()))
    (prepared / "vocabulary.txt").write_text("\n".join(f"{t} {n}" for t, n in vocab.most_common()), encoding="utf-8")
    (prepared / "attributes.json").write_text(
        json.dumps(
            {"brands": {b.lower(): {"value": b, "count": 50} for b in BRANDS}, "colors": dict.fromkeys(COLORS, 80)}
        ),
        encoding="utf-8",
    )
    # Evaluation sample: each kind is a query; exact = same kind, substitute = same kind other brand... keep it simple:
    queries, judgments = [], []
    for qid, (kind, _) in enumerate(KINDS):
        queries.append({"query_id": qid, "query": kind})
        for r in rows:
            if kind in r["title"]:
                label = "E" if rng.random() < 0.7 else "S"
                judgments.append(
                    {
                        "query_id": qid,
                        "query": kind,
                        "product_id": r["product_id"],
                        "esci_label": label,
                        "gain": 1.0 if label == "E" else 0.1,
                    }
                )
    pq.write_table(pa.Table.from_pylist(queries), prepared / "eval_queries.parquet")
    pq.write_table(pa.Table.from_pylist(judgments), prepared / "eval_judgments.parquet")
    reports = target / "reports"
    reports.mkdir()
    (reports / "eval.json").write_text(
        json.dumps(
            {
                "queries": len(queries),
                "rankers": {
                    "hybrid": {
                        "ndcg10": 0.0,
                        "mrr10": 0.0,
                        "recall50_es": 0.0,
                        "latency_ms": {"p50": 0, "p95": 0, "p99": 0},
                    }
                },
                "comparisons": [],
            }
        ),
        encoding="utf-8",
    )
    print(f"ci fixture: {catalog.num_rows} products, {len(queries)} queries, {len(judgments)} judgments under {target}")


if __name__ == "__main__":
    main()
