"""Command line: data preparation, indexing, evaluation, interleaving simulation and the API."""

from __future__ import annotations

import json
import sys
import time

import typer

from hss.config import settings
from hss.logging_setup import configure_logging

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Hybrid search service")


def _echo(title: str, payload: dict, started: float) -> None:
    typer.echo(f"{title} in {time.time() - started:.1f}s")
    typer.echo(json.dumps(payload, indent=2, default=str))


@app.callback()
def _setup() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    configure_logging(settings().log_level, "hss")


def build_engine():
    from hss.embeddings import Models
    from hss.engine import Engine
    from hss.index import client
    from hss.query import QueryUnderstanding

    s = settings()
    models = Models(s.embed_model, s.rerank_model, s.models_dir)
    return Engine(
        client(s.opensearch_url),
        s.index,
        models,
        QueryUnderstanding(s.prepared_dir),
        s.rrf_k,
        s.candidates,
        s.rerank_top,
        ef_search=s.knn_ef_search,
    )


@app.command()
def prepare(catalog_size: int | None = None, eval_queries: int | None = None) -> None:
    """Sample the evaluation queries and build the catalog from the ESCI files."""
    from hss.data import prepare as run

    s = settings()
    started = time.time()
    stats = run(s.raw_dir, s.prepared_dir, catalog_size or s.catalog_size, eval_queries or s.eval_queries, s.seed)
    s.reports_dir.mkdir(parents=True, exist_ok=True)
    (s.reports_dir / "prepare-stats.json").write_text(json.dumps(stats.__dict__, indent=2), encoding="utf-8")
    _echo("prepare", stats.__dict__, started)


@app.command()
def index(recreate: bool = True) -> None:
    """Embed the catalog (cached) and bulk load the OpenSearch index."""
    from hss.data import load_catalog
    from hss.embeddings import Models
    from hss.index import client, embeddings_for, ensure_index, load

    s = settings()
    started = time.time()
    catalog = load_catalog(s.prepared_dir)
    models = Models(s.embed_model, s.rerank_model, s.models_dir)
    vectors = embeddings_for(catalog, models, s.prepared_dir / "embeddings.npy")
    embed_s = time.time() - started
    os_client = client(s.opensearch_url)
    ensure_index(os_client, s.index, vectors.shape[1], recreate=recreate)
    stats = load(os_client, s.index, catalog, vectors)
    _echo("index", {**stats, "embedding_seconds": round(embed_s, 1), "dimension": int(vectors.shape[1])}, started)


@app.command()
def search(query: str, ranker: str = "hybrid", size: int = 10) -> None:
    """One query from the command line."""
    engine = build_engine()
    result = engine.search(query, ranker=ranker, size=size)
    typer.echo(json.dumps(result.to_dict(), indent=2))


@app.command()
def evaluate(
    limit: int | None = None, rankers: str = "bm25,knn,hybrid,hybrid_rerank", apply_filters: bool = False
) -> None:
    """NDCG@10, MRR@10 and latency per ranker on the labelled sample; writes reports/eval.json."""
    from hss.data import load_eval
    from hss.evaluate import evaluate as run
    from hss.evaluate import write_report

    s = settings()
    started = time.time()
    queries, judgments = load_eval(s.prepared_dir)
    report = run(
        build_engine(), queries, judgments, tuple(rankers.split(",")), limit=limit, apply_filters=apply_filters
    )
    name = "eval-with-filters.json" if apply_filters else "eval.json"
    write_report(report, s.reports_dir / name)
    brief = {k: {kk: vv for kk, vv in v.items() if kk != "per_query"} for k, v in report["rankers"].items()}
    _echo("evaluate", brief, started)


@app.command()
def interleave_sim(queries: int = 1000, a: str = "hybrid", b: str = "hybrid_rerank", seed: int = 42) -> None:
    """Team-draft interleaving of two rankers with a click model over the labels; writes reports/interleaving.json."""
    from hss.data import load_eval
    from hss.interleave import simulate

    s = settings()
    started = time.time()
    q, j = load_eval(s.prepared_dir)
    report = simulate(build_engine(), q, j, a, b, queries, seed)
    (s.reports_dir).mkdir(parents=True, exist_ok=True)
    (s.reports_dir / "interleaving.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    _echo("interleave-sim", report, started)


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000) -> None:  # noqa: S104
    """Run the API."""
    import os

    import uvicorn

    uvicorn.run(
        "hss.serving.app:app",
        host=host,
        port=port,
        log_config=None,
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("HSS_FORWARDED_ALLOW_IPS", "127.0.0.1"),
        workers=int(os.environ.get("HSS_WORKERS", "1")),
    )


if __name__ == "__main__":
    app()
