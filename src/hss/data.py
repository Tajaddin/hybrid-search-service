"""Prepare the catalog and the labelled evaluation sample from the ESCI parquet files.

US locale, the dataset's small version. The evaluation queries are a fixed random sample of the test
split with every judgment they have; the catalog is every product those queries judged plus random
products from the rest of the small-version US catalog up to the configured size, so that the index
contains realistic distractors and unjudged results are possible (they count as irrelevant in the eval).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

GAIN = {"E": 1.0, "S": 0.1, "C": 0.01, "I": 0.0}
TOKEN = re.compile(r"[a-z0-9][a-z0-9'\-]{1,}")


@dataclass
class PrepareStats:
    eval_queries: int
    eval_judgments: int
    judged_products: int
    catalog_products: int
    brands: int
    colors: int
    vocabulary_terms: int
    label_counts: dict[str, int]


def _clean(text: str | None, limit: int) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def prepare(raw_dir: Path, out_dir: Path, catalog_size: int, eval_queries: int, seed: int) -> PrepareStats:
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    examples = pq.read_table(raw_dir / "shopping_queries_dataset_examples.parquet")
    us = examples.filter(pc.and_(pc.equal(examples["product_locale"], "us"), pc.equal(examples["small_version"], 1)))
    test = us.filter(pc.equal(us["split"], "test"))
    query_ids = np.array(sorted(set(test["query_id"].to_pylist())))
    chosen = set(rng.choice(query_ids, size=min(eval_queries, len(query_ids)), replace=False).tolist())
    mask = pa.array([q in chosen for q in test["query_id"].to_pylist()])
    judgments = test.filter(mask).select(["query_id", "query", "product_id", "esci_label"])
    judgments = judgments.append_column(
        "gain", pa.array([GAIN[lbl] for lbl in judgments["esci_label"].to_pylist()], pa.float64())
    )
    queries = (
        judgments.group_by("query_id")
        .aggregate([("query", "min")])
        .rename_columns(["query_id", "query"])
        .sort_by("query_id")
    )
    pq.write_table(judgments, out_dir / "eval_judgments.parquet")
    pq.write_table(queries, out_dir / "eval_queries.parquet")

    judged = set(judgments["product_id"].to_pylist())
    small_products = set(us["product_id"].to_pylist())
    pool = np.array(sorted(small_products - judged))
    extra = max(0, catalog_size - len(judged))
    distractors = set(rng.choice(pool, size=min(extra, len(pool)), replace=False).tolist()) if extra else set()
    keep = judged | distractors

    products = pq.read_table(raw_dir / "shopping_queries_dataset_products.parquet")
    products = products.filter(pc.equal(products["product_locale"], "us"))
    products = products.filter(pa.array([p in keep for p in products["product_id"].to_pylist()]))
    rows = []
    brand_counter: Counter[str] = Counter()
    color_counter: Counter[str] = Counter()
    vocab: Counter[str] = Counter()
    for r in products.to_pylist():
        title = _clean(r["product_title"], 300)
        brand = _clean(r["product_brand"], 80)
        color = _clean(r["product_color"], 60)
        bullets = _clean(r["product_bullet_point"], 1500)
        description = _clean(r["product_description"], 2000)
        if not title:
            continue
        rows.append(
            {
                "product_id": r["product_id"],
                "title": title,
                "brand": brand or None,
                "color": color or None,
                "bullets": bullets or None,
                "description": description or None,
            }
        )
        if brand:
            brand_counter[brand] += 1
        if color:
            color_counter[color.lower()] += 1
        vocab.update(TOKEN.findall((title + " " + brand).lower()))
    catalog = pa.Table.from_pylist(rows)
    pq.write_table(catalog, out_dir / "catalog.parquet")

    # Spelling vocabulary: terms seen at least three times in titles and brands, with frequencies (SymSpell format).
    with (out_dir / "vocabulary.txt").open("w", encoding="utf-8") as fh:
        for term, n in vocab.most_common():
            if n >= 3:
                fh.write(f"{term} {n}\n")
    # Brand and colour values the query understanding may recognise (lowercase -> canonical, with counts).
    (out_dir / "attributes.json").write_text(
        json.dumps(
            {
                "brands": {
                    b.lower(): {"value": b, "count": n} for b, n in brand_counter.items() if n >= 20 and len(b) >= 3
                },
                "colors": {c: n for c, n in color_counter.items() if n >= 50 and len(c) >= 3},
            },
            indent=0,
        ),
        encoding="utf-8",
    )
    labels = Counter(judgments["esci_label"].to_pylist())
    stats = PrepareStats(
        eval_queries=queries.num_rows,
        eval_judgments=judgments.num_rows,
        judged_products=len(judged),
        catalog_products=catalog.num_rows,
        brands=len(brand_counter),
        colors=len(color_counter),
        vocabulary_terms=sum(1 for _, n in vocab.items() if n >= 3),
        label_counts=dict(labels),
    )
    (out_dir / "prepare-stats.json").write_text(json.dumps(asdict(stats), indent=2), encoding="utf-8")
    return stats


def load_catalog(prepared_dir: Path) -> pa.Table:
    return pq.read_table(prepared_dir / "catalog.parquet")


def load_eval(prepared_dir: Path) -> tuple[pa.Table, pa.Table]:
    return pq.read_table(prepared_dir / "eval_queries.parquet"), pq.read_table(prepared_dir / "eval_judgments.parquet")
