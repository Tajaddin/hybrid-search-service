import { useEffect, useState } from 'react';
import { api, type EvalSummary, type Hit, type InterleaveResponse, type Ranker, RANKERS, type SearchResponse } from './api';
import './index.css';

type Mode = 'search' | 'interleave' | 'eval';

export default function App() {
  const [mode, setMode] = useState<Mode>('search');
  const [health, setHealth] = useState('');

  useEffect(() => {
    api
      .health()
      .then((h) => setHealth(`${h.status} · ${h.index.documents.toLocaleString()} products · cache ${h.cache}`))
      .catch(() => setHealth('API unreachable'));
  }, []);

  return (
    <div className="app">
      <header>
        <h1>Hybrid product search</h1>
        <span className="muted" data-testid="health">
          {health}
        </span>
        <nav>
          {(['search', 'interleave', 'eval'] as Mode[]).map((m) => (
            <button key={m} className={mode === m ? 'tab active' : 'tab'} onClick={() => setMode(m)} data-testid={`tab-${m}`}>
              {m === 'search' ? 'Search' : m === 'interleave' ? 'Interleaving' : 'Evaluation'}
            </button>
          ))}
        </nav>
      </header>
      <main>
        {mode === 'search' && <SearchPage />}
        {mode === 'interleave' && <InterleavePage />}
        {mode === 'eval' && <EvalPage />}
      </main>
      <footer className="muted">
        Amazon ESCI catalog (Apache 2.0), 200,000 US products. BM25 and kNN in OpenSearch, reciprocal rank fusion, cross-encoder rerank on CPU.
      </footer>
    </div>
  );
}

