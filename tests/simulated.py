"""A simulated factory for flow tests: scripted agents and an in-process worker.

The board is driven exactly as the operator drives it (Textual pilot, real
services, real database, real product repo); only two things are replaced:

- the model: `ScriptedAgents` answers at `run_agent` — the same boundary a live
  call crosses — with plausible JSON chosen from the prompt's `# Mode:` marker,
  so every parse, gate and record downstream is the real one;
- the detached worker process: `drain` claims and runs the queued jobs in the
  calling thread, so a click's consequences are on screen when the test looks.

Never the coding stage: a batch launch is as far as these flows go.
"""

from __future__ import annotations

import json
import re
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from factory.adapters.result import AgentResult
from factory.runs.worker import _execute
from factory.state import workflow as store
from factory.state.db import get_db

_UNCOVERED = re.compile(r"^- (\w+) \(", re.M)


def _option(label: str, description: str) -> dict:
    return {"label": label, "description": description}


def _question(topic: str, text: str) -> dict:
    # The agent lists its recommended option first.
    return {"topic": topic, "question": text, "options": [
        _option(f"Simple {topic}", f"The smallest thing that settles {topic}"),
        _option(f"Rich {topic}", f"More {topic}, more work"),
    ]}


class ScriptedAgents:
    """`run_agent` stand-in. `calls` records (agent, mode) in order."""

    def __init__(self, *, stories: tuple[str, ...] = ("Board", "Moves", "Captures"),
                 story_questions: int = 2) -> None:
        self.stories = stories
        self.story_questions = story_questions
        self.calls: list[tuple[str, str]] = []
        self.prompts: list[str] = []
        self.failures: dict[str, int] = {}

    def fail_next(self, mode: str, times: int = 1) -> None:
        """The next `times` calls in `mode` fail like an unreachable provider."""
        self.failures[mode] = times

    def __call__(self, agent: str, prompt: str, *args, **kwargs) -> AgentResult:
        mode = "backlog" if agent == "backlog-agent" else self._mode(prompt)
        self.calls.append((agent, mode))
        self.prompts.append(prompt)
        if self.failures.get(mode):
            self.failures[mode] -= 1
            return AgentResult(agent=agent, output="ERROR: provider unavailable",
                               duration_secs=0.01, returncode=1)
        output = getattr(self, f"_{mode}")(prompt)
        return AgentResult(agent=agent, output=json.dumps(output), duration_secs=0.01,
                           returncode=0)

    @staticmethod
    def _mode(prompt: str) -> str:
        found = re.search(r"^# Mode: (\w+)", prompt, re.M)
        return found.group(1) if found else "product"

    def _backlog(self, _prompt: str) -> dict:
        return {"stories": [{"title": t, "request": f"Build {t.lower()}.",
                             "rationale": f"{t} comes next."} for t in self.stories]}

    def _product(self, prompt: str) -> dict:
        topics = _UNCOVERED.findall(prompt.split("## Required topics still uncovered", 1)[-1])
        return {"questions": [_question(t, f"What about {t}?") for t in topics[:3]],
                "done": not topics}

    def _technical(self, prompt: str) -> dict:
        if '"topic": "technical"' in prompt:
            return {"questions": [], "done": True}
        return {"questions": [_question("technical", "Where should it run?")], "done": False}

    def _stack(self, _prompt: str) -> dict:
        return {"name": "Shop", "description": "Sells socks.", "language": "TypeScript 5",
                "framework": "React 19", "database": "none"}

    def _story(self, prompt: str) -> dict:
        if "(nothing asked yet)" not in prompt:
            return {"questions": [], "done": True}
        return {"questions": [_question("screens", f"Story question {n}?")
                              for n in range(1, self.story_questions + 1)], "done": False}

    def _impact(self, prompt: str) -> dict:
        request = json.loads(prompt.split("\n", 1)[1])["request"]
        slug = re.sub(r"\W+", "_", request.split("\n", 1)[0].lower()).strip("_")[:24]
        path = f"src/{slug}.ts"
        return {
            "spec": {"story_id": "US-0001", "title": request.split("\n", 1)[0][:60],
                     "type": "feature", "problem": "p", "why": "w",
                     "acceptance_criteria": ["It renders", "It shows an empty state"],
                     "non_goals": [],
                     "tasks": [{"id": "T-0001", "title": "Build it", "purpose": "p",
                                "scope": [path], "completion_evidence": "a test passes"}],
                     "verdict": "pass", "questions": []},
            "architecture": {"verdict": "pass", "architecture_notes": "One module.",
                             "modules_affected": [path], "data_model": "none",
                             "api_design": "none", "implementation_constraints": [],
                             "risks": [], "db_impact": "no", "api_impact": "no",
                             "migration_needed": "no", "breaking_changes": [],
                             "external_dependencies": [], "sensitivity": []},
            "resources": [{"kind": "file", "name": path, "mode": "write"},
                          {"kind": "component", "name": slug, "mode": "write"}],
            "dependencies": [], "uncertainties": [], "rationale": "Independent module.",
        }


