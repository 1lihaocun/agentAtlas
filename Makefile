export TMPDIR := $(CURDIR)/.agentatlas/work
export UV_CACHE_DIR := $(CURDIR)/.agentatlas/work/uv-cache
export npm_config_cache := $(CURDIR)/.agentatlas/work/npm-cache

.PHONY: prepare install build serve dev check test browser-test

prepare:
	mkdir -p .agentatlas/work

install: prepare
	uv sync --project backend --locked
	npm --prefix frontend ci

build: prepare
	npm --prefix frontend run build
	uv build --project backend --out-dir backend/dist

serve: prepare
	backend/.venv/bin/atlas --workspace "$(CURDIR)" serve

dev: prepare
	node scripts/dev.mjs

check: prepare
	backend/.venv/bin/ruff check backend/src
	npm --prefix frontend run lint
	npm --prefix frontend run typecheck

test: prepare
	backend/.venv/bin/pytest backend/tests

browser-test: prepare
	npm --prefix frontend run build
	npm --prefix frontend test
