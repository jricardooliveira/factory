"""What the workflow screen says about an item: plain text, never a JSON dump.

Pure functions over the `runs.dashboard` read model, so what the operator reads
is unit-testable without a terminal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from factory.domain.interview import TOPIC_TITLES


@dataclass(frozen=True)
class Actions:
    """The buttons a decision offers, and whether it takes a pick or words."""

    primary: str
    secondary: str | None = None
    placeholder: str | None = None  # a text box is shown only when words are asked for
    picker: bool = False


def decision_row(d: dict[str, Any]) -> tuple[str, str]:
    """(state, title) for the list."""
    kind = d["kind"]
    if kind == "backlog":
        return "Approve", f"Backlog proposal · {len(d['context'].get('stories', []))} stories"
    if kind == "brief":
        return "Approve", "Product brief and technical choices"
    if kind == "release":
        return "Release", d["question"]
    subject = d.get("subject")
    return "Question", f"{subject} · {d['question']}" if subject else d["question"]


def why(d: dict[str, Any]) -> str:
    """Why the factory cannot settle this question itself."""
    context = d["context"]
    phase = context.get("phase", "")
    topic = context.get("question", {}).get("topic", "")
    if phase == "story":
        # The subject is already the heading: don't repeat the story's title here.
        return "This story can't be planned until this is settled; the brief leaves it open."
    if phase == "amend":
        return "Your amendment to the brief raises this."
    if phase == "technical":
        return "A technical choice the project spec leaves open."
    if phase == "execution":
        return "This sets how much the factory may do without asking you."
    return f"The product brief needs this: {TOPIC_TITLES.get(topic, topic or 'a missing topic')}."


def decision_body(d: dict[str, Any]) -> str:
    kind, context = d["kind"], d["context"]
    if kind == "backlog":
        stories = context.get("stories", [])
        lines = [f"Backlog proposal · {len(stories)} stories", context.get("effect", "")]
        if context.get("feedback"):
            lines += ["", "Your earlier changes:"] + [f"- {f}" for f in context["feedback"]]
        for n, story in enumerate(stories, 1):
            lines += ["", f"{n}. {story['title']}", f"   {story['request']}"]
            if story.get("rationale"):
                lines.append(f"   Why now: {story['rationale']}")
        return "\n".join(lines)
    if kind == "brief":
        # The short choices first; the brief itself is a document (`decision_document`).
        lines = ["Technical choices"]
        spec = context.get("spec") or {}
        for key, title in (("language", "Language"), ("framework", "Framework"),
                           ("database", "Database"), ("orm", "Data layer")):
            if spec.get(key):
                lines.append(f"- {title}: {spec[key]}")
        if spec.get("additional_tech"):
            lines.append("- Also: " + ", ".join(spec["additional_tech"]))
        return "\n".join(lines)
    if kind == "release":
        shown = {k: v for k, v in context.items() if k not in ("effect", "proposal_id")}
        lines = [d["question"], context.get("effect", ""), ""]
        lines += [f"{k.replace('_', ' ').capitalize()}: {v}" for k, v in shown.items()]
        return "\n".join(lines)
    return "\n".join([why(d), context.get("effect", "")])


def decision_head(d: dict[str, Any]) -> str:
    """Pinned above the scroll: what this is about, never scrolled out of view."""
    if d["kind"] != "question" and not d["context"].get("question"):
        return decision_row(d)[1]
    position = d.get("position")
    head = d.get("subject") or "Question"
    if position:
        head += f" · question {position[0]} of {position[1]}"
    return f"{head}\n{d['question']}"


def decision_document(d: dict[str, Any]) -> str | None:
    """Markdown to RENDER under the text (the brief), or None."""
    return d["context"].get("brief") if d["kind"] == "brief" else None


def decision_actions(d: dict[str, Any]) -> Actions:
    kind = d["kind"]
    if kind == "backlog":
        return Actions("Approve backlog", "Request changes",
                       "What should change? (only for Request changes)")
    if kind == "brief":
        return Actions("Approve brief", "Request changes",
                       "What should change? (only for Request changes)")
    if kind == "release":
        return Actions("Approve integration")
    if d["context"].get("question"):
        return Actions("Answer", picker=True)
    return Actions("Submit answer", placeholder="Your answer")


@dataclass(frozen=True)
class ProjectAction:
    id: str
    label: str
    enabled: bool = True
    reason: str = ""  # why it is disabled, shown as the button's tooltip


def project_actions(state: dict[str, Any] | None) -> list[ProjectAction]:
    """Only the project actions that apply now; a disabled one says why."""
    if not state:
        return []
    busy = "The interview is waiting on you in Needs you, or running."
    if not state["brief"]:
        actions = [ProjectAction("wf-intake", "Interview", not state["intake_open"], busy)]
    elif not state["agreement"]:
        actions = [ProjectAction("wf-intake", "Complete agreement", not state["intake_open"], busy)]
    else:
        actions = [ProjectAction("wf-intake", "Amend brief…", not state["intake_open"], busy)]
    if state["brief"]:
        actions.append(ProjectAction(
            "wf-backlog", "Re-propose backlog" if state.get("stories") else "Propose backlog",
            not state["backlog_open"],
            "A backlog proposal is already waiting in Needs you, or being written."))
    if state["ready"]:
        actions.append(ProjectAction(
            "wf-propose", f"Propose batch ({state['ready']} ready)", not state.get("batch_open"),
            "A batch proposal is already waiting in Needs you: launch or abandon it first."))
    actions.append(ProjectAction("wf-pause", "Resume new starts" if state["paused"]
                                 else "Pause new starts"))
    return actions


def job_row(job: dict[str, Any]) -> tuple[str, str]:
    error = (job.get("error") or "").split("\n", 1)[0]
    state = {"failed": "Failed", "interrupted": "Interrupted", "queued": "Queued",
             "running": "Working"}.get(job["status"], job["status"])
    return state, f"{job['subject']}: {error}" if error else job["subject"]


def job_body(job: dict[str, Any]) -> str:
    lines = [f"{job['subject']} · {job_row(job)[0].lower()}"]
    if job.get("error"):
        lines += ["", job["error"]]
    if job["status"] == "failed" and job.get("retryable"):
        lines += ["", "Nothing was lost: the saved answers stay; Retry runs this step again."]
    elif job["status"] in ("failed", "interrupted"):
        lines += ["", "Work is retained. Inspect the saved error and workspace before "
                      "reconciling; a lost worker's step is never repeated automatically."]
    return "\n".join(lines)


def local_time(stamp: str) -> str:
    """HH:MM:SS in the operator's timezone (records are UTC; the clock on screen is not)."""
    return datetime.fromisoformat(stamp).astimezone().strftime("%H:%M:%S")


