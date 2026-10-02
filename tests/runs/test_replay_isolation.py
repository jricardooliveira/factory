"""`factory replay` is zero-token AND side-effect-free on the product.

Found by review: a replay of a project run drove the graph in the LIVE product
repository. The coder materialized code there and the evidence writers re-stamped
INTENT/SPEC/PLAN/ADR and committed them with `factory:` subjects, moving the
product's HEAD. Approving the replay's checkpoint then resumed with LIVE agents,
because nothing recorded that the run was a replay.

A replay now runs in a scratch clone (`<home>/replays/run-<id>/`) at the replayed
run's baseline, the run row records `replay_of`, and a resume of it keeps
replaying frozen outputs in the same clone.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.state import db
from factory.workspace.git import git_head
from factory.workspace.projects import create_project

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "agent_outputs"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


def _blocked(*_a, **_k):
    raise RuntimeError("LIVE CALL BLOCKED")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


class ReplayIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve()
        self.db_path = self.home / "factory.db"
        self.project = create_project(self.db_path, home=self.home, slug="demo")
        self.repo = Path(self.project["repo_path"])
        guard = patch("factory.pipeline.agent_calls.run_agent", _blocked)
        guard.start()
        self.addCleanup(guard.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, architect: str) -> int:
        """An original project run whose four agents' outputs are frozen."""
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "Seed", "convert things",
                            project_id=self.project["id"])
            rid = db.start_run(conn, "US-0001", project_id=self.project["id"],
                               base_commit=git_head(self.repo))
            db.log_agent(conn, rid, "spec-agent", "p", _load("clean_pass.spec.json"),
                         verdict="pass")
            db.log_agent(conn, rid, "architect-agent", "p", architect, verdict="pass")
            db.log_agent(conn, rid, "coder-agent", "p", _load("clean_pass.coder.json"),
                         verdict="complete", stage_type="T-0001")
            db.log_agent(conn, rid, "tester-agent", "p", _load("tester_pass.json"),
                         verdict="pass")
            db.finish_run(conn, rid, "completed")
        return rid

    def _product_state(self) -> tuple[str, str, list[str]]:
        files = sorted(
            str(p.relative_to(self.repo)) for p in self.repo.rglob("*")
            if ".git" not in p.parts and p.is_file()
        )
        return git_head(self.repo) or "", _git(self.repo, "status", "--porcelain"), files

    def test_a_project_replay_never_touches_the_product_repository(self) -> None:
        from factory import runs

        orig = self._seed(_load("clean_pass.architect.json"))
        before = self._product_state()

        outcome = runs.replay_run(orig, db_path=self.db_path)

        self.assertEqual(outcome.status, "waiting_human")  # reviewed → Checkpoint 3
        self.assertEqual(self._product_state(), before, "the replay mutated the product")
        sandbox = self.home / "replays" / f"run-{outcome.run_id}"
        self.assertTrue((sandbox / "convert.py").is_file(), "code lands in the sandbox")
        self.assertIn("factory:", _git(sandbox, "log", "--format=%s"))

    def test_the_replay_records_which_run_it_replays(self) -> None:
        from factory import runs

        orig = self._seed(_load("clean_pass.architect.json"))
        outcome = runs.replay_run(orig, db_path=self.db_path)
        with db.get_db(self.db_path) as conn:
            self.assertEqual(db.get_run(conn, outcome.run_id)["replay_of"], orig)

    def test_the_replays_trust_package_measures_the_sandbox(self) -> None:
        from factory import runs
        from factory.evidence import trust_package

        orig = self._seed(_load("clean_pass.architect.json"))
        outcome = runs.replay_run(orig, db_path=self.db_path)
        pkg = trust_package.assemble(self.db_path, outcome.run_id)
        self.assertEqual(pkg["diff"]["source"], "git")
        self.assertEqual([f["path"] for f in pkg["diff"]["files"]], ["convert.py"])

    def test_approving_a_parked_replay_keeps_replaying_in_the_sandbox(self) -> None:
        from factory import runs

        sensitive = json.loads(_load("clean_pass.architect.json"))
        sensitive["sensitivity"] = ["pii"]
        orig = self._seed(json.dumps(sensitive))
        before = self._product_state()

        parked = runs.replay_run(orig, db_path=self.db_path)
        self.assertEqual(parked.status, "waiting_human")

        # run_agent is patched to raise: a live call would fail the run.
        resumed = runs.resume_run(parked.run_id, "approve", db_path=self.db_path)
        self.assertEqual(resumed.status, "waiting_human", resumed.error)  # now Checkpoint 3
        # ...and releasing it is replayed too: still no live call, still the sandbox.
        released = runs.resume_run(parked.run_id, "approve", db_path=self.db_path)
        self.assertEqual(released.status, "completed", released.error)
        self.assertEqual(self._product_state(), before)
        sandbox = self.home / "replays" / f"run-{parked.run_id}"
        self.assertTrue((sandbox / "convert.py").is_file())

    def test_a_non_project_replay_does_not_write_into_the_working_directory(self) -> None:
        from factory import runs

        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0002", "Loose", "convert things")
            rid = db.start_run(conn, "US-0002")
            db.log_agent(conn, rid, "spec-agent", "p", _load("clean_pass.spec.json"), verdict="pass")
            db.log_agent(conn, rid, "architect-agent", "p", _load("clean_pass.architect.json"),
                         verdict="pass")
            db.log_agent(conn, rid, "coder-agent", "p", _load("clean_pass.coder.json"),
                         verdict="complete", stage_type="T-0001")
            db.log_agent(conn, rid, "tester-agent", "p", _load("tester_pass.json"), verdict="pass")
            db.finish_run(conn, rid, "completed")

        with tempfile.TemporaryDirectory() as cwd:
            old = os.getcwd()
            os.chdir(cwd)
            try:
                outcome = runs.replay_run(rid, db_path=self.db_path)
            finally:
                os.chdir(old)
            self.assertEqual(os.listdir(cwd), [])
        self.assertEqual(outcome.status, "waiting_human")  # reviewed → Checkpoint 3


if __name__ == "__main__":
    unittest.main()


class AgentsLinkTests(unittest.TestCase):
    """A live project run must drive THIS checkout's agents.

    Found by review: each product's `.opencode` is an absolute symlink into one
    factory checkout, so a run launched from a clone or worktree silently used the
    MAIN checkout's agents — not the ones `make evals` had just validated there.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve()
        self.db_path = self.home / "factory.db"
        self.project = create_project(self.db_path, home=self.home, slug="demo")
        self.link = Path(self.project["repo_path"]) / ".opencode"
        guard = patch("factory.pipeline.agent_calls.run_agent", _blocked)
        guard.start()
        self.addCleanup(guard.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_link_into_another_checkout_refuses_the_run_before_any_state(self) -> None:
        from factory import runs

        other = self.home / "other-checkout" / ".opencode"
        other.mkdir(parents=True)
        self.link.unlink()
        self.link.symlink_to(other, target_is_directory=True)

        with self.assertRaises(runs.RunError) as raised:
            runs.run_project_pipeline("demo", "do it", db_path=self.db_path)
        self.assertIn("ln -sfn", str(raised.exception))
        with db.get_db(self.db_path) as conn:
            self.assertEqual(db.list_runs(conn), [], "a refused run must leave no row")

    def test_a_missing_link_is_created_pointing_at_this_checkout(self) -> None:
        from factory.workspace.projects import agents_link_problem, factory_opencode_dir

        self.link.unlink()
        self.assertIsNone(agents_link_problem(self.link.parent))
        self.assertEqual(self.link.resolve(), factory_opencode_dir().resolve())
