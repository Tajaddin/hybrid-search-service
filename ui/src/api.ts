export type Ranker = 'bm25' | 'knn' | 'hybrid' | 'hybrid_rerank';
export const RANKERS: Ranker[] = ['bm25', 'knn', 'hybrid', 'hybrid_rerank'];

export interface Hit {
  product_id: string;
  title: string;
  brand: string | null;
  color: string | null;
  score: number;
  bm25_rank: number | null;
  knn_rank: number | null;
  rerank_score: number | null;
}

export interface SearchResponse {
  query: { original: string; corrected: string; corrections: { from: string; to: string }[]; brand: string | null; color: string | null };
  ranker: string;
  filters: Record<string, string>;
  took_ms: Record<string, number>;
  hits: Hit[];
  cached: boolean;
}

export interface InterleaveResponse {
  impression_id: string;
  query: string;
  items: Hit[];
  took_ms: Record<string, number | null>;
}

export interface EvalSummary {
  eval: {
    queries: number;
    rankers: Record<string, { ndcg10: number; mrr10: number; recall50_es: number; latency_ms: { p50: number; p95: number; p99: number } }>;
    comparisons?: { a: string; b: string; mean_delta: number; ci95: number[]; wins: number; losses: number; sign_test_p: number }[];
  } | null;
  interleaving: { a: string; b: string; queries: number; wins_a: number; wins_b: number; b_win_rate: number | null; verdict: string } | null;
  load: Record<string, unknown> | null;
}

declare global {
  interface Window {
    __HSS_API_BASE__?: string;
  }
}

const base = (window.__HSS_API_BASE__ ?? '/api').replace(/\/$/, '');

async function get<T>(path: string, params: Record<string, string | number | boolean | undefined>): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '') qs.set(k, String(v));
  const res = await fetch(`${base}${path}?${qs}`);
  if (!res.ok) throw new Error(`${res.status}`);
  return (await res.json()) as T;
}

export const api = {
  search: (q: string, ranker: Ranker, brand?: string, color?: string, autoFilters = true) =>
    get<SearchResponse>('/search', { q, ranker, size: 20, brand, color, auto_filters: autoFilters }),
  suggest: (q: string) => get<{ corrected: string | null; completions: string[] }>('/suggest', { q }),
  interleave: (q: string, a: Ranker, b: Ranker) => get<InterleaveResponse>('/interleave', { q, a, b, size: 10 }),
  click: async (impression_id: string, position: number) => {
    await fetch(`${base}/click`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ impression_id, position }),
    });
  },
  evalSummary: () => get<EvalSummary>('/eval/summary', {}),
  health: () => get<{ status: string; index: { documents: number; status: string }; cache: string }>('/health', {}),
};
