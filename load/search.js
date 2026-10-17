// k6 load test: constant arrival rate against /search, queries drawn from the labelled sample. The hybrid and
// rerank scenarios bypass the cache (no_cache=true) so they measure the engine; a third, shorter scenario
// replays the sample with the cache on, which is what repeat traffic sees. Thresholds are the SLO: uncached
// hybrid p99 under 200 ms. The rerank scenario runs at RERANK_RATE (default 2/s): a CPU cross-encoder over
// 50 candidates is about 0.35 s of work, and 4/s overran this machine (reports/k6-before-rerank-4ps-r50.json).
// setup() warms the index (first k-NN queries load the HNSW graph).
//   RATE=25 docker compose --profile load run --rm k6     -> reports/k6-raw/k6-r25.json
//   make load                                            runs 10, 25 and 50 requests/s in turn
import http from 'k6/http';
import { check } from 'k6';
import { SharedArray } from 'k6/data';
import { Trend } from 'k6/metrics';

const API = __ENV.API_BASE || 'http://localhost:8120';
const RATE = Number(__ENV.RATE || 50);
const DURATION = __ENV.DURATION || '60s';
const queries = new SharedArray('queries', () => JSON.parse(open('./queries.json')));

const hybrid = new Trend('search_hybrid_ms', true);
const rerank = new Trend('search_hybrid_rerank_ms', true);
const cached = new Trend('search_cached_ms', true);

export const options = {
  scenarios: {
    hybrid: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: 20,
      maxVUs: 200,
      exec: 'searchHybrid',
    },
    rerank: {
      executor: 'constant-arrival-rate',
      rate: Number(__ENV.RERANK_RATE || 2),
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: 10,
      maxVUs: 100,
      exec: 'searchRerank',
      startTime: DURATION,
    },
    cached: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: '30s',
      preAllocatedVUs: 10,
      maxVUs: 100,
      exec: 'searchCached',
      startTime: `${2 * parseInt(DURATION, 10)}s`,
    },
  },
  thresholds: {
    search_hybrid_ms: ['p(99)<200'],
    http_req_failed: ['rate<0.01'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max', 'count'],
};

function pick() {
  return queries[Math.floor(Math.random() * queries.length)];
}

function run(ranker, trend, noCache) {
  const q = pick();
  const url = `${API}/search?q=${encodeURIComponent(q)}&ranker=${ranker}&size=10${noCache ? '&no_cache=true' : ''}`;
  const res = http.get(url, { tags: { ranker } });
  check(res, { 'status 200': (r) => r.status === 200 });
  if (res.status === 200 && trend) {
    const body = res.json();
    if (noCache) trend.add(res.timings.duration);
    else if (body.cached) trend.add(res.timings.duration); // the cached scenario only reports cache hits
  }
}

export function setup() {
  for (let i = 0; i < 30; i++) run('hybrid', null, true);
  for (let i = 0; i < 3; i++) run('hybrid_rerank', null, true);
}

export function searchHybrid() {
  run('hybrid', hybrid, true);
}

export function searchRerank() {
  run('hybrid_rerank', rerank, true);
}

export function searchCached() {
  run('hybrid', cached, false);
}

export function handleSummary(data) {
  const pick = (name) => {
    const m = data.metrics[name];
    return m ? { count: m.values.count, med: m.values.med, p90: m.values['p(90)'], p95: m.values['p(95)'], p99: m.values['p(99)'], max: m.values.max } : null;
  };
  const summary = {
    rate_per_s: RATE,
    duration: DURATION,
    hybrid_uncached_ms: pick('search_hybrid_ms'),
    hybrid_rerank_uncached_ms: pick('search_hybrid_rerank_ms'),
    cached_ms: pick('search_cached_ms'),
    http_req_failed: data.metrics.http_req_failed ? data.metrics.http_req_failed.values.rate : null,
    requests: data.metrics.http_reqs ? data.metrics.http_reqs.values.count : null,
    thresholds: Object.fromEntries(Object.entries(data.metrics).filter(([, m]) => m.thresholds).map(([k, m]) => [k, Object.values(m.thresholds).every((t) => t.ok)])),
  };
  return { [`/out/k6-r${RATE}.json`]: JSON.stringify(summary, null, 2), stdout: JSON.stringify(summary, null, 2) + '\n' };
}
