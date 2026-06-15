# Software Factory v1 — Plan 1: Storage + Schema Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the storage and schema foundation the rest of the factory depends on: DuckDB schemas (factory + per-project), Pydantic models for handoffs/stories/tasks, ID and path utilities, atomic file I/O, story/task/pipeline artifact writers, and DB↔filesystem reconciliation. No LLM calls, no orchestration logic — pure data layer.

**Architecture:** A single Python package `factory` with submodules for IDs, paths, file I/O, models, state (DuckDB access), and artifact writers. All higher-level code (opencode client, LangGraph orchestrator, CLI) imports this package and consumes its public API. No network. No subprocess. No threads. Tests are fast and deterministic.

**Tech Stack:** Python 3.13, uv, DuckDB, Pydantic v2, python-frontmatter, pytest, pytest-cov, ruff. JSON Schema generation comes for free via `Pydantic.model_json_schema()`.

**Plan position:** This is plan 1 of 4 for v1. Plans 2–4 will add the opencode integration and agent role files, the LangGraph orchestrator + CLI + sample project, and stage-1 hardening (interrupts, retries, budget caps).

**Source design:** `docs/superpowers/specs/2026-04-27-software-factory-design.md`. Where this plan deviates from the design, it is documented inline and the design is updated in Task 0.

---

## File Structure

Files this plan creates or modifies:

| Path | Purpose |
|---|---|
| `orchestrator/pyproject.toml` | Package definition, deps, build config |
| `orchestrator/.python-version` | Pin Python 3.13 |
| `orchestrator/ruff.toml` | Lint + format config |
| `orchestrator/src/factory/__init__.py` | Public API re-exports |
| `orchestrator/src/factory/ids.py` | ID generation (`US-0001`, `T-0001`, `REQ-0001`, `PROJ-001`, etc.) |
| `orchestrator/src/factory/paths.py` | Path constructors for all factory artifacts |
| `orchestrator/src/factory/io.py` | Atomic file writes (write-and-rename) |
| `orchestrator/src/factory/models/__init__.py` | Empty marker |
| `orchestrator/src/factory/models/handoff.py` | Pydantic Handoff model + verdict enum |
| `orchestrator/src/factory/models/story.py` | Pydantic Story model + frontmatter round-trip |
| `orchestrator/src/factory/models/task.py` | Pydantic Task model + frontmatter round-trip |
| `orchestrator/src/factory/models/project_rules.py` | Pydantic ProjectRules model (port_range, db_engine, etc.) |
| `orchestrator/src/factory/state/__init__.py` | Empty marker |
| `orchestrator/src/factory/state/schemas.py` | Embedded SQL DDL constants |
| `orchestrator/src/factory/state/connection.py` | DuckDB context managers (factory + project) |
| `orchestrator/src/factory/state/factory_db.py` | Factory DB queries |
| `orchestrator/src/factory/state/project_db.py` | Project DB queries |
| `orchestrator/src/factory/state/reconcile.py` | DB↔FS consistency check |
| `orchestrator/src/factory/artifacts.py` | Story/task/handoff/summary file writers |
| `orchestrator/src/factory/templates/task.md` | Task markdown template |
| `orchestrator/scripts/gen_handoff_schema.py` | One-shot script to dump JSON schema |
| `orchestrator/tests/...` | One test module per source module |
| `state/factory.schema.sql` | Source-of-truth for factory DB DDL (generated from `schemas.py`) |
| `state/project.schema.sql` | Source-of-truth for project DB DDL (same) |
| `handoffs/handoff.schema.json` | Generated from `models/handoff.py` |
| `handoffs/examples/example-handoff.json` | Hand-written example, validated in test |
| `stories/.template.md` | Story markdown template |
| `stories/index.md` | Hand-written index header (auto-generated body comes later) |
| `docs/superpowers/specs/2026-04-27-software-factory-design.md` | Patched in Task 0 to fix the per-project stories inconsistency |

---

## Task 0: Patch design and bootstrap orchestrator package

**Files:**
- Modify: `docs/superpowers/specs/2026-04-27-software-factory-design.md`
- Create: `orchestrator/pyproject.toml`
- Create: `orchestrator/.python-version`
- Create: `orchestrator/ruff.toml`
- Create: `orchestrator/src/factory/__init__.py`
- Create: `orchestrator/tests/__init__.py`
- Create: `orchestrator/tests/test_smoke.py`

- [ ] **Step 1: Patch the design doc to remove the stories-at-project-level inconsistency**

In `docs/superpowers/specs/2026-04-27-software-factory-design.md`, in §3 (Repository layout), remove the per-project `docs/work/stories/` entry. The block currently reads:

```
        ├── docs/
        │   ├── work/
        │   │   ├── stories/
        │   │   └── tasks/
```

Change it to:

```
        ├── docs/
        │   ├── work/
        │   │   └── tasks/                  # project-scoped task files
```

Also remove the top-level `tasks/` block from §3 (factory-level tasks have no purpose; tasks are always project-scoped). The block currently reads:

```
├── tasks/
│   ├── .template.md
│   └── index.md
```

Delete those four lines. Add a one-line note under §8.3 Versioning:

```
> **Layout note:** Stories live exclusively at the factory level
> (`/stories/US-XXXX/`). Tasks live exclusively at the project level
> (`/projects/<id>/docs/work/tasks/T-XXXX.md`). Task templates ship
> as code (`orchestrator/src/factory/templates/task.md`) and are not
> a top-level directory.
```

- [ ] **Step 2: Create the orchestrator directory and pin Python**

```bash
mkdir -p /Users/joaooliveira/dev/personal/factory/orchestrator/src/factory
mkdir -p /Users/joaooliveira/dev/personal/factory/orchestrator/tests
echo "3.13" > /Users/joaooliveira/dev/personal/factory/orchestrator/.python-version
```

- [ ] **Step 3: Write `orchestrator/pyproject.toml`**

```toml
[project]
name = "factory"
version = "0.1.0"
description = "Software factory orchestrator (storage layer)"
requires-python = ">=3.13,<3.14"
dependencies = [
    "duckdb>=1.1.0",
    "pydantic>=2.9.0",
    "python-frontmatter>=1.1.0",
    "click>=8.1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "pytest-cov>=5.0.0",
    "ruff>=0.6.0",
]

[project.scripts]
factory-gen-handoff-schema = "factory.scripts.gen_handoff_schema:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/factory"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers --strict-config"
markers = [
    "integration: tests that require a real opencode server (opt-in via -m integration)",
    "real_llm: tests that consume real LLM tokens (opt-in)",
]
```

- [ ] **Step 4: Write `orchestrator/ruff.toml`**

```toml
target-version = "py313"
line-length = 100

[lint]
select = [
    "E",      # pycodestyle errors
    "F",      # pyflakes
    "W",      # pycodestyle warnings
    "I",      # isort
    "N",      # pep8-naming
    "UP",     # pyupgrade
    "B",      # flake8-bugbear
    "SIM",    # flake8-simplify
    "RUF",    # ruff-specific
]
ignore = [
    "E501",   # line too long (formatter handles it)
]

[format]
quote-style = "double"
indent-style = "space"
```

- [ ] **Step 5: Write `orchestrator/src/factory/__init__.py`**

```python
"""Software factory orchestrator (storage layer).

Public API: see specific submodules. This module is intentionally empty
of code and reserved for re-exports added in later tasks.
"""
__version__ = "0.1.0"
```

- [ ] **Step 6: Write `orchestrator/tests/__init__.py`** (empty file)

```python
```

- [ ] **Step 7: Write a sanity test at `orchestrator/tests/test_smoke.py`**

```python
def test_factory_package_imports():
    import factory
    assert factory.__version__ == "0.1.0"
```

- [ ] **Step 8: Install and verify**

```bash
cd /Users/joaooliveira/dev/personal/factory/orchestrator
uv sync --all-extras
uv run pytest -v
```

Expected output: `tests/test_smoke.py::test_factory_package_imports PASSED` and 1 passed.

- [ ] **Step 9: Verify ruff is happy**

```bash
cd /Users/joaooliveira/dev/personal/factory/orchestrator
uv run ruff check .
uv run ruff format --check .
```

Expected: both exit 0 with "All checks passed!" (and "X files already formatted").

- [ ] **Step 10: Commit**

```bash
cd /Users/joaooliveira/dev/personal/factory
git add orchestrator/ docs/superpowers/specs/2026-04-27-software-factory-design.md
git commit -m "$(cat <<'EOF'
feat(orchestrator): bootstrap Python package + patch design layout

- Pin Python 3.13 via .python-version
- pyproject.toml with duckdb, pydantic, python-frontmatter, click
- Dev deps: pytest, pytest-cov, ruff
- ruff config for py313, line-length 100
- Smoke test: factory package imports

Design fixes:
- Remove per-project docs/work/stories/ (stories are factory-level)
- Remove top-level tasks/ (tasks are project-level)
- Add layout note under §8.3 to clarify story/task scopes
EOF
)"
```

---

## Task 1: ID generation

**Files:**
- Create: `orchestrator/src/factory/ids.py`
- Create: `orchestrator/tests/test_ids.py`

ID prefixes and widths come from the design and input spec:

| Entity | Prefix | Width | Example |
|---|---|---|---|
| Project | `PROJ` | 3 | `PROJ-001` |
| Story | `US` | 4 | `US-0001` |
| Task | `T` | 4 | `T-0001` |
| Request | `REQ` | 4 | `REQ-0001` |
| ADR | `ADR` | 4 | `ADR-0001` |
| Release | `REL` | 4 | `REL-0001` |
| Blocker | `BLK` | 4 | `BLK-0001` |
| Agent run | `AR` | 6 | `AR-000001` |

- [ ] **Step 1: Write failing tests**

Create `orchestrator/tests/test_ids.py`:

```python
import pytest

from factory.ids import IdKind, format_id, parse_id


class TestFormatId:
    def test_project_id_pads_to_3_digits(self):
        assert format_id(IdKind.PROJECT, 1) == "PROJ-001"

    def test_project_id_pads_to_3_digits_two_digit_input(self):
        assert format_id(IdKind.PROJECT, 42) == "PROJ-042"

    def test_story_id_pads_to_4_digits(self):
        assert format_id(IdKind.STORY, 1) == "US-0001"

    def test_task_id_uses_T_prefix(self):
        assert format_id(IdKind.TASK, 7) == "T-0007"

    def test_request_id(self):
        assert format_id(IdKind.REQUEST, 3) == "REQ-0003"

    def test_adr_id(self):
        assert format_id(IdKind.ADR, 12) == "ADR-0012"

    def test_release_id(self):
        assert format_id(IdKind.RELEASE, 1) == "REL-0001"

    def test_blocker_id(self):
        assert format_id(IdKind.BLOCKER, 99) == "BLK-0099"

    def test_agent_run_id_uses_6_digits(self):
        assert format_id(IdKind.AGENT_RUN, 1) == "AR-000001"

    def test_zero_is_rejected(self):
        with pytest.raises(ValueError, match="must be >= 1"):
            format_id(IdKind.STORY, 0)

    def test_negative_is_rejected(self):
        with pytest.raises(ValueError, match="must be >= 1"):
            format_id(IdKind.STORY, -1)

    def test_overflow_for_3_digit_kind(self):
        with pytest.raises(ValueError, match="too large"):
            format_id(IdKind.PROJECT, 1000)

    def test_overflow_for_4_digit_kind(self):
        with pytest.raises(ValueError, match="too large"):
            format_id(IdKind.STORY, 10000)


class TestParseId:
    def test_parse_project(self):
        assert parse_id("PROJ-001") == (IdKind.PROJECT, 1)

    def test_parse_story(self):
        assert parse_id("US-0042") == (IdKind.STORY, 42)

    def test_parse_task(self):
        assert parse_id("T-0007") == (IdKind.TASK, 7)

    def test_parse_agent_run(self):
        assert parse_id("AR-000123") == (IdKind.AGENT_RUN, 123)

    def test_parse_unknown_prefix_raises(self):
        with pytest.raises(ValueError, match="unknown prefix"):
            parse_id("XYZ-0001")

    def test_parse_malformed_raises(self):
        with pytest.raises(ValueError, match="malformed"):
            parse_id("US0001")

    def test_parse_non_numeric_suffix_raises(self):
        with pytest.raises(ValueError, match="malformed"):
            parse_id("US-abcd")

    def test_round_trip(self):
        for kind in IdKind:
            n = 5
            assert parse_id(format_id(kind, n)) == (kind, n)
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /Users/joaooliveira/dev/personal/factory/orchestrator
uv run pytest tests/test_ids.py -v
```

Expected: ImportError on `factory.ids` — all tests error.

- [ ] **Step 3: Implement `orchestrator/src/factory/ids.py`**

```python
"""Identifier generation and parsing for factory entities.

IDs are deterministic strings of the form ``PREFIX-NNNN`` (or
``PREFIX-NNN`` for projects, ``PREFIX-NNNNNN`` for agent runs).
"""

from __future__ import annotations

from enum import Enum


class IdKind(Enum):
    """Entity kinds that have factory-issued IDs."""

    PROJECT = ("PROJ", 3)
    STORY = ("US", 4)
    TASK = ("T", 4)
    REQUEST = ("REQ", 4)
    ADR = ("ADR", 4)
    RELEASE = ("REL", 4)
    BLOCKER = ("BLK", 4)
    AGENT_RUN = ("AR", 6)

    @property
    def prefix(self) -> str:
        return self.value[0]

    @property
    def width(self) -> int:
        return self.value[1]


_PREFIX_TO_KIND = {kind.prefix: kind for kind in IdKind}


def format_id(kind: IdKind, n: int) -> str:
    """Format an integer as a padded factory ID for the given kind."""
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    if n >= 10**kind.width:
        raise ValueError(f"n={n} too large for {kind.name} (max width {kind.width})")
    return f"{kind.prefix}-{n:0{kind.width}d}"


def parse_id(value: str) -> tuple[IdKind, int]:
    """Parse a factory ID back into (kind, integer)."""
    if "-" not in value:
        raise ValueError(f"malformed id: {value!r}")
    prefix, _, suffix = value.partition("-")
    kind = _PREFIX_TO_KIND.get(prefix)
    if kind is None:
        raise ValueError(f"unknown prefix in {value!r}")
    if not suffix.isdigit() or len(suffix) != kind.width:
        raise ValueError(f"malformed id: {value!r}")
    return kind, int(suffix)
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
cd /Users/joaooliveira/dev/personal/factory/orchestrator
uv run pytest tests/test_ids.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run ruff**

```bash
cd /Users/joaooliveira/dev/personal/factory/orchestrator
uv run ruff check .
uv run ruff format --check .
```

Expected: both exit 0.

- [ ] **Step 6: Commit**

```bash
cd /Users/joaooliveira/dev/personal/factory
git add orchestrator/src/factory/ids.py orchestrator/tests/test_ids.py
git commit -m "feat(ids): add IdKind enum and format/parse helpers"
```

---

## Task 2: Path helpers

**Files:**
- Create: `orchestrator/src/factory/paths.py`
- Create: `orchestrator/tests/test_paths.py`

Paths are computed from a `factory_root` (the repo root, e.g., `/Users/joaooliveira/dev/personal/factory`) and entity IDs. We pass `factory_root` explicitly so tests can use a temporary directory.

- [ ] **Step 1: Write failing tests**

Create `orchestrator/tests/test_paths.py`:

```python
from pathlib import Path

from factory.paths import (
    factory_state_db_path,
    factory_state_log_path,
    handoff_path,
    pipeline_dir,
    project_dir,
    project_pipeline_dir,
    project_repo_dir,
    project_secrets_dir,
    project_state_db_path,
    project_task_file_path,
    story_current_symlink,
    story_dir,
    story_version_path,
    summary_path,
)


def test_factory_state_db_path(tmp_path: Path):
    assert factory_state_db_path(tmp_path) == tmp_path / "state" / "factory.duckdb"


