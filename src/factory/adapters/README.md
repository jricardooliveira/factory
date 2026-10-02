# factory.adapters

## Responsibility

The boundary to the outside world. `opencode.py` is the only place a model is called (it shells
out to the `opencode` CLI and turns the result into an `AgentResult`); `notify.py` is an opt-in,
best-effort macOS desktop notification. Everything else in the factory stays pure Python and
talks to models only through `run_agent`.

## Modules

| Module | What it does |
|---|---|
| `opencode.py` | `run_agent(agent_name, prompt, cwd, model, timeout)` runs `opencode run --agent <name> --format json --dangerously-skip-permissions [--model m] -- <prompt>`. `AgentResult` dataclass (`agent`, `output`, `duration_secs`, `returncode`, `tokens_in/out`, `cost_usd`, `model_name`, `agent_prompt_hash`, `.success`). Parsers `_extract_text_from_json_stream`, `_extract_usage_from_json_stream`, helper `_hash_agent_definition`. |
| `notify.py` | `notify(title, message)` runs `osascript display notification`; `enabled()` reads `FACTORY_NOTIFY`. |

## How it works

- **Text** is the concatenation of `type == "text"` events in opencode's JSON-lines stream; an
  `error` event becomes `ERROR: <message>`. If there is no text, stderr or the exit code is
  turned into an `ERROR: ...` output, so a failed call still yields a string the caller can log.
- **Failures never raise.** Timeout (`FACTORY_AGENT_TIMEOUT`, default 600s, or the `timeout`
  argument) and a missing `opencode` binary both return an `AgentResult` with `returncode=-1` and
  an `ERROR:` output.
- **Usage extraction**: a message-level object with `tokens.{input,output}` plus `modelID` wins
  (last one seen). Otherwise the `step_finish` events (what opencode actually emits; they carry
  no `modelID`) are summed, with reasoning tokens counted as output because they are billed as
  output. `cost_usd` is `None` when no step reported a cost: unknown is not zero. When the stream
  names no model, the requested `model` is recorded instead.
- **`stdin=subprocess.DEVNULL` is load-bearing**: `opencode run` reads stdin when it is not a
  terminal and waits for EOF, so under a background job, cron, CI or the TUI worker every call
  would hang until the timeout. A test pins this invariant.
- **`--` before the prompt** stops a prompt starting with `-` being parsed as an option.
- **`agent_prompt_hash`** is a 16-hex SHA-256 of the `.opencode/agents/<name>.md` found by walking
  up from `cwd`, mirroring opencode's config discovery, so a log row records which agent
  definition ran (`None` if not found).
- **Model choice is not made here**: callers pass `model` (resolved from `agent_config.tiers`).
- **Notifications are off by default** (`FACTORY_NOTIFY` in `1/true/yes/on`); `osascript` was seen
  launching Script Editor. No-op off macOS or without `osascript`; never raises. Text is stripped
  to `[\w \-.#:/]` and truncated to 180 chars to avoid AppleScript injection from story titles
  or gate reasons.

## Dependencies

Imports nothing from other `factory` packages (layering table allows only `domain`).
Used by `pipeline/agent_calls.py` (`run_agent`, `AgentResult`), `preflight/doctor.py`
(`run_agent`, for per-model probes) and `runs/service.py` (`notify`).

## Gotchas

- Tests must never make a live call: patch `factory.pipeline.agent_calls.run_agent` /
  `_run_or_replay`, or `factory.preflight.doctor.run_agent`; `tests/adapters/test_opencode.py`
  patches `subprocess.run`.
- Usage fields are NULL for logs before `step_finish` support, and the ChatGPT/Codex login reports
  cost 0 for real tokens: do not trust a dollar figure.
- Adding another provider or CLI belongs here, behind the same `AgentResult` shape.