function SearchPage() {
  const [q, setQ] = useState('wireless headphnes');
  const [ranker, setRanker] = useState<Ranker>('hybrid');
  const [brand, setBrand] = useState('');
  const [color, setColor] = useState('');
  const [auto, setAuto] = useState(true);
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const run = async (query = q, r = ranker, b = brand, c = color) => {
    if (!query.trim()) return;
    setBusy(true);
    setError('');
    try {
      setResult(await api.search(query, r, b || undefined, c || undefined, auto));
    } catch (e) {
      setError(`Search failed (${(e as Error).message})`);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onType = async (value: string) => {
    setQ(value);
    if (value.length >= 3) {
      try {
        setSuggestions((await api.suggest(value)).completions);
      } catch {
        setSuggestions([]);
      }
    } else setSuggestions([]);
  };

  const facets = (key: 'brand' | 'color') => {
    const counts = new Map<string, number>();
    for (const h of result?.hits ?? []) {
      const v = h[key];
      if (v) counts.set(v, (counts.get(v) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);
  };

  return (
    <section>
      <form
        className="bar"
        onSubmit={(e) => {
          e.preventDefault();
          setSuggestions([]);
          void run();
        }}
      >
        <input value={q} onChange={(e) => void onType(e.target.value)} placeholder="search products" data-testid="q" list="suggestions" />
        <datalist id="suggestions">
          {suggestions.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
        <select value={ranker} onChange={(e) => setRanker(e.target.value as Ranker)} data-testid="ranker">
          {RANKERS.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
        <label className="muted">
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> auto filters
        </label>
        <button type="submit" className="primary" disabled={busy} data-testid="go">
          {busy ? 'Searching…' : 'Search'}
        </button>
      </form>
      {error && <p className="error">{error}</p>}
      {result && (
        <>
          <p className="muted" data-testid="understood">
            {result.query.corrections.length > 0 && (
              <>
                Showing results for <b>{result.query.corrected}</b> (corrected from “{result.query.original}”).{' '}
              </>
            )}
            {result.query.brand && <>Brand filter: {result.query.brand}. </>}
            {result.query.color && <>Colour filter: {result.query.color}. </>}
            {result.ranker} · {result.hits.length} results · {Math.round(result.took_ms.total ?? 0)} ms {result.cached ? '(cached)' : ''}
            {result.took_ms.rerank !== undefined && ` · rerank ${Math.round(result.took_ms.rerank)} ms`}
          </p>
          <div className="layout">
            <aside>
              {(['brand', 'color'] as const).map((key) => (
                <div key={key} className="facet">
                  <h3>{key}</h3>
                  {facets(key).map(([v, n]) => (
                    <button
                      key={v}
                      className={(key === 'brand' ? brand : color) === v ? 'chip active' : 'chip'}
                      onClick={() => {
                        const next = (key === 'brand' ? brand : color) === v ? '' : v;
                        if (key === 'brand') setBrand(next);
                        else setColor(next);
                        void run(q, ranker, key === 'brand' ? next : brand, key === 'color' ? next : color);
                      }}
                      data-testid={`facet-${key}`}
                    >
                      {v} <span className="muted">{n}</span>
                    </button>
                  ))}
                </div>
              ))}
            </aside>
            <ol className="results">
              {result.hits.map((h) => (
                <li key={h.product_id} data-testid="hit">
                  <Product hit={h} showRanks />
                </li>
              ))}
            </ol>
          </div>
        </>
      )}
    </section>
  );
}

function Product({ hit, showRanks }: { hit: Hit; showRanks?: boolean }) {
  return (
    <div className="product">
      <div className="title">{hit.title}</div>
      <div className="muted small">
        {hit.brand ?? 'no brand'} {hit.color ? `· ${hit.color}` : ''} · <code>{hit.product_id}</code>
        {showRanks && (
          <>
            {hit.bm25_rank !== null && <span className="badge">bm25 #{hit.bm25_rank}</span>}
            {hit.knn_rank !== null && <span className="badge">knn #{hit.knn_rank}</span>}
            {hit.rerank_score !== null && <span className="badge">rerank {hit.rerank_score.toFixed(2)}</span>}
          </>
        )}
      </div>
    </div>
  );
}

function InterleavePage() {
  const [q, setQ] = useState('running shoes');
  const [a, setA] = useState<Ranker>('hybrid');
  const [b, setB] = useState<Ranker>('hybrid_rerank');
  const [page, setPage] = useState<InterleaveResponse | null>(null);
  const [clicked, setClicked] = useState<number[]>([]);
  const [error, setError] = useState('');

  const run = async () => {
    setError('');
    setClicked([]);
    try {
      setPage(await api.interleave(q, a, b));
    } catch (e) {
      setError(`Interleaving failed (${(e as Error).message})`);
    }
  };

  const click = async (position: number) => {
    if (!page) return;
    await api.click(page.impression_id, position);
    setClicked((c) => [...c, position]);
  };

  return (
    <section>
      <p className="muted">
        Two rankers answer the same query; their lists are merged by team-draft interleaving and the result you click is credited to the ranker that
        drafted it. The page never shows which ranker produced which result.
      </p>
      <form
        className="bar"
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <input value={q} onChange={(e) => setQ(e.target.value)} data-testid="iq" />
        <select value={a} onChange={(e) => setA(e.target.value as Ranker)} data-testid="ranker-a">
          {RANKERS.map((r) => (
            <option key={r}>{r}</option>
          ))}
        </select>
        <span className="muted">vs</span>
        <select value={b} onChange={(e) => setB(e.target.value as Ranker)} data-testid="ranker-b">
          {RANKERS.map((r) => (
            <option key={r}>{r}</option>
          ))}
        </select>
        <button type="submit" className="primary" data-testid="igo">
          Interleave
        </button>
      </form>
      {error && <p className="error">{error}</p>}
      {page && (
        <ol className="results">
          {page.items.map((h, i) => (
            <li key={h.product_id} data-testid="ihit" className={clicked.includes(i) ? 'clicked' : ''}>
              <button className="link" onClick={() => void click(i)} data-testid="iclick">
                <Product hit={h} />
              </button>
            </li>
          ))}
        </ol>
      )}
      {clicked.length > 0 && (
        <p className="muted" data-testid="clicks">
          {clicked.length} click{clicked.length > 1 ? 's' : ''} recorded for impression <code>{page?.impression_id.slice(0, 8)}</code>.
        </p>
      )}
    </section>
  );
}

function EvalPage() {
  const [data, setData] = useState<EvalSummary | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    api
      .evalSummary()
      .then(setData)
      .catch((e: Error) => setError(e.message === '404' ? 'No evaluation report yet (run `hss evaluate`).' : `Could not load (${e.message})`));
  }, []);
  if (error) return <p className="error">{error}</p>;
  if (!data?.eval) return <p className="muted">Loading…</p>;
  const rankers = Object.entries(data.eval.rankers);
  return (
    <section>
      <h2>Offline evaluation on {data.eval.queries.toLocaleString()} labelled queries</h2>
      <table data-testid="eval-table">
        <thead>
          <tr>
            <th>ranker</th>
            <th>NDCG@10</th>
            <th>MRR@10</th>
            <th>recall@50 (E+S)</th>
            <th>p50 ms</th>
            <th>p95 ms</th>
            <th>p99 ms</th>
          </tr>
        </thead>
        <tbody>
          {rankers.map(([name, r]) => (
            <tr key={name}>
              <td>{name}</td>
              <td>{r.ndcg10.toFixed(4)}</td>
              <td>{r.mrr10.toFixed(4)}</td>
              <td>{r.recall50_es.toFixed(4)}</td>
              <td>{r.latency_ms.p50}</td>
              <td>{r.latency_ms.p95}</td>
              <td>{r.latency_ms.p99}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data.eval.comparisons && (
        <>
          <h3>Paired differences in NDCG@10</h3>
          <table>
            <thead>
              <tr>
                <th>comparison</th>
                <th>mean delta</th>
                <th>95% CI</th>
                <th>wins / losses</th>
                <th>sign test p</th>
              </tr>
            </thead>
            <tbody>
              {data.eval.comparisons.map((c) => (
                <tr key={c.a + c.b}>
                  <td>
                    {c.b} vs {c.a}
                  </td>
                  <td>
                    {c.mean_delta > 0 ? '+' : ''}
                    {c.mean_delta.toFixed(4)}
                  </td>
                  <td>
                    {c.ci95[0].toFixed(4)} to {c.ci95[1].toFixed(4)}
                  </td>
                  <td>
                    {c.wins} / {c.losses}
                  </td>
                  <td>{c.sign_test_p}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {data.interleaving && (
        <p className="muted">
          Simulated interleaving over {data.interleaving.queries} queries: {data.interleaving.b} won {data.interleaving.wins_b}, {data.interleaving.a}{' '}
          won {data.interleaving.wins_a}; {data.interleaving.verdict}.
        </p>
      )}
      <p className="muted">
        Gains E = 1, S = 0.1, C = 0.01, I = 0; unjudged results count as irrelevant. Latencies are single-query, in-process, on one machine.
      </p>
    </section>
  );
}