def test_factory_state_log_path(tmp_path: Path):
    assert factory_state_log_path(tmp_path) == tmp_path / "state" / "factory.log.jsonl"


def test_story_dir(tmp_path: Path):
    assert story_dir(tmp_path, "US-0001") == tmp_path / "stories" / "US-0001"


def test_story_version_path(tmp_path: Path):
    assert (
        story_version_path(tmp_path, "US-0001", 2)
        == tmp_path / "stories" / "US-0001" / "US-0001.v2.md"
    )


def test_story_current_symlink(tmp_path: Path):
    assert (
        story_current_symlink(tmp_path, "US-0001")
        == tmp_path / "stories" / "US-0001" / "current"
    )


def test_project_dir(tmp_path: Path):
    assert project_dir(tmp_path, "PROJ-001") == tmp_path / "projects" / "PROJ-001"


def test_project_dir_with_suffix(tmp_path: Path):
    assert (
        project_dir(tmp_path, "PROJ-001", suffix="example")
        == tmp_path / "projects" / "PROJ-001-example"
    )


def test_project_state_db_path(tmp_path: Path):
    assert (
        project_state_db_path(tmp_path, "PROJ-001-example")
        == tmp_path / "projects" / "PROJ-001-example" / "state" / "project.duckdb"
    )


def test_project_repo_dir(tmp_path: Path):
    assert (
        project_repo_dir(tmp_path, "PROJ-001-example")
        == tmp_path / "projects" / "PROJ-001-example" / "repo"
    )


def test_project_secrets_dir(tmp_path: Path):
    assert (
        project_secrets_dir(tmp_path, "PROJ-001-example")
        == tmp_path / "projects" / "PROJ-001-example" / ".secrets"
    )


def test_project_task_file_path(tmp_path: Path):
    assert (
        project_task_file_path(tmp_path, "PROJ-001-example", "T-0001")
        == tmp_path / "projects" / "PROJ-001-example" / "docs" / "work" / "tasks" / "T-0001.md"
    )


def test_project_pipeline_dir(tmp_path: Path):
    assert (
        project_pipeline_dir(tmp_path, "PROJ-001-example", "T-0001")
        == tmp_path / "projects" / "PROJ-001-example" / "docs" / "pipeline" / "T-0001"
    )


def test_pipeline_dir_alias(tmp_path: Path):
    # pipeline_dir is the public alias used by callers that don't care about the project layout
    assert pipeline_dir(tmp_path, "PROJ-001-example", "T-0001") == project_pipeline_dir(
        tmp_path, "PROJ-001-example", "T-0001"
    )


def test_handoff_path(tmp_path: Path):
    assert (
        handoff_path(tmp_path, "PROJ-001-example", "T-0001", "coder-agent")
        == tmp_path
        / "projects"
        / "PROJ-001-example"
        / "docs"
        / "pipeline"
        / "T-0001"
        / "coder-agent.handoff.json"
    )


def test_summary_path(tmp_path: Path):
    assert (
        summary_path(tmp_path, "PROJ-001-example", "T-0001", "coder-agent")
        == tmp_path
        / "projects"
        / "PROJ-001-example"
        / "docs"
        / "pipeline"
        / "T-0001"
        / "coder-agent.summary.md"
    )
```

- [ ] **Step 2: Run tests, verify they fail with ImportError**

```bash
cd /Users/joaooliveira/dev/personal/factory/orchestrator
uv run pytest tests/test_paths.py -v
```

Expected: ImportError on `factory.paths`.

- [ ] **Step 3: Implement `orchestrator/src/factory/paths.py`**

```python
"""Filesystem paths for factory artifacts.

All paths are computed from a ``factory_root`` (the repo root) and
entity identifiers. Functions never touch the filesystem — they only
compute paths.
"""

from __future__ import annotations

from pathlib import Path


def factory_state_db_path(factory_root: Path) -> Path:
    return factory_root / "state" / "factory.duckdb"


def factory_state_log_path(factory_root: Path) -> Path:
    return factory_root / "state" / "factory.log.jsonl"


def story_dir(factory_root: Path, story_id: str) -> Path:
    return factory_root / "stories" / story_id


def story_version_path(factory_root: Path, story_id: str, version: int) -> Path:
    return story_dir(factory_root, story_id) / f"{story_id}.v{version}.md"


def story_current_symlink(factory_root: Path, story_id: str) -> Path:
    return story_dir(factory_root, story_id) / "current"


def project_dir(factory_root: Path, project_id: str, *, suffix: str | None = None) -> Path:
    name = project_id if suffix is None else f"{project_id}-{suffix}"
    return factory_root / "projects" / name


def project_state_db_path(factory_root: Path, project_dir_name: str) -> Path:
    return factory_root / "projects" / project_dir_name / "state" / "project.duckdb"


def project_repo_dir(factory_root: Path, project_dir_name: str) -> Path:
    return factory_root / "projects" / project_dir_name / "repo"


def project_secrets_dir(factory_root: Path, project_dir_name: str) -> Path:
    return factory_root / "projects" / project_dir_name / ".secrets"


def project_task_file_path(
    factory_root: Path, project_dir_name: str, task_id: str
) -> Path:
    return (
        factory_root
        / "projects"
        / project_dir_name
        / "docs"
        / "work"
        / "tasks"
        / f"{task_id}.md"
    )


def project_pipeline_dir(
    factory_root: Path, project_dir_name: str, task_id: str
) -> Path:
    return (
        factory_root / "projects" / project_dir_name / "docs" / "pipeline" / task_id
    )


# Public alias preferred by callers
pipeline_dir = project_pipeline_dir


def handoff_path(
    factory_root: Path, project_dir_name: str, task_id: str, stage: str
) -> Path:
    return pipeline_dir(factory_root, project_dir_name, task_id) / f"{stage}.handoff.json"


def summary_path(
    factory_root: Path, project_dir_name: str, task_id: str, stage: str
) -> Path:
    return pipeline_dir(factory_root, project_dir_name, task_id) / f"{stage}.summary.md"
```

> **Note on `project_dir_name`:** Callers pass the on-disk directory name (e.g. `PROJ-001-example`), not the bare ID. The factory DB stores both — the ID `PROJ-001` and the dir name `PROJ-001-example` — so call sites always have the dir name available.

- [ ] **Step 4: Run tests, verify they pass**

```bash
cd /Users/joaooliveira/dev/personal/factory/orchestrator
uv run pytest tests/test_paths.py -v
```

Expected: all 14 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/joaooliveira/dev/personal/factory
git add orchestrator/src/factory/paths.py orchestrator/tests/test_paths.py
git commit -m "feat(paths): pure-function path helpers for factory artifacts"
```

---

## Task 3: Atomic file I/O

**Files:**
- Create: `orchestrator/src/factory/io.py`
- Create: `orchestrator/tests/test_io.py`

Atomic write semantics: write to `<path>.tmp` then `os.replace` to the final path. Crashes mid-write leave only the `.tmp` file or the prior version of `<path>` — never a partial `<path>`.

- [ ] **Step 1: Write failing tests**

Create `orchestrator/tests/test_io.py`:

```python
import json
import os
from pathlib import Path

import pytest

from factory.io import (
    atomic_write_json,
    atomic_write_text,
    ensure_dir,
    read_json,
    read_text,
    update_symlink,
)


def test_atomic_write_text_creates_file(tmp_path: Path):
    target = tmp_path / "hello.txt"
    atomic_write_text(target, "hi there\n")
    assert target.read_text() == "hi there\n"


def test_atomic_write_text_creates_parent(tmp_path: Path):
    target = tmp_path / "deep" / "nest" / "file.txt"
    atomic_write_text(target, "x")
    assert target.exists()


def test_atomic_write_text_no_tmp_remains(tmp_path: Path):
    target = tmp_path / "f.txt"
    atomic_write_text(target, "x")
    assert not (tmp_path / "f.txt.tmp").exists()


def test_atomic_write_json_round_trip(tmp_path: Path):
    target = tmp_path / "data.json"
    atomic_write_json(target, {"a": 1, "b": [2, 3]})
    assert json.loads(target.read_text()) == {"a": 1, "b": [2, 3]}


def test_atomic_write_json_pretty_prints(tmp_path: Path):
    target = tmp_path / "data.json"
    atomic_write_json(target, {"a": 1})
    assert "\n" in target.read_text()  # not single-line


def test_read_text(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_text("hello")
    assert read_text(target) == "hello"


def test_read_json(tmp_path: Path):
    target = tmp_path / "data.json"
    target.write_text('{"a": 1}')
    assert read_json(target) == {"a": 1}


def test_read_json_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        read_json(tmp_path / "missing.json")


def test_ensure_dir(tmp_path: Path):
    target = tmp_path / "a" / "b" / "c"
    ensure_dir(target)
    assert target.is_dir()


def test_ensure_dir_idempotent(tmp_path: Path):
    target = tmp_path / "x"
    ensure_dir(target)
    ensure_dir(target)  # second call must not raise
    assert target.is_dir()


def test_update_symlink_creates_relative_symlink(tmp_path: Path):
    real = tmp_path / "real.txt"
    real.write_text("x")
    link = tmp_path / "current"
    update_symlink(link, real)
    assert link.is_symlink()
    assert link.resolve() == real
    # symlink target stored as relative (just the filename)
    assert os.readlink(link) == "real.txt"


def test_update_symlink_replaces_existing(tmp_path: Path):
    a = tmp_path / "a.txt"
    a.write_text("a")
    b = tmp_path / "b.txt"
    b.write_text("b")
    link = tmp_path / "current"
    update_symlink(link, a)
    update_symlink(link, b)
    assert link.resolve() == b


def test_atomic_write_text_overwrites(tmp_path: Path):
    target = tmp_path / "f.txt"
    atomic_write_text(target, "first")
    atomic_write_text(target, "second")
    assert target.read_text() == "second"
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
cd /Users/joaooliveira/dev/personal/factory/orchestrator
uv run pytest tests/test_io.py -v
```

Expected: ImportError on `factory.io`.

- [ ] **Step 3: Implement `orchestrator/src/factory/io.py`**

```python
"""Atomic filesystem operations.

Writes use the ``write-and-rename`` pattern: data is first written to a
sibling temp file, then atomically renamed onto the target. Symlinks
are updated by writing the link to a temp name and renaming as well.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def ensure_dir(path: Path) -> None:
    """Create a directory and all parents. Idempotent."""
    path.mkdir(parents=True, exist_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    """Write text to ``path`` atomically. Creates parent dirs."""
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def atomic_write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """Write ``data`` as pretty-printed JSON atomically."""
    atomic_write_text(path, json.dumps(data, indent=indent, sort_keys=True) + "\n")


def read_text(path: Path) -> str:
    return path.read_text()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def update_symlink(link: Path, target: Path) -> None:
    """Atomically point ``link`` at ``target`` (relative to the link's parent)."""
    ensure_dir(link.parent)
    relative_target = os.path.relpath(target, start=link.parent)
    tmp_link = link.with_name(link.name + ".tmp")
    if tmp_link.exists() or tmp_link.is_symlink():
        tmp_link.unlink()
    os.symlink(relative_target, tmp_link)
    os.replace(tmp_link, link)
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
cd /Users/joaooliveira/dev/personal/factory/orchestrator
uv run pytest tests/test_io.py -v
```

Expected: all 13 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/joaooliveira/dev/personal/factory
git add orchestrator/src/factory/io.py orchestrator/tests/test_io.py
git commit -m "feat(io): atomic writes for text/json + symlink updates"
```

---

## Task 4: Pydantic handoff model

**Files:**
- Create: `orchestrator/src/factory/models/__init__.py`
- Create: `orchestrator/src/factory/models/handoff.py`
- Create: `orchestrator/tests/test_handoff_model.py`

Handoff schema is defined by spec §9 with the additions from §10 (`remediation_loop`, `tokens_used`, `defects_discovered`).

- [ ] **Step 1: Create empty package marker**

```bash
touch /Users/joaooliveira/dev/personal/factory/orchestrator/src/factory/models/__init__.py
```

Content of `orchestrator/src/factory/models/__init__.py`:

```python
"""Pydantic models for factory artifacts."""
```

- [ ] **Step 2: Write failing tests**

Create `orchestrator/tests/test_handoff_model.py`:

```python
import pytest
from pydantic import ValidationError

from factory.models.handoff import (
    DefectSeverity,
    DiscoveredDefect,
    Handoff,
    NextAuthorization,
    Stage,
    Verdict,
)


def _good_handoff() -> dict:
    return {
        "work_id": "T-0001",
        "parent_story": "US-0001",
        "project_id": "PROJ-001",
        "stage": "coder-agent",
        "input_artifacts": [],
        "output_artifacts": [],
        "verdict": "complete",
        "blockers": [],
        "risks": [],
        "assumptions": [],
        "scope_deviations": [],
        "next_authorization": "tester-agent",
        "remediation_loop": 0,
        "tokens_used": 0,
        "defects_discovered": [],
    }


class TestHandoffHappyPath:
    def test_full_valid(self):
        h = Handoff.model_validate(_good_handoff())
        assert h.work_id == "T-0001"
        assert h.verdict is Verdict.COMPLETE

    def test_round_trip(self):
        h = Handoff.model_validate(_good_handoff())
        again = Handoff.model_validate(h.model_dump(mode="json"))
        assert again == h


class TestHandoffRequiredFields:
    @pytest.mark.parametrize(
        "missing",
        [
            "work_id",
            "parent_story",
            "project_id",
            "stage",
            "verdict",
            "input_artifacts",
            "output_artifacts",
            "blockers",
            "next_authorization",
            "remediation_loop",
            "tokens_used",
            "defects_discovered",
        ],
    )
    def test_missing_field_rejected(self, missing: str):
        data = _good_handoff()
        del data[missing]
        with pytest.raises(ValidationError):
            Handoff.model_validate(data)


class TestHandoffEnumValidation:
    def test_invalid_verdict_rejected(self):
        data = _good_handoff()
        data["verdict"] = "almost-pass"
        with pytest.raises(ValidationError):
            Handoff.model_validate(data)

    def test_all_verdict_values_accepted(self):
        for v in ["pass", "warn", "fail", "blocked", "complete", "not_applicable"]:
            data = _good_handoff()
            data["verdict"] = v
            Handoff.model_validate(data)  # must not raise

    def test_invalid_stage_rejected(self):
        data = _good_handoff()
        data["stage"] = "wizard-agent"
        with pytest.raises(ValidationError):
            Handoff.model_validate(data)

    def test_all_stage_values_accepted(self):
        for s in [
            "god",
            "spec-agent",
            "boss",
            "architect-agent",
            "boundary-agent",
            "coder-agent",
            "tester-agent",
            "release-agent",
        ]:
            data = _good_handoff()
            data["stage"] = s
            Handoff.model_validate(data)


class TestHandoffIdShape:
    def test_invalid_work_id_rejected(self):
        data = _good_handoff()
        data["work_id"] = "TASK-1"
        with pytest.raises(ValidationError, match="work_id"):
            Handoff.model_validate(data)

    def test_invalid_parent_story_rejected(self):
        data = _good_handoff()
        data["parent_story"] = "STORY-0001"
        with pytest.raises(ValidationError, match="parent_story"):
            Handoff.model_validate(data)

    def test_invalid_project_id_rejected(self):
        data = _good_handoff()
        data["project_id"] = "P-1"
        with pytest.raises(ValidationError, match="project_id"):
            Handoff.model_validate(data)


class TestHandoffNumericConstraints:
    def test_negative_remediation_loop_rejected(self):
        data = _good_handoff()
        data["remediation_loop"] = -1
        with pytest.raises(ValidationError):
            Handoff.model_validate(data)

    def test_negative_tokens_used_rejected(self):
        data = _good_handoff()
        data["tokens_used"] = -1
        with pytest.raises(ValidationError):
            Handoff.model_validate(data)