_EVENT_TEXT = {
    "backlog: needs_input": "Backlog proposal ready for your review",
    "refine: needs_input": "Refinement has questions for you",
    "refine: ready": "Story prepared: ready for a batch",
    "refine: blocked": "Story prepared, with open points",
    "refine: refining": "Refinement moved to its next step",
}


def event_text(event: dict[str, Any]) -> str:
    """One line in the operator's words, not the worker's status codes."""
    details = event.get("details") or {}
    if event["message"] == "Decision answered" and details.get("question"):
        answered = f"Answered: {details['question']}"
        return f"{answered} — {details['answer']}" if details.get("answer") else answered
    return _EVENT_TEXT.get(event["message"], event["message"])


def event_body(event: dict[str, Any]) -> str:
    lines = [f"{event_text(event)}  ({local_time(event['created_at'])})"]
    for key, value in (event.get("details") or {}).items():
        if key.endswith("_id") or key in ("question", "answer"):
            continue  # identifiers mean nothing to the operator
        shown = ", ".join(map(str, value)) if isinstance(value, list) else value
        lines.append(f"{key.replace('_', ' ').capitalize()}: {shown}")
    return "\n".join(lines)


_STORY_STATE = {
    "Draft": "Not refined yet. Refine story prepares its spec, design and impact — no coding.",
    "Refining": "Being refined; its questions will appear in Needs you.",
    "Needs input": "Waiting on you: answer its questions in Needs you.",
    "Ready": "Prepared and ready to join a batch (Propose batch).",
    "Blocked": "Prepared, but something prevents a batch: see Open points.",
    "Scheduled": "In a launched batch, waiting for capacity.",
    "Working": "Being built.",
    "Done": "Built and released.",
}


