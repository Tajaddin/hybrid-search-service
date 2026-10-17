#!/usr/bin/env bash
# Amazon Shopping Queries Dataset (ESCI), Apache 2.0, github.com/amazon-science/esci-data. Two parquet files
# served through Git LFS; pinned by sha256. About 1.1 GB.
set -euo pipefail
cd "$(dirname "$0")/.."
BASE=https://github.com/amazon-science/esci-data/raw/main/shopping_queries_dataset
mkdir -p data/raw
declare -A SHA=(
  [shopping_queries_dataset_examples.parquet]=4a735b693b4a424a6fc67f5be6e4c811495c488bbf66d02a602d308b2744263a
  [shopping_queries_dataset_products.parquet]=25124442d064d64b26f74082d6fa09438d679efc0c183cf28d19064a2b65a265
)
for f in "${!SHA[@]}"; do
  if [ ! -f "data/raw/$f" ]; then
    echo "downloading $f"
    curl -sSL -o "data/raw/$f" "$BASE/$f"
  fi
  echo "${SHA[$f]}  data/raw/$f" | sha256sum -c - >/dev/null && echo "ok $f"
done
