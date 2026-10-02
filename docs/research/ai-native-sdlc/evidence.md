# Review evidence and reproducibility

## Reviewed state

- Review date: **2026-10-02**.
- Commit: **`de5fe873ec4baece49035f1b6c11139d695aa7aa`**, `boss: authorize every agent stage from the recorded verdicts, as code`.
- Stable snapshot captured at **19:32:56 UTC**; working tree was clean at capture.
- Snapshot contained 267 source/configuration/test/documentation files, preserving agent symlinks. A second content comparison found no changes during capture.
- Temporary review location: `/private/tmp/factory-sdlc-review-2026-10-02-v2`.
- Interpreter: Python **3.14.6**, using the existing factory virtual environment; pytest **8.4.2**, Pydantic **2.13.3**, LangGraph **0.6.11**.
- Imports were directed to the snapshot's `src` with `PYTHONPATH`. Tests and probes used temporary product directories and databases. No live model calls were made.

An earlier snapshot caught concurrent work between a new test and its implementation and failed collection. It was discarded as a basis for verification. The results below all refer to the clean, committed second snapshot.

**Concurrent work after capture:** while the report was being written, additional changes appeared, including `agents/release-agent.md`, `pipeline/nodes/release.py`, release prompts, release-checkpoint tests, and edits to gates, contracts, evidence, and run handling. Those changes were not made by this assessment and are not covered by its test results. They may address some findings, especially F1. Their presence alone does not prove the findings resolved. Recheck the acceptance proofs against a finished revision before using this report as a verdict on that later code.

This review added only the three Markdown files in `docs/research/ai-native-sdlc/`. It did not alter, stage, commit, merge, or push application changes.

## Existing checks executed

The interpreter and source paths below show how the stable snapshot was selected. These were run against copied source, not the actively edited checkout.

```sh
PYTHONPATH=/private/tmp/factory-sdlc-review-2026-10-02-v2/src \
  /Users/joaooliveira/dev/personal/factory/.venv/bin/python -m pytest -q \
  /private/tmp/factory-sdlc-review-2026-10-02-v2/tests
```

From the snapshot root:

```sh
PYTHONPATH=/private/tmp/factory-sdlc-review-2026-10-02-v2/src \
  /Users/joaooliveira/dev/personal/factory/.venv/bin/python \
  -c 'from factory.interfaces.cli.main import main; main()' simulate

PYTHONPATH=/private/tmp/factory-sdlc-review-2026-10-02-v2/src \
  /Users/joaooliveira/dev/personal/factory/.venv/bin/python \
  -c 'from factory.interfaces.cli.main import main; main()' evals
```

| Check | Observed result | What it establishes |
|---|---|---|
| Full pytest suite | **619 passed**, 2,521 warnings, 77.99 seconds; exit 0 | Existing unit, integration, architecture, and interface contracts pass in this environment. |
| Simulations | **10/10 scenarios behaving as expected**; exit 0 | The supplied offline scenarios produce expected outcomes. |
| Evals | **44/44 checks green**, 100% threshold; exit 0 | Configuration invariants and the supplied frozen-answer cases pass. |

Warnings included a `TesterOutput` collection warning and dependency deprecation warnings involving `asyncio.iscoroutinefunction`. The tests passed; the warnings were not treated as a clean, warning-free result.

I did **not** run or claim a passing `make check`: lint has optional behavior, and the explicit suite/simulation/eval commands above are the checks actually executed. I did not install new dependencies or run live doctor checks.

## Additional probes

These were small inspection experiments in temporary directories, not changes to the repository's test suite. They used the reviewed code unchanged. The complete local probe script and JSON output were left in the temporary review directory as `review-probes.py` and `review-probes.json`; the portable reproductions and observations are recorded below because temporary files may disappear.

### P1. Collection executes code despite disabled test bodies

Create a temporary `test_demo.py` with one passing test. Create a `conftest.py` whose top-level code writes `import-ran.txt` next to itself. Invoke `verify_changes` for the test file with `FACTORY_RUN_TESTS=0`.

```python
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
from factory.verification import verify_changes

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / "test_demo.py").write_text("def test_ok():\n    assert True\n")
    (root / "conftest.py").write_text(
        'from pathlib import Path\n'
        'Path(__file__).with_name("import-ran.txt").write_text("ran")\n'
    )
    with patch.dict(os.environ, {"FACTORY_RUN_TESTS": "0"}):
        result = verify_changes([root / "test_demo.py"], root=root)
    print((root / "import-ran.txt").exists(), result.summary, result.passed)
```

