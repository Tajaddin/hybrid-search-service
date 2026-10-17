.PHONY: install data prepare index eval interleave load serve up up-obs down lint format typecheck test test-integration alerts-test images tf-validate ui-dev e2e report deploy teardown

install:
	uv sync --extra dev
	cd ui && npm ci --no-audit --no-fund

data:           ## download the ESCI parquet files (1.1 GB, sha256 pinned)
	bash scripts/download_data.sh

prepare:        ## 2,000 evaluation queries and a 200,000-product catalog from the ESCI files
	uv run hss prepare
	uv run python scripts/export_load_queries.py

infra:          ## OpenSearch (:9201) and Redis (:6381)
	docker compose up -d opensearch redis

index:          ## embed the catalog on CPU (cached) and bulk load the index
	uv run hss index

eval:           ## NDCG@10 and MRR@10 per ranker on the labelled sample -> reports/eval.json
	uv run hss evaluate

interleave:     ## simulated team-draft interleaving -> reports/interleaving.json
	uv run hss interleave-sim --queries 1000

load:           ## k6 at 10, 25 and 50 requests/s against the compose API -> reports/k6-r*.json (needs make up)
	mkdir -p reports/k6-raw
	for r in 10 25 50; do RATE=$$r docker compose --profile load run --rm k6 || true; done
	cp reports/k6-raw/k6-r*.json reports/

serve:
	uv run hss serve --port 8120

up:             ## OpenSearch, Redis, API (:8120) and UI (:4310) from local images
	docker compose up -d --build opensearch redis api ui

up-obs:
	docker compose --profile observability up -d

down:
	docker compose --profile observability --profile load --profile jobs down -v

lint:
	uv run ruff check . && uv run ruff format --check .
	cd ui && npm run lint

format:
	uv run ruff format . && uv run ruff check --fix .
	cd ui && npm run format

typecheck:
	uv run mypy

test:
	uv run pytest -m "not integration" --cov=hss --cov-report=term-missing --cov-report=xml

test-integration: ## needs make infra and a built index (or builds a small one)
	uv run pytest -m integration -q

alerts-test:
	docker run --rm -v "$(CURDIR)/observability:/w" -w /w --entrypoint promtool prom/prometheus:v3.5.0 test rules alerts_test.yml

images:
	docker build -t hss/api:local .
	docker build -t hss/ui:local ui

tf-validate:
	docker run --rm -v "$(CURDIR):/w" -w /w/infra/terraform hashicorp/terraform:1.9 fmt -check
	docker run --rm -v "$(CURDIR):/w" -w /w/infra/terraform hashicorp/terraform:1.9 init -backend=false -input=false >/dev/null && docker run --rm -v "$(CURDIR):/w" -w /w/infra/terraform hashicorp/terraform:1.9 validate

ui-dev:         ## Vite dev server on :5173 proxying /api to :8120
	cd ui && npm run dev

e2e:            ## Playwright against the UI dev server and an API on :8120
	cd ui && npx playwright test

report:
	uv run python scripts/render_results.py --write

deploy:         ## Oracle Cloud free-tier VM via Terraform (docs/DEPLOY.md)
	bash scripts/deploy.sh

teardown:
	bash scripts/teardown.sh