class TestDefectsDiscovered:
    def test_defect_shape(self):
        data = _good_handoff()
        data["defects_discovered"] = [
            {
                "description": "noticed dead code in unrelated module",
                "severity": "low",
                "location": "src/proj001/legacy.py:42",
            }
        ]
        h = Handoff.model_validate(data)
        assert h.defects_discovered[0].severity is DefectSeverity.LOW

    def test_invalid_severity_rejected(self):
        data = _good_handoff()
        data["defects_discovered"] = [
            {"description": "x", "severity": "kinda-bad", "location": "y"}
        ]
        with pytest.raises(ValidationError):
            Handoff.model_validate(data)


class TestNextAuthorization:
    def test_none_value_allowed(self):
        data = _good_handoff()
        data["next_authorization"] = "none"
        Handoff.model_validate(data)

    def test_invalid_next_rejected(self):
        data = _good_handoff()
        data["next_authorization"] = "wizard-agent"
        with pytest.raises(ValidationError):
            Handoff.model_validate(data)
```

- [ ] **Step 3: Run tests, verify failure**

```bash
cd /Users/joaooliveira/dev/personal/factory/orchestrator
uv run pytest tests/test_handoff_model.py -v
```

Expected: ImportError on `factory.models.handoff`.

- [ ] **Step 4: Implement `orchestrator/src/factory/models/handoff.py`**

```python
"""Handoff schema — the structured output every agent must produce.

Spec §9 plus amendments §10.1 (defects_discovered), §10.2
(remediation_loop, tokens_used).
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Verdict(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    BLOCKED = "blocked"
    COMPLETE = "complete"
    NOT_APPLICABLE = "not_applicable"


class Stage(str, Enum):
    GOD = "god"
    SPEC_AGENT = "spec-agent"
    BOSS = "boss"
    ARCHITECT_AGENT = "architect-agent"
    BOUNDARY_AGENT = "boundary-agent"
    CODER_AGENT = "coder-agent"
    TESTER_AGENT = "tester-agent"
    RELEASE_AGENT = "release-agent"


class NextAuthorization(str, Enum):
    GOD = "god"
    SPEC_AGENT = "spec-agent"
    BOSS = "boss"
    ARCHITECT_AGENT = "architect-agent"
    BOUNDARY_AGENT = "boundary-agent"
    CODER_AGENT = "coder-agent"
    TESTER_AGENT = "tester-agent"
    RELEASE_AGENT = "release-agent"
    NONE = "none"


class DefectSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DiscoveredDefect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    severity: DefectSeverity
    location: str


_TASK_RE = re.compile(r"^T-\d{4}$")
_STORY_RE = re.compile(r"^US-\d{4}$")
_PROJECT_RE = re.compile(r"^PROJ-\d{3}$")


class Handoff(BaseModel):
    """Structured handoff between agents.

    Every required field per spec §8/§9/§10 is non-optional and validated.
    """

    model_config = ConfigDict(extra="forbid")

    work_id: str
    parent_story: str
    project_id: str
    stage: Stage
    input_artifacts: list[str]
    output_artifacts: list[str]
    verdict: Verdict
    blockers: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    scope_deviations: list[str] = Field(default_factory=list)
    next_authorization: NextAuthorization
    remediation_loop: int = Field(ge=0)
    tokens_used: int = Field(ge=0)
    defects_discovered: list[DiscoveredDefect]

    @field_validator("work_id")
    @classmethod
    def _validate_work_id(cls, v: str) -> str:
        if not _TASK_RE.match(v):
            raise ValueError(f"work_id must match T-NNNN, got {v!r}")
        return v

    @field_validator("parent_story")
    @classmethod
    def _validate_parent_story(cls, v: str) -> str:
        if not _STORY_RE.match(v):
            raise ValueError(f"parent_story must match US-NNNN, got {v!r}")
        return v

    @field_validator("project_id")
    @classmethod
    def _validate_project_id(cls, v: str) -> str:
        if not _PROJECT_RE.match(v):
            raise ValueError(f"project_id must match PROJ-NNN, got {v!r}")
        return v
```

- [ ] **Step 5: Run tests, verify they pass**

```bash
cd /Users/joaooliveira/dev/personal/factory/orchestrator
uv run pytest tests/test_handoff_model.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/joaooliveira/dev/personal/factory
git add orchestrator/src/factory/models/ orchestrator/tests/test_handoff_model.py
git commit -m "feat(models): Pydantic Handoff model with validated IDs and enums"
```

---

## Task 5: Generate handoff JSON Schema from Pydantic

**Files:**
- Create: `orchestrator/src/factory/scripts/__init__.py`
- Create: `orchestrator/src/factory/scripts/gen_handoff_schema.py`
- Create: `orchestrator/tests/test_gen_handoff_schema.py`
- Create (by running script): `handoffs/handoff.schema.json`
- Create: `handoffs/examples/example-handoff.json`

- [ ] **Step 1: Create scripts package**

`orchestrator/src/factory/scripts/__init__.py`:

```python
"""One-shot CLI scripts for code generation and admin tasks."""
```

- [ ] **Step 2: Write failing test**

`orchestrator/tests/test_gen_handoff_schema.py`:

```python
import json
from pathlib import Path

import jsonschema
import pytest

from factory.scripts.gen_handoff_schema import generate_schema, write_schema


def test_generate_schema_produces_object_schema():
    schema = generate_schema()
    assert schema["type"] == "object"
    assert "work_id" in schema["properties"]
    # extra fields forbidden (Pydantic extra="forbid" -> additionalProperties: False)
    assert schema.get("additionalProperties") is False


def test_required_fields_present():
    schema = generate_schema()
    expected_required = {
        "work_id",
        "parent_story",
        "project_id",
        "stage",
        "input_artifacts",
        "output_artifacts",
        "verdict",
        "next_authorization",
        "remediation_loop",
        "tokens_used",
        "defects_discovered",
    }
    assert expected_required.issubset(set(schema["required"]))


def test_write_schema(tmp_path: Path):
    target = tmp_path / "handoff.schema.json"
    write_schema(target)
    assert target.exists()
    data = json.loads(target.read_text())
    assert data["type"] == "object"


def test_example_handoff_validates_against_schema(tmp_path: Path):
    schema = generate_schema()
    example = {
        "work_id": "T-0001",
        "parent_story": "US-0001",
        "project_id": "PROJ-001",
        "stage": "coder-agent",
        "input_artifacts": ["projects/PROJ-001/docs/pipeline/T-0001/boundary-agent.handoff.json"],
        "output_artifacts": ["projects/PROJ-001/repo/src/feature.py"],
        "verdict": "complete",
        "blockers": [],
        "risks": [],
        "assumptions": [],
        "scope_deviations": [],
        "next_authorization": "tester-agent",
        "remediation_loop": 0,
        "tokens_used": 8421,
        "defects_discovered": [],
    }
    jsonschema.validate(example, schema)


def test_missing_field_fails_jsonschema(tmp_path: Path):
    schema = generate_schema()
    bad = {"work_id": "T-0001"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)
```

- [ ] **Step 3: Add `jsonschema` to dev deps**

In `orchestrator/pyproject.toml`, add to the `[project.optional-dependencies]` `dev` list:

```toml
    "jsonschema>=4.23.0",
```

Then `uv sync --all-extras`.

- [ ] **Step 4: Run tests to confirm failure**

```bash
cd /Users/joaooliveira/dev/personal/factory/orchestrator
uv run pytest tests/test_gen_handoff_schema.py -v
```

Expected: ImportError on `factory.scripts.gen_handoff_schema`.

- [ ] **Step 5: Implement `orchestrator/src/factory/scripts/gen_handoff_schema.py`**

```python
"""Regenerate ``handoffs/handoff.schema.json`` from the Pydantic model.

Run via ``uv run factory-gen-handoff-schema`` or directly as
``python -m factory.scripts.gen_handoff_schema``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from factory.models.handoff import Handoff


def generate_schema() -> dict:
    """Return the JSON Schema dict for the Handoff model."""
    return Handoff.model_json_schema()


def write_schema(target: Path) -> None:
    """Write the generated schema to ``target`` (atomic-ish: parent must exist)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(generate_schema(), indent=2, sort_keys=True) + "\n")


@click.command()
@click.option(
    "--out",
    "out",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("handoffs/handoff.schema.json"),
    show_default=True,
    help="Path (relative to factory root) for the generated schema.",
)
@click.option(
    "--factory-root",
    "factory_root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path.cwd(),
    show_default=True,
    help="Factory root (defaults to CWD).",
)
def main(out: Path, factory_root: Path) -> None:
    target = factory_root / out
    write_schema(target)
    click.echo(f"Wrote schema to {target}")


if __name__ == "__main__":
    main()
    sys.exit(0)
```

- [ ] **Step 6: Run tests, verify pass**

```bash
cd /Users/joaooliveira/dev/personal/factory/orchestrator
uv run pytest tests/test_gen_handoff_schema.py -v
```

Expected: 5 tests pass.

- [ ] **Step 7: Generate the real schema file**

```bash
cd /Users/joaooliveira/dev/personal/factory
uv --project orchestrator run factory-gen-handoff-schema --factory-root .
ls -la handoffs/
```

Expected: `handoffs/handoff.schema.json` exists.

- [ ] **Step 8: Hand-write the example handoff**

Create `handoffs/examples/example-handoff.json`:

```json
{
  "work_id": "T-0001",
  "parent_story": "US-0001",
  "project_id": "PROJ-001",
  "stage": "coder-agent",
  "input_artifacts": [
    "projects/PROJ-001-example/docs/pipeline/T-0001/boundary-agent.handoff.json"
  ],
  "output_artifacts": [
    "projects/PROJ-001-example/repo/src/proj001/feature.py",
    "projects/PROJ-001-example/repo/tests/test_feature.py"
  ],
  "verdict": "complete",
  "blockers": [],
  "risks": [],
  "assumptions": [
    "Tenant id comes from auth context, not request body"
  ],
  "scope_deviations": [],
  "next_authorization": "tester-agent",
  "remediation_loop": 0,
  "tokens_used": 8421,
  "defects_discovered": []
}
```

- [ ] **Step 9: Verify the example validates against the generated schema**

```bash
cd /Users/joaooliveira/dev/personal/factory
uv --project orchestrator run python -c "
import json, jsonschema
schema = json.load(open('handoffs/handoff.schema.json'))
example = json.load(open('handoffs/examples/example-handoff.json'))
jsonschema.validate(example, schema)
print('OK')
"
```

Expected output: `OK`.

- [ ] **Step 10: Commit**

```bash
cd /Users/joaooliveira/dev/personal/factory
git add orchestrator/src/factory/scripts/ orchestrator/tests/test_gen_handoff_schema.py orchestrator/pyproject.toml orchestrator/uv.lock handoffs/
git commit -m "feat(handoff): generate JSON Schema from Pydantic model + example"
```

---

## Task 6: Story and task Pydantic models with frontmatter round-trip

**Files:**
- Create: `orchestrator/src/factory/models/story.py`
- Create: `orchestrator/src/factory/models/task.py`
- Create: `orchestrator/tests/test_story_model.py`
- Create: `orchestrator/tests/test_task_model.py`
- Create: `stories/.template.md`
- Create: `orchestrator/src/factory/templates/__init__.py`
- Create: `orchestrator/src/factory/templates/task.md`

Stories and tasks are markdown files with YAML frontmatter for the structured fields plus a free-form body for the prose (problem, why, acceptance criteria, etc.). Spec §5.2 defines the field set.

- [ ] **Step 1: Write failing tests for the Story model**

`orchestrator/tests/test_story_model.py`:

```python
import pytest
from pydantic import ValidationError

from factory.models.story import (
    Story,
    StoryStatus,
    StoryType,
    parse_story_markdown,
    serialize_story_markdown,
)


def _good_story_data() -> dict:
    return {
        "id": "US-0001",
        "type": "feature",
        "status": "draft",
        "title": "Add /health endpoint",
        "version": 1,
        "linked_requests": ["REQ-0001"],
        "task_ids": [],
        "acceptance_criteria": ["GET /health returns 200"],
        "non_goals": ["Authentication"],
        "assumptions": [],
        "risks": [],
    }


class TestStory:
    def test_happy_path(self):
        s = Story.model_validate(_good_story_data())
        assert s.id == "US-0001"
        assert s.type is StoryType.FEATURE

    def test_invalid_id_rejected(self):
        d = _good_story_data()
        d["id"] = "STORY-1"
        with pytest.raises(ValidationError, match="id"):
            Story.model_validate(d)

    def test_invalid_status_rejected(self):
        d = _good_story_data()
        d["status"] = "in-progress"
        with pytest.raises(ValidationError):
            Story.model_validate(d)

    def test_all_status_values(self):
        for s in ["draft", "ready", "in_progress", "blocked", "done", "rejected"]:
            d = _good_story_data()
            d["status"] = s
            Story.model_validate(d)

    def test_all_type_values(self):
        for t in ["feature", "bug", "tech-debt", "operational"]:
            d = _good_story_data()
            d["type"] = t
            Story.model_validate(d)

    def test_version_must_be_positive(self):
        d = _good_story_data()
        d["version"] = 0
        with pytest.raises(ValidationError):
            Story.model_validate(d)


class TestStoryRoundTrip:
    def test_serialize_then_parse(self):
        s = Story.model_validate(_good_story_data())
        body = "# Story US-0001: Add /health endpoint\n\n## Problem\nNo health endpoint.\n"
        text = serialize_story_markdown(s, body)
        s2, body2 = parse_story_markdown(text)
        assert s2 == s
        assert body2.strip() == body.strip()

    def test_parse_with_yaml_block(self):
        text = """---
id: US-0001
type: feature
status: draft
title: Add /health endpoint
version: 1
linked_requests:
  - REQ-0001
task_ids: []
acceptance_criteria:
  - GET /health returns 200
non_goals:
  - Authentication
assumptions: []
risks: []
---
# Story US-0001: Add /health endpoint

## Problem
No health endpoint.
"""
        story, body = parse_story_markdown(text)
        assert story.id == "US-0001"
        assert "Problem" in body

    def test_parse_missing_frontmatter_raises(self):
        with pytest.raises(ValueError, match="frontmatter"):
            parse_story_markdown("# just a heading\n")
```

- [ ] **Step 2: Write failing tests for the Task model**

`orchestrator/tests/test_task_model.py`:

```python
import pytest
from pydantic import ValidationError

from factory.models.task import (
    Task,
    TaskStatus,
    parse_task_markdown,
    serialize_task_markdown,
)


def _good_task_data() -> dict:
    return {
        "id": "T-0001",
        "parent_story": "US-0001",
        "project_id": "PROJ-001",
        "title": "Implement /health route",
        "status": "ready",
        "current_stage": "architect-agent",
        "allowed_scope": ["src/proj001/main.py"],
        "forbidden_scope": ["src/proj001/db.py"],
        "expected_output": ["new endpoint"],
        "completion_evidence": ["pytest tests/test_health.py passes"],
        "change_budget": "lines<=80; files<=2",
        "max_remediation_loops": 3,
        "max_token_budget": 200000,
        "dependencies": [],
        "idempotency_rule": "Re-running this task should overwrite the route definition idempotently.",
        "stop_conditions": ["any change to db.py"],
        "blocked_by": None,
    }


class TestTask:
    def test_happy_path(self):
        t = Task.model_validate(_good_task_data())
        assert t.id == "T-0001"
        assert t.parent_story == "US-0001"

    def test_default_max_remediation_loops(self):
        d = _good_task_data()
        del d["max_remediation_loops"]
        t = Task.model_validate(d)
        assert t.max_remediation_loops == 3

    def test_default_max_token_budget(self):
        d = _good_task_data()
        del d["max_token_budget"]
        t = Task.model_validate(d)
        assert t.max_token_budget == 200_000

    def test_invalid_id_rejected(self):
        d = _good_task_data()
        d["id"] = "TASK-1"
        with pytest.raises(ValidationError):
            Task.model_validate(d)

    def test_invalid_status_rejected(self):
        d = _good_task_data()
        d["status"] = "kinda-ready"
        with pytest.raises(ValidationError):
            Task.model_validate(d)

    def test_max_remediation_loops_must_be_positive(self):
        d = _good_task_data()
        d["max_remediation_loops"] = 0
        with pytest.raises(ValidationError):
            Task.model_validate(d)


class TestTaskRoundTrip:
    def test_serialize_then_parse(self):
        t = Task.model_validate(_good_task_data())
        body = "# Task T-0001\n\n## Purpose\nWire health endpoint.\n"
        text = serialize_task_markdown(t, body)
        t2, body2 = parse_task_markdown(text)
        assert t2 == t
        assert body2.strip() == body.strip()
```

- [ ] **Step 3: Run tests, expect failure**

```bash
cd /Users/joaooliveira/dev/personal/factory/orchestrator
uv run pytest tests/test_story_model.py tests/test_task_model.py -v
```

Expected: ImportError on both.

- [ ] **Step 4: Implement `orchestrator/src/factory/models/story.py`**

```python
"""Story model and frontmatter <-> object round-trip."""

from __future__ import annotations

import re
from enum import Enum

import frontmatter
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StoryType(str, Enum):
    FEATURE = "feature"
    BUG = "bug"
    TECH_DEBT = "tech-debt"
    OPERATIONAL = "operational"


class StoryStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    REJECTED = "rejected"


_STORY_ID_RE = re.compile(r"^US-\d{4}$")


class Story(BaseModel):
    """Frontmatter representation of a story.

    Body prose (problem, why, user story, etc.) is held separately
    by the parsing helpers below.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    type: StoryType
    status: StoryStatus
    title: str
    version: int = Field(ge=1)
    linked_requests: list[str] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not _STORY_ID_RE.match(v):
            raise ValueError(f"id must match US-NNNN, got {v!r}")
        return v


