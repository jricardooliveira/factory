"""Golden prompts: the exact text each agent receives is pinned byte-for-byte.

Agent prompt TEXT is production behaviour — a stray newline or a reordered block
changes what the factory builds as surely as editing a gate. These tests drive
every agent node up to the single agent-call boundary, capture the prompt it
would have sent, and compare it with a committed golden file under
`tests/fixtures/prompts/`. A refactor of prompt assembly must leave every golden
identical; nothing here calls opencode.

A DELIBERATE prompt change (an edited agent policy, a new block) regenerates the
goldens, and the diff of `tests/fixtures/prompts/` is then the review artefact::

    FACTORY_UPDATE_GOLDEN=1 .venv/bin/python -m pytest tests/pipeline/prompts/test_prompt_golden.py
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from factory.pipeline import build_spec_prompt, build_tester_prompt
from factory.pipeline.nodes.architect import node_architect_agent
from factory.pipeline.nodes.coder import node_coder_agent
from factory.pipeline.nodes.spec import node_spec_agent
from factory.pipeline.nodes.tester import node_tester_agent
from factory.state import db

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "prompts"
UPDATE = os.environ.get("FACTORY_UPDATE_GOLDEN") == "1"

# Where the pipeline looks these names up. When prompt assembly moves, these
# follow the name to its new module — the goldens themselves must not change.
AGENT_BOUNDARY = "factory.pipeline.agent_calls._run_or_replay"
DIFF_LOOKUPS = (
    "factory.pipeline.prompts.tester.collect_repo_diff",
    "factory.pipeline.nodes.coder.collect_repo_diff",
)

PROJECT_SPEC = (
    "# Project: Ledger\n\n"
    "## Stack\n- python 3.12\n- sqlite\n\n"
    "## Forbidden\n- network calls at import time\n\n"
    "## NFRs\n- p95 latency under 200 ms\n"
)

SPEC = {
    "verdict": "pass",
    "story_id": "US-0099",
    "title": "Overdue invoices",
    "problem": "Operators cannot see which invoices are late.",
    "why": "Late invoices cost money.",
    "acceptance_criteria": [
        "An invoice unpaid 30 days after issue is listed as overdue",
        "The overdue list is sorted oldest first",
    ],
    "tasks": [
        {"id": "T-0001", "title": "Overdue query", "purpose": "find late invoices"},
        {
            "id": "T-0002",
            "title": "Overdue listing",
            "purpose": "show them",
            "depends_on": ["T-0001"],
        },
    ],
    "questions": [],
}

ARCHITECT = {
    "verdict": "pass",
    "architecture_notes": "Add ledger/overdue.py with a pure query over invoices.",
    "modules_affected": ["ledger/overdue.py", "ledger/cli.py"],
    "risks": ["clock skew"],
}

CODER = {
    "verdict": "complete",
    "implementation_summary": "Added overdue query.",
    "code_blocks": [{"path": "ledger/overdue.py", "content": "def overdue():\n    return []\n"}],
    "assumptions": [],
}

TESTER_STATE_EXTRAS = {
    "coder": CODER,
    "gate_build": {"passed": True, "verdict": "pass", "reason": "[T-0002] ok", "task": "T-0002"},
}

FIXED_DIFF = (
    "diff --git a/ledger/overdue.py b/ledger/overdue.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/ledger/overdue.py\n"
    "@@ -0,0 +1,2 @@\n"
    "+def overdue():\n"
    "+    return []"
)


class _Captured(Exception):
    """Raised at the agent boundary once the prompt is recorded (nodes catch it)."""


class PromptGoldenTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.project = self.root / "project"
        # A project directory IS its repository: the evidence (PROJECT_RULES.md,
        # ADRs) sits beside the code, and the prompts must not change because of it.
        self.repo = self.project
        self.repo.mkdir(parents=True)
        self.db_path = self.root / "factory.db"
        db.init_db(self.db_path)
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "Seed", "list overdue invoices")
            self.run_id = db.start_run(conn, "US-0001")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ── fixtures ──────────────────────────────────────────────────

    def _seed_project_memory(self) -> None:
        (self.project / "PROJECT_RULES.md").write_text(
            "- Money is integer cents, never float.\n", encoding="utf-8"
        )
        adr_dir = self.project / "docs" / "architecture" / "adr"
        adr_dir.mkdir(parents=True)
        (adr_dir / "ADR-US-0000-storage.md").write_text(
            "# ADR US-0000: storage\n\n- Status: approved by operator\n\nUse sqlite.\n",
            encoding="utf-8",
        )
        # This story's own ADR must never feed back into its own prompts.
        (adr_dir / "ADR-US-0001-overdue.md").write_text(
            "# ADR US-0001: overdue\n\n- Status: proposed\n\nSELF.\n", encoding="utf-8"
        )

    def _seed_repo(self) -> None:
        pkg = self.repo / "ledger"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "invoices.py").write_text(
            "class Invoice:\n    def total_cents(self) -> int:\n        return 0\n\n\n"
            "def load(path: str) -> list[Invoice]:\n    return []\n",
            encoding="utf-8",
        )

    def _state(self, *, full: bool, **extra: Any) -> dict[str, Any]:
        state: dict[str, Any] = {
            "request": "List invoices that are overdue, oldest first.",
            "story_id": "US-0001",
            "run_id": self.run_id,
            "db_path": str(self.db_path),
            "opencode_cwd": str(self.repo),
            "status": "running",
        }
        if full:
            self._seed_project_memory()
            self._seed_repo()
            state["project_spec"] = PROJECT_SPEC
            state["project_dir"] = str(self.project)
        state.update(extra)
        return state

    # ── capture + compare ─────────────────────────────────────────

    def _capture(self, node, state: dict[str, Any], diff: str | None = None) -> str:
        """Run `node` up to the agent call and return the prompt it would send."""
        seen: list[str] = []

        def boundary(_state, _agent, prompt, _slot=None):
            seen.append(prompt)
            raise _Captured()

        patches = [patch(AGENT_BOUNDARY, side_effect=boundary)]
        if diff is not None:
            patches += [patch(target, return_value=diff) for target in DIFF_LOOKUPS]
        for p in patches:
            p.start()
        try:
            node(state)
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertEqual(len(seen), 1, "node did not reach the agent boundary exactly once")
        return seen[0]

    def _assert_golden(self, name: str, prompt: str) -> None:
        normalized = prompt.replace(str(self.root.resolve()), "<TMP>").replace(
            str(self.root), "<TMP>"
        )
        path = GOLDEN_DIR / f"{name}.txt"
        if UPDATE:
            GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(normalized.encode("utf-8"))
            return
        self.assertTrue(
            path.is_file(),
            f"missing golden {path.name}; generate it with FACTORY_UPDATE_GOLDEN=1",
        )
        self.assertEqual(normalized, path.read_bytes().decode("utf-8"), f"prompt drift: {name}")

    # ── spec-agent ────────────────────────────────────────────────

    def test_spec_prompt_bare_request(self) -> None:
        state = self._state(full=False)
        prompt = self._capture(node_spec_agent, state)
        self.assertEqual(prompt, build_spec_prompt(state))
        self._assert_golden("spec_bare", prompt)

    def test_spec_prompt_with_constraints_and_operator_answers(self) -> None:
        state = self._state(
            full=True,
            triggered_by="spec-rejected",
            prior_findings=["Overdue means 30 days after issue date."],
        )
        prompt = self._capture(node_spec_agent, state)
        self.assertEqual(prompt, build_spec_prompt(state))
        self._assert_golden("spec_full", prompt)

    # ── architect-agent ───────────────────────────────────────────

    def test_architect_prompt_bare(self) -> None:
        prompt = self._capture(node_architect_agent, self._state(full=False, spec=SPEC))
        self._assert_golden("architect_bare", prompt)

    def test_architect_prompt_with_memory_inventory_and_feedback(self) -> None:
        state = self._state(
            full=True,
            spec=SPEC,
            triggered_by="architecture-rejected",
            prior_findings=["Use a view, not a table."],
        )
        self._assert_golden("architect_full", self._capture(node_architect_agent, state))

    def test_architect_prompt_with_coder_feedback(self) -> None:
        state = self._state(
            full=False,
            spec=SPEC,
            triggered_by="design-infeasible",
            prior_findings=["The schema has no issue date."],
        )
        self._assert_golden("architect_infeasible", self._capture(node_architect_agent, state))

    # ── coder-agent ───────────────────────────────────────────────

    def test_coder_prompt_first_task(self) -> None:
        state = self._state(full=True, spec=SPEC, architect=ARCHITECT)
        self._assert_golden("coder_task_first", self._capture(node_coder_agent, state))

    def test_coder_prompt_retry_of_second_task(self) -> None:
        state = self._state(
            full=True,
            spec=SPEC,
            architect=ARCHITECT,
            task_index=1,
            attempt_number=2,
            tasks_completed=["T-0001"],
            triggered_by="gate-build",
            prior_findings=["[T-0002] pytest failed: test_sorted"],
        )
        self._assert_golden("coder_task_retry", self._capture(node_coder_agent, state))

    def test_coder_prompt_bare_first_task(self) -> None:
        state = self._state(full=False, spec=SPEC, architect=ARCHITECT)
        self._assert_golden("coder_task_bare", self._capture(node_coder_agent, state))

    def test_coder_remediation_prompt(self) -> None:
        state = self._state(
            full=True,
            spec=SPEC,
            architect=ARCHITECT,
            remediation=True,
            attempt_number=2,
            triggered_by="tester-agent",
            prior_findings=["Missing test coverage: sorted order", "Security: none"],
        )
        prompt = self._capture(node_coder_agent, state, diff=FIXED_DIFF)
        self._assert_golden("coder_remediation", prompt)

    # ── tester-agent ──────────────────────────────────────────────

    def _tester(self, name: str, diff: str | None, *, full: bool = True) -> None:
        state = self._state(full=full, spec=SPEC, architect=ARCHITECT, **TESTER_STATE_EXTRAS)
        prompt = self._capture(node_tester_agent, state, diff=diff)
        if diff is None:
            direct = build_tester_prompt(state)
        else:
            with patch(DIFF_LOOKUPS[0], return_value=diff):
                direct = build_tester_prompt(state)
        self.assertEqual(prompt, direct)
        self._assert_golden(name, prompt)

    def test_tester_prompt_with_real_diff(self) -> None:
        self._tester("tester_git_diff", FIXED_DIFF)

    def test_tester_prompt_with_clean_repo(self) -> None:
        self._tester("tester_clean", "")

    def test_tester_prompt_off_git_falls_back_to_self_report(self) -> None:
        # The tmp repo is not a git repo, so the real lookup returns None.
        self._tester("tester_no_git", None, full=False)


if __name__ == "__main__":
    unittest.main()
