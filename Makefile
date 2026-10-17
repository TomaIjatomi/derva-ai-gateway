# DERVA developer commands. `make help` lists them.
# Tools come from .venv by default; CI installs system-wide and passes BIN=.
BIN ?= .venv/bin/
PY ?= $(BIN)python
PG_URL ?= postgresql+psycopg://postgres:derva-test@localhost/derva_test

.PHONY: help install lock lint format typecheck test test-pg check gate mock demo smoke up down

help:  ## List commands
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install:  ## Create .venv with every extra and the small spaCy model
	uv venv --python 3.12 .venv
	uv pip install --python $(PY) -r requirements.lock -e ".[dev,evals]"
	$(PY) -m spacy download en_core_web_sm

lock:  ## Re-lock the image dependencies
	uv pip compile pyproject.toml --extra gateway --extra presidio --extra postgres --extra gcp --python-version 3.12 -o requirements.lock

lint:  ## Ruff lint and format check
	$(BIN)ruff check src tests scripts
	$(BIN)ruff format --check src tests scripts

format:  ## Apply ruff formatting and fixes
	$(BIN)ruff format src tests scripts
	$(BIN)ruff check --fix src tests scripts

typecheck:  ## mypy, strict
	$(BIN)mypy src

test:  ## Unit and integration tests (Postgres tests skip without a database)
	$(BIN)pytest -q

test-pg:  ## Include the Postgres audit tests
	DERVA_TEST_PG_ADMIN_URL=$(PG_URL) $(BIN)pytest -q

check: lint typecheck test gate  ## Everything CI runs

gate:  ## Offline regression gate: rules decider vs committed baselines
	@for task in golden injection; do \
	  $(BIN)derva eval run $$task --decider rules --split all >/dev/null && \
	  run=$$(ls -td results/runs/*-$$task-rules-* | head -1) && \
	  $(BIN)derva eval gate $$run --baseline evals/baselines/$$task-rules.json || exit 1; \
	done

mock:  ## Mock decision server and demo model on :8100
	$(BIN)derva mock-server

demo:  ## Gateway on :4000 against the mock (run `make mock` first)
	set -a; . ./.env.demo; set +a; $(BIN)litellm --config config/litellm.demo.yaml --port 4000

smoke:  ## End-to-end checks against a running gateway
	$(PY) scripts/smoke_test.py

up:  ## Docker demo: gateway, Postgres, mock
	docker compose up --build --wait

down:
	docker compose down -v
