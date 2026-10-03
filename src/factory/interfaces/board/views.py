"""What the workflow screen says about an item: plain text, never a JSON dump.

Pure functions over the `runs.dashboard` read model, so what the operator reads
is unit-testable without a terminal.
"""

from __future__ import annotations

from dataclasses import dataclass
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
        lines = [context.get("brief", "").rstrip(), "", "Technical choices"]
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
    position = d.get("position")
    head = d.get("subject") or "Question"
    if position:
        head += f" · question {position[0]} of {position[1]}"
    return "\n".join([head, "", d["question"], "", why(d), context.get("effect", "")])


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
            "wf-backlog", "Propose backlog", not state["backlog_open"],
            "A backlog proposal is already waiting in Needs you, or being written."))
    if state["ready"]:
        actions.append(ProjectAction("wf-propose", f"Propose batch ({state['ready']} ready)"))
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
