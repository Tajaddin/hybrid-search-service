"""FastAPI: search with a cache, suggestions, team-draft interleaving with a click log, the evaluation
summary, health and metrics. Rate limits per client address; an admin key for the interleaving readout."""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any

import structlog
from fastapi import Body, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from hss import __version__
from hss.cache import Store
from hss.config import Settings, settings
from hss.engine import RANKERS, Engine
from hss.interleave import credit, interleave, readout
from hss.logging_setup import configure_logging, get_logger

REQUESTS = Histogram(
    "http_request_duration_seconds",
    "Request latency",
    ["method", "route", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1, 2.5, 5),
)
SEARCHES = Counter("search_requests_total", "Searches", ["ranker", "cached"])
STAGE = Histogram(
    "search_stage_seconds",
    "Time per stage of a search",
    ["ranker", "stage"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1),
)
SEARCH_LATENCY = Histogram(
    "search_latency_seconds",
    "End-to-end search latency inside the handler",
    ["ranker", "cached"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1, 2.5),
)
EMPTY = Counter("search_empty_total", "Searches with no results", ["ranker"])
CLICKS = Counter("interleave_clicks_total", "Clicks credited", ["team"])
IMPRESSIONS = Counter("interleave_impressions_total", "Interleaved result pages served")
RATE_LIMITED = Counter("rate_limit_rejections_total", "Requests answered 429")
CACHE_BACKEND = Gauge("cache_backend_redis", "1 when the cache is Redis, 0 when in-process")
INDEX_DOCS = Gauge("index_documents", "Documents in the product index")
ERRORS = Counter("search_errors_total", "Searches that raised", ["ranker"])

log = get_logger("api")


