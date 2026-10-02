# Documentation guide

This folder explains the factory's behavior, design, and example tasks. Start
with the root [`README.md`](../README.md) to install and use the CLI.

## Current behavior and rules

- [`contract/EFFECTIVENESS.md`](contract/EFFECTIVENESS.md) is the governing
  contract: what the factory is for, where a person must decide, and what evidence
  is required before trusting a result.
- [`contract/GATES.md`](contract/GATES.md) explains the automatic checks that can
  stop a run.
- [`contract/AGENTS.md`](contract/AGENTS.md) lists the agents and the response
  format each one must follow.
- [`contract/REVIEW_QUEUE.md`](contract/REVIEW_QUEUE.md) explains why work can
  pause and how to review or resume it.
- [`contract/context-pack.template.md`](contract/context-pack.template.md) is the
  template for context given to an agent.

## Code and design

- [`ARCHITECTURE.md`](ARCHITECTURE.md) maps the code, describes the allowed
  dependency direction, and explains where new code belongs.
- [`design/`](design/) holds the original brief and design plans. These describe
  how the project was planned; they may describe earlier decisions.
- [`challenges/`](challenges/) holds sample product briefs, such as
  [`supportflow.md`](challenges/supportflow.md).

For saved agent-output examples that are replayed as regression checks, see
[`../evals/cases/`](../evals/cases/). For example project specifications, see
[`../examples/specs/`](../examples/specs/).
