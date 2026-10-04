# Rare Disease Atlas: JAK-STAT MVP
#
#   make data    rebuild data/build/graph.json (uses data/cache/ when present)
#   make test    contract + ground-truth + unit tests (tests/) and the API tests (api/tests), offline
#   make dev     API on :8000 and, if web/package.json exists, the Next.js dev server
#   make schema  regenerate docs/graph.schema.json from pipeline/models.py
#   make search-index  embed searchable nodes for natural-language /search (run after make data)

.PHONY: help data test dev dev-api dev-web schema search-index

UV ?= uv
API_PORT ?= 8000

help:
	@sed -n 's/^#   //p' Makefile

# pipeline/export.py is written in M1; until then this target fails with "No module named pipeline.export".
data:
	$(UV) run python -m pipeline.export

# Needs OPENAI_EMBED_MODEL and an OpenAI or OpenRouter key; embeddings are cached, so reruns are cheap.
search-index:
	$(UV) run python -m pipeline.search_index

# ATLAS_OFFLINE=1: every test replays caches and fixtures; nothing hits a live service. Tests that
# need one are marked `network` (pyproject.toml) and run only with ATLAS_NETWORK_TESTS=1.
test:
	ATLAS_OFFLINE=1 $(UV) run pytest -q tests api/tests

schema:
	$(UV) run python -m pipeline.schema

# `make -j2` runs the two dev servers side by side; Ctrl-C stops both.
dev:
	$(MAKE) -j2 dev-api dev-web

dev-api:
	$(UV) run uvicorn api.main:app --reload --port $(API_PORT)

dev-web:
	@if [ -f web/package.json ]; then \
		cd web && npm run dev; \
	else \
		echo "dev-web: web/package.json not found, skipping the web dev server"; \
	fi
