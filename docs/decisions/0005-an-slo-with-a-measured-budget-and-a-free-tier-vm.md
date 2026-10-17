# 0005. An SLO with a measured budget, Redis in front, and a free-tier VM as the deployment

Date: 2026-10-17

## Context

The brief asks for rate limiting, an SLO such as p99 under 200 ms with an alert, k6 load tests,
OpenTelemetry and a deployment with infrastructure as code. OpenSearch needs a few gigabytes of memory,
which the free tiers of container platforms do not offer.

## Decision

- **SLO:** p99 under 200 ms for uncached hybrid searches at the stated request rate, measured by k6 on
  one machine against the Compose stack, and watched in production by a Prometheus alert on the same
  histogram (`search_latency_seconds{ranker="hybrid",cached="false"}`). The reranked variant is measured
  and reported separately because a CPU cross-encoder cannot meet 200 ms on 50 candidates; on this
  machine it sustains 2 requests/s and queues at 4.
- **Cache:** Redis keyed by normalised query, ranker, size and filters with a five-minute TTL; the API
  falls back to an in-process cache when Redis is unreachable and raises a warning alert, rather than
  failing.
- **Rate limit:** per client address per route; the UI's nginx forwards the client address and is the
  only trusted proxy.
- **Observability:** JSON logs with request ids, Prometheus metrics per ranker and stage (understand,
  BM25, embed, kNN, rerank), OpenTelemetry traces when an endpoint is configured, a Grafana dashboard,
  six alert rules with promtool tests.
- **Deployment:** Terraform for an Oracle Cloud always-free ARM VM (4 OCPUs, 24 GB) with a cloud-init
  that installs Docker and runs the same Compose file from GHCR images; the prepared data and embeddings
  are uploaded, the index is built on the VM by the indexer job. Only port 80 (the UI's nginx) is open;
  OpenSearch and Redis are not published.

## Consequences

- The stated latency numbers are single-machine; the README says so and gives the machine.
- Images are built for arm64 and amd64 with buildx; the torch CPU wheels exist for both.
- The free tier has capacity limits per region; Terraform may need a second region if the A1 shape is
  out of stock, which is noted in the deployment guide.
