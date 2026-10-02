# Eval cases

An eval case is a saved example of how the factory handled a task. Each `.json`
file contains the outputs from the agents and the result the factory should
produce when it processes those outputs again.

Think of a case as a practice question with an answer key. The factory replays
the saved answers through its real pipeline. If the result changes, the eval
fails and warns us that something may have broken. Because the agent outputs
are already saved, replaying a case does not call an AI model or use tokens.

## What is in a case?

Here are the main fields:

| Field | Meaning |
|---|---|
| `name` | A short name for the example. |
| `description` | What the example checks and why it matters. |
| `agent_outputs` | The saved responses from each agent. These are fed into the pipeline during replay. |
| `expect.status` | The final run status the factory should produce, such as `completed` or `failed`. |
| `expect.gates` | Optional checks for specific gates. For example, `"gate-build": false` means the build gate is expected to fail. |
| `git` | Optional. Set to `true` when the replay needs a temporary Git repository. |
| `source_run_id` | Optional. The ID of the real run this case was saved from. |

For example, `P3-go-vet-finding-must-block.json` saves agent responses for a
case where a Go quality check should block the run:

```json
{
  "name": "P3-go-vet-finding-must-block",
  "description": "A Go vet finding should prevent the run from passing.",
  "git": true,
  "agent_outputs": {
    "spec-agent": "...saved response...",
    "architect-agent": "...saved response...",
    "coder-agent": {
      "T-0001": "...saved response for task T-0001..."
    }
  },
  "expect": {
    "status": "failed",
    "gates": {
      "gate-build": false
    }
  }
}
```

The `...saved response...` text above is shortened to keep the example readable;
real case files contain the full agent outputs.

## Run the cases

From the project root, run:

```bash
make evals
```

The eval command loads every `.json` file in this folder automatically. The
checks run offline and should all pass. If one fails, read its name and failure
detail to see which expected result changed.

## Add a case

When a run exposes a bug or an important behavior, save it here so the same
problem is easier to catch next time. You can capture a real run with:

```bash
factory evals capture <run_id> <short-name>
```

You can also add a `.json` file by hand. Give it a useful name, explain the
behavior in `description`, include the saved outputs in `agent_outputs`, and
write the expected result under `expect`. Then run `make evals` and make sure
the new case passes.