def parse_story_markdown(text: str) -> tuple[Story, str]:
    """Parse a story markdown document. Returns (story, body)."""
    post = frontmatter.loads(text)
    if not post.metadata:
        raise ValueError("story file is missing YAML frontmatter")
    return Story.model_validate(post.metadata), post.content


def serialize_story_markdown(story: Story, body: str) -> str:
    """Serialize a story + body back to markdown with YAML frontmatter."""
    post = frontmatter.Post(content=body, **story.model_dump(mode="json"))
    return frontmatter.dumps(post)
```

- [ ] **Step 5: Implement `orchestrator/src/factory/models/task.py`**

```python
"""Task model and frontmatter <-> object round-trip."""

from __future__ import annotations

import re
from enum import Enum

import frontmatter
from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskStatus(str, Enum):
    READY = "ready"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETE = "complete"
    FAILED = "failed"
    SUPERSEDED = "superseded"


_TASK_ID_RE = re.compile(r"^T-\d{4}$")
_STORY_ID_RE = re.compile(r"^US-\d{4}$")
_PROJECT_ID_RE = re.compile(r"^PROJ-\d{3}$")


class Task(BaseModel):
    """Frontmatter representation of a task."""

    model_config = ConfigDict(extra="forbid")

    id: str
    parent_story: str
    project_id: str
    title: str
    status: TaskStatus
    current_stage: str | None = None
    allowed_scope: list[str]
    forbidden_scope: list[str] = Field(default_factory=list)
    expected_output: list[str]
    completion_evidence: list[str]
    change_budget: str
    max_remediation_loops: int = Field(default=3, ge=1)
    max_token_budget: int = Field(default=200_000, ge=1)
    dependencies: list[str] = Field(default_factory=list)
    idempotency_rule: str
    stop_conditions: list[str] = Field(default_factory=list)
    blocked_by: str | None = None

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not _TASK_ID_RE.match(v):
            raise ValueError(f"id must match T-NNNN, got {v!r}")
        return v

    @field_validator("parent_story")
    @classmethod
    def _validate_parent_story(cls, v: str) -> str:
        if not _STORY_ID_RE.match(v):
            raise ValueError(f"parent_story must match US-NNNN, got {v!r}")
        return v

    @field_validator("project_id")
    @classmethod
    def _validate_project_id(cls, v: str) -> str:
        if not _PROJECT_ID_RE.match(v):
            raise ValueError(f"project_id must match PROJ-NNN, got {v!r}")
        return v


def parse_task_markdown(text: str) -> tuple[Task, str]:
    post = frontmatter.loads(text)
    if not post.metadata:
        raise ValueError("task file is missing YAML frontmatter")
    return Task.model_validate(post.metadata), post.content


def serialize_task_markdown(task: Task, body: str) -> str:
    post = frontmatter.Post(content=body, **task.model_dump(mode="json"))
    return frontmatter.dumps(post)
```

- [ ] **Step 6: Run tests, verify pass**

```bash
cd /Users/joaooliveira/dev/personal/factory/orchestrator
uv run pytest tests/test_story_model.py tests/test_task_model.py -v
```

Expected: all pass.

- [ ] **Step 7: Write the story template at `stories/.template.md`**

```markdown
---
id: US-XXXX
type: feature
status: draft
title: <one-line title>
version: 1
linked_requests: []
task_ids: []
acceptance_criteria:
  - <measurable outcome>
non_goals:
  - <explicitly out of scope>
assumptions: []
risks: []
---
# Story US-XXXX: <title>

## Type
feature | bug | tech-debt | operational

## Problem
<what problem exists>

## Why
<why this work matters>

## User Story
As a <role>,
I want <capability>,
so that <outcome>.

## Acceptance Criteria
- <measurable outcome>

## Non-Goals
- <explicitly out of scope>

## Assumptions
- <assumption>

## Risks
- <risk>

## Linked Requests
- <REQ-ID>

## Tasks
- <T-ID>: <task title>
```

- [ ] **Step 8: Write task template package**

`orchestrator/src/factory/templates/__init__.py`:

```python
"""Markdown templates packaged with the orchestrator code."""

from importlib import resources


def task_template_text() -> str:
    return (resources.files(__package__) / "task.md").read_text()
```

`orchestrator/src/factory/templates/task.md`:

```markdown
---
id: T-XXXX
parent_story: US-XXXX
project_id: PROJ-XXX
title: <one-line title>
status: ready
current_stage: null
allowed_scope:
  - <path or module>
forbidden_scope: []
expected_output:
  - <artifact>
completion_evidence:
  - <evidence>
change_budget: "lines<=200; files<=4"
max_remediation_loops: 3
max_token_budget: 200000
dependencies: []
idempotency_rule: <how reruns should behave>
stop_conditions: []
blocked_by: null
---
# Task T-XXXX: <title>

## Parent Story
US-XXXX

## Purpose
<why this task exists>

## Allowed Scope
- <path or module>

## Forbidden Scope
- <path or module or action>

## Expected Output
- <artifact>

## Completion Evidence
- <evidence>

## Change Budget
- lines<=200; files<=4

## Dependencies
- <task-id>

## Idempotency Rule
<how reruns should behave>

## Stop Conditions
- <condition>
```

> **Note:** Hatch's wheel build needs to know that the `templates/` directory contains data files. Add this to `orchestrator/pyproject.toml` under `[tool.hatch.build.targets.wheel]`:
>
> ```toml
> [tool.hatch.build.targets.wheel.force-include]
> "src/factory/templates/task.md" = "factory/templates/task.md"
> ```

- [ ] **Step 9: Add a test that the task template parses cleanly**

Append to `orchestrator/tests/test_task_model.py`:

```python
def test_task_template_parses(tmp_path):
    from factory.templates import task_template_text

    text = task_template_text()
    # The template uses placeholder values; parsing as Task should fail
    # validation (e.g. T-XXXX is not a real ID), but the YAML must parse.
    import frontmatter

    post = frontmatter.loads(text)
    assert post.metadata["id"] == "T-XXXX"
    assert post.metadata["parent_story"] == "US-XXXX"
    assert post.metadata["max_remediation_loops"] == 3
```

- [ ] **Step 10: Run all tests**

```bash
cd /Users/joaooliveira/dev/personal/factory/orchestrator
uv run pytest -v
```

Expected: all tests pass (everything from prior tasks plus story/task tests).

- [ ] **Step 11: Commit**

```bash
cd /Users/joaooliveira/dev/personal/factory
git add orchestrator/src/factory/models/ orchestrator/src/factory/templates/ \
        orchestrator/tests/test_story_model.py orchestrator/tests/test_task_model.py \
        orchestrator/pyproject.toml stories/.template.md
git commit -m "feat(models): Story and Task models with frontmatter round-trip + templates"
```

---

## Task 7: ProjectRules model

**Files:**
- Create: `orchestrator/src/factory/models/project_rules.py`
- Create: `orchestrator/tests/test_project_rules.py`

`PROJECT_RULES.md` declares per-project conventions (port range, database engine, git remote, deviations).

- [ ] **Step 1: Failing tests**

`orchestrator/tests/test_project_rules.py`:

```python
import pytest
from pydantic import ValidationError

from factory.models.project_rules import (
    DatabaseEngine,
    ProjectRules,
    parse_project_rules_markdown,
)


def _good() -> dict:
    return {
        "project_id": "PROJ-001",
        "port_range": "8000-8099",
        "database_engine": "sqlite",
        "database_name": "proj001",
        "git_remote": "git@github.com:joaooliveira/proj-001-example.git",
        "deviation_notes": [],
    }


def test_happy_path():
    p = ProjectRules.model_validate(_good())
    assert p.port_range_low == 8000
    assert p.port_range_high == 8099
    assert p.database_engine is DatabaseEngine.SQLITE


def test_port_range_low_must_be_less_than_high():
    d = _good()
    d["port_range"] = "8099-8000"
    with pytest.raises(ValidationError, match="port_range"):
        ProjectRules.model_validate(d)


def test_port_range_must_have_dash():
    d = _good()
    d["port_range"] = "8000"
    with pytest.raises(ValidationError, match="port_range"):
        ProjectRules.model_validate(d)


def test_postgres_engine():
    d = _good()
    d["database_engine"] = "postgres"
    p = ProjectRules.model_validate(d)
    assert p.database_engine is DatabaseEngine.POSTGRES


def test_invalid_engine():
    d = _good()
    d["database_engine"] = "mysql"
    with pytest.raises(ValidationError):
        ProjectRules.model_validate(d)


def test_parse_markdown():
    text = """---
project_id: PROJ-001
port_range: 8000-8099
database_engine: sqlite
database_name: proj001
git_remote: git@github.com:joaooliveira/proj-001-example.git
deviation_notes:
  - "SQLite chosen over Postgres for v1"
---
# Project Rules: PROJ-001

Just notes here.
"""
    p, body = parse_project_rules_markdown(text)
    assert p.project_id == "PROJ-001"
    assert p.port_range_low == 8000
    assert "Just notes" in body
```

- [ ] **Step 2: Run tests, expect failure**

```bash
uv run pytest tests/test_project_rules.py -v
```

- [ ] **Step 3: Implement `orchestrator/src/factory/models/project_rules.py`**

```python
"""ProjectRules model — per-project conventions.

Parsed from ``PROJECT_RULES.md`` at the root of each project directory.
"""

from __future__ import annotations

import re
from enum import Enum

import frontmatter
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator


class DatabaseEngine(str, Enum):
    POSTGRES = "postgres"
    SQLITE = "sqlite"


_PROJECT_ID_RE = re.compile(r"^PROJ-\d{3}$")
_PORT_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")


class ProjectRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    port_range: str
    database_engine: DatabaseEngine
    database_name: str
    git_remote: str
    deviation_notes: list[str] = Field(default_factory=list)

    @field_validator("project_id")
    @classmethod
    def _validate_project_id(cls, v: str) -> str:
        if not _PROJECT_ID_RE.match(v):
            raise ValueError(f"project_id must match PROJ-NNN, got {v!r}")
        return v

    @model_validator(mode="after")
    def _validate_port_range(self) -> "ProjectRules":
        m = _PORT_RANGE_RE.match(self.port_range)
        if not m:
            raise ValueError(f"port_range must be 'low-high', got {self.port_range!r}")
        low, high = int(m.group(1)), int(m.group(2))
        if low >= high:
            raise ValueError(f"port_range low must be < high, got {low}-{high}")
        return self

    @computed_field  # type: ignore[misc]
    @property
    def port_range_low(self) -> int:
        return int(_PORT_RANGE_RE.match(self.port_range).group(1))  # type: ignore[union-attr]

    @computed_field  # type: ignore[misc]
    @property
    def port_range_high(self) -> int:
        return int(_PORT_RANGE_RE.match(self.port_range).group(2))  # type: ignore[union-attr]


