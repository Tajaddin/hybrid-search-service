# 0002. Offline evaluation on ESCI labels with graded gains, and what it does not measure

Date: 2026-10-17

## Context

The brief asks for NDCG@10 and MRR on a labelled query set. The Amazon Shopping Queries Dataset labels
each query-product pair Exact, Substitute, Complement or Irrelevant; it is the public dataset with human
relevance judgments for product search.

## Decision

- Gains follow the ESCI paper: E = 1.0, S = 0.1, C = 0.01, I = 0. NDCG@10 against the ideal ordering of
  the query's own judgments; MRR@10 counts only Exact matches.
- Products the ranker returns that were never judged for that query count as irrelevant. That is the
  standard, conservative choice; it penalises rankers that surface good unjudged products, and it is
  stated wherever the numbers are shown.
- The evaluation sample is 2,000 test-split queries chosen once with a fixed seed; every judgment of those
  queries is kept. The catalog contains every judged product plus random distractors to 200,000, so the
  task is retrieval from a realistic catalog, not re-ranking a judged list.
- Variants: BM25, kNN, hybrid (RRF), hybrid with rerank. Besides the means, the report carries a 95%
  interval on NDCG@10 and paired comparisons (mean difference with interval, wins, losses, sign test),
  because two means without the pairing say little.
- Latency per query is recorded in the same run but is an in-process measurement; the load numbers come
  from k6 against the running service.

## Consequences

- Query understanding is evaluated as a separate switch (`--apply-filters`), since a wrong brand filter
  hurts recall and the labelled set did not come with filters.
- The numbers are for one subset of one dataset; they rank the variants against each other and are not
  comparable to leaderboard figures computed on the full test split with other catalogs.
