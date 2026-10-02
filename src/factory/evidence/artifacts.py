"""The committed artifact chain: INTENT → SPEC → PLAN (→ ADR → diff).

The AI-Native SDLC playbook's central governance claim is that each stage commits
an artifact the next stage reads, and that the chain IS the audit trail — "evidence
is version-controlled and timestamped."

This factory committed exactly one link: the ADR (``evidence.adr.write_adr``). The story
the design answered, the operator's original ask, and the order of work all lived
only in ``agent_logs.output_text`` inside a ``*.db`` file that ``.gitignore``
excludes. Delete the DB and the entire record of why any project exists is gone,
with no author and no timestamp anywhere. Three of the six stage audits asked for
this independently.

Nothing here calls an LLM. Every field is data the factory already computes and
then discarded:

| Artifact  | Playbook stage | Source                                              |
|-----------|----------------|-----------------------------------------------------|
| INTENT.md | 1 (Plan)       | the operator's raw request + author + date          |
| SPEC.md   | 2 (Design)     | ``SpecOutput`` — problem, why, AC, non-goals, tasks |
| PLAN.md   | 3 (Build)      | ``order_tasks`` + ``TaskDef.scope`` + architect risks |
| ADR       | 3 (Build)      | ``evidence.adr.write_adr`` (already existed)        |
| diff      | 5 (Deploy)     | ``workspace.git.git_changed_files`` via the trust package  |

The PLAN is what makes the decomposition binding rather than descriptive:
``planned_scope()`` is the same declared scope ``trust_package`` checks the real
git diff against, so "the merged diff is checked against the committed plan" is a
real check and not a slogan.

Artifacts are written under the PROJECT directory (``docs/work/<story>/``), which
is tracked by the factory repo — the same convention ADRs already use. Filenames
are deterministic so a replay overwrites rather than accumulating.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from factory.domain.contracts import ArchitectOutput, SpecOutput
from factory.domain.task_order import order_tasks

_UNKNOWN_AUTHOR = "operator"


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _git_author() -> str:
    """The operator's git identity, so the artifact carries a real author.

    Falls back to a neutral label rather than guessing — an artifact attributed to
    the wrong person is worse than one attributed to none.
    """
    if shutil.which("git") is None:
        return _UNKNOWN_AUTHOR
    try:
        proc = subprocess.run(
            ["git", "config", "user.name"], capture_output=True, text=True, timeout=5,
            stdin=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, OSError):
        return _UNKNOWN_AUTHOR
    return proc.stdout.strip() or _UNKNOWN_AUTHOR


def work_dir_for(project_dir: Path, story_id: str) -> Path:
    """Where a story's chain lives. One directory per story keeps the chain legible."""
    return Path(project_dir) / "docs" / "work" / story_id


def _bullets(items: list[str], empty: str = "- (none recorded)") -> str:
    return "\n".join(f"- {i}" for i in items) if items else empty


def _write(project_dir: Path | None, story_id: str, filename: str, text: str) -> Path | None:
    """Write one artifact, or no-op when there is no project to write into.

    Ad-hoc runs (``factory "..."`` with no project) have nowhere durable to put
    evidence; scattering files into the cwd would be worse than skipping.
    """
    if project_dir is None:
        return None
    target = work_dir_for(Path(project_dir), story_id)
    target.mkdir(parents=True, exist_ok=True)
    path = target / filename
    path.write_text(text, encoding="utf-8")
    return path


# ── Stage 1: INTENT ───────────────────────────────────────────────

def render_intent(
    story_id: str,
    request: str,
    *,
    author: str,
    created_at: str,
    project_id: str = "",
) -> str:
    """The operator's ask, on disk, before any agent has touched it.

    Deliberately NOT the spec-agent's interpretation: this is the raw input, so a
    later reader can see what was asked as well as what was built. The playbook's
    template headings are kept even where the factory cannot fill them from a
    one-line CLI request — an empty heading is an honest prompt to the operator,
    whereas omitting it hides that the information was never captured.
    """
    return "\n".join([
        f"# INTENT {story_id}",
        "",
        f"- Author: {author}",
        f"- Date: {created_at}",
        f"- Project: {project_id or '—'}",
        "- Status: captured (the request as received, before interpretation)",
        "",
        "## Problem",
        "",
        request.strip() or "(no request recorded)",
        "",
        "## Proposed outcome",
        "",
        "See `SPEC.md` in this directory — the spec-agent's acceptance criteria are",
        "the machine-readable statement of the outcome, derived from the problem above.",
        "",
        "## Affected users and systems",
        "",
        "See `PLAN.md` → *Files that change*, and the project's `project-spec.json`",
        "for the systems in scope.",
        "",
        "## Constraints",
        "",
        "The project's `PROJECT_RULES.md` and `project-spec.json` (stack, conventions,",
        "NFRs and the `forbidden` list) are injected into every agent and bind this work.",
        "",
        "## Open questions",
        "",
        "Recorded in `SPEC.md` → *Open questions*. A non-empty list parks the run at",
        "Checkpoint 1 for the operator rather than failing it.",
        "",
    ])


def write_intent(
    project_dir: Path | None,
    story_id: str,
    request: str,
    *,
    author: str | None = None,
    project_id: str = "",
) -> Path | None:
    return _write(
        project_dir,
        story_id,
        "INTENT.md",
        render_intent(
            story_id,
            request,
            author=author or _git_author(),
            created_at=_today(),
            project_id=project_id,
        ),
    )


# ── Stage 2: SPEC ─────────────────────────────────────────────────

