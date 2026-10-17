"""Write 300 queries from the labelled sample to load/queries.json for the k6 script (committed: small)."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    queries = pq.read_table(ROOT / "data/prepared/eval_queries.parquet")["query"].to_pylist()
    rng = random.Random(42)
    sample = sorted(rng.sample(queries, min(300, len(queries))))
    (ROOT / "load").mkdir(exist_ok=True)
    (ROOT / "load/queries.json").write_text(json.dumps(sample, indent=0, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(sample)} queries")


if __name__ == "__main__":
    main()
