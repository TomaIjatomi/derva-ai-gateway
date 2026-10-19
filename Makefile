# DERVA developer commands. `make help` lists them. Everything runs through uv,
# so the environment always matches uv.lock. On Windows without make, run the
# commands after the `##` descriptions directly (see the review guide).
PG_URL ?= postgresql+psycopg://postgres:derva-test@localhost/derva_test

.PHONY: help install lock lint format typecheck test test-pg check gate mock demo smoke up down

help:  ## List commands
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install:  ## Create .venv from uv.lock with every extra and the dev tools
	uv sync --frozen --all-extras

lock:  ## Re-resolve uv.lock after changing dependencies in pyproject.toml
	uv lock

lint:  ## Ruff lint and format check
	uv run ruff check src tests scripts
	uv run ruff format --check src tests scripts

format:  ## Apply ruff formatting and fixes
	uv run ruff format src tests scripts
	uv run ruff check --fix src tests scripts

typecheck:  ## mypy, strict
	uv run mypy src

test:  ## Unit and integration tests (Postgres tests skip without a database)
	uv run pytest -q

test-pg:  ## Include the Postgres audit tests
	DERVA_TEST_PG_ADMIN_URL=$(PG_URL) uv run pytest -q

check: lint typecheck test gate  ## Everything CI runs

gate:  ## Offline regression gate: rules decider vs committed baselines
	@for task in golden injection; do \
	  uv run derva eval run $$task --decider rules --split all >/dev/null && \
	  run=$$(ls -td results/runs/*-$$task-rules-* | head -1) && \
	  uv run derva eval gate $$run --baseline evals/baselines/$$task-rules.json || exit 1; \
	done

mock:  ## Mock decision server and demo model on :8100
	uv run derva mock-server

demo:  ## Gateway on :4000 against the mock (run `make mock` first)
	set -a; . ./.env.demo; set +a; uv run litellm --config config/litellm.demo.yaml --port 4000

smoke:  ## End-to-end checks against a running gateway
	uv run python scripts/smoke_test.py

up:  ## Docker demo: gateway, Postgres, mock
	docker compose up --build --wait

down:
	docker compose down -v