def parse_project_rules_markdown(text: str) -> tuple[ProjectRules, str]:
    post = frontmatter.loads(text)
    if not post.metadata:
        raise ValueError("PROJECT_RULES.md is missing YAML frontmatter")
    return ProjectRules.model_validate(post.metadata), post.content
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_project_rules.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/src/factory/models/project_rules.py orchestrator/tests/test_project_rules.py
git commit -m "feat(models): ProjectRules with port_range + database_engine"
```

---

## Task 8: SQL DDL constants and `state/*.schema.sql` files

**Files:**
- Create: `orchestrator/src/factory/state/__init__.py`
- Create: `orchestrator/src/factory/state/schemas.py`
- Create: `state/factory.schema.sql`
- Create: `state/project.schema.sql`
- Create: `orchestrator/tests/test_schemas.py`

The DDL is held as Python string constants in `schemas.py` (so the orchestrator can run it on a fresh DB) and mirrored to `state/*.schema.sql` files (so humans can read it without booting Python). The mirror is generated by a one-shot script.

- [ ] **Step 1: Create state package marker**

`orchestrator/src/factory/state/__init__.py`:

```python
"""Persistent state — DuckDB schemas, connections, and queries."""
```

- [ ] **Step 2: Failing tests**

`orchestrator/tests/test_schemas.py`:

```python
import duckdb

from factory.state.schemas import (
    FACTORY_SCHEMA_SQL,
    PROJECT_SCHEMA_SQL,
    factory_table_names,
    project_table_names,
)


def _table_names(conn: duckdb.DuckDBPyConnection) -> set[str]:
    rows = conn.execute("SELECT table_name FROM information_schema.tables").fetchall()
    return {r[0] for r in rows}


def test_factory_schema_creates_expected_tables(tmp_path):
    db = tmp_path / "factory.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute(FACTORY_SCHEMA_SQL)
    names = _table_names(conn)
    assert names == set(factory_table_names())


def test_factory_schema_has_required_tables():
    assert set(factory_table_names()) == {
        "projects",
        "stories",
        "requests",
        "blockers",
    }


def test_project_schema_creates_expected_tables(tmp_path):
    db = tmp_path / "project.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute(PROJECT_SCHEMA_SQL)
    names = _table_names(conn)
    assert names == set(project_table_names())


def test_project_schema_has_required_tables():
    assert set(project_table_names()) == {
        "tasks",
        "agent_runs",
        "blockers",
        "releases",
    }


def test_factory_schema_idempotent(tmp_path):
    db = tmp_path / "factory.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute(FACTORY_SCHEMA_SQL)
    conn.execute(FACTORY_SCHEMA_SQL)  # must not raise


def test_project_schema_idempotent(tmp_path):
    db = tmp_path / "project.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute(PROJECT_SCHEMA_SQL)
    conn.execute(PROJECT_SCHEMA_SQL)


def test_projects_port_range_uniqueness_enforced(tmp_path):
    db = tmp_path / "factory.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute(FACTORY_SCHEMA_SQL)
    conn.execute(
        "INSERT INTO projects (id, name, path, status, port_range_low, port_range_high, "
        "created_at, updated_at) VALUES ('PROJ-001', 'a', 'projects/a', 'active', 8000, 8099, NOW(), NOW())"
    )
    # Overlapping range should fail (uniqueness on the (low, high) tuple is the simplest)
    import pytest

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO projects (id, name, path, status, port_range_low, port_range_high, "
            "created_at, updated_at) VALUES ('PROJ-002', 'b', 'projects/b', 'active', 8000, 8099, NOW(), NOW())"
        )


def test_tasks_lock_columns_present(tmp_path):
    db = tmp_path / "project.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute(PROJECT_SCHEMA_SQL)
    cols = conn.execute("PRAGMA table_info('tasks')").fetchall()
    col_names = {c[1] for c in cols}
    assert {"locked_at", "locked_by_run_id", "remediation_loops_used", "token_budget_used"}.issubset(
        col_names
    )
```

- [ ] **Step 3: Run tests, expect failure**

```bash
uv run pytest tests/test_schemas.py -v
```

- [ ] **Step 4: Implement `orchestrator/src/factory/state/schemas.py`**

```python
"""Embedded SQL DDL for factory and project DuckDB databases.

These constants are the single source of truth. The mirror files at
``state/factory.schema.sql`` and ``state/project.schema.sql`` are
generated from these constants by ``scripts/gen_schema_sql.py``.
"""

from __future__ import annotations


FACTORY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    path            TEXT NOT NULL,
    status          TEXT NOT NULL,
    port_range_low  INTEGER NOT NULL,
    port_range_high INTEGER NOT NULL,
    created_at      TIMESTAMP NOT NULL,
    updated_at      TIMESTAMP NOT NULL,
    UNIQUE (port_range_low, port_range_high)
);

CREATE TABLE IF NOT EXISTS stories (
    id                 TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL,
    type               TEXT NOT NULL,
    title              TEXT NOT NULL,
    status             TEXT NOT NULL,
    current_version    INTEGER NOT NULL DEFAULT 1,
    priority           TEXT,
    source_request_id  TEXT,
    created_at         TIMESTAMP NOT NULL,
    updated_at         TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS requests (
    id           TEXT PRIMARY KEY,
    raw_text     TEXT NOT NULL,
    project_id   TEXT,
    submitted_at TIMESTAMP NOT NULL,
    status       TEXT NOT NULL,
    routed_to    TEXT,
    notes        TEXT
);

CREATE TABLE IF NOT EXISTS blockers (
    id           TEXT PRIMARY KEY,
    scope        TEXT NOT NULL,            -- 'factory' (cross-project)
    project_id   TEXT,
    severity     TEXT NOT NULL,
    description  TEXT NOT NULL,
    status       TEXT NOT NULL,
    created_at   TIMESTAMP NOT NULL,
    resolved_at  TIMESTAMP
);
"""


PROJECT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id                       TEXT PRIMARY KEY,
    story_id                 TEXT NOT NULL,
    project_id               TEXT NOT NULL,
    title                    TEXT NOT NULL,
    status                   TEXT NOT NULL,
    current_stage            TEXT,
    blocked_by               TEXT,
    remediation_loops_used   INTEGER NOT NULL DEFAULT 0,
    token_budget_used        INTEGER NOT NULL DEFAULT 0,
    locked_at                TIMESTAMP,
    locked_by_run_id         TEXT,
    created_at               TIMESTAMP NOT NULL,
    updated_at               TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id            TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    agent_name    TEXT NOT NULL,
    verdict       TEXT NOT NULL,
    started_at    TIMESTAMP NOT NULL,
    finished_at   TIMESTAMP,
    summary_path  TEXT,
    handoff_path  TEXT,
    tokens_used   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS blockers (
    id           TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL,
    severity     TEXT NOT NULL,
    description  TEXT NOT NULL,
    status       TEXT NOT NULL,
    created_at   TIMESTAMP NOT NULL,
    resolved_at  TIMESTAMP
);

CREATE TABLE IF NOT EXISTS releases (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL,
    story_id            TEXT,
    task_id             TEXT,
    status              TEXT NOT NULL,
    release_notes_path  TEXT,
    pr_url              TEXT,
    created_at          TIMESTAMP NOT NULL
);
"""


def factory_table_names() -> list[str]:
    return ["projects", "stories", "requests", "blockers"]


def project_table_names() -> list[str]:
    return ["tasks", "agent_runs", "blockers", "releases"]
```

- [ ] **Step 5: Run tests, verify pass**

```bash
uv run pytest tests/test_schemas.py -v
```

- [ ] **Step 6: Add a script to mirror SQL to disk**

`orchestrator/src/factory/scripts/gen_schema_sql.py`:

```python
"""Mirror the SQL DDL constants to ``state/*.schema.sql`` files."""

from __future__ import annotations

from pathlib import Path

import click

from factory.state.schemas import FACTORY_SCHEMA_SQL, PROJECT_SCHEMA_SQL


@click.command()
@click.option(
    "--factory-root",
    "factory_root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path.cwd(),
    show_default=True,
)
def main(factory_root: Path) -> None:
    factory_sql = factory_root / "state" / "factory.schema.sql"
    project_sql = factory_root / "state" / "project.schema.sql"
    factory_sql.parent.mkdir(parents=True, exist_ok=True)
    factory_sql.write_text(FACTORY_SCHEMA_SQL.strip() + "\n")
    project_sql.write_text(PROJECT_SCHEMA_SQL.strip() + "\n")
    click.echo(f"Wrote {factory_sql}")
    click.echo(f"Wrote {project_sql}")


if __name__ == "__main__":
    main()
```

Add to `orchestrator/pyproject.toml` `[project.scripts]`:

```toml
factory-gen-schema-sql = "factory.scripts.gen_schema_sql:main"
```

Then `uv sync --all-extras` to register the new entry point.

- [ ] **Step 7: Generate the mirror files**

```bash
cd /Users/joaooliveira/dev/personal/factory
uv --project orchestrator run factory-gen-schema-sql --factory-root .
ls state/
cat state/factory.schema.sql | head -20
```

Expected: `factory.schema.sql` and `project.schema.sql` exist, factory schema starts with `CREATE TABLE IF NOT EXISTS projects`.

- [ ] **Step 8: Commit**

```bash
cd /Users/joaooliveira/dev/personal/factory
git add orchestrator/src/factory/state/ \
        orchestrator/src/factory/scripts/gen_schema_sql.py \
        orchestrator/tests/test_schemas.py \
        orchestrator/pyproject.toml \
        state/
git commit -m "feat(state): factory + project DuckDB schemas + SQL mirror script"
```

---

## Task 9: Connection management

**Files:**
- Create: `orchestrator/src/factory/state/connection.py`
- Create: `orchestrator/tests/test_connection.py`

Two context-managed connections: `factory_conn(factory_root)` and `project_conn(factory_root, project_dir_name)`. Both auto-create the DB and apply the schema if missing.

- [ ] **Step 1: Failing tests**

`orchestrator/tests/test_connection.py`:

```python
from pathlib import Path

from factory.state.connection import factory_conn, project_conn


def test_factory_conn_creates_db(tmp_path: Path):
    with factory_conn(tmp_path) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
        names = {r[0] for r in rows}
        assert "projects" in names
    assert (tmp_path / "state" / "factory.duckdb").exists()


def test_factory_conn_idempotent_open(tmp_path: Path):
    with factory_conn(tmp_path) as conn:
        conn.execute(
            "INSERT INTO projects (id, name, path, status, port_range_low, "
            "port_range_high, created_at, updated_at) "
            "VALUES ('PROJ-001', 'a', 'projects/a', 'active', 8000, 8099, NOW(), NOW())"
        )
    # Re-open: data persisted, schema didn't blow away
    with factory_conn(tmp_path) as conn:
        rows = conn.execute("SELECT id FROM projects").fetchall()
        assert rows == [("PROJ-001",)]


def test_project_conn_creates_db(tmp_path: Path):
    with project_conn(tmp_path, "PROJ-001-example") as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
        names = {r[0] for r in rows}
        assert "tasks" in names
        assert "agent_runs" in names
    assert (tmp_path / "projects" / "PROJ-001-example" / "state" / "project.duckdb").exists()


def test_project_conn_isolation(tmp_path: Path):
    with project_conn(tmp_path, "PROJ-001-example") as conn:
        conn.execute(
            "INSERT INTO tasks (id, story_id, project_id, title, status, "
            "created_at, updated_at) "
            "VALUES ('T-0001', 'US-0001', 'PROJ-001', 'a', 'ready', NOW(), NOW())"
        )
    with project_conn(tmp_path, "PROJ-002-other") as conn:
        rows = conn.execute("SELECT id FROM tasks").fetchall()
        assert rows == []
    with project_conn(tmp_path, "PROJ-001-example") as conn:
        rows = conn.execute("SELECT id FROM tasks").fetchall()
        assert rows == [("T-0001",)]
```

- [ ] **Step 2: Run tests, expect failure**

```bash
uv run pytest tests/test_connection.py -v
```

- [ ] **Step 3: Implement `orchestrator/src/factory/state/connection.py`**

```python
"""DuckDB connection context managers for factory + project DBs."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

from factory.io import ensure_dir
from factory.paths import factory_state_db_path, project_state_db_path
from factory.state.schemas import FACTORY_SCHEMA_SQL, PROJECT_SCHEMA_SQL


@contextmanager
def factory_conn(factory_root: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open the factory-level DuckDB. Creates and migrates if missing."""
    db = factory_state_db_path(factory_root)
    ensure_dir(db.parent)
    conn = duckdb.connect(str(db))
    try:
        conn.execute(FACTORY_SCHEMA_SQL)
        yield conn
    finally:
        conn.close()


@contextmanager
def project_conn(
    factory_root: Path, project_dir_name: str
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open a project-level DuckDB. Creates and migrates if missing."""
    db = project_state_db_path(factory_root, project_dir_name)
    ensure_dir(db.parent)
    conn = duckdb.connect(str(db))
    try:
        conn.execute(PROJECT_SCHEMA_SQL)
        yield conn
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_connection.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/src/factory/state/connection.py orchestrator/tests/test_connection.py
git commit -m "feat(state): factory_conn + project_conn context managers"
```

---

## Task 10: Factory DB queries

**Files:**
- Create: `orchestrator/src/factory/state/factory_db.py`
- Create: `orchestrator/tests/test_factory_db.py`

Operations needed at this layer (other queries can be added in later plans):

- `register_project(conn, project_id, name, path, port_range_low, port_range_high) -> None`
- `get_project(conn, project_id) -> ProjectRow | None`
- `list_projects(conn) -> list[ProjectRow]`
- `port_range_overlaps(conn, low, high) -> bool`
- `create_story(conn, story_id, project_id, type, title, source_request_id) -> None`
- `bump_story_version(conn, story_id) -> int`
- `get_story(conn, story_id) -> StoryRow | None`
- `list_stories(conn, *, project_id=None, status=None) -> list[StoryRow]`
- `enqueue_request(conn, request_id, raw_text) -> None`
- `claim_next_request(conn) -> RequestRow | None` (sets status=classifying)
- `mark_request_routed(conn, request_id, project_id) -> None`
- `mark_request_rejected(conn, request_id, notes) -> None`

Rows are returned as immutable dataclasses (or Pydantic models). Pick dataclasses for simplicity.

- [ ] **Step 1: Failing tests**

`orchestrator/tests/test_factory_db.py`:

```python
from datetime import datetime
from pathlib import Path

import pytest

from factory.state.connection import factory_conn
from factory.state.factory_db import (
    ProjectRow,
    RequestRow,
    StoryRow,
    bump_story_version,
    claim_next_request,
    create_story,
    enqueue_request,
    get_project,
    get_story,
    list_projects,
    list_stories,
    mark_request_rejected,
    mark_request_routed,
    port_range_overlaps,
    register_project,
)


def test_register_and_get_project(tmp_path: Path):
    with factory_conn(tmp_path) as conn:
        register_project(
            conn,
            project_id="PROJ-001",
            name="example",
            path="projects/PROJ-001-example",
            port_range_low=8000,
            port_range_high=8099,
        )
        row = get_project(conn, "PROJ-001")
        assert row is not None
        assert row.id == "PROJ-001"
        assert row.port_range_low == 8000


def test_list_projects(tmp_path: Path):
    with factory_conn(tmp_path) as conn:
        register_project(conn, "PROJ-001", "a", "projects/a", 8000, 8099)
        register_project(conn, "PROJ-002", "b", "projects/b", 8100, 8199)
        rows = list_projects(conn)
        assert {r.id for r in rows} == {"PROJ-001", "PROJ-002"}


def test_port_range_overlap_detected(tmp_path: Path):
    with factory_conn(tmp_path) as conn:
        register_project(conn, "PROJ-001", "a", "projects/a", 8000, 8099)
        assert port_range_overlaps(conn, 8050, 8150) is True
        assert port_range_overlaps(conn, 8100, 8199) is False


def test_create_and_get_story(tmp_path: Path):
    with factory_conn(tmp_path) as conn:
        register_project(conn, "PROJ-001", "a", "projects/a", 8000, 8099)
        create_story(
            conn,
            story_id="US-0001",
            project_id="PROJ-001",
            type_="feature",
            title="Add /health",
            source_request_id=None,
        )
        s = get_story(conn, "US-0001")
        assert s is not None
        assert s.title == "Add /health"
        assert s.current_version == 1


def test_bump_story_version(tmp_path: Path):
    with factory_conn(tmp_path) as conn:
        register_project(conn, "PROJ-001", "a", "projects/a", 8000, 8099)
        create_story(conn, "US-0001", "PROJ-001", "feature", "x", None)
        v = bump_story_version(conn, "US-0001")
        assert v == 2
        s = get_story(conn, "US-0001")
        assert s is not None and s.current_version == 2


def test_list_stories_filter(tmp_path: Path):
    with factory_conn(tmp_path) as conn:
        register_project(conn, "PROJ-001", "a", "projects/a", 8000, 8099)
        register_project(conn, "PROJ-002", "b", "projects/b", 8100, 8199)
        create_story(conn, "US-0001", "PROJ-001", "feature", "x", None)
        create_story(conn, "US-0002", "PROJ-002", "bug", "y", None)
        rows = list_stories(conn, project_id="PROJ-002")
        assert [r.id for r in rows] == ["US-0002"]


def test_request_lifecycle(tmp_path: Path):
    with factory_conn(tmp_path) as conn:
        register_project(conn, "PROJ-001", "a", "projects/a", 8000, 8099)
        enqueue_request(conn, "REQ-0001", "please add a /health endpoint")
        r = claim_next_request(conn)
        assert r is not None
        assert r.id == "REQ-0001"
        assert r.status == "classifying"
        # second claim returns nothing
        assert claim_next_request(conn) is None
        mark_request_routed(conn, "REQ-0001", "PROJ-001")
        # already routed, not claimable again
        assert claim_next_request(conn) is None


def test_request_rejected(tmp_path: Path):
    with factory_conn(tmp_path) as conn:
        enqueue_request(conn, "REQ-0001", "spam")
        claim_next_request(conn)
        mark_request_rejected(conn, "REQ-0001", "input flagged")
```

- [ ] **Step 2: Run tests, expect failure**

```bash
uv run pytest tests/test_factory_db.py -v
```

- [ ] **Step 3: Implement `orchestrator/src/factory/state/factory_db.py`**

```python
"""Factory-level DuckDB queries.

All functions take an explicit ``conn`` so transactions can be
composed by callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import duckdb


@dataclass(frozen=True)
class ProjectRow:
    id: str
    name: str
    path: str
    status: str
    port_range_low: int
    port_range_high: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class StoryRow:
    id: str
    project_id: str
    type: str
    title: str
    status: str
    current_version: int
    priority: str | None
    source_request_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class RequestRow:
    id: str
    raw_text: str
    project_id: str | None
    submitted_at: datetime
    status: str
    routed_to: str | None
    notes: str | None


def register_project(
    conn: duckdb.DuckDBPyConnection,
    *,
    project_id: str,
    name: str,
    path: str,
    port_range_low: int,
    port_range_high: int,
) -> None:
    conn.execute(
        """
        INSERT INTO projects
            (id, name, path, status, port_range_low, port_range_high, created_at, updated_at)
        VALUES (?, ?, ?, 'active', ?, ?, NOW(), NOW())
        """,
        [project_id, name, path, port_range_low, port_range_high],
    )


def get_project(conn: duckdb.DuckDBPyConnection, project_id: str) -> Optional[ProjectRow]:
    row = conn.execute(
        "SELECT id, name, path, status, port_range_low, port_range_high, created_at, updated_at "
        "FROM projects WHERE id = ?",
        [project_id],
    ).fetchone()
    if row is None:
        return None
    return ProjectRow(*row)


def list_projects(conn: duckdb.DuckDBPyConnection) -> list[ProjectRow]:
    rows = conn.execute(
        "SELECT id, name, path, status, port_range_low, port_range_high, created_at, updated_at "
        "FROM projects ORDER BY id"
    ).fetchall()
    return [ProjectRow(*r) for r in rows]


def port_range_overlaps(
    conn: duckdb.DuckDBPyConnection, low: int, high: int
) -> bool:
    """Return True iff [low, high] overlaps any existing project's range."""
    row = conn.execute(
        """
        SELECT 1 FROM projects
        WHERE NOT (port_range_high < ? OR port_range_low > ?)
        LIMIT 1
        """,
        [low, high],
    ).fetchone()
    return row is not None


def create_story(
    conn: duckdb.DuckDBPyConnection,
    *,
    story_id: str,
    project_id: str,
    type_: str,
    title: str,
    source_request_id: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO stories
            (id, project_id, type, title, status, current_version, source_request_id,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, 'draft', 1, ?, NOW(), NOW())
        """,
        [story_id, project_id, type_, title, source_request_id],
    )


def bump_story_version(conn: duckdb.DuckDBPyConnection, story_id: str) -> int:
    new_v = conn.execute(
        "UPDATE stories SET current_version = current_version + 1, updated_at = NOW() "
        "WHERE id = ? RETURNING current_version",
        [story_id],
    ).fetchone()
    if new_v is None:
        raise KeyError(f"story not found: {story_id}")
    return int(new_v[0])


def get_story(conn: duckdb.DuckDBPyConnection, story_id: str) -> StoryRow | None:
    row = conn.execute(
        "SELECT id, project_id, type, title, status, current_version, priority, "
        "source_request_id, created_at, updated_at "
        "FROM stories WHERE id = ?",
        [story_id],
    ).fetchone()
    return StoryRow(*row) if row else None


def list_stories(
    conn: duckdb.DuckDBPyConnection,
    *,
    project_id: str | None = None,
    status: str | None = None,
) -> list[StoryRow]:
    sql = (
        "SELECT id, project_id, type, title, status, current_version, priority, "
        "source_request_id, created_at, updated_at FROM stories WHERE 1=1"
    )
    args: list = []
    if project_id is not None:
        sql += " AND project_id = ?"
        args.append(project_id)
    if status is not None:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY id"
    return [StoryRow(*r) for r in conn.execute(sql, args).fetchall()]


def enqueue_request(
    conn: duckdb.DuckDBPyConnection, request_id: str, raw_text: str
) -> None:
    conn.execute(
        "INSERT INTO requests (id, raw_text, submitted_at, status) VALUES (?, ?, NOW(), 'queued')",
        [request_id, raw_text],
    )


def claim_next_request(conn: duckdb.DuckDBPyConnection) -> RequestRow | None:
    """Atomically pick the oldest queued request and mark it 'classifying'."""
    row = conn.execute(
        """
        UPDATE requests
        SET status = 'classifying'
        WHERE id = (
            SELECT id FROM requests WHERE status = 'queued'
            ORDER BY submitted_at ASC LIMIT 1
        )
        RETURNING id, raw_text, project_id, submitted_at, status, routed_to, notes
        """,
    ).fetchone()
    return RequestRow(*row) if row else None


def mark_request_routed(
    conn: duckdb.DuckDBPyConnection, request_id: str, project_id: str
) -> None:
    conn.execute(
        "UPDATE requests SET status='routed', routed_to=?, project_id=? WHERE id=?",
        [project_id, project_id, request_id],
    )


def mark_request_rejected(
    conn: duckdb.DuckDBPyConnection, request_id: str, notes: str
) -> None:
    conn.execute(
        "UPDATE requests SET status='rejected', notes=? WHERE id=?",
        [notes, request_id],
    )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_factory_db.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/src/factory/state/factory_db.py orchestrator/tests/test_factory_db.py
git commit -m "feat(state): factory DB queries — projects, stories, requests"
```

---

## Task 11: Project DB queries

**Files:**
- Create: `orchestrator/src/factory/state/project_db.py`
- Create: `orchestrator/tests/test_project_db.py`

Operations needed:

- `create_task(conn, task_id, story_id, project_id, title) -> None`
- `get_task(conn, task_id) -> TaskRow | None`
- `list_tasks(conn, *, status=None) -> list[TaskRow]`
- `set_task_stage(conn, task_id, stage) -> None`
- `acquire_lock(conn, task_id, run_id) -> bool` — atomic lock acquisition; returns False if any task in the project is currently locked
- `release_lock(conn, task_id) -> None`
- `lock_holder(conn) -> str | None` — returns the locked task_id or None
- `record_agent_run(conn, run_id, task_id, agent_name, verdict, summary_path, handoff_path, started_at, finished_at, tokens_used) -> None`
- `list_agent_runs(conn, task_id) -> list[AgentRunRow]`
- `add_token_usage(conn, task_id, tokens) -> None`
- `increment_remediation_loops(conn, task_id) -> int`

- [ ] **Step 1: Failing tests**

`orchestrator/tests/test_project_db.py`:

```python
from datetime import datetime
from pathlib import Path

import pytest

from factory.state.connection import project_conn
from factory.state.project_db import (
    AgentRunRow,
    TaskRow,
    acquire_lock,
    add_token_usage,
    create_task,
    get_task,
    increment_remediation_loops,
    list_agent_runs,
    list_tasks,
    lock_holder,
    record_agent_run,
    release_lock,
    set_task_stage,
)


def _setup_task(conn) -> str:
    create_task(
        conn,
        task_id="T-0001",
        story_id="US-0001",
        project_id="PROJ-001",
        title="Add /health",
    )
    return "T-0001"


def test_create_and_get_task(tmp_path: Path):
    with project_conn(tmp_path, "PROJ-001-example") as conn:
        _setup_task(conn)
        t = get_task(conn, "T-0001")
        assert t is not None
        assert t.title == "Add /health"
        assert t.status == "ready"


def test_list_tasks(tmp_path: Path):
    with project_conn(tmp_path, "PROJ-001-example") as conn:
        _setup_task(conn)
        create_task(conn, "T-0002", "US-0001", "PROJ-001", "x")
        rows = list_tasks(conn)
        assert {r.id for r in rows} == {"T-0001", "T-0002"}


def test_set_task_stage(tmp_path: Path):
    with project_conn(tmp_path, "PROJ-001-example") as conn:
        _setup_task(conn)
        set_task_stage(conn, "T-0001", "architect-agent")
        t = get_task(conn, "T-0001")
        assert t is not None and t.current_stage == "architect-agent"


def test_lock_acquire_release(tmp_path: Path):
    with project_conn(tmp_path, "PROJ-001-example") as conn:
        _setup_task(conn)
        assert acquire_lock(conn, "T-0001", "AR-000001") is True
        assert lock_holder(conn) == "T-0001"
        release_lock(conn, "T-0001")
        assert lock_holder(conn) is None


def test_lock_blocked_when_another_held(tmp_path: Path):
    with project_conn(tmp_path, "PROJ-001-example") as conn:
        create_task(conn, "T-0001", "US-0001", "PROJ-001", "a")
        create_task(conn, "T-0002", "US-0001", "PROJ-001", "b")
        assert acquire_lock(conn, "T-0001", "AR-000001") is True
        assert acquire_lock(conn, "T-0002", "AR-000002") is False
        assert lock_holder(conn) == "T-0001"


def test_record_and_list_agent_runs(tmp_path: Path):
    with project_conn(tmp_path, "PROJ-001-example") as conn:
        _setup_task(conn)
        record_agent_run(
            conn,
            run_id="AR-000001",
            task_id="T-0001",
            agent_name="architect-agent",
            verdict="pass",
            summary_path="docs/pipeline/T-0001/architect-agent.summary.md",
            handoff_path="docs/pipeline/T-0001/architect-agent.handoff.json",
            tokens_used=4321,
        )
        runs = list_agent_runs(conn, "T-0001")
        assert len(runs) == 1
        assert runs[0].agent_name == "architect-agent"
        assert runs[0].finished_at is not None


def test_token_usage_and_remediation_counters(tmp_path: Path):
    with project_conn(tmp_path, "PROJ-001-example") as conn:
        _setup_task(conn)
        add_token_usage(conn, "T-0001", 100)
        add_token_usage(conn, "T-0001", 250)
        t = get_task(conn, "T-0001")
        assert t is not None and t.token_budget_used == 350
        n = increment_remediation_loops(conn, "T-0001")
        assert n == 1
        n = increment_remediation_loops(conn, "T-0001")
        assert n == 2
```

- [ ] **Step 2: Run tests, expect failure**

```bash
uv run pytest tests/test_project_db.py -v
```

- [ ] **Step 3: Implement `orchestrator/src/factory/state/project_db.py`**

```python
"""Project-level DuckDB queries.

All functions take an explicit ``conn`` (project DB) so transactions
can be composed by callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import duckdb


@dataclass(frozen=True)
class TaskRow:
    id: str
    story_id: str
    project_id: str
    title: str
    status: str
    current_stage: str | None
    blocked_by: str | None
    remediation_loops_used: int
    token_budget_used: int
    locked_at: datetime | None
    locked_by_run_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AgentRunRow:
    id: str
    task_id: str
    agent_name: str
    verdict: str
    started_at: datetime
    finished_at: datetime | None
    summary_path: str | None
    handoff_path: str | None
    tokens_used: int


def create_task(
    conn: duckdb.DuckDBPyConnection,
    *,
    task_id: str,
    story_id: str,
    project_id: str,
    title: str,
) -> None:
    conn.execute(
        """
        INSERT INTO tasks
            (id, story_id, project_id, title, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'ready', NOW(), NOW())
        """,
        [task_id, story_id, project_id, title],
    )


def get_task(conn: duckdb.DuckDBPyConnection, task_id: str) -> TaskRow | None:
    row = conn.execute(
        """
        SELECT id, story_id, project_id, title, status, current_stage, blocked_by,
               remediation_loops_used, token_budget_used, locked_at, locked_by_run_id,
               created_at, updated_at
        FROM tasks WHERE id = ?
        """,
        [task_id],
    ).fetchone()
    return TaskRow(*row) if row else None


def list_tasks(
    conn: duckdb.DuckDBPyConnection, *, status: str | None = None
) -> list[TaskRow]:
    sql = (
        "SELECT id, story_id, project_id, title, status, current_stage, blocked_by, "
        "remediation_loops_used, token_budget_used, locked_at, locked_by_run_id, "
        "created_at, updated_at FROM tasks"
    )
    args: list = []
    if status is not None:
        sql += " WHERE status = ?"
        args.append(status)
    sql += " ORDER BY id"
    return [TaskRow(*r) for r in conn.execute(sql, args).fetchall()]


def set_task_stage(
    conn: duckdb.DuckDBPyConnection, task_id: str, stage: str
) -> None:
    conn.execute(
        "UPDATE tasks SET current_stage = ?, updated_at = NOW() WHERE id = ?",
        [stage, task_id],
    )


def acquire_lock(
    conn: duckdb.DuckDBPyConnection, task_id: str, run_id: str
) -> bool:
    """Atomically acquire the project-wide single-task lock.

    Returns True if the lock was acquired by this task, False otherwise.
    """
    # Refuse if any other task currently holds the lock
    holder = lock_holder(conn)
    if holder is not None and holder != task_id:
        return False
    conn.execute(
        "UPDATE tasks SET locked_at = NOW(), locked_by_run_id = ?, updated_at = NOW() "
        "WHERE id = ? AND locked_at IS NULL",
        [run_id, task_id],
    )
    # Verify
    return lock_holder(conn) == task_id


def release_lock(conn: duckdb.DuckDBPyConnection, task_id: str) -> None:
    conn.execute(
        "UPDATE tasks SET locked_at = NULL, locked_by_run_id = NULL, updated_at = NOW() "
        "WHERE id = ?",
        [task_id],
    )


def lock_holder(conn: duckdb.DuckDBPyConnection) -> str | None:
    row = conn.execute(
        "SELECT id FROM tasks WHERE locked_at IS NOT NULL ORDER BY locked_at LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def record_agent_run(
    conn: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    task_id: str,
    agent_name: str,
    verdict: str,
    summary_path: str,
    handoff_path: str,
    tokens_used: int,
) -> None:
    conn.execute(
        """
        INSERT INTO agent_runs
            (id, task_id, agent_name, verdict, started_at, finished_at,
             summary_path, handoff_path, tokens_used)
        VALUES (?, ?, ?, ?, NOW(), NOW(), ?, ?, ?)
        """,
        [run_id, task_id, agent_name, verdict, summary_path, handoff_path, tokens_used],
    )


def list_agent_runs(
    conn: duckdb.DuckDBPyConnection, task_id: str
) -> list[AgentRunRow]:
    rows = conn.execute(
        """
        SELECT id, task_id, agent_name, verdict, started_at, finished_at,
               summary_path, handoff_path, tokens_used
        FROM agent_runs WHERE task_id = ? ORDER BY started_at
        """,
        [task_id],
    ).fetchall()
    return [AgentRunRow(*r) for r in rows]


def add_token_usage(
    conn: duckdb.DuckDBPyConnection, task_id: str, tokens: int
) -> None:
    conn.execute(
        "UPDATE tasks SET token_budget_used = token_budget_used + ?, updated_at = NOW() "
        "WHERE id = ?",
        [tokens, task_id],
    )


def increment_remediation_loops(
    conn: duckdb.DuckDBPyConnection, task_id: str
) -> int:
    row = conn.execute(
        "UPDATE tasks SET remediation_loops_used = remediation_loops_used + 1, "
        "updated_at = NOW() WHERE id = ? RETURNING remediation_loops_used",
        [task_id],
    ).fetchone()
    if row is None:
        raise KeyError(f"task not found: {task_id}")
    return int(row[0])
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_project_db.py -v
```

- [ ] **Step 5: Commit**

```bash
git add orchestrator/src/factory/state/project_db.py orchestrator/tests/test_project_db.py
git commit -m "feat(state): project DB queries — tasks, locks, agent_runs, budgets"
```

---

## Task 12: Story file writers (versioned + symlink)

**Files:**
- Create: `orchestrator/src/factory/artifacts.py`
- Create: `orchestrator/tests/test_artifacts_story.py`

`write_story_v1(factory_root, story, body)` creates `stories/US-XXXX/`, writes `US-XXXX.v1.md`, sets `current` symlink.

`append_story_version(factory_root, story_id, story, body)` infers next version, writes `US-XXXX.v{N}.md`, updates `current` symlink.

- [ ] **Step 1: Failing tests**

`orchestrator/tests/test_artifacts_story.py`:

```python
import os
from pathlib import Path

import pytest

from factory.artifacts import (
    append_story_version,
    read_current_story,
    write_story_v1,
)
from factory.models.story import Story


def _story(version: int = 1) -> Story:
    return Story.model_validate(
        {
            "id": "US-0001",
            "type": "feature",
            "status": "draft",
            "title": "Add /health",
            "version": version,
            "linked_requests": ["REQ-0001"],
            "task_ids": [],
            "acceptance_criteria": ["GET /health returns 200"],
            "non_goals": [],
            "assumptions": [],
            "risks": [],
        }
    )


def test_write_story_v1_creates_files(tmp_path: Path):
    write_story_v1(tmp_path, _story(), body="# US-0001\n\n## Problem\nx\n")
    assert (tmp_path / "stories" / "US-0001" / "US-0001.v1.md").exists()
    link = tmp_path / "stories" / "US-0001" / "current"
    assert link.is_symlink()
    assert os.readlink(link) == "US-0001.v1.md"


def test_write_story_v1_with_existing_dir_raises(tmp_path: Path):
    (tmp_path / "stories" / "US-0001").mkdir(parents=True)
    with pytest.raises(FileExistsError):
        write_story_v1(tmp_path, _story(), body="x")


def test_append_story_version_increments(tmp_path: Path):
    write_story_v1(tmp_path, _story(version=1), body="x")
    s2 = _story(version=2)
    s2 = s2.model_copy(update={"title": "Add /health (revised)"})
    append_story_version(tmp_path, s2, body="x revised")
    assert (tmp_path / "stories" / "US-0001" / "US-0001.v2.md").exists()
    assert os.readlink(tmp_path / "stories" / "US-0001" / "current") == "US-0001.v2.md"


def test_read_current_story(tmp_path: Path):
    write_story_v1(tmp_path, _story(), body="# x\n## Problem\nbody\n")
    s, body = read_current_story(tmp_path, "US-0001")
    assert s.id == "US-0001"
    assert "Problem" in body


def test_append_story_version_mismatched_version_raises(tmp_path: Path):
    write_story_v1(tmp_path, _story(version=1), body="x")
    bad = _story(version=99)
    with pytest.raises(ValueError, match="version"):
        append_story_version(tmp_path, bad, body="x")
```

- [ ] **Step 2: Run tests, expect failure**

```bash
uv run pytest tests/test_artifacts_story.py -v
```

- [ ] **Step 3: Implement story writers in `orchestrator/src/factory/artifacts.py`**

```python
"""Filesystem writers for stories, tasks, and pipeline artifacts."""

from __future__ import annotations

from pathlib import Path

from factory.io import atomic_write_text, ensure_dir, read_text, update_symlink
from factory.models.story import Story, parse_story_markdown, serialize_story_markdown
from factory.paths import story_current_symlink, story_dir, story_version_path


def write_story_v1(factory_root: Path, story: Story, body: str) -> Path:
    """Write the first version of a story. Creates stories/<id>/ and current symlink."""
    if story.version != 1:
        raise ValueError(f"write_story_v1 requires version=1, got {story.version}")
    sdir = story_dir(factory_root, story.id)
    if sdir.exists():
        raise FileExistsError(f"story directory already exists: {sdir}")
    ensure_dir(sdir)
    target = story_version_path(factory_root, story.id, 1)
    atomic_write_text(target, serialize_story_markdown(story, body))
    update_symlink(story_current_symlink(factory_root, story.id), target)
    return target


def append_story_version(factory_root: Path, story: Story, body: str) -> Path:
    """Append a new version to an existing story. Updates ``current`` symlink."""
    sdir = story_dir(factory_root, story.id)
    if not sdir.is_dir():
        raise FileNotFoundError(f"story directory missing: {sdir}")
    existing = sorted(sdir.glob(f"{story.id}.v*.md"))
    next_version = len(existing) + 1
    if story.version != next_version:
        raise ValueError(
            f"version mismatch: filesystem expects v{next_version}, story declares v{story.version}"
        )
    target = story_version_path(factory_root, story.id, next_version)
    atomic_write_text(target, serialize_story_markdown(story, body))
    update_symlink(story_current_symlink(factory_root, story.id), target)
    return target


def read_current_story(factory_root: Path, story_id: str) -> tuple[Story, str]:
    """Read the story currently pointed at by the ``current`` symlink."""
    link = story_current_symlink(factory_root, story_id)
    if not link.exists():
        raise FileNotFoundError(f"current symlink missing: {link}")
    text = read_text(link)
    return parse_story_markdown(text)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_artifacts_story.py -v
```

- [ ] **Step 5: Commit**

```bash
git add orchestrator/src/factory/artifacts.py orchestrator/tests/test_artifacts_story.py
git commit -m "feat(artifacts): write_story_v1 + append_story_version + read_current_story"
```

---

## Task 13: Task and pipeline artifact writers

**Files:**
- Modify: `orchestrator/src/factory/artifacts.py`
- Create: `orchestrator/tests/test_artifacts_task.py`
- Create: `orchestrator/tests/test_artifacts_pipeline.py`

Add to `artifacts.py`:

- `write_task(factory_root, project_dir_name, task, body)` — `projects/<dir>/docs/work/tasks/T-XXXX.md`
- `read_task(factory_root, project_dir_name, task_id) -> (Task, body)`
- `write_handoff(factory_root, project_dir_name, handoff)` — validates, atomic-writes JSON, returns path
- `read_handoff(factory_root, project_dir_name, task_id, stage) -> Handoff`
- `write_summary(factory_root, project_dir_name, task_id, stage, text)` — atomic-writes the summary md
- `read_summary(factory_root, project_dir_name, task_id, stage) -> str`

- [ ] **Step 1: Failing tests for task artifacts**

`orchestrator/tests/test_artifacts_task.py`:

```python
from pathlib import Path

import pytest

from factory.artifacts import read_task, write_task
from factory.models.task import Task


def _task() -> Task:
    return Task.model_validate(
        {
            "id": "T-0001",
            "parent_story": "US-0001",
            "project_id": "PROJ-001",
            "title": "Implement /health",
            "status": "ready",
            "current_stage": None,
            "allowed_scope": ["src/proj001/main.py"],
            "forbidden_scope": [],
            "expected_output": ["new endpoint"],
            "completion_evidence": ["pytest passes"],
            "change_budget": "lines<=80",
            "max_remediation_loops": 3,
            "max_token_budget": 200000,
            "dependencies": [],
            "idempotency_rule": "Re-running overwrites the route definition.",
            "stop_conditions": [],
            "blocked_by": None,
        }
    )


def test_write_and_read_task(tmp_path: Path):
    write_task(tmp_path, "PROJ-001-example", _task(), body="# T-0001\n\n## Purpose\n")
    target = (
        tmp_path
        / "projects"
        / "PROJ-001-example"
        / "docs"
        / "work"
        / "tasks"
        / "T-0001.md"
    )
    assert target.exists()
    t, body = read_task(tmp_path, "PROJ-001-example", "T-0001")
    assert t.id == "T-0001"
    assert "Purpose" in body


def test_overwrite_task(tmp_path: Path):
    write_task(tmp_path, "PROJ-001-example", _task(), body="first")
    t = _task().model_copy(update={"title": "Implement /health (revised)"})
    write_task(tmp_path, "PROJ-001-example", t, body="second")
    t2, body = read_task(tmp_path, "PROJ-001-example", "T-0001")
    assert t2.title.endswith("(revised)")
    assert "second" in body
```

- [ ] **Step 2: Failing tests for pipeline artifacts**

`orchestrator/tests/test_artifacts_pipeline.py`:

```python
import json
from pathlib import Path

import pytest

from factory.artifacts import read_handoff, read_summary, write_handoff, write_summary
from factory.models.handoff import Handoff


def _handoff() -> Handoff:
    return Handoff.model_validate(
        {
            "work_id": "T-0001",
            "parent_story": "US-0001",
            "project_id": "PROJ-001",
            "stage": "coder-agent",
            "input_artifacts": [],
            "output_artifacts": [],
            "verdict": "complete",
            "blockers": [],
            "risks": [],
            "assumptions": [],
            "scope_deviations": [],
            "next_authorization": "tester-agent",
            "remediation_loop": 0,
            "tokens_used": 0,
            "defects_discovered": [],
        }
    )


def test_write_and_read_handoff(tmp_path: Path):
    path = write_handoff(tmp_path, "PROJ-001-example", _handoff())
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["work_id"] == "T-0001"
    assert data["stage"] == "coder-agent"
    h = read_handoff(tmp_path, "PROJ-001-example", "T-0001", "coder-agent")
    assert h == _handoff()


def test_handoff_at_canonical_path(tmp_path: Path):
    path = write_handoff(tmp_path, "PROJ-001-example", _handoff())
    expected = (
        tmp_path
        / "projects"
        / "PROJ-001-example"
        / "docs"
        / "pipeline"
        / "T-0001"
        / "coder-agent.handoff.json"
    )
    assert path == expected


def test_write_summary(tmp_path: Path):
    target = write_summary(
        tmp_path,
        "PROJ-001-example",
        "T-0001",
        "coder-agent",
        text="## Coder Summary\n- Verdict: complete\n",
    )
    assert target.exists()
    assert read_summary(tmp_path, "PROJ-001-example", "T-0001", "coder-agent").startswith(
        "## Coder Summary"
    )


def test_read_handoff_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        read_handoff(tmp_path, "PROJ-001-example", "T-9999", "coder-agent")
```

- [ ] **Step 3: Run tests, expect failure**

```bash
uv run pytest tests/test_artifacts_task.py tests/test_artifacts_pipeline.py -v
```

- [ ] **Step 4: Extend `orchestrator/src/factory/artifacts.py`**

Append to the existing file:

```python
from factory.io import atomic_write_json, read_json
from factory.models.handoff import Handoff
from factory.models.task import Task, parse_task_markdown, serialize_task_markdown
from factory.paths import (
    handoff_path as _handoff_path,
    project_task_file_path,
    summary_path as _summary_path,
)


def write_task(
    factory_root: Path,
    project_dir_name: str,
    task: Task,
    body: str,
) -> Path:
    """Write/overwrite a task markdown file at the canonical project path."""
    target = project_task_file_path(factory_root, project_dir_name, task.id)
    atomic_write_text(target, serialize_task_markdown(task, body))
    return target


def read_task(
    factory_root: Path, project_dir_name: str, task_id: str
) -> tuple[Task, str]:
    target = project_task_file_path(factory_root, project_dir_name, task_id)
    if not target.exists():
        raise FileNotFoundError(f"task file missing: {target}")
    return parse_task_markdown(read_text(target))


def write_handoff(
    factory_root: Path, project_dir_name: str, handoff: Handoff
) -> Path:
    """Atomically write a validated Handoff to its canonical path."""
    target = _handoff_path(
        factory_root, project_dir_name, handoff.work_id, handoff.stage.value
    )
    atomic_write_json(target, handoff.model_dump(mode="json"))
    return target


def read_handoff(
    factory_root: Path,
    project_dir_name: str,
    task_id: str,
    stage: str,
) -> Handoff:
    target = _handoff_path(factory_root, project_dir_name, task_id, stage)
    if not target.exists():
        raise FileNotFoundError(f"handoff missing: {target}")
    return Handoff.model_validate(read_json(target))


def write_summary(
    factory_root: Path,
    project_dir_name: str,
    task_id: str,
    stage: str,
    *,
    text: str,
) -> Path:
    target = _summary_path(factory_root, project_dir_name, task_id, stage)
    atomic_write_text(target, text)
    return target


def read_summary(
    factory_root: Path,
    project_dir_name: str,
    task_id: str,
    stage: str,
) -> str:
    target = _summary_path(factory_root, project_dir_name, task_id, stage)
    if not target.exists():
        raise FileNotFoundError(f"summary missing: {target}")
    return read_text(target)
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_artifacts_task.py tests/test_artifacts_pipeline.py -v
```

- [ ] **Step 6: Commit**

```bash
git add orchestrator/src/factory/artifacts.py orchestrator/tests/test_artifacts_task.py orchestrator/tests/test_artifacts_pipeline.py
git commit -m "feat(artifacts): task + handoff + summary file writers/readers"
```

---

## Task 14: Reconciliation logic (DB ↔ filesystem)

**Files:**
- Create: `orchestrator/src/factory/state/reconcile.py`
- Create: `orchestrator/tests/test_reconcile.py`

`reconcile_task(factory_root, project_dir_name, conn, task_id) -> ReconcileReport` checks every `agent_runs` row for that task: does the file at `summary_path` exist? does the file at `handoff_path` exist? does the JSON parse? Are there pipeline files on disk that have no DB row?

- [ ] **Step 1: Failing tests**

`orchestrator/tests/test_reconcile.py`:

```python
from pathlib import Path

from factory.artifacts import write_handoff, write_summary
from factory.models.handoff import Handoff
from factory.state.connection import project_conn
from factory.state.project_db import create_task, record_agent_run
from factory.state.reconcile import ReconcileReport, reconcile_task


def _good_handoff() -> Handoff:
    return Handoff.model_validate(
        {
            "work_id": "T-0001",
            "parent_story": "US-0001",
            "project_id": "PROJ-001",
            "stage": "architect-agent",
            "input_artifacts": [],
            "output_artifacts": [],
            "verdict": "pass",
            "blockers": [],
            "risks": [],
            "assumptions": [],
            "scope_deviations": [],
            "next_authorization": "boundary-agent",
            "remediation_loop": 0,
            "tokens_used": 0,
            "defects_discovered": [],
        }
    )


def _seed(tmp_path: Path):
    write_handoff(tmp_path, "PROJ-001-example", _good_handoff())
    write_summary(
        tmp_path,
        "PROJ-001-example",
        "T-0001",
        "architect-agent",
        text="## Architect Summary\n- Verdict: pass\n",
    )


def test_reconcile_clean(tmp_path: Path):
    _seed(tmp_path)
    with project_conn(tmp_path, "PROJ-001-example") as conn:
        create_task(conn, "T-0001", "US-0001", "PROJ-001", "x")
        record_agent_run(
            conn,
            run_id="AR-000001",
            task_id="T-0001",
            agent_name="architect-agent",
            verdict="pass",
            summary_path="projects/PROJ-001-example/docs/pipeline/T-0001/architect-agent.summary.md",
            handoff_path="projects/PROJ-001-example/docs/pipeline/T-0001/architect-agent.handoff.json",
            tokens_used=0,
        )
        report = reconcile_task(tmp_path, "PROJ-001-example", conn, "T-0001")
    assert report.is_clean()
    assert report.missing_files == []
    assert report.orphaned_files == []


def test_reconcile_missing_summary(tmp_path: Path):
    _seed(tmp_path)
    # Delete the summary file
    (
        tmp_path
        / "projects"
        / "PROJ-001-example"
        / "docs"
        / "pipeline"
        / "T-0001"
        / "architect-agent.summary.md"
    ).unlink()
    with project_conn(tmp_path, "PROJ-001-example") as conn:
        create_task(conn, "T-0001", "US-0001", "PROJ-001", "x")
        record_agent_run(
            conn,
            run_id="AR-000001",
            task_id="T-0001",
            agent_name="architect-agent",
            verdict="pass",
            summary_path="projects/PROJ-001-example/docs/pipeline/T-0001/architect-agent.summary.md",
            handoff_path="projects/PROJ-001-example/docs/pipeline/T-0001/architect-agent.handoff.json",
            tokens_used=0,
        )
        report = reconcile_task(tmp_path, "PROJ-001-example", conn, "T-0001")
    assert not report.is_clean()
    assert any("summary.md" in p for p in report.missing_files)


def test_reconcile_orphaned_handoff_file(tmp_path: Path):
    _seed(tmp_path)
    with project_conn(tmp_path, "PROJ-001-example") as conn:
        create_task(conn, "T-0001", "US-0001", "PROJ-001", "x")
        # No agent_run inserted -> the on-disk files are orphans
        report = reconcile_task(tmp_path, "PROJ-001-example", conn, "T-0001")
    assert not report.is_clean()
    assert any("architect-agent.handoff.json" in p for p in report.orphaned_files)


def test_reconcile_unparseable_handoff(tmp_path: Path):
    _seed(tmp_path)
    bad = (
        tmp_path
        / "projects"
        / "PROJ-001-example"
        / "docs"
        / "pipeline"
        / "T-0001"
        / "architect-agent.handoff.json"
    )
    bad.write_text("{not json")
    with project_conn(tmp_path, "PROJ-001-example") as conn:
        create_task(conn, "T-0001", "US-0001", "PROJ-001", "x")
        record_agent_run(
            conn,
            run_id="AR-000001",
            task_id="T-0001",
            agent_name="architect-agent",
            verdict="pass",
            summary_path="projects/PROJ-001-example/docs/pipeline/T-0001/architect-agent.summary.md",
            handoff_path="projects/PROJ-001-example/docs/pipeline/T-0001/architect-agent.handoff.json",
            tokens_used=0,
        )
        report = reconcile_task(tmp_path, "PROJ-001-example", conn, "T-0001")
    assert not report.is_clean()
    assert any("unparseable" in m or "invalid" in m for m in report.invalid_files)
```

- [ ] **Step 2: Run tests, expect failure**

```bash
uv run pytest tests/test_reconcile.py -v
```

- [ ] **Step 3: Implement `orchestrator/src/factory/state/reconcile.py`**

```python
"""DB ↔ filesystem reconciliation for a single task's pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from factory.io import read_json
from factory.models.handoff import Handoff
from factory.paths import pipeline_dir
from factory.state.project_db import list_agent_runs


@dataclass(frozen=True)
class ReconcileReport:
    task_id: str
    missing_files: list[str] = field(default_factory=list)
    orphaned_files: list[str] = field(default_factory=list)
    invalid_files: list[str] = field(default_factory=list)

    def is_clean(self) -> bool:
        return not (self.missing_files or self.orphaned_files or self.invalid_files)


def reconcile_task(
    factory_root: Path,
    project_dir_name: str,
    conn: duckdb.DuckDBPyConnection,
    task_id: str,
) -> ReconcileReport:
    runs = list_agent_runs(conn, task_id)

    expected_paths: set[str] = set()
    missing: list[str] = []
    invalid: list[str] = []
    for run in runs:
        for raw in (run.summary_path, run.handoff_path):
            if not raw:
                continue
            expected_paths.add(raw)
            absolute = factory_root / raw
            if not absolute.exists():
                missing.append(raw)
                continue
            if absolute.suffix == ".json":
                try:
                    data = read_json(absolute)
                    Handoff.model_validate(data)
                except Exception as exc:  # noqa: BLE001 — we want to log any parsing/validation problem
                    invalid.append(f"{raw}: {type(exc).__name__}: {exc}")

    pipe = pipeline_dir(factory_root, project_dir_name, task_id)
    orphans: list[str] = []
    if pipe.is_dir():
        for f in pipe.iterdir():
            if not f.is_file():
                continue
            rel = str(f.relative_to(factory_root))
            if rel not in expected_paths:
                orphans.append(rel)

    return ReconcileReport(
        task_id=task_id,
        missing_files=sorted(missing),
        orphaned_files=sorted(orphans),
        invalid_files=sorted(invalid),
    )
```

> **Note on invalid file detection:** The test asserts `"unparseable" in m or "invalid" in m`. Our message format is `"<path>: <ExcName>: <details>"`, which won't satisfy that assertion. Update the test to check for the exception class name instead, OR update the implementation to prefix invalid messages explicitly.

Update the test in step 1 (already written above) to use:

```python
    assert any("JSONDecodeError" in m or "invalid" in m for m in report.invalid_files)
```

(Edit the test file accordingly before running again.)

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_reconcile.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/src/factory/state/reconcile.py orchestrator/tests/test_reconcile.py
git commit -m "feat(state): reconcile_task — detect missing/orphaned/invalid pipeline files"
```

---

## Task 15: Public API + integration smoke test

**Files:**
- Modify: `orchestrator/src/factory/__init__.py`
- Create: `orchestrator/tests/test_public_api.py`
- Create: `orchestrator/tests/test_integration_smoke.py`

Re-export the major surface. Then write an integration test that exercises the entire public API in one realistic flow: register a project, create a story, write story v1, create a task, write the task, write a handoff + summary for a stage, record an agent_run, run reconcile, expect clean.

- [ ] **Step 1: Update `orchestrator/src/factory/__init__.py`**

```python
"""Software factory orchestrator (storage layer).

Public API surface for plans 2+ to consume.
"""

__version__ = "0.1.0"

from factory.artifacts import (
    append_story_version,
    read_current_story,
    read_handoff,
    read_summary,
    read_task,
    write_handoff,
    write_story_v1,
    write_summary,
    write_task,
)
from factory.ids import IdKind, format_id, parse_id
from factory.models.handoff import (
    DefectSeverity,
    DiscoveredDefect,
    Handoff,
    NextAuthorization,
    Stage,
    Verdict,
)
from factory.models.project_rules import (
    DatabaseEngine,
    ProjectRules,
    parse_project_rules_markdown,
)
from factory.models.story import (
    Story,
    StoryStatus,
    StoryType,
    parse_story_markdown,
    serialize_story_markdown,
)
from factory.models.task import (
    Task,
    TaskStatus,
    parse_task_markdown,
    serialize_task_markdown,
)
from factory.paths import (
    factory_state_db_path,
    factory_state_log_path,
    handoff_path,
    pipeline_dir,
    project_dir,
    project_repo_dir,
    project_secrets_dir,
    project_state_db_path,
    project_task_file_path,
    story_current_symlink,
    story_dir,
    story_version_path,
    summary_path,
)
from factory.state.connection import factory_conn, project_conn
from factory.state.factory_db import (
    ProjectRow,
    RequestRow,
    StoryRow,
    bump_story_version,
    claim_next_request,
    create_story,
    enqueue_request,
    get_project,
    get_story,
    list_projects,
    list_stories,
    mark_request_rejected,
    mark_request_routed,
    port_range_overlaps,
    register_project,
)
from factory.state.project_db import (
    AgentRunRow,
    TaskRow,
    acquire_lock,
    add_token_usage,
    create_task,
    get_task,
    increment_remediation_loops,
    list_agent_runs,
    list_tasks,
    lock_holder,
    record_agent_run,
    release_lock,
    set_task_stage,
)
from factory.state.reconcile import ReconcileReport, reconcile_task

__all__ = [
    # ids
    "IdKind", "format_id", "parse_id",
    # models
    "DefectSeverity", "DiscoveredDefect", "Handoff", "NextAuthorization", "Stage", "Verdict",
    "DatabaseEngine", "ProjectRules", "parse_project_rules_markdown",
    "Story", "StoryStatus", "StoryType", "parse_story_markdown", "serialize_story_markdown",
    "Task", "TaskStatus", "parse_task_markdown", "serialize_task_markdown",
    # paths
    "factory_state_db_path", "factory_state_log_path", "handoff_path", "pipeline_dir",
    "project_dir", "project_repo_dir", "project_secrets_dir", "project_state_db_path",
    "project_task_file_path", "story_current_symlink", "story_dir", "story_version_path",
    "summary_path",
    # state
    "factory_conn", "project_conn",
    "ProjectRow", "RequestRow", "StoryRow",
    "bump_story_version", "claim_next_request", "create_story", "enqueue_request",
    "get_project", "get_story", "list_projects", "list_stories",
    "mark_request_rejected", "mark_request_routed", "port_range_overlaps", "register_project",
    "AgentRunRow", "TaskRow",
    "acquire_lock", "add_token_usage", "create_task", "get_task",
    "increment_remediation_loops", "list_agent_runs", "list_tasks", "lock_holder",
    "record_agent_run", "release_lock", "set_task_stage",
    "ReconcileReport", "reconcile_task",
    # artifacts
    "append_story_version", "read_current_story", "read_handoff", "read_summary",
    "read_task", "write_handoff", "write_story_v1", "write_summary", "write_task",
]
```

- [ ] **Step 2: Add a public-API smoke test**

`orchestrator/tests/test_public_api.py`:

```python
def test_public_api_imports():
    import factory

    # spot-check critical exports
    assert hasattr(factory, "Handoff")
    assert hasattr(factory, "Story")
    assert hasattr(factory, "Task")
    assert hasattr(factory, "factory_conn")
    assert hasattr(factory, "project_conn")
    assert hasattr(factory, "write_handoff")
    assert hasattr(factory, "reconcile_task")
```

- [ ] **Step 3: Add the integration smoke test**

`orchestrator/tests/test_integration_smoke.py`:

```python
"""End-to-end exercise of the storage layer in one realistic flow.

This is the test we run after every refactor to verify the surface still
works as advertised.
"""

from pathlib import Path

import factory


def test_full_storage_flow(tmp_path: Path):
    # --- Register a project ---
    with factory.factory_conn(tmp_path) as conn:
        factory.register_project(
            conn,
            project_id="PROJ-001",
            name="example",
            path="projects/PROJ-001-example",
            port_range_low=8000,
            port_range_high=8099,
        )
        project = factory.get_project(conn, "PROJ-001")
        assert project is not None

    # --- Create + write the story ---
    story = factory.Story.model_validate(
        {
            "id": "US-0001",
            "type": "feature",
            "status": "draft",
            "title": "Add /health endpoint",
            "version": 1,
            "linked_requests": [],
            "task_ids": ["T-0001"],
            "acceptance_criteria": ["GET /health returns 200"],
            "non_goals": [],
            "assumptions": [],
            "risks": [],
        }
    )
    factory.write_story_v1(tmp_path, story, body="# US-0001\n\n## Problem\nNo health endpoint.\n")
    with factory.factory_conn(tmp_path) as conn:
        factory.create_story(
            conn,
            story_id="US-0001",
            project_id="PROJ-001",
            type_="feature",
            title="Add /health endpoint",
            source_request_id=None,
        )

    # --- Create + write the task ---
    task = factory.Task.model_validate(
        {
            "id": "T-0001",
            "parent_story": "US-0001",
            "project_id": "PROJ-001",
            "title": "Implement /health route",
            "status": "ready",
            "current_stage": None,
            "allowed_scope": ["src/proj001/main.py"],
            "forbidden_scope": [],
            "expected_output": ["new endpoint"],
            "completion_evidence": ["pytest passes"],
            "change_budget": "lines<=80",
            "max_remediation_loops": 3,
            "max_token_budget": 200000,
            "dependencies": [],
            "idempotency_rule": "Re-running overwrites the route.",
            "stop_conditions": [],
            "blocked_by": None,
        }
    )
    factory.write_task(
        tmp_path, "PROJ-001-example", task, body="# T-0001\n\n## Purpose\nWire /health.\n"
    )
    with factory.project_conn(tmp_path, "PROJ-001-example") as conn:
        factory.create_task(
            conn,
            task_id="T-0001",
            story_id="US-0001",
            project_id="PROJ-001",
            title="Implement /health route",
        )

    # --- Lock, run an agent, write its handoff + summary, record the run ---
    with factory.project_conn(tmp_path, "PROJ-001-example") as conn:
        assert factory.acquire_lock(conn, "T-0001", "AR-000001") is True
        factory.set_task_stage(conn, "T-0001", "architect-agent")

        handoff = factory.Handoff.model_validate(
            {
                "work_id": "T-0001",
                "parent_story": "US-0001",
                "project_id": "PROJ-001",
                "stage": "architect-agent",
                "input_artifacts": [],
                "output_artifacts": ["projects/PROJ-001-example/docs/architecture/adr/ADR-0001"],
                "verdict": "pass",
                "blockers": [],
                "risks": [],
                "assumptions": [],
                "scope_deviations": [],
                "next_authorization": "boundary-agent",
                "remediation_loop": 0,
                "tokens_used": 4321,
                "defects_discovered": [],
            }
        )
        factory.write_handoff(tmp_path, "PROJ-001-example", handoff)
        factory.write_summary(
            tmp_path,
            "PROJ-001-example",
            "T-0001",
            "architect-agent",
            text="## Architect Summary\n- Verdict: pass\n",
        )
        factory.record_agent_run(
            conn,
            run_id="AR-000001",
            task_id="T-0001",
            agent_name="architect-agent",
            verdict="pass",
            summary_path="projects/PROJ-001-example/docs/pipeline/T-0001/architect-agent.summary.md",
            handoff_path="projects/PROJ-001-example/docs/pipeline/T-0001/architect-agent.handoff.json",
            tokens_used=4321,
        )
        factory.add_token_usage(conn, "T-0001", 4321)

        # --- Reconcile: should be clean ---
        report = factory.reconcile_task(tmp_path, "PROJ-001-example", conn, "T-0001")
        assert report.is_clean(), report

        # --- Release the lock ---
        factory.release_lock(conn, "T-0001")
        assert factory.lock_holder(conn) is None
```

- [ ] **Step 4: Run all tests**

```bash
cd /Users/joaooliveira/dev/personal/factory/orchestrator
uv run pytest -v
```

Expected: every test from every prior task plus the two new tests pass.

- [ ] **Step 5: Run ruff one more time**

```bash
uv run ruff check .
uv run ruff format --check .
```

Expected: both clean.

- [ ] **Step 6: Get test coverage report**

```bash
uv run pytest --cov=factory --cov-report=term-missing
```

Expected: coverage > 90% for `factory.*` modules. Anything below 90% is a sign that a code path isn't exercised; add a test or remove the dead code.

- [ ] **Step 7: Commit**

```bash
git add orchestrator/src/factory/__init__.py \
        orchestrator/tests/test_public_api.py \
        orchestrator/tests/test_integration_smoke.py
git commit -m "feat(api): public re-exports + end-to-end storage smoke test"
```

---

## Plan 1 done

After Task 15 the storage foundation is complete. Plan 2 (opencode integration + agent role files) consumes this layer.

**To verify the plan landed correctly, run from the repo root:**

```bash
cd /Users/joaooliveira/dev/personal/factory
uv --project orchestrator run pytest -v
uv --project orchestrator run ruff check orchestrator
git log --oneline | head -20
```

You should see ~15 commits on top of the initial commit, all tests passing, and clean ruff output.

---

## Self-review notes (for the planner; remove before execution)

**Spec coverage:**

- ✅ §3 repo layout (orchestrator dir, state, stories, handoffs, projects)
- ✅ §8.1 schema (factory tables: projects, stories, requests, blockers; project tables: tasks, agent_runs, blockers, releases)
- ✅ §8.1 task additions (locked_at, locked_by_run_id, remediation_loops_used, token_budget_used)
- ✅ §8.2 source-of-truth rule (reconcile)
- ✅ §8.3 versioning (story v1/v2 + current symlink)
- ✅ §9 handoff schema (Pydantic + generated JSON Schema + example)
- ✅ §10.1 defects_discovered (in Handoff model)
- ✅ §10.2 remediation_loop, tokens_used (Handoff + tasks columns + queries)
- ✅ §10.3 single-project lock (acquire_lock, lock_holder)
- ✅ §11.3 port_range uniqueness constraint
- ✅ §11.4 database engine (ProjectRules.database_engine)
- ✅ §11.6 request queue (requests table + claim_next_request)
- ⏭ Deferred to Plan 2: opencode client, agent role files, prompt building, response parsing
- ⏭ Deferred to Plan 3: LangGraph, CLI, sample project, log emitter
- ⏭ Deferred to Plan 4: human interrupts, remediation loops, retry, human escalation, end-to-end

**Cross-task type consistency:**

- `factory.models.handoff.Handoff` referenced consistently across Tasks 4, 5, 13, 14, 15.
- `factory.models.story.Story` consistent across 6, 12, 15.
- `factory.models.task.Task` consistent across 6, 13, 15.
- `factory.state.factory_db.ProjectRow` etc. — defined in Task 10, used in Task 15.
- `factory.state.project_db.AgentRunRow`/`TaskRow` — defined in Task 11, used in Tasks 14 and 15.
- `pipeline_dir` is a public alias of `project_pipeline_dir` (defined in Task 2, used in Task 14).
- `_handoff_path`/`_summary_path` aliasing in `artifacts.py` (Task 13) avoids name collision with the function names from `paths` re-imported as private names.

**Placeholder scan:** none.

**Risk:** Pydantic + DuckDB `RETURNING` clause behavior is verified in DuckDB ≥ 0.9. Task 8's pyproject pins `duckdb>=1.1.0`.
