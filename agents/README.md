# Agent configuration

This folder contains the instructions and model settings for the factory's six
agents. The agents propose and review work; the Python pipeline checks their
outputs and controls what can be written to a product repository.

## The agents

| File | Role |
|---|---|
| `spec-agent.md` | Turns your request into a story with clear requirements and small tasks. |
| `architect-agent.md` | Plans how to implement an approved story and records design risks. |
| `coder-agent.md` | Implements one approved task at a time and returns proposed file changes. |
| `boundary-agent.md` | Reviews tenant, authorization, API and security boundaries before code is written — only when the design touches one. |
| `tester-agent.md` | Reviews the result for test coverage, security, and performance. |
| `release-agent.md` | Writes the release notes you read at Checkpoint 3. It decides nothing. |

Agent files define each agent's job, input, and required response format. Their
tool permissions are deliberately restricted. In particular, agents do not edit
product files directly; the factory checks and applies the coder's proposed
changes.

## Shared review rules and models

- [`policies/REVIEW.md`](policies/REVIEW.md) is the tester's detailed review
  policy: what to check, when to block, and how to report findings.
- [`tiers.toml`](tiers.toml) assigns a model tier to each agent, names the
  model used by each tier, lists each model's price (for the $10-per-story cap)
  and controls which agents move to another tier on a retry. Reviewers never use
  the same model family as the agents whose work they review.

When changing an agent's tier or model, keep the `model_tier` and `model` in its
Markdown front matter in sync with `tiers.toml`. Run `make evals` to catch
configuration mismatches. To use a different model temporarily, set the matching
`FACTORY_TIER_<TIER>` environment variable instead of editing the files.

For the full agent roster and output contracts, see
[`../docs/contract/AGENTS.md`](../docs/contract/AGENTS.md). The `.opencode/agents/`
directory contains per-agent links so opencode can load these files; edit the
source files here.
