# Agent configuration

This folder contains the instructions and model settings for the factory's four
agents. The agents propose and review work; the Python pipeline checks their
outputs and controls what can be written to a product repository.

## The agents

| File | Role |
|---|---|
| `spec-agent.md` | Turns your request into a story with clear requirements and small tasks. |
| `architect-agent.md` | Plans how to implement an approved story and records design risks. |
| `coder-agent.md` | Implements one approved task at a time and returns proposed file changes. |
| `tester-agent.md` | Reviews the result for test coverage, security, and performance. |

Agent files define each agent's job, input, and required response format. Their
tool permissions are deliberately restricted. In particular, agents do not edit
product files directly; the factory checks and applies the coder's proposed
changes.

## Shared review rules and models

- [`policies/REVIEW.md`](policies/REVIEW.md) is the tester's detailed review
  policy: what to check, when to block, and how to report findings.
- [`tiers.toml`](tiers.toml) assigns a model tier to each agent and names the
  model used by each tier. It also controls which agents move to a stronger tier
  on a retry.

When changing an agent's tier or model, keep the `model_tier` and `model` in its
Markdown front matter in sync with `tiers.toml`. Run `make evals` to catch
configuration mismatches. To use a different model temporarily, set the matching
`FACTORY_TIER_<TIER>` environment variable instead of editing the files.

For the full agent roster and output contracts, see
[`../docs/contract/AGENTS.md`](../docs/contract/AGENTS.md). The `.opencode/agents/`
directory contains per-agent links so opencode can load these files; edit the
source files here.