def story_body(story: dict[str, Any]) -> str:
    lines = [story["request"], "", f"State: {story['state']} — {_STORY_STATE.get(story['state'], '')}"]
    plan = story.get("plan")
    if plan:
        spec = plan.get("spec") or {}
        if spec.get("acceptance_criteria"):
            lines += ["", "Acceptance criteria"] + [f"- {c}" for c in spec["acceptance_criteria"]]
        if spec.get("tasks"):
            lines += ["", "Tasks"] + [
                f"- {t['id']} {t['title']}" + (f" ({', '.join(t['scope'])})" if t.get("scope") else "")
                for t in spec["tasks"]]
        if plan.get("resources"):
            lines += ["", "Changes"] + [f"- {r['mode']}s {r['kind']} {r['name']}"
                                        for r in plan["resources"]]
        if plan.get("dependencies"):
            lines += ["", "Depends on: " + ", ".join(f"#{d}" for d in plan["dependencies"])]
        if plan.get("uncertainties"):
            lines += ["", "Open points"] + [f"- {u}" for u in plan["uncertainties"]]
        if plan.get("rationale"):
            lines += ["", f"Why it can go alone: {plan['rationale']}"]
    return "\n".join(lines)


@dataclass(frozen=True)
class NextStep:
    action: str  # the workflow screen's action id
    label: str  # the button
    title: str  # the list row
    explanation: str
    story_id: int | None = None


def next_step(data: dict[str, Any]) -> NextStep | None:
    """The ONE thing that can start now for the selected project, if any."""
    state = data.get("project")
    if not state:
        return None
    if not state["brief"]:
        if state["intake_open"]:
            return None
        return NextStep("wf-intake", "Interview", "Interview: define the product",
                        "The factory needs the product brief before anything can be planned.")
    stories = [s for s in data["stories"] if s["project"] == state["slug"]]
    if not stories:
        if state["backlog_open"]:
            return None
        return NextStep("wf-backlog", "Propose backlog", "Propose the backlog",
                        "The brief is approved; the backlog-agent proposes the stories, you approve them.")
    if state["ready"] and not any(p["status"] == "proposed" for p in data["proposals"]):
        return NextStep("wf-propose", "Propose batch", f"Propose a batch of the {state['ready']} ready stories",
                        "Ready stories that do not touch the same areas can be launched together.")
    draft = next((s for s in stories if s["state"] == "Draft" and s["status"] == "approved"), None)
    if draft:
        return NextStep("wf-refine", "Refine story", f"Refine story #{draft['id']} {draft['title']}",
                        "Preparing a story asks only what its request leaves open, then plans it. "
                        "No code is written.", draft["id"])
    # Last: refining does not need it, and the stories should not wait on it.
    if not state["agreement"] and not state["intake_open"]:
        return NextStep("wf-intake", "Complete agreement", "Complete the technical and execution agreement",
                        "This brief predates the technical and execution sections; they are asked once.")
    return None


def batch_body(proposal: dict[str, Any], plans: dict[str, dict], stories: dict[int, dict]) -> str:
    """What launching this batch authorizes, and what was left out and why."""
    payload = proposal["payload"]
    members = [plans.get(i, {}) for i in payload["plan_ids"]]
    each = max((p.get("budget_usd", 0) for p in members), default=0)
    lines = [f"Budget ${payload['budget_usd']:.2f} (up to ${each:.2f} per story) · "
             f"{payload['limit']} at once",
             "Launching lets the factory build the ticked stories, each in its own worktree; "
             "release still needs you."]
    if proposal["status"] == "proposed":
        lines.append("Untick a story to leave it for a later batch.")
    excluded = payload.get("excluded") or {}
    if excluded:
        lines += ["", "Not in this batch"]
        for story_id, reasons in excluded.items():
            title = stories.get(int(story_id), {}).get("title", "")
            lines.append(f"- #{story_id} {title}: " + "; ".join(reasons))
    return "\n".join(lines)


def batch_choice(plan: dict[str, Any], stories: dict[int, dict]) -> str:
    """One tickable line: the story and what it will change."""
    title = stories.get(plan["backlog_id"], {}).get("title", "")
    writes = [r["name"] for r in plan.get("resources", []) if r.get("mode") == "write"]
    return f"#{plan['backlog_id']} {title}" + (f" — changes {', '.join(writes)}" if writes else "")
