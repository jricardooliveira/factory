"""LangGraph pipeline: spec-agent → gate-1 → architect-agent → gate-2 → coder-agent."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from factory.gates import (
    MAX_CODER_ATTEMPTS,
    MAX_REARCHITECT_LOOPS,
    MAX_TASK_COST_USD,
    MAX_TESTER_REMEDIATIONS,
    GateResult,
    defined_threshold_terms,
    gate_after_architect,
    gate_after_spec,
    gate_after_tester,
)
from factory import artifacts, review_policy
from factory.context_pack import build_remediation_pack, build_task_pack, order_tasks
from factory.materialize import materialize_code_blocks, normalize_block_path
from factory.memory import load_project_memory, write_adr
from factory.model_tiers import resolve_model
from factory.models import ArchitectOutput, CoderOutput, SpecOutput, TesterOutput
from factory.repo_map import build_repo_inventory
from factory.traceability import trace_criteria, unassessed_criteria
from factory.opencode_client import AgentResult, run_agent
from factory.utils import parse_agent_json
from factory.verify import collect_repo_diff, git_changed_paths, git_commit_all, verify_changes
from factory.state.db import (
    finish_run,
    get_agent_log,
    get_agent_log_by_stage,
    get_run_cost,
    log_agent,
    log_gate,
    update_run_stage,
    update_story_status,
    update_story_title,
)


class PipelineState(TypedDict, total=False):
    request: str
    story_id: str
    run_id: int
    db_path: str
    opencode_cwd: str
    project_spec: str  # rendered project spec context for agents
    project_dir: str  # project root (parent of repo/) — enables ADRs + decision memory
    replay_run_id: int  # if set, feed stored agent outputs instead of calling opencode

    # Agent outputs (raw + parsed)
    spec_raw: str
    spec: dict[str, Any]
    architect_raw: str
    architect: dict[str, Any]
    coder_raw: str
    coder: dict[str, Any]
    tester_raw: str
    tester: dict[str, Any]

    # Gate results
    gate_1: dict[str, Any]
    gate_2: dict[str, Any]
    gate_build: dict[str, Any]
    gate_test: dict[str, Any]

    # Committed artifact chain (INTENT -> SPEC -> PLAN -> ADR)
    intent_path: str
    spec_path: str
    plan_path: str
    adr_path: str

    # Remediation loop
    attempt_number: int  # coder attempt count for the CURRENT task (1-based)
    triggered_by: str  # what caused a re-entry, e.g. "gate-build"
    prior_findings: list[str]  # findings from the last failed attempt, fed into the retry
    next_action: str  # coder routing: "complete" | "retry" | "next_task" | "give_up"
    remediation: bool  # coder is in a tester-driven remediation pass (not a task)
    tester_attempt: int  # how many times the tester has run for this story (1-based)
    rearchitect_count: int  # times the coder has bounced an infeasible design back

    # Per-task execution
    task_index: int  # index into the dependency-ordered task list
    tasks_completed: list[str]  # task ids already implemented

    # Overall
    status: str
    error: str


def _extract_json(text: str) -> dict[str, Any]:
    """Extract JSON from agent output; synthesize a 'blocked' result on failure."""
    parsed = parse_agent_json(text)
    if parsed is not None:
        return parsed
    # No valid JSON — return a synthetic blocked response instead of crashing.
    return {
        "verdict": "blocked",
        "error": "Agent did not return valid JSON",
        "agent_response": text[:500].strip(),
    }


_JSON_REPAIR_SUFFIX = (
    "\n\n---\nYOUR PREVIOUS RESPONSE WAS NOT VALID JSON AND COULD NOT BE PARSED. "
    "Reply with ONLY the JSON object for this stage — no prose, no markdown fences, "
    "no commentary before or after. Begin with '{' and end with '}'."
)


def _run_agent_json(
    state: PipelineState, agent_name: str, prompt: str, slot: str | None = None
) -> tuple[AgentResult, dict[str, Any]]:
    """Run an agent and parse its JSON, with ONE repair retry on a live run.

    Models occasionally wrap output in prose or fences that won't parse. Rather
    than dead-ending the whole run on a formatting slip, re-ask once for JSON-only.
    Replay runs skip the retry (their outputs are frozen) and fall through to the
    synthetic 'blocked' result, preserving existing replay behavior. The returned
    dict is the parsed object, or the synthetic 'blocked' shape `_extract_json`
    produces — so callers' existing off-script handling is unchanged.
    """
    result = _run_or_replay(state, agent_name, prompt, slot)
    parsed = parse_agent_json(result.output)
    if parsed is not None:
        return result, parsed
    if not state.get("replay_run_id"):
        result = _run_or_replay(state, agent_name, prompt + _JSON_REPAIR_SUFFIX, slot)
        reparsed = parse_agent_json(result.output)
        if reparsed is not None:
            return result, reparsed
    return result, _extract_json(result.output)


def _run_or_replay(
    state: PipelineState, agent_name: str, prompt: str, slot: str | None = None
) -> AgentResult:
    """Call the agent, or replay a stored output when replay_run_id is set.

    Replay reads the frozen output of the SAME agent from the original run's
    agent_logs, so the orchestration (gates, parsing, edges, materialization) is
    re-exercised with zero opencode calls and zero cost. `slot` (a task id, stored
    in stage_type) selects the right per-task coder output during replay.
    """
    replay_id = state.get("replay_run_id")
    if replay_id:
        conn = _get_db_conn(state)
        try:
            if slot is not None:
                log = get_agent_log_by_stage(conn, replay_id, agent_name, slot)
            else:
                log = get_agent_log(conn, replay_id, agent_name)
        finally:
            conn.close()
        if log is None:
            raise RuntimeError(
                f"Cannot replay: no stored output for '{agent_name}' in run {replay_id}"
            )
        return AgentResult(
            agent=agent_name,
            output=log.get("output_text") or "",
            duration_secs=0.0,
            returncode=0,
            tokens_in=log.get("tokens_in"),
            tokens_out=log.get("tokens_out"),
            cost_usd=log.get("cost_usd"),
            model_name=log.get("model_name"),
            agent_prompt_hash=log.get("agent_prompt_hash"),
        )
    # Tier policy: frontier models for the thinking stages, a cheap model for the
    # coder — escalated to frontier on a remediation retry. attempt_number is the
    # coder's per-task counter; it's harmless for the (non-escalating) others.
    model, _tier = resolve_model(agent_name, state.get("attempt_number", 1))
    return run_agent(agent_name, prompt, cwd=state.get("opencode_cwd"), model=model)


def _project_memory_block(state: PipelineState) -> str:
    """Prior ADRs + PROJECT_RULES for this project, as a prompt block ('' if none)."""
    project_dir = state.get("project_dir")
    if not project_dir:
        return ""
    memory = load_project_memory(Path(project_dir), exclude_story=state.get("story_id"))
    return f"{memory}\n\n" if memory else ""


def _repo_inventory_block(state: PipelineState) -> str:
    """Interface map of the existing repo, so agents design/code against what's
    really there (brownfield awareness). Empty on a greenfield repo."""
    inventory = build_repo_inventory(Path(state.get("opencode_cwd") or "."))
    if not inventory:
        return ""
    return (
        "## Existing codebase (interfaces only — integrate with these, do not rewrite)\n\n"
        f"{inventory}\n\n"
    )


def _reviewer_feedback_block(state: PipelineState) -> str:
    """Prompt block carrying feedback into a re-architecture pass.

    Two sources re-enter the architect with findings: a human REJECTING the
    architecture checkpoint, and the CODER reporting the design is infeasible.
    Either way the re-attempt must address the feedback, not re-propose the same
    design. Empty when neither applies.
    """
    trigger = state.get("triggered_by")
    findings = state.get("prior_findings") or []
    if not findings:
        return ""
    if trigger == "architecture-rejected":
        header = "## Reviewer feedback to address (your previous design was rejected)"
        intro = "Revise the design to resolve these points; do not simply re-propose it:"
    elif trigger == "design-infeasible":
        header = "## Implementer feedback (the coder could not build your previous design)"
        intro = "The coder found the design infeasible. Revise it to resolve:"
    else:
        return ""
    lines = "\n".join(f"- {f}" for f in findings)
    return f"{header}\n\n{intro}\n{lines}\n\n"


def _retry_context_block(state: PipelineState, attempt: int) -> str:
    """Prompt block telling the coder this is a remediation attempt ('' on first try)."""
    findings = state.get("prior_findings") or []
    if attempt <= 1 or not findings:
        return ""
    trigger = state.get("triggered_by", "a gate")
    lines = "\n".join(f"- {f}" for f in findings)
    return (
        f"## Previous attempt failed (attempt {attempt} of {MAX_CODER_ATTEMPTS})\n\n"
        f"The prior implementation failed `{trigger}`. Fix these specifically; "
        f"the files you wrote already exist in the working directory — correct them:\n"
        f"{lines}\n\n"
    )


def _scope_diff(coder: CoderOutput, written: list[Path], root: Path) -> tuple[set[str], set[str]]:
    """Return (unclaimed, missing) file sets, preferring the git-measured truth.

    `unclaimed` = files that changed in the repo but the coder did NOT declare in
    code_blocks — i.e. out-of-band writes (an agent scribbling outside the
    factory's controlled materialize path). `missing` = declared but not on disk.
    """
    git_changed = git_changed_paths(root)
    if git_changed is None:
        root_resolved = root.resolve()
        actual: set[str] = set()
        for p in written:
            try:
                actual.add(str(Path(p).resolve().relative_to(root_resolved)))
            except ValueError:
                actual.add(str(p))
    else:
        actual = set(git_changed)
    claimed = {c.path for c in coder.code_blocks}
    return actual - claimed, claimed - actual


def _scope_note(unclaimed: set[str], missing: set[str]) -> str | None:
    if not unclaimed and not missing:
        return None
    bits = []
    if unclaimed:
        bits.append(f"unclaimed: {sorted(unclaimed)}")
    if missing:
        bits.append(f"claimed-but-absent: {sorted(missing)}")
    return "scope-mismatch (" + "; ".join(bits) + ")"


def _usage_kwargs(result: AgentResult) -> dict[str, Any]:
    """Build the cost/provenance kwargs for log_agent from an agent result."""
    return {
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "cost_usd": result.cost_usd,
        "model_name": result.model_name,
        "agent_prompt_hash": result.agent_prompt_hash,
    }


def _get_db_conn(state: PipelineState) -> sqlite3.Connection:
    """Get a DB connection from state."""
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(state["db_path"])
    conn.row_factory = _sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ── Node: spec-agent ──────────────────────────────────────────────

def build_spec_prompt(state: PipelineState) -> str:
    """Assemble the spec-agent's prompt.

    It used to be the bare request string, which meant the story was written blind
    to the project's own hard constraints — the `forbidden` list, the NFRs, the
    existing modules, PROJECT_RULES. The same constraint block was already injected
    for the architect, the coder and the tester, so a criterion that contradicted a
    hard constraint got written at stage 2 and could only be silently dropped at
    stage 3. Constraints belong at the moment the work is DEFINED, which is the
    playbook's Stage-2 "skills applied as constraints" play.

    Also carries the operator's answers back in when a rejected spec is re-run, so
    a Checkpoint-1 rejection is a conversation rather than a dead end.
    """
    parts: list[str] = []
    if state.get("project_spec"):
        parts.append(f"{state['project_spec']}\n")
    memory = _project_memory_block(state)
    if memory:
        parts.append(memory)

    findings = state.get("prior_findings") or []
    if state.get("triggered_by") == "spec-rejected" and findings:
        parts.append(
            "## Operator feedback on your previous story (it was REJECTED)\n\n"
            "Re-specify the work taking these answers as settled. Do not re-ask "
            "them, and do not simply re-propose the same story:\n"
            + "\n".join(f"- {f}" for f in findings)
            + "\n"
        )

    parts.append(f"## Request\n\n{state['request']}")
    if len(parts) > 1:
        parts.append(
            "\nDefine the story WITHIN the constraints above. If a constraint makes "
            "the request impossible as stated, say so in `questions` rather than "
            "writing an acceptance criterion that violates it."
        )
    return "\n".join(parts)


def _write_chain_artifact(state: PipelineState, kind: str, *args: Any) -> str | None:
    """Write one link of the committed artifact chain (best-effort).

    Evidence must never be able to fail a run — but a silent `except: pass` is how
    the trust-package writer stayed invisible across 16 runs, so the failure is
    returned for the caller to record rather than swallowed.
    """
    project_dir = state.get("project_dir")
    if not project_dir:
        return None
    try:
        writer = {
            "intent": artifacts.write_intent,
            "spec": artifacts.write_spec,
            "plan": artifacts.write_plan,
        }[kind]
        path = writer(Path(project_dir), state["story_id"], *args)
        return str(path) if path else None
    except (OSError, KeyError, ValueError):
        return None


def node_spec_agent(state: PipelineState) -> dict[str, Any]:
    conn = _get_db_conn(state)
    try:
        update_run_stage(conn, state["run_id"], "spec-agent")
        conn.commit()

        # Link 1 of the chain: the operator's raw ask, on disk with an author and a
        # date, BEFORE any agent interprets it — so a run that dies at the spec
        # still leaves a record of what was asked.
        intent_path = _write_chain_artifact(state, "intent", state.get("request", ""))

        prompt = build_spec_prompt(state)
        result, parsed = _run_agent_json(state, "spec-agent", prompt)

        # Handle synthetic blocked response from _extract_json
        if parsed.get("error") == "Agent did not return valid JSON":
            agent_said = parsed.get("agent_response", "unknown")
            error = f"spec-agent did not return JSON. Agent said: {agent_said}"
            log_agent(
                conn, state["run_id"], "spec-agent", prompt,
                result.output, verdict="blocked", duration_secs=result.duration_secs,
                **_usage_kwargs(result),
            )
            finish_run(conn, state["run_id"], "blocked", error=error)
            conn.commit()
            return {
                "spec_raw": result.output,
                "spec": {"verdict": "blocked", "title": "", "acceptance_criteria": [], "tasks": [], "questions": [f"Agent went off-script: {agent_said}"]},
                "status": "blocked",
                "error": error,
            }

        spec = SpecOutput.model_validate(parsed)

        log_agent(
            conn, state["run_id"], "spec-agent", prompt,
            result.output, verdict=spec.verdict, duration_secs=result.duration_secs,
            **_usage_kwargs(result),
        )
        # Give the story its real title. Do NOT overwrite the canonical story_id
        # (state["story_id"] is the DB row); the agent's spec.story_id is its own
        # numbering and clobbering it would orphan later story-row updates.
        if spec.title:
            update_story_title(conn, state["story_id"], spec.title)
        conn.commit()

        # Link 2: the story the design must answer. The ADR recorded the decision
        # but never the requirements it was a decision about.
        spec_path = _write_chain_artifact(state, "spec", spec)

        out: dict[str, Any] = {"spec_raw": result.output, "spec": parsed}
        if intent_path:
            out["intent_path"] = intent_path
        if spec_path:
            out["spec_path"] = spec_path
        return out
    except Exception as e:
        log_agent(
            conn, state["run_id"], "spec-agent",
            locals().get("prompt") or state.get("request", ""), str(e), verdict="error",
        )
        finish_run(conn, state["run_id"], "failed", error=f"spec-agent failed: {e}")
        conn.commit()
        return {"status": "failed", "error": f"spec-agent failed: {e}"}
    finally:
        conn.close()


# ── Node: gate-1 ──────────────────────────────────────────────────

def settled_threshold_terms(state: PipelineState) -> frozenset[str]:
    """Threshold terms a HUMAN has actually settled for this project.

    Deliberately NOT the whole project-memory block. ADRs are authored by the
    architect-agent and `memory.render_adr` stamps every one
    `Status: proposed (pending architecture sign-off)` — never approved. Reading
    them here let an agent-invented number launder itself into a decision:
    observed live, one run's architect chose "24 hours" and wrote an ADR, and the
    NEXT run then passed the ambiguity gate on the strength of that ADR and chose
    48 hours instead. Two runs, two contradictory undocumented business rules,
    neither approved by anyone.

    Sources that count:
      - the project spec (operator-authored JSON),
      - PROJECT_RULES.md (operator-authored),
      - an ADR whose Status line records an actual approval.
    """
    parts: list[str] = [state.get("project_spec") or ""]

    # The operator's OWN answers at this run's checkpoints. These are the most
    # authoritative source there is — they were typed by the person the gate
    # exists to protect. Omitting them made the gate un-dischargeable: the
    # operator defined "overdue", the spec-agent used the definition correctly,
    # and the gate asked the same question again. An interruption that cannot be
    # answered spends the scarcest resource this factory has and buys nothing.
    run_id, db_path = state.get("run_id"), state.get("db_path")
    if run_id and db_path:
        try:
            conn = _get_db_conn(state)
            try:
                rows = conn.execute(
                    "SELECT human_response FROM gate_results WHERE run_id = ? "
                    "AND human_response IS NOT NULL",
                    (run_id,),
                ).fetchall()
            finally:
                conn.close()
            parts.extend(r["human_response"] or "" for r in rows)
        except sqlite3.Error:
            pass  # evidence is best-effort; never let it break the gate

    project_dir = state.get("project_dir")
    if project_dir:
        root = Path(project_dir)
        rules = root / "PROJECT_RULES.md"
        if rules.is_file():
            try:
                parts.append(rules.read_text(encoding="utf-8"))
            except OSError:
                pass
        adr_dir = root / "docs" / "architecture" / "adr"
        if adr_dir.is_dir():
            for adr in sorted(adr_dir.glob("ADR-*.md")):
                if f"ADR-{state.get('story_id')}-" in adr.name:
                    continue  # never let this story's own ADR settle its own question
                try:
                    text = adr.read_text(encoding="utf-8")
                except OSError:
                    continue
                status = next(
                    (ln for ln in text.splitlines() if ln.lower().startswith("- status:")),
                    "",
                ).lower()
                if "approved" in status and "pending" not in status:
                    parts.append(text)
    return defined_threshold_terms("\n".join(parts))


def node_gate_1(state: PipelineState) -> dict[str, Any]:
    if state.get("status") in ("failed", "blocked"):
        return state

    spec = SpecOutput.model_validate(state["spec"])
    result = gate_after_spec(
        spec,
        defined_terms=settled_threshold_terms(state),
        # Keyed off the operator's own words: a term THEY used without a
        # number needs sign-off however the agents resolve it.
        request=state.get("request", ""),
    )

    error = f"Gate 1 failed: {result.reason}" if not result.passed else None
    conn = _get_db_conn(state)
    try:
        human_q_text = "\n\n".join(result.human_questions) if result.human_questions else None
        log_gate(
            conn, state["run_id"], result.gate, result.passed, result.reason,
            needs_human=result.needs_human, human_questions=human_q_text,
        )
        # A rejection must be PERSISTED, not just returned in graph state. Without
        # this the run stayed 'running' with a NULL error forever: invisible to
        # `factory queue`, and eventually mopped up by `reconcile` as a dead
        # process — misreporting a policy decision as a crash.
        if not result.passed:
            update_story_status(conn, state["story_id"], "failed")
            finish_run(conn, state["run_id"], "failed", error=error)
        elif result.needs_human:
            finish_run(conn, state["run_id"], "waiting_human", error=None)
            update_run_stage(conn, state["run_id"], "gate-1-human")
        conn.commit()
    finally:
        conn.close()

    gate_dict = {
        "gate": result.gate,
        "passed": result.passed,
        "reason": result.reason,
        "needs_human": result.needs_human,
        "human_questions": result.human_questions,
    }

    if not result.passed:
        return {"gate_1": gate_dict, "status": "failed", "error": error}

    if result.needs_human:
        return {"gate_1": gate_dict, "status": "waiting_human"}

    return {"gate_1": gate_dict}


# ── Node: architect-agent ─────────────────────────────────────────

def node_architect_agent(state: PipelineState) -> dict[str, Any]:
    if state.get("status") == "failed":
        return state

    conn = _get_db_conn(state)
    try:
        update_run_stage(conn, state["run_id"], "architect-agent")
        conn.commit()

        spec_context = f"## Story\n\n```json\n{json.dumps(state['spec'], indent=2)}\n```\n\n"
        project_context = ""
        if state.get("project_spec"):
            project_context = f"{state['project_spec']}\n\n"
        memory_context = _project_memory_block(state)
        repo_context = _repo_inventory_block(state)
        feedback_context = _reviewer_feedback_block(state)

        prompt = (
            f"{project_context}"
            f"{memory_context}"
            f"{repo_context}"
            f"{feedback_context}"
            f"{spec_context}"
            f"## Original Request\n\n{state['request']}\n\n"
            "Design the technical approach for this story. "
            "You MUST stay within the project specification above, honor the Project "
            "Rules, and stay consistent with the Prior Architecture Decisions. "
            "Do not introduce technologies, patterns, or modules not listed in the spec."
        )

        result, parsed = _run_agent_json(state, "architect-agent", prompt)

        # Handle synthetic blocked response
        if parsed.get("error") == "Agent did not return valid JSON":
            log_agent(
                conn, state["run_id"], "architect-agent", prompt,
                result.output, verdict="blocked", duration_secs=result.duration_secs,
                **_usage_kwargs(result),
            )
            agent_said = parsed.get("agent_response", "unknown")
            error = f"architect-agent did not return JSON. Agent said: {agent_said}"
            # PERSIST it. Returning 'failed' in graph state only left the run as
            # 'running' with a NULL error — invisible to `factory queue` and later
            # mislabelled by `reconcile` as a dead process.
            update_story_status(conn, state["story_id"], "failed")
            finish_run(conn, state["run_id"], "failed", error=error)
            conn.commit()
            return {
                "architect_raw": result.output,
                "architect": {"verdict": "fail", "architecture_notes": f"Agent went off-script: {agent_said}", "modules_affected": [], "risks": []},
                "status": "failed",
                "error": error,
            }

        arch = ArchitectOutput.model_validate(parsed)

        log_agent(
            conn,
            state["run_id"],
            "architect-agent",
            prompt,
            result.output,
            verdict=arch.verdict,
            duration_secs=result.duration_secs,
            **_usage_kwargs(result),
        )
        conn.commit()

        # Persist the decision as an ADR (decision memory). Project runs only.
        adr_path = None
        if state.get("project_dir") and arch.verdict != "fail":
            spec = SpecOutput.model_validate(state["spec"])
            adr_path = str(
                write_adr(Path(state["project_dir"]), state["story_id"], spec.title, arch)
            )

        out: dict[str, Any] = {"architect_raw": result.output, "architect": parsed}
        if adr_path:
            out["adr_path"] = adr_path
        # Link 3: the plan. `order_tasks` already computes the dependency order on
        # every coder call and threw it away; committing it makes the sequence a
        # reviewer reads the sequence that ran, and its declared scope is what the
        # trust package checks the real diff against.
        if arch.verdict != "fail":
            plan_path = _write_chain_artifact(
                state, "plan", SpecOutput.model_validate(state["spec"]), arch
            )
            if plan_path:
                out["plan_path"] = plan_path
        return out
    except Exception as e:
        error = f"architect-agent failed: {e}"
        log_agent(conn, state["run_id"], "architect-agent", "", str(e), verdict="error")
        update_story_status(conn, state["story_id"], "failed")
        finish_run(conn, state["run_id"], "failed", error=error)
        conn.commit()
        return {"status": "failed", "error": error}
    finally:
        conn.close()


# ── Node: gate-2 ──────────────────────────────────────────────────

def node_gate_2(state: PipelineState) -> dict[str, Any]:
    if state.get("status") == "failed":
        return state

    arch = ArchitectOutput.model_validate(state["architect"])
    result = gate_after_architect(arch)

    conn = _get_db_conn(state)
    try:
        human_q_text = "\n\n".join(result.human_questions) if result.human_questions else None
        log_gate(
            conn, state["run_id"], result.gate, result.passed, result.reason,
            needs_human=result.needs_human, human_questions=human_q_text,
        )
        if not result.passed:
            finish_run(conn, state["run_id"], "failed", error=f"Gate 2 failed: {result.reason}")
        elif result.needs_human:
            finish_run(conn, state["run_id"], "waiting_human", error=None)
            update_run_stage(conn, state["run_id"], "gate-2-human")
        conn.commit()
    finally:
        conn.close()

    gate_dict = {
        "gate": result.gate,
        "passed": result.passed,
        "reason": result.reason,
        "needs_human": result.needs_human,
        "human_questions": result.human_questions,
    }

    if not result.passed:
        return {"gate_2": gate_dict, "status": "failed", "error": f"Gate 2 failed: {result.reason}"}

    if result.needs_human:
        return {"gate_2": gate_dict, "status": "waiting_human"}

    return {"gate_2": gate_dict}


# ── Node: coder-agent ─────────────────────────────────────────────

def _coder_remediation(state: PipelineState, conn: sqlite3.Connection) -> dict[str, Any]:
    """One cross-cutting coder pass to resolve tester findings, then back to the
    tester. Runs on the frontier tier (escalated via attempt_number) because a
    remediation is reasoning-heavy. A pass that breaks the build stops the run.
    """
    spec = SpecOutput.model_validate(state["spec"])
    root = Path(state.get("opencode_cwd") or ".")
    findings = state.get("prior_findings") or []
    diff = collect_repo_diff(root) or ""
    project_context = f"{state['project_spec']}\n\n" if state.get("project_spec") else ""
    prompt = build_remediation_pack(
        spec, state["architect"], findings, diff,
        project_context=project_context, memory_context=_project_memory_block(state),
    )

    result, parsed = _run_agent_json(state, "coder-agent", prompt, slot="remediation")
    if parsed.get("error") == "Agent did not return valid JSON":
        agent_said = parsed.get("agent_response", "unknown")
        log_agent(conn, state["run_id"], "coder-agent", prompt, result.output,
                  verdict="blocked", duration_secs=result.duration_secs,
                  stage_type="remediation", **_usage_kwargs(result))
        finish_run(conn, state["run_id"], "blocked", error=f"[remediation] off-script: {agent_said}")
        conn.commit()
        return {"coder_raw": result.output, "coder": parsed, "remediation": False,
                "next_action": "give_up", "status": "blocked",
                "error": f"remediation coder did not return JSON: {agent_said}"}

    coder = CoderOutput.model_validate(parsed)
    for block in coder.code_blocks:
        block.path = normalize_block_path(root, block.path)
    written = materialize_code_blocks(list(coder.code_blocks), root=root)
    log_agent(conn, state["run_id"], "coder-agent", prompt, result.output,
              verdict=coder.verdict, duration_secs=result.duration_secs,
              stage_type="remediation", **_usage_kwargs(result))

    verify_result = verify_changes(written, root=root)
    unclaimed, _missing = _scope_diff(coder, written, root)
    if unclaimed:
        gate_reason = (
            f"[remediation] GOVERNANCE: out-of-band file writes not declared in "
            f"code_blocks: {sorted(unclaimed)}."
        )
        log_gate(conn, state["run_id"], "gate-build", False, gate_reason)
        update_story_status(conn, state["story_id"], "blocked")
        finish_run(conn, state["run_id"], "blocked", error=gate_reason)
        conn.commit()
        return {"coder_raw": result.output, "coder": parsed, "remediation": False,
                "gate_build": {"passed": False, "verdict": "fail", "reason": gate_reason,
                               "task": "remediation"},
                "next_action": "give_up", "status": "blocked", "error": gate_reason}

    gate_reason = f"[remediation] {verify_result.summary}"
    log_gate(conn, state["run_id"], "gate-build", verify_result.passed, gate_reason)
    gate_build = {"passed": verify_result.passed, "verdict": verify_result.verdict,
                  "reason": gate_reason, "task": "remediation"}

    if verify_result.passed and coder.verdict == "complete":
        git_commit_all(root, "factory: remediation (tester findings)")
        conn.commit()
        # Back to the tester to re-judge the fixed implementation.
        return {"coder_raw": result.output, "coder": parsed, "gate_build": gate_build,
                "remediation": False, "next_action": "complete"}

    error = f"remediation failed gate-build: {gate_reason}"
    update_story_status(conn, state["story_id"], "failed")
    finish_run(conn, state["run_id"], "failed", error=error)
    conn.commit()
    return {"coder_raw": result.output, "coder": parsed, "gate_build": gate_build,
            "remediation": False, "next_action": "give_up", "status": "failed", "error": error}


def node_coder_agent(state: PipelineState) -> dict[str, Any]:
    """Implement ONE task per invocation (dependency-ordered), then route back
    for the next task or a bounded per-task retry. This makes the spec-agent's
    decomposition executional and keeps each LLM call small enough to finish.
    """
    if state.get("status") == "failed":
        return state

    conn = _get_db_conn(state)
    try:
        update_run_stage(conn, state["run_id"], "coder-agent")
        conn.commit()

        # Tester-driven remediation: a cross-cutting fix pass, not a per-task call.
        if state.get("remediation"):
            return _coder_remediation(state, conn)

        spec = SpecOutput.model_validate(state["spec"])
        tasks = order_tasks(list(spec.tasks))
        if not tasks:
            finish_run(conn, state["run_id"], "failed", error="No tasks to implement")
            conn.commit()
            return {"status": "failed", "error": "No tasks to implement"}

        task_index = state.get("task_index", 0)
        completed = list(state.get("tasks_completed", []))
        attempt = state.get("attempt_number", 1)
        task = tasks[task_index]

        project_context = f"{state['project_spec']}\n\n" if state.get("project_spec") else ""
        prompt = build_task_pack(
            task,
            spec,
            state["architect"],
            project_context=project_context,
            memory_context=_project_memory_block(state),
            repo_context=_repo_inventory_block(state),
            retry_context=_retry_context_block(state, attempt),
            completed=completed,
            position=(task_index + 1, len(tasks)),
        )

        result, parsed = _run_agent_json(state, "coder-agent", prompt, slot=task.id)

        # Handle synthetic blocked response
        if parsed.get("error") == "Agent did not return valid JSON":
            agent_said = parsed.get("agent_response", "unknown")
            log_agent(
                conn, state["run_id"], "coder-agent", prompt,
                result.output, verdict="blocked", duration_secs=result.duration_secs,
                stage_type=task.id, **_usage_kwargs(result),
            )
            finish_run(conn, state["run_id"], "blocked",
                       error=f"[{task.id}] Agent went off-script: {agent_said}")
            conn.commit()
            return {
                "coder_raw": result.output,
                "coder": {"verdict": "blocked",
                          "implementation_summary": f"[{task.id}] off-script: {agent_said}",
                          "assumptions": [agent_said]},
                "status": "blocked",
                "error": f"coder-agent did not return JSON for {task.id}. Agent said: {agent_said}",
            }

        coder = CoderOutput.model_validate(parsed)

        # Coder → architect feedback: the design itself is infeasible. Honored only
        # at the first task's first attempt (nothing committed yet → clean restart);
        # later, or once the budget is spent, park for the human rather than ship
        # code that works around a design known to be wrong.
        if coder.design_feedback.strip():
            count = state.get("rearchitect_count", 0)
            log_agent(conn, state["run_id"], "coder-agent", prompt, result.output,
                      verdict="design-infeasible", duration_secs=result.duration_secs,
                      stage_type=task.id, **_usage_kwargs(result))
            if task_index == 0 and attempt == 1 and count < MAX_REARCHITECT_LOOPS:
                conn.commit()
                return {
                    "coder_raw": result.output, "coder": parsed,
                    "next_action": "rearchitect",
                    "triggered_by": "design-infeasible",
                    "prior_findings": [coder.design_feedback.strip()],
                    "rearchitect_count": count + 1,
                    "task_index": 0, "attempt_number": 1, "tasks_completed": [],
                }
            error = (
                f"coder reports the design is infeasible but re-architecture budget is "
                f"spent (max {MAX_REARCHITECT_LOOPS}): {coder.design_feedback.strip()}"
            )
            update_story_status(conn, state["story_id"], "failed")
            finish_run(conn, state["run_id"], "failed", error=error)
            conn.commit()
            return {"coder_raw": result.output, "coder": parsed,
                    "next_action": "give_up", "status": "failed", "error": error}

        root = Path(state.get("opencode_cwd") or ".")
        # Agents prefix paths with 'repo/' but cwd IS the repo -> de-double so
        # files land at the repo root and claimed==actual for the scope check.
        for block in coder.code_blocks:
            block.path = normalize_block_path(root, block.path)
        written = materialize_code_blocks(list(coder.code_blocks), root=root)

        log_agent(
            conn, state["run_id"], "coder-agent", prompt, result.output,
            verdict=coder.verdict, duration_secs=result.duration_secs,
            stage_type=task.id, **_usage_kwargs(result),
        )

        # ── gate-build: verify the cumulative repo after this task ──
        verify_result = verify_changes(written, root=root)
        unclaimed, missing = _scope_diff(coder, written, root)
        scope_note = _scope_note(unclaimed, missing)
        gate_reason = f"[{task.id}] {verify_result.summary}"
        if scope_note:
            gate_reason += f"; {scope_note}"

        # Governance: any file that landed in the repo but was NOT declared in
        # code_blocks is an out-of-band write (agent bypassing materialize). Block
        # it — retrying won't help, and shipping un-vetted code defeats the gates.
        if unclaimed:
            gate_reason = (
                f"[{task.id}] GOVERNANCE: out-of-band file writes not declared in "
                f"code_blocks: {sorted(unclaimed)}. Agents must return code via "
                f"code_blocks, not write files directly."
            )
            log_gate(conn, state["run_id"], "gate-build", False, gate_reason)
            update_story_status(conn, state["story_id"], "blocked")
            finish_run(conn, state["run_id"], "blocked", error=gate_reason)
            conn.commit()
            return {
                "coder_raw": result.output, "coder": parsed,
                "gate_build": {"passed": False, "verdict": "fail", "reason": gate_reason,
                               "task": task.id},
                "next_action": "give_up", "status": "blocked", "error": gate_reason,
            }

        log_gate(conn, state["run_id"], "gate-build", verify_result.passed, gate_reason)
        gate_build = {
            "passed": verify_result.passed,
            "verdict": verify_result.verdict,
            "reason": gate_reason,
            "task": task.id,
        }

        # Task succeeded? Advance to the next task, or complete the story.
        if verify_result.passed and coder.verdict == "complete":
            completed = completed + [task.id]
            # Checkpoint this task so the NEXT task's scope check sees only its
            # own new files (otherwise prior tasks' files look "out-of-band").
            git_commit_all(root, f"factory: {task.id} {task.title}")
            if task_index + 1 >= len(tasks):
                # Coding done — hand to the tester gate, which finalizes the run.
                conn.commit()
                return {"coder_raw": result.output, "coder": parsed, "gate_build": gate_build,
                        "next_action": "complete", "tasks_completed": completed}
            conn.commit()
            return {"coder_raw": result.output, "coder": parsed, "gate_build": gate_build,
                    "next_action": "next_task", "task_index": task_index + 1,
                    "attempt_number": 1, "prior_findings": [], "tasks_completed": completed}

        # Task failed: retry the SAME task within budget, else give up + queue.
        cost_so_far = get_run_cost(conn, state["run_id"])
        budget_left = attempt < MAX_CODER_ATTEMPTS and cost_so_far < MAX_TASK_COST_USD
        if not verify_result.passed and budget_left:
            conn.commit()  # keep run 'running'; same task_index -> retries this task
            return {
                "coder_raw": result.output,
                "coder": parsed,
                "gate_build": gate_build,
                "next_action": "retry",
                "attempt_number": attempt + 1,
                "triggered_by": "gate-build",
                "prior_findings": [gate_reason],
            }

        if not verify_result.passed:
            error = (
                f"gate-build failed on {task.id} after {attempt} attempt(s) "
                f"(budget: {MAX_CODER_ATTEMPTS} attempts / ${MAX_TASK_COST_USD:.2f}, "
                f"spent ${cost_so_far:.4f}): {gate_reason}"
            )
        else:
            error = f"coder reported verdict '{coder.verdict}' on {task.id}"
        update_story_status(conn, state["story_id"], "failed")
        finish_run(conn, state["run_id"], "failed", error=error)
        conn.commit()
        return {
            "coder_raw": result.output,
            "coder": parsed,
            "gate_build": gate_build,
            "next_action": "give_up",
            "status": "failed",
            "error": error,
        }
    except Exception as e:
        log_agent(conn, state["run_id"], "coder-agent", "", str(e), verdict="error")
        finish_run(conn, state["run_id"], "failed", error=str(e))
        conn.commit()
        return {"status": "failed", "error": f"coder-agent failed: {e}"}
    finally:
        conn.close()


# ── Node: tester-agent ────────────────────────────────────────────

def _changes_under_review_block(state: PipelineState) -> str:
    """The artifact the tester judges: the REAL cumulative git diff of every task's
    change when available, falling back to the agent's self-report off-git."""
    diff = collect_repo_diff(Path(state.get("opencode_cwd") or "."))
    if diff:
        return f"## Cumulative changes under review (real git diff)\n\n```diff\n{diff}\n```\n\n"
    if diff == "":
        return "## Cumulative changes under review\n\n(git repo is clean — no diff detected)\n\n"
    # Not a git repo — fall back to the last task's self-reported implementation.
    return (
        "## Implementation (self-reported; repo is not under git)\n\n"
        f"```json\n{json.dumps(state.get('coder', {}), indent=2)}\n```\n\n"
    )


def build_tester_prompt(state: PipelineState) -> str:
    """Assemble the tester's prompt, including the VERSIONED review policy.

    The review passes, the severity ladder and the skip list used to live as prose
    in `.opencode/agents/tester-agent.md`, with the blocking threshold in
    `gates.py` — one policy in two places. `docs/factory/REVIEW.md` is now the
    single home; the agent definition keeps only the role and the JSON contract.
    """
    project_context = f"{state['project_spec']}\n\n" if state.get("project_spec") else ""
    return (
        f"{project_context}"
        f"{_project_memory_block(state)}"
        f"{review_policy.policy_block()}"
        f"## Story\n\n```json\n{json.dumps(state.get('spec', {}), indent=2)}\n```\n\n"
        f"## Architecture\n\n```json\n{json.dumps(state.get('architect', {}), indent=2)}\n```\n\n"
        f"{_changes_under_review_block(state)}"
        f"## Build gate result\n\n{json.dumps(state.get('gate_build', {}), indent=2)}\n\n"
        "Review the implementation against the acceptance criteria and the review "
        "policy above, and emit your QA / security / performance sub-verdicts as JSON."
    )


def node_tester_agent(state: PipelineState) -> dict[str, Any]:
    if state.get("status") in ("failed", "blocked"):
        return state

    conn = _get_db_conn(state)
    try:
        update_run_stage(conn, state["run_id"], "tester-agent")
        conn.commit()

        prompt = build_tester_prompt(state)

        result, parsed = _run_agent_json(state, "tester-agent", prompt)
        if parsed.get("error") == "Agent did not return valid JSON":
            agent_said = parsed.get("agent_response", "unknown")
            log_agent(conn, state["run_id"], "tester-agent", prompt, result.output,
                      verdict="blocked", duration_secs=result.duration_secs, **_usage_kwargs(result))
            finish_run(conn, state["run_id"], "blocked", error=f"tester off-script: {agent_said}")
            conn.commit()
            return {"tester_raw": result.output,
                    "tester": {"overall": "blocked", "summary": agent_said},
                    "status": "blocked", "error": f"tester-agent did not return JSON: {agent_said}"}

        tester = TesterOutput.model_validate(parsed)
        log_agent(conn, state["run_id"], "tester-agent", prompt, result.output,
                  verdict=tester.overall, duration_secs=result.duration_secs, **_usage_kwargs(result))
        conn.commit()
        return {"tester_raw": result.output, "tester": parsed}
    except Exception as e:
        log_agent(conn, state["run_id"], "tester-agent", "", str(e), verdict="error")
        finish_run(conn, state["run_id"], "failed", error=f"tester-agent failed: {e}")
        conn.commit()
        return {"status": "failed", "error": f"tester-agent failed: {e}"}
    finally:
        conn.close()


# ── Node: gate-test ───────────────────────────────────────────────

def _write_trust_package(state: PipelineState) -> None:
    """Best-effort: save the assembled trust package to docs/releases/ (project runs)."""
    project_dir = state.get("project_dir")
    if not project_dir:
        return
    try:
        import json as _json

        from factory import trust_package

        pkg = trust_package.assemble(Path(state["db_path"]), state["run_id"])
        releases = Path(project_dir) / "docs" / "releases"
        releases.mkdir(parents=True, exist_ok=True)
        (releases / f"run-{state['run_id']}-trust-package.json").write_text(
            _json.dumps(pkg, indent=2), encoding="utf-8"
        )
    except Exception:
        pass  # never let release-note I/O fail the run


def _tester_findings(tester: TesterOutput) -> list[str]:
    """Flatten a failed tester's sub-verdicts into actionable findings for the coder."""
    findings: list[str] = []
    if tester.missing_coverage:
        findings.append("Missing test coverage: " + ", ".join(tester.missing_coverage))
    if tester.security_findings:
        findings.append("Security: " + ", ".join(tester.security_findings))
    if tester.performance_findings:
        findings.append("Performance: " + ", ".join(tester.performance_findings))
    if tester.summary:
        findings.append(tester.summary)
    return findings or ["Tester failed; address the failing QA/security/performance verdicts"]


def node_gate_test(state: PipelineState) -> dict[str, Any]:
    if state.get("status") in ("failed", "blocked"):
        return state

    tester = TesterOutput.model_validate(state["tester"])
    result = gate_after_tester(tester)

    # Surface any acceptance criterion the tester never assessed (a silent drop).
    # Non-blocking here — recorded as evidence; the trust package carries the full
    # mapping and the release checkpoint is where it should hard-enforce.
    reason = result.reason
    try:
        spec_obj = SpecOutput.model_validate(state["spec"])
        unassessed = unassessed_criteria(
            trace_criteria(spec_obj.acceptance_criteria, tester.ac_coverage, tester.missing_coverage)
        )
        if unassessed:
            reason += f"; ⚠️ {len(unassessed)} AC unassessed by tester: {unassessed}"
    except Exception:
        pass  # never let traceability annotation fail the gate

    gate_dict = {"gate": result.gate, "passed": result.passed, "reason": reason}

    conn = _get_db_conn(state)
    try:
        log_gate(conn, state["run_id"], result.gate, result.passed, reason)

        if result.passed:
            update_story_status(conn, state["story_id"], "completed")
            finish_run(conn, state["run_id"], "completed")
            _write_trust_package(state)
            conn.commit()
            return {"gate_test": gate_dict, "status": "completed"}

        # Tester failed — route back to the coder for a bounded remediation pass
        # carrying the findings, unless the remediation or cost budget is spent.
        tester_attempt = state.get("tester_attempt", 1)
        cost_so_far = get_run_cost(conn, state["run_id"])
        if tester_attempt <= MAX_TESTER_REMEDIATIONS and cost_so_far < MAX_TASK_COST_USD:
            conn.commit()  # leave the run 'running'; route_after_gate_test → coder
            return {
                "gate_test": gate_dict,
                "remediation": True,
                "triggered_by": "tester-agent",
                "prior_findings": _tester_findings(tester),
                "tester_attempt": tester_attempt + 1,
                "attempt_number": 2,  # escalate the remediation coder to the frontier tier
            }

        error = (
            f"Gate test failed after {tester_attempt} tester pass(es) "
            f"(remediation budget {MAX_TESTER_REMEDIATIONS}, spent ${cost_so_far:.4f}): "
            f"{result.reason}"
        )
        update_story_status(conn, state["story_id"], "failed")
        finish_run(conn, state["run_id"], "failed", error=error)
        conn.commit()
        return {"gate_test": gate_dict, "status": "failed", "error": error}
    finally:
        conn.close()


# ── Conditional edges ─────────────────────────────────────────────

def should_continue_after_gate_1(state: PipelineState) -> str:
    # `waiting_human` is Checkpoint 1: the line stops until the operator answers.
    if state.get("status") in ("failed", "blocked", "waiting_human"):
        return END
    return "architect-agent"


def should_continue_after_gate_2(state: PipelineState) -> str:
    if state.get("status") in ("failed", "waiting_human"):
        return END
    return "coder-agent"


def route_after_coder(state: PipelineState) -> str:
    """Loop back for the next task / retry, re-architect on infeasible-design
    feedback, hand a completed story to the tester, else end."""
    action = state.get("next_action")
    if action in ("retry", "next_task"):
        return "coder-agent"
    if action == "rearchitect":
        return "architect-agent"
    if action == "complete":
        return "tester-agent"
    return END


def should_continue_after_spec(state: PipelineState) -> str:
    if state.get("status") in ("failed", "blocked"):
        return END
    return "gate-1"


def route_after_gate_test(state: PipelineState) -> str:
    """Loop back to the coder for a tester-driven remediation pass, else end."""
    if state.get("remediation") and state.get("status") not in ("failed", "blocked", "completed"):
        return "coder-agent"
    return END


# ── Build the graph ───────────────────────────────────────────────

def _wire_tester(graph: StateGraph) -> None:
    """Attach the post-implementation tester gate (coder 'complete' routes here)."""
    graph.add_node("tester-agent", node_tester_agent)
    graph.add_node("gate-test", node_gate_test)
    graph.add_edge("tester-agent", "gate-test")
    graph.add_conditional_edges("gate-test", route_after_gate_test)


def build_pipeline() -> StateGraph:
    graph = StateGraph(PipelineState)

    graph.add_node("spec-agent", node_spec_agent)
    graph.add_node("gate-1", node_gate_1)
    graph.add_node("architect-agent", node_architect_agent)
    graph.add_node("gate-2", node_gate_2)
    graph.add_node("coder-agent", node_coder_agent)

    graph.set_entry_point("spec-agent")

    graph.add_conditional_edges("spec-agent", should_continue_after_spec)
    graph.add_conditional_edges("gate-1", should_continue_after_gate_1)
    graph.add_conditional_edges("gate-2", should_continue_after_gate_2)
    graph.add_edge("architect-agent", "gate-2")
    graph.add_conditional_edges("coder-agent", route_after_coder)
    _wire_tester(graph)

    return graph


def compile_pipeline():
    return build_pipeline().compile()


def build_coder_only_pipeline() -> StateGraph:
    """Mini-pipeline entered at the coder (resume after human approval). Includes
    the architect + gate-2 nodes so the coder's infeasible-design feedback can
    still route to a re-architecture; they're never entered on the happy path."""
    graph = StateGraph(PipelineState)
    graph.add_node("coder-agent", node_coder_agent)
    graph.add_node("architect-agent", node_architect_agent)
    graph.add_node("gate-2", node_gate_2)
    graph.set_entry_point("coder-agent")
    graph.add_conditional_edges("coder-agent", route_after_coder)
    graph.add_edge("architect-agent", "gate-2")
    graph.add_conditional_edges("gate-2", should_continue_after_gate_2)
    _wire_tester(graph)
    return graph


def compile_coder_only_pipeline():
    return build_coder_only_pipeline().compile()


def resume_entry_for(gate_name: str | None, action: str) -> str:
    """Which stage a parked run re-enters, given the gate that parked it.

    Keeps the routing decision out of the CLI's I/O so it is testable offline.

    - Checkpoint 1 (gate-1-spec): approve accepts the story as written and moves
      to design; reject means the operator ANSWERED the open questions, so the
      story itself must be rewritten — re-enter at the spec-agent.
    - Checkpoint 2 (gate-2-architect): approve continues to implementation;
      reject re-runs the design with the feedback. (Pre-existing behaviour, and
      the fallback for any gate we don't recognize.)
    """
    if gate_name == "gate-1-spec":
        return "architect" if action == "approve" else "spec"
    return "coder" if action == "approve" else "architect"


def build_spec_resume_pipeline() -> StateGraph:
    """Re-entry from the story itself: used when a human REJECTS Checkpoint 1.
    Re-runs the spec-agent (carrying the operator's answers as `prior_findings`)
    and then flows on through the normal line."""
    graph = StateGraph(PipelineState)
    graph.add_node("spec-agent", node_spec_agent)
    graph.add_node("gate-1", node_gate_1)
    graph.add_node("architect-agent", node_architect_agent)
    graph.add_node("gate-2", node_gate_2)
    graph.add_node("coder-agent", node_coder_agent)
    graph.set_entry_point("spec-agent")
    graph.add_conditional_edges("spec-agent", should_continue_after_spec)
    graph.add_conditional_edges("gate-1", should_continue_after_gate_1)
    graph.add_edge("architect-agent", "gate-2")
    graph.add_conditional_edges("gate-2", should_continue_after_gate_2)
    graph.add_conditional_edges("coder-agent", route_after_coder)
    _wire_tester(graph)
    return graph


def compile_spec_resume_pipeline():
    return build_spec_resume_pipeline().compile()


def build_architect_resume_pipeline() -> StateGraph:
    """Re-entry from architecture: used when a human REJECTS the architecture
    checkpoint. Re-runs the architect (with reviewer feedback) → gate-2 → coder.
    """
    graph = StateGraph(PipelineState)
    graph.add_node("architect-agent", node_architect_agent)
    graph.add_node("gate-2", node_gate_2)
    graph.add_node("coder-agent", node_coder_agent)
    graph.set_entry_point("architect-agent")
    graph.add_edge("architect-agent", "gate-2")
    graph.add_conditional_edges("gate-2", should_continue_after_gate_2)
    graph.add_conditional_edges("coder-agent", route_after_coder)
    _wire_tester(graph)
    return graph


def compile_architect_resume_pipeline():
    return build_architect_resume_pipeline().compile()
