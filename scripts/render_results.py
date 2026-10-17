"""Render reports/*.json into the README between markers.

python scripts/render_results.py --write
python scripts/render_results.py --check
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


def load(name: str) -> dict | None:
    path = REPORTS / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def section(ev: dict | None, il: dict | None, k6s: list[dict], prep: dict | None) -> str:
    lines = []
    if prep:
        lines.append(
            f"- **Catalog and labels:** {prep['catalog_products']:,} US products indexed ({prep['judged_products']:,} judged for the sample, the rest random distractors); "
            f"{prep['eval_queries']:,} evaluation queries with {prep['eval_judgments']:,} human judgments "
            f"(E {prep['label_counts'].get('E', 0):,}, S {prep['label_counts'].get('S', 0):,}, C {prep['label_counts'].get('C', 0):,}, I {prep['label_counts'].get('I', 0):,})."
        )
    if ev:
        r = ev["rankers"]
        lines.append(
            "- **Offline ranking quality** (NDCG@10 with gains E 1 / S 0.1 / C 0.01 / I 0, unjudged = 0; MRR@10 on Exact):"
        )
        lines.append("")
        lines.append("  | ranker | NDCG@10 | 95% CI | MRR@10 | recall@50 (E+S) | p50 ms | p99 ms |")
        lines.append("  |---|---|---|---|---|---|---|")
        for name in ("bm25", "knn", "hybrid", "hybrid_rerank"):
            if name in r:
                x = r[name]
                lines.append(
                    f"  | {name} | **{x['ndcg10']:.4f}** | {x['ndcg10_ci95'][0]:.4f} to {x['ndcg10_ci95'][1]:.4f} | {x['mrr10']:.4f} | {x['recall50_es']:.4f} | {x['latency_ms']['p50']} | {x['latency_ms']['p99']} |"
                )
        lines.append("")
        for c in ev.get("comparisons", []):
            sign = "+" if c["mean_delta"] >= 0 else ""
            lines.append(
                f"  - {c['b']} vs {c['a']}: NDCG@10 {sign}{c['mean_delta']:.4f} (95% CI {c['ci95'][0]:.4f} to {c['ci95'][1]:.4f}), "
                f"{c['wins']} queries better, {c['losses']} worse, {c['ties']} tied; sign test p = {c['sign_test_p']}."
            )
    if il:
        lines.append(
            f"- **Interleaving** ({il['a']} vs {il['b']}, {il['queries']} simulated queries with a click model over the labels): "
            f"{il['b']} won {il['wins_b']}, {il['a']} won {il['wins_a']}, {il['ties']} ties; {il['b']} win rate {il['b_win_rate']:.3f} "
            f"(95% CI {il['b_win_rate_ci95'][0]:.3f} to {il['b_win_rate_ci95'][1]:.3f}), binomial p = {il['binomial_p']}: **{il['verdict']}**."
        )
    if k6s:
        lines.append(
            "- **Load (k6 against the Compose stack on one machine, constant arrival rate: 60 s of uncached hybrid "
            "searches at the offered rate, then 60 s of uncached reranked searches at 2 requests/s, then 30 s of "
            "the same query sample with the cache on):**"
        )
        lines.append("")
        lines.append(
            "  | offered rate | hybrid uncached p50 / p95 / p99 ms | n | with rerank p50 / p99 ms | n | cached p99 ms | failed | p99 < 200 ms |"
        )
        lines.append("  |---|---|---|---|---|---|---|---|")
        met = []
        for k6 in k6s:
            h, rr, c = k6.get("hybrid_uncached_ms"), k6.get("hybrid_rerank_uncached_ms"), k6.get("cached_ms")
            ok = k6.get("thresholds", {}).get("search_hybrid_ms")
            if ok:
                met.append(k6["rate_per_s"])
            lines.append(
                f"  | {k6['rate_per_s']}/s"
                + (f" | {h['med']:.0f} / {h['p95']:.0f} / **{h['p99']:.0f}** | {h['count']:,}" if h else " | n/a | 0")
                + (f" | {rr['med']:.0f} / {rr['p99']:.0f} | {rr['count']:,}" if rr else " | n/a | 0")
                + (f" | {c['p99']:.0f}" if c else " | n/a")
                + f" | {100 * (k6.get('http_req_failed') or 0):.2f}% | {'met' if ok else 'not met' if ok is False else 'n/a'} |"
            )
        lines.append("")
        lines.append(
            f"  The SLO holds up to **{max(met)} requests/s** of uncached hybrid searches on this machine."
            if met
            else "  The SLO did not hold at any tested rate on this machine."
        )
    return "\n".join(lines) or "_No reports yet: run `make eval load`._"


def main() -> int:
    ev, il, prep = load("eval.json"), load("interleaving.json"), load("prepare-stats.json")
    k6s = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(REPORTS.glob("k6-r*.json"), key=lambda p: int(p.stem.split("-r", 1)[1]))
    ]
    if "--check" in sys.argv:
        missing = [
            n for n, v in (("eval.json", ev), ("interleaving.json", il), ("k6-r*.json", k6s or None)) if v is None
        ]
        print("missing: " + ", ".join(missing) if missing else "reports present")
        return 1 if missing else 0
    text = section(ev, il, k6s, prep)
    if "--write" in sys.argv:
        readme = ROOT / "README.md"
        content = readme.read_text(encoding="utf-8")
        start, end = "<!-- RESULTS -->", "<!-- /RESULTS -->"
        if start in content and end in content:
            before, rest = content.split(start, 1)
            _, after = rest.split(end, 1)
            readme.write_text(before + start + "\n" + text + "\n" + end + after, encoding="utf-8")
            print("wrote README.md")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
