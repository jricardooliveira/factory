# The factory's own feedback loop.
#
# `make check` is THE single verification command (AI-Native SDLC playbook, Stage 4:
# "wrap verification in a single command, list it in CLAUDE.md, state a quantifiable
# target"). Everything it runs is offline and costs zero tokens, so an agent — or a
# CI job, or you — can verify work before a human ever looks at it.
#
# Quantifiable target: tests 195/195 green, scenarios 8/8 as expected, evals 100% pass.

PY := .venv/bin/python
FACTORY := .venv/bin/factory

.DEFAULT_GOAL := check
.PHONY: check test lint sim evals metrics clean help

## check: lint + unit tests + scenario matrix + agent-config evals. Run before claiming done.
check: lint test sim evals
	@echo ""
	@echo "✅ make check passed — tests, scenarios and agent-config evals are all green."

## test: the factory's own unit + orchestration suite (offline, no opencode calls).
test:
	@echo "── pytest ──────────────────────────────────────────"
	@$(PY) -m pytest -q

## lint: ruff, if installed. Configured in pyproject.toml but optional in this venv.
lint:
	@echo "── ruff ────────────────────────────────────────────"
	@if [ -x "$(PY)" ] && $(PY) -c "import ruff" 2>/dev/null; then \
		$(PY) -m ruff check . && $(PY) -m ruff format --check . ; \
	elif command -v ruff >/dev/null 2>&1; then \
		ruff check . && ruff format --check . ; \
	else \
		echo "   (skipped: ruff not installed — 'uv add --dev ruff' to enable)" ; \
	fi

## sim: offline scenario matrix — 8 representative stories through the real pipeline.
sim:
	@echo "── factory simulate ───────────────────────────────"
	@$(FACTORY) simulate

## evals: regression-test the AGENT CONFIGURATION (agent .md files, gates, tiers, prompts).
evals:
	@echo "── factory evals ──────────────────────────────────"
	@$(FACTORY) evals

## metrics: the playbook's leading/lagging SDLC indicators, computed from factory state.
metrics:
	@$(FACTORY) metrics

## clean: drop caches and generated reports (never touches SQLite state or projects/).
clean:
	@find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@rm -rf .pytest_cache .ruff_cache
	@echo "cleaned"

## help: list targets.
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/^## /  /'