# Refinement and backlog work only: a launched batch's build jobs stay queued, so no
# flow test ever reaches the coding stage.
DRAINED_KINDS = ("refine", "backlog")


def drain(db_path: Path) -> int:
    """Run queued refinement/backlog jobs to completion in this thread; how many ran."""
    owner = f"test:{uuid.uuid4().hex}"
    ran = 0
    while True:
        with get_db(db_path) as conn:
            head = conn.execute("SELECT kind FROM workflow_jobs WHERE status='queued' "
                                "ORDER BY created_at, id LIMIT 1").fetchone()
            if head is None or head[0] not in DRAINED_KINDS:
                return ran
            job = store.claim_job(conn, owner)
        if job is None:
            return ran
        _execute(job, db_path)
        ran += 1


def simulate(agents: ScriptedAgents) -> ExitStack:
    """Patch the model and the worker everywhere the board reaches them."""
    stack = ExitStack()
    for target in ("factory.runs.interview.run_agent", "factory.runs.backlog.run_agent"):
        stack.enter_context(patch(target, side_effect=agents))

    def never(*_args, **_kwargs):
        raise AssertionError("the simulated factory never runs the pipeline's agents")
    stack.enter_context(patch("factory.pipeline.agent_calls.run_agent", side_effect=never))
    worker = lambda *, db_path, **_kw: drain(db_path)  # noqa: E731
    for target in ("factory.interfaces.board.tui.start_worker",
                   "factory.interfaces.board.workflow_screen.start_worker"):
        stack.enter_context(patch(target, side_effect=worker))
    return stack


def park_run(db_path: Path, project: dict, *, title: str, stage: str, questions: str = "",
             logs: dict[str, dict] | None = None, backlog_id: int | None = None) -> int:
    """A run parked at a checkpoint (`stage`: gate-1-spec, gate-2-architect,
    gate-release-human), linked to a backlog row when given — the record a real run
    leaves, so the board shows it as the operator would find it."""
    from factory.state import db as dbm

    gate = "gate-release" if "release" in stage else stage
    with get_db(db_path) as conn:
        story_id = f"US-{9000 + conn.execute('SELECT COUNT(*) FROM stories').fetchone()[0]}"
        dbm.create_story(conn, story_id, title, f"Build {title.lower()}.", project_id=project["id"])
        run_id = dbm.start_run(conn, story_id, project_id=project["id"])
        for agent, output in (logs or {}).items():
            dbm.log_agent(conn, run_id, agent, "p", json.dumps(output), verdict="pass")
        dbm.update_run_stage(conn, run_id, stage)
        dbm.log_gate(conn, run_id, gate, True, "needs human", needs_human=True,
                     human_questions=questions or "Approve?")
        dbm.finish_run(conn, run_id, "waiting_human")
        if backlog_id is not None:
            conn.execute("UPDATE backlog_stories SET status='started', run_id=?, story_id=? "
                         "WHERE id=?", (run_id, story_id, backlog_id))
    return run_id