class State:
    def __init__(self, s: Settings, engine: Engine | None = None, store: Store | None = None) -> None:
        self.settings = s
        if engine is None:
            from hss.cli import build_engine

            engine = build_engine()
        self.engine = engine
        self.store = store or Store(s.redis_url)
        CACHE_BACKEND.set(1 if self.store.backend == "redis" else 0)
        self.rng = random.Random()
        self.reports = {name: self._json(s.reports_dir / f"{name}.json") for name in ("eval", "interleaving", "k6")}
        self.warmup_s = self._warm_up()

    def _warm_up(self) -> float | None:
        started = time.perf_counter()
        try:
            self.engine.models.embed_query("warm up")
            self.engine.models.rerank_scores("warm up", ["a product"])
        except Exception as exc:  # the models are loaded lazily; a failure here shows up in /health later
            log.warning("model warm-up failed", error=str(exc)[:200])
            return None
        return round(time.perf_counter() - started, 2)

    @staticmethod
    def _json(path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def index_health(self) -> dict[str, Any]:
        try:
            # cluster.health on a missing index waits for it to appear; answer at once instead so readiness
            # probes get a quick, honest answer before the indexer has run
            if not self.engine.os.indices.exists(index=self.engine.index, request_timeout=2):
                return {"status": "missing", "documents": 0}
            health = self.engine.os.cluster.health(index=self.engine.index, request_timeout=2)
            count = self.engine.os.count(index=self.engine.index)["count"]
            INDEX_DOCS.set(count)
            return {"status": health.get("status"), "documents": count}
        except Exception as exc:
            return {"status": "unavailable", "error": str(exc)[:120]}


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = settings()
    if not hasattr(app.state, "api"):
        app.state.api = State(s)
    log.info("api ready", cache=app.state.api.store.backend, index=app.state.api.index_health())
    yield


def _key(request: Request) -> str:
    return get_remote_address(request)


def create_app(state: State | None = None) -> FastAPI:
    s = settings()
    configure_logging(s.log_level, s.service_name)
    app = FastAPI(title="Hybrid search service", version=__version__, lifespan=lifespan, docs_url="/docs")
    if state is not None:
        app.state.api = state
    limiter = Limiter(key_func=_key, default_limits=[])
    app.state.limiter = limiter
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in s.cors_origins.split(",") if o.strip()],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Admin-Key"],
    )
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        _setup_tracing(app, s)

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limited(request: Request, exc: RateLimitExceeded):
        RATE_LIMITED.inc()
        return JSONResponse(
            {"error": "rate limit exceeded", "detail": str(exc.detail)}, status_code=429, headers={"Retry-After": "60"}
        )

    @app.middleware("http")
    async def _observe(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - started
        route = request.scope.get("route")
        template = getattr(route, "path", request.url.path)
        REQUESTS.labels(request.method, template, str(response.status_code)).observe(elapsed)
        response.headers["X-Request-ID"] = request_id
        if template != "/metrics":
            log.info(
                "request",
                method=request.method,
                route=template,
                status=response.status_code,
                ms=round(elapsed * 1000, 1),
            )
        structlog.contextvars.unbind_contextvars("request_id")
        return response

    def st(request: Request) -> State:
        return request.app.state.api

    # ---- open --------------------------------------------------------------------------------------------
    @app.get("/health")
    def health(request: Request):
        x = st(request)
        index = x.index_health()
        ok = index.get("status") in ("green", "yellow") and x.store.ping()
        return {
            "status": "ok" if ok else "degraded",
            "version": __version__,
            "index": index,
            "cache": x.store.backend,
            "models": {
                "embedder": x.engine.models.embed_name,
                "reranker": x.engine.models.rerank_name,
                "warmup_s": x.warmup_s,
            },
            "rankers": list(RANKERS),
        }

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/search")
    @limiter.limit(s.rate_limit)
    def search(
        request: Request,
        q: Annotated[str, Query(min_length=1, max_length=200)],
        ranker: Annotated[str, Query(pattern="^(bm25|knn|hybrid|hybrid_rerank)$")] = "hybrid",
        size: Annotated[int, Query(ge=1, le=50)] = 10,
        brand: Annotated[str | None, Query(max_length=80)] = None,
        color: Annotated[str | None, Query(max_length=60)] = None,
        auto_filters: bool = True,
        no_cache: bool = False,
    ):
        x = st(request)
        filters = {k: v for k, v in (("brand", brand), ("color", color)) if v}
        key = (
            "hss:search:"
            + hashlib.sha1(
                json.dumps([q.strip().lower(), ranker, size, filters, auto_filters]).encode(), usedforsecurity=False
            ).hexdigest()
        )
        started = time.perf_counter()
        if not no_cache:
            hit = x.store.get_json(key)
            if hit is not None:
                SEARCHES.labels(ranker, "true").inc()
                SEARCH_LATENCY.labels(ranker, "true").observe(time.perf_counter() - started)
                return {**hit, "cached": True}
        try:
            result = x.engine.search(
                q, ranker=ranker, size=size, filters=filters, apply_understood_filters=auto_filters
            )
        except Exception as exc:
            ERRORS.labels(ranker).inc()
            log.error("search failed", error=str(exc)[:200])
            raise HTTPException(503, "search backend unavailable") from exc
        payload = result.to_dict()
        for stage, ms in result.took_ms.items():
            STAGE.labels(ranker, stage).observe(ms / 1000)
        if not result.hits:
            EMPTY.labels(ranker).inc()
        x.store.set_json(key, payload, x.settings.cache_ttl_s)
        SEARCHES.labels(ranker, "false").inc()
        SEARCH_LATENCY.labels(ranker, "false").observe(time.perf_counter() - started)
        return {**payload, "cached": False}

    @app.get("/suggest")
    @limiter.limit(s.rate_limit)
    def suggest(request: Request, q: Annotated[str, Query(min_length=1, max_length=100)]):
        x = st(request)
        understood = x.engine.qu.understand(q)
        return {
            "corrected": understood.corrected if understood.corrections else None,
            "completions": x.engine.qu.suggest(q),
        }

    # ---- interleaving --------------------------------------------------------------------------------------
    @app.get("/interleave")
    @limiter.limit(s.rate_limit)
    def interleave_(
        request: Request,
        q: Annotated[str, Query(min_length=1, max_length=200)],
        a: Annotated[str, Query(pattern="^(bm25|knn|hybrid|hybrid_rerank)$")] = "hybrid",
        b: Annotated[str, Query(pattern="^(bm25|knn|hybrid|hybrid_rerank)$")] = "hybrid_rerank",
        size: Annotated[int, Query(ge=2, le=30)] = 10,
    ):
        x = st(request)
        if a == b:
            raise HTTPException(422, "pick two different rankers")
        try:
            shown = interleave(x.engine, q, a, b, size, x.rng)
        except Exception as exc:
            ERRORS.labels("interleave").inc()
            raise HTTPException(503, "search backend unavailable") from exc
        impression_id = str(uuid.uuid4())
        record = {
            "impression_id": impression_id,
            "q": q,
            "a": a,
            "b": b,
            "teams": [it["team"] for it in shown.items],
            "clicks": [],
            "ts": time.time(),
        }
        x.store.set_json(f"hss:impression:{impression_id}", record, 6 * 3600)
        x.store.push("hss:impressions", impression_id)
        IMPRESSIONS.inc()
        # The team is kept server-side; the client must not know which ranker produced a result.
        return {
            "impression_id": impression_id,
            "query": q,
            "items": [{k: v for k, v in it.items() if k != "team"} for it in shown.items],
            "took_ms": shown.meta,
        }

    @app.post("/click")
    @limiter.limit(s.rate_limit)
    def click(request: Request, body: Annotated[dict, Body()]):
        x = st(request)
        impression_id = str(body.get("impression_id", ""))
        position = body.get("position")
        if not impression_id or not isinstance(position, int):
            raise HTTPException(422, "impression_id and integer position are required")
        record = x.store.get_json(f"hss:impression:{impression_id}")
        if record is None:
            raise HTTPException(404, "unknown or expired impression")
        if not 0 <= position < len(record["teams"]):
            raise HTTPException(422, "position out of range")
        record["clicks"].append(position)
        x.store.set_json(f"hss:impression:{impression_id}", record, 6 * 3600)
        CLICKS.labels(record["teams"][position]).inc()
        return {"ok": True, "clicks": len(record["clicks"])}

    @app.get("/interleave/report")
    def interleave_report(request: Request, x_admin_key: Annotated[str | None, Header()] = None):
        x = st(request)
        if not x.settings.admin_key or x_admin_key != x.settings.admin_key:
            raise HTTPException(401, "admin key required")
        wins: dict[tuple[str, str], dict[str, int]] = {}
        impressions = 0
        for impression_id in x.store.items("hss:impressions"):
            record = x.store.get_json(f"hss:impression:{impression_id}")
            if not record or not record["clicks"]:
                continue
            impressions += 1
            items = [{"team": t} for t in record["teams"]]
            winner = credit(items, record["clicks"])
            tally = wins.setdefault((record["a"], record["b"]), {"a": 0, "b": 0, "tie": 0})
            tally[winner] += 1
        live = [readout(t["a"], t["b"], t["tie"], a, b) for (a, b), t in wins.items()]
        return {"impressions_with_clicks": impressions, "live": live, "simulated": x.reports.get("interleaving")}

    # ---- reports ---------------------------------------------------------------------------------------------
    @app.get("/eval/summary")
    def eval_summary(request: Request):
        x = st(request)
        if not x.reports.get("eval"):
            raise HTTPException(404, "no evaluation report; run `hss evaluate`")
        return {"eval": x.reports["eval"], "interleaving": x.reports.get("interleaving"), "load": x.reports.get("k6")}

    return app


def _setup_tracing(app: FastAPI, s: Settings) -> None:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": s.service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)


app = create_app()