Observed: `True`, `py_compile:pass, pytest_collect:pass`, `True`.

**Conclusion:** this verification path executes import-time Python code. The experiment does not demonstrate credential theft or an exploit; the host-isolation concern follows separately from inspection of the subprocess boundary.

### P2. Unsupported extension produces a passing overall result

Invoke `verify_changes([root / "component.vue"], root=root)` in a temporary directory. No file needs to exist for this dispatch-level check because that extension selects no verifier.

Observed:

```json
{"summary": "verify:skip", "verdict": "pass", "passed": true}
```

**Conclusion:** “passed” currently means no selected check reported failure; it does not guarantee a relevant check ran. This is a helper-level reproduction, not a claim that a particular Vue product was generated and released.

### P3. Completed run and saved package disagree

Reuse `ProjectWorkspaceRunTests` from `tests/integration/test_project_workspace.py`, which blocks live calls and seeds frozen outputs. Replay a passing spec, architect, coder, and tester for a simple `src/search.py` implementation. Read the saved trust JSON and compare it with `trust_package.assemble()` after completion.

The probe ran from the snapshot root, with snapshot imports:

```python
import json
import os
import runpy
from unittest.mock import patch
from factory.evidence import trust_package

ns = runpy.run_path("tests/integration/test_project_workspace.py")
case = ns["ProjectWorkspaceRunTests"]()
case.setUp()
try:
    with patch.dict(os.environ, {
        "FACTORY_HOME": str(case.home), "FACTORY_RUN_TESTS": "0"
    }):
        outcome = case._replay(ns["_coder"]((
            "src/search.py", "def search():\n    return []\n"
        )))
        path = case.work / "docs/releases" / (
            f"run-{outcome.run_id}-trust-package.json"
        )
        saved = json.loads(path.read_text())
        fresh = trust_package.assemble(case.db_path, outcome.run_id)
        print(outcome.status, saved["verdict"], fresh["verdict"])
        print(saved["blockers"], fresh["blockers"], fresh["tests"]["executed"])
finally:
    case.doCleanups()
    case.tearDown()
```

Observed:

| Field | Value |
|---|---|
| Final run status | `completed` |
| Saved package verdict | `running` |
| Fresh package verdict | `pass` |
| Saved blockers | `running` plus the missing-test-execution blocker |
| Fresh blockers | Missing-test-execution blocker |
| Fresh `tests.executed` | `false` |

**Conclusion:** saved evidence reads state before the final transaction commits; completion is possible without executed tests. The package's blocker is valuable information, but it does not currently prevent completion.

### P4. Test evidence combines attempts without identifying the final candidate

Call `trust_package._test_execution` with synthetic gate records:

```python
from factory.evidence.trust_package import _test_execution

def gate(reason):
    return {"gate_name": "gate-build", "reason": reason}

print(_test_execution([gate("pytest_run:pass"), gate("pytest_run:skip")]))
print(_test_execution([gate("pytest_run:fail"), gate("pytest_run:pass")]))
```

Observed, as `(executed, passed, command)`:

```text
pass then skip: (True, True, 'pytest -q')
fail then pass: (True, False, 'pytest -q')
```

**Conclusion:** the helper does not distinguish tasks, superseded attempts, or revisions. These are isolated evidence-aggregation probes, not assertions that both sequences occurred in a live product.

### P5. Missing usage becomes zero measured spend

In a temporary database, create a story and run, then call `db.log_agent` without a cost value. Query `db.get_run_cost` for that run.

Observed: **`0.0`**.

**Conclusion:** the budget accessor cannot distinguish unmeasured spend from confirmed zero spend. This does not establish how many real provider calls currently lack usage.

## Confidence boundaries

| Statement type | Basis |
|---|---|
| Existing tests/checks pass | Executed commands and captured exit codes/output for the specified snapshot. |
| P1–P5 behavior | Direct temporary reproductions against the reviewed code. |
| Gate/authorization structure, scope exceptions, inventory limitations, eval mechanism | Inspection of the linked source functions, agent definitions, and tests. |
| Risk of losing existing behavior during full-file replacement | Inference from missing implementation context and write semantics; no destructive product experiment. |
| Lack of enforced host sandbox in the inspected runner | Code inspection; effective external OpenCode configuration was not exhaustively audited. |
| Repository branch protection, production safety, live model quality, actual cost coverage | **Not established by this review.** |
| Proposed improvements | Recommendations, with acceptance proofs for a future implementation. |

The [assessment](assessment.md) should be read with these boundaries. It identifies specific tested defects and architectural gaps without claiming that every possible run or external configuration was examined.