def render_spec(story_id: str, spec: SpecOutput) -> str:
    """The story as the design must answer it: problem, why, criteria, non-goals.

    This is the link that was most conspicuously missing. The ADR recorded the
    DECISION but not the requirements it was a decision about, so a committed
    design had nothing committed to justify it.
    """
    tasks = "\n".join(
        f"- **{t.id}** — {t.title}"
        + (f"\n  - purpose: {t.purpose}" if t.purpose else "")
        + (f"\n  - scope: {', '.join(t.scope)}" if t.scope else "")
        + (f"\n  - done when: {t.completion_evidence}" if t.completion_evidence else "")
        + (f"\n  - depends on: {', '.join(t.depends_on)}" if t.depends_on else "")
        for t in spec.tasks
    ) or "- (no tasks defined)"

    lines = [
        f"# SPEC {story_id}: {spec.title}",
        "",
        f"- Date: {_today()}",
        f"- Type: {spec.type}",
        f"- Verdict: {spec.verdict}",
        "- Status: proposed (pending Checkpoint 1 sign-off)",
        "",
        "## Problem",
        "",
        spec.problem or "(none recorded)",
        "",
        "## Why this matters",
        "",
        spec.why or "(none recorded)",
        "",
        "## Acceptance criteria",
        "",
        _bullets(spec.acceptance_criteria, "- (none — gate-1 rejects this)"),
        "",
        "## Non-goals",
        "",
        _bullets(spec.non_goals, "- (none declared)"),
        "",
        "## Tasks",
        "",
        tasks,
        "",
    ]
    if spec.questions:
        lines += [
            "## Open questions",
            "",
            "These parked the run at Checkpoint 1 for the operator:",
            "",
            _bullets(spec.questions),
            "",
        ]
    return "\n".join(lines)


def write_spec(project_dir: Path | None, story_id: str, spec: SpecOutput) -> Path | None:
    return _write(project_dir, story_id, "SPEC.md", render_spec(story_id, spec))


# ── Stage 3: PLAN ─────────────────────────────────────────────────

def planned_scope(spec: SpecOutput) -> list[str]:
    """The union of every task's declared scope, in declaration order.

    This is the plan's binding claim about what may change, and the same list
    ``trust_package`` checks the real git diff against. Without a consumer,
    ``TaskDef.scope`` was prompt decoration.
    """
    scope: list[str] = []
    for task in spec.tasks:
        for entry in task.scope:
            if entry and entry not in scope:
                scope.append(entry)
    return scope


def render_plan(story_id: str, spec: SpecOutput, architect: ArchitectOutput) -> str:
    """The playbook's plan.md, filled entirely from data already computed.

    `order_tasks` runs a full Kahn sort on the task DAG on every coder call and
    the result was consumed only in memory; here it becomes the committed *order
    of work*, so the sequence a reviewer reads is the sequence that ran.
    """
    ordered = order_tasks(list(spec.tasks))
    declared = planned_scope(spec)

    files = "\n".join(f"- `{p}`" for p in declared) or (
        "- (no task declared a scope — the diff cannot be checked against this plan;\n"
        "  see EFFECTIVENESS.md §6)"
    )
    modules = _bullets(
        [f"`{m}`" for m in architect.modules_affected], "- (none listed)"
    )
    order = "\n".join(
        f"{i}. **{t.id}** — {t.title}"
        + (f"  _(after {', '.join(t.depends_on)})_" if t.depends_on else "")
        for i, t in enumerate(ordered, start=1)
    ) or "1. (no tasks)"
    proof = "\n".join(
        f"- **{t.id}**: {t.completion_evidence or '(no completion evidence declared)'}"
        for t in ordered
    ) or "- (no tasks)"

    return "\n".join([
        f"# PLAN {story_id}: {spec.title}",
        "",
        f"- Date: {_today()}",
        "- Status: proposed (pending Checkpoint 2 sign-off)",
        f"- Design: see `ADR-{story_id}-*.md` under `docs/architecture/adr/`",
        "",
        "## Files that change",
        "",
        "Declared task scope — the real git diff is checked against this list and",
        "anything outside it is reported as a scope violation in the trust package:",
        "",
        files,
        "",
        "Modules the architecture expects to touch:",
        "",
        modules,
        "",
        "## Order of work",
        "",
        "Dependency-ordered (topological); the coder implements one task per call in",
        "exactly this order:",
        "",
        order,
        "",
        "## Risks",
        "",
        _bullets(architect.risks, "- (none flagged)"),
        "",
        "Implementation constraints the coder must honor:",
        "",
        _bullets(architect.implementation_constraints, "- (none)"),
        "",
        f"- DB impact: {architect.db_impact} · API impact: {architect.api_impact} "
        f"· Migration needed: {architect.migration_needed}",
        "",
        "Breaking changes:",
        "",
        _bullets(architect.breaking_changes, "- (none)"),
        "",
        "## Proof",
        "",
        "Per-task completion evidence, plus the deterministic gates every task passes:",
        "",
        proof,
        "",
        "- `gate-build`: the materialized code compiles (and, with FACTORY_RUN_TESTS=1,",
        "  the test bodies run and pass).",
        "- `gate-test`: acceptance-criteria coverage, security and performance",
        "  sub-verdicts from the tester.",
        "",
    ])


def write_plan(
    project_dir: Path | None,
    story_id: str,
    spec: SpecOutput,
    architect: ArchitectOutput,
) -> Path | None:
    return _write(project_dir, story_id, "PLAN.md", render_plan(story_id, spec, architect))
