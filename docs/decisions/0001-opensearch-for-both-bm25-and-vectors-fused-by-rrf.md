# 0001. One OpenSearch index for BM25 and vectors, fused by reciprocal rank fusion

Date: 2026-10-17

## Context

Product search needs lexical matching (model numbers, brand names, exact phrases) and semantic matching
(synonyms, descriptions that do not share the query's words). The brief allows OpenSearch with the k-NN
plugin or pgvector next to OpenSearch.

## Decision

- One OpenSearch 2.19 index holds the text fields (English analyzer with stemming) and an HNSW vector
  field (Lucene engine, cosine) over all-MiniLM-L6-v2 embeddings of title, brand and bullet points.
  Filters (brand, colour) apply to both retrievers; the Lucene engine filters during the graph search
  rather than after it.
- The catalog is static, so the loader force-merges it to one segment: a k-NN query walks every segment's
  HNSW graph, and the bulk load had left seven. One graph explores fewer candidates at the default beam
  (ef_search = k), which cost kNN NDCG@10 0.334 -> 0.326 in the eval; `ef_search` 256 per query gives the
  recall back at the same latency. BM25 and k-NN go to OpenSearch as one `msearch`, so a hybrid query costs
  about the slower of the two.
- The two ranked lists are fused by **reciprocal rank fusion** with k = 60 (score = sum of 1/(k + rank)),
  which needs no score normalisation across BM25 and cosine similarity and is robust to one retriever
  being miscalibrated.
- A cross-encoder (ms-marco-MiniLM-L-6-v2) reranks the fused top 50 as a separate ranker variant, so its
  cost is measured, not assumed.
- Both models run on CPU with PyTorch wheels from the CPU index: the serving container has no GPU, CI has
  none, and the latency numbers must come from the same configuration the service ships with.

## Consequences

- Operationally one store instead of two; pgvector would have added a database for one feature.
- RRF ignores score magnitudes, so a very confident BM25 match and a weak one weigh the same at the same
  rank; the eval decides whether that costs quality against learned fusion (it is the baseline a learned
  model would have to beat).
- The embedding of 200,000 products on CPU is a one-off of several minutes, cached as a `.npy` file and
  uploaded to the VM rather than recomputed there.
