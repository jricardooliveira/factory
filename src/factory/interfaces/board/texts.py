"""What the board says, as Textual markup: pure functions over `runs.board`'s model.

Copy and order follow the design handoff (`design_handoff_factory_board/screens.js`,
`info()` / `detail()` / `workingLines()`); the data is whatever the records hold — a field
the factory does not record is left out, never invented.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from textual.markup import escape as e

from factory.domain.board import GROUP_TITLES, age, inbox_group
from factory.runs.board import Board, Decision, Story

STAGES = ("spec", "design", "code", "test")


def stage_strip(stage: tuple[str, ...]) -> str:
    marks = {"done": ("$success", "✓"), "wait": ("$warning", "◆"), "run": ("$accent", "●"),
             "fail": ("$error", "✗")}
    out = []
    for name, state in zip(STAGES, stage):
        colour, mark = marks.get(state, ("$dim", "·"))
        out.append(f"[{colour}]{name} {mark}[/]")
    return "  ".join(out)


def money(value: float | None, cap: float | None = None) -> str:
    if not value:
        return "not started"
    return f"${value:.2f}" + (f" of ${cap:g}" if cap else "")


def local(stamp: str) -> str:
    return datetime.fromisoformat(stamp).astimezone().strftime("%H:%M") if stamp else ""


# ── the inbox ─────────────────────────────────────────────────────────
KIND_LABEL = {"fail": "[$error]✗ Failed[/]", "questions": "[$warning]? Answer[/]"}
KIND_ICON = {"fail": "[$error]✗[/]", "questions": "[$warning]?[/]"}


def kind_label(d: Decision) -> str:
    return KIND_LABEL.get(d.kind, "[$warning]◆ Approve[/]")


def kind_icon(d: Decision) -> str:
    return KIND_ICON.get(d.kind, "[$warning]◆[/]")


def group_header(d: Decision, decisions: list[Decision]) -> str:
    g = inbox_group(d.kind)
    n = sum(x.count for x in decisions if inbox_group(x.kind) == g)
    colour = ("$error", "$warning", "$accent")[g]
    return f"[{colour}]{GROUP_TITLES[g]} · {n}[/]"


# ── details ───────────────────────────────────────────────────────────
@dataclass
class Detail:
    title: str
    right: str
    top: str
    body: str
    buttons: list[tuple[str, str, bool]]  # (label, key, primary)
    hint: str = ""
    keys: list[tuple[str, str]] = field(default_factory=list)
    text_label: str = "What should change?"
    text_hint: str = ""


def _wrap(text: str, width: int, indent: str = "  ") -> list[str]:
    return [indent + e(line) for line in textwrap.wrap(text, max(20, width - len(indent)))] or [indent]


def detail(d: Decision, model: Board, *, now: datetime, width: int = 90,
           show_error: bool = False) -> Detail:
    cap = model.budget_usd
    if d.kind == "questions":
        data = d.data
        qi, total = data["qi"], data["total"]
        current = data["pending"][0]
        question = current["context"].get("question", {})
        dots = "".join("[$success]●[/]" if i < qi else "[$accent]●[/]" if i == qi else "[$dim]○[/]"
                       for i in range(total))
        context = current["context"].get("why_text") or _why(d)
        top = f"{dots}  [b $bright]{e(current['question'])}[/]\n[$dim]{e(context)}[/]"
        body: list[str] = []
        rest = data["pending"][1:]
        if rest:
            body.append("[$dim]Next in this story[/]" if d.story is not None else "[$dim]Next in this round[/]")
            body += [f"  {qi + i + 2}. {e(x['question'])}" for i, x in enumerate(rest)]
            body.append("")
        if data["answered"]:
            body.append("[$dim]Answered[/]")
            body += [f"  [$success]✓[/] {e(_clip(a['question'], 40))} → {e(_clip(a['answer'] or '', 36))}"
                     for a in data["answered"]]
            body.append("")
        body.append("[$dim]Each answer is saved as you go. Esc returns and keeps your place.[/]")
        n = len(question.get("options") or []) + 2
        return Detail(d.title, f"question {qi + 1} of {total}", top, "\n".join(body),
                      [("Answer", "enter", True), ("Skip for now", "s", False)],
                      f"↑↓ or 1–{n} to choose",
                      [(f"↑↓ 1-{n}", "choose"), ("enter", "answer"), ("s", "skip"), ("esc", "back")])

    if d.kind == "backlog":
        decision = d.data["decision"]
        stories = decision["context"].get("stories", [])
        size, n = d.data["size"], len(stories)
        top = (f"Adds {n} stories (backlog {size} → {size + n}). Nothing starts until they are "
               "refined and launched.")
        body = []
        for i, s in enumerate(stories, 1):
            body += [f"[b $bright]#{size + i}  {e(s['title'])}[/]",
                     f"    [$dim]Request[/]  {e(s['request'])}"]
            if s.get("rationale"):
                body.append(f"    [$dim]Why now[/]  {e(s['rationale'])}")
            body.append("")
        if decision["context"].get("feedback"):
            body += ["[b $bright]Your earlier changes[/]"] + [
                f"  [$dim]{e(f)}[/]" for f in decision["context"]["feedback"]]
        return Detail(d.title, f"proposed {local(d.created_at)}", top, "\n".join(body),
                      [(f"Approve {n} stories", "a", True), ("Request changes…", "c", False)],
                      "↓ pgdn scroll",
                      [("↓", "scroll"), ("a", "approve"), ("c", "changes"), ("esc", "back")],
                      text_hint="new proposal · one model call")

    if d.kind == "brief":
        decision, change = d.data["decision"], d.data.get("change")
        ctx = decision["context"]
        if change:
            top = "Revised brief, drafted from what you said changed."
            body = ["[b $bright]What changed[/]", *_wrap(change, width), "",
                    "[$dim]Approving re-checks the affected stories before they run.[/]"]
        else:
            top = "Drafted from your interview. Approving starts planning."
            body = _brief_sections(ctx.get("brief", ""), width)
        spec = ctx.get("spec") or {}
        tech = " · ".join(e(str(spec[k])) for k in ("language", "framework", "database") if spec.get(k))
        if tech:
            body += ["", "[b $bright]Technical choices[/]", f"  {tech}"]
        label = "Approve change" if change else "Approve brief"
        return Detail(d.title, "ready", top, "\n".join(body),
                      [(label, "a", True), ("Request changes…", "c", False)], "↓ scroll",
                      [("↓", "scroll"), ("a", "approve"), ("c", "changes"), ("esc", "back")],
                      text_hint="new draft · one model call")

    if d.kind == "ckpt":
        data = d.data
        story: Story | None = data.get("story_obj")
        cp, name = data["cp"], data["stage_name"]
        strip = stage_strip(story.stage) if story else ""
        # A run with no recorded usage: say nothing rather than a fake "not started".
        spend = f"      [$dim]Spend[/] {money(story.spend, cap)}" if story and story.spend else ""
        top = (f"{strip}{spend}\n"
               "[$dim]Paused until you decide. Nothing else waits on this.[/]")
        body: list[str] = []
        if name == "design":
            design = data.get("design") or {}
            if design.get("architecture_notes"):
                body += ["[b $bright]Design to approve[/]", *_wrap(design["architecture_notes"], width), ""]
            if data.get("parts"):
                body.append("[b $bright]Parts[/]")
                for kind, path, also in data["parts"]:
                    note = f" [$dim](also used by #{also})[/]" if also else ""
                    body.append(f"  [$dim]{kind:<8}[/] {e(path)}{note}")
                body.append("")
            choices = design.get("implementation_constraints") or []
            if choices:
                body += ["[b $bright]Choices the agent made[/]  [$dim]overturn them in your feedback[/]",
                         *[f"  • {e(c)}" for c in choices], ""]
        else:
            spec = data.get("spec") or {}
            body.append(f"[b $bright]{name.capitalize()} to approve[/]")
            if story:
                body += _wrap(story.request, width)
            for c in spec.get("acceptance_criteria") or []:
                body.append(f"  • {e(c)}")
            body.append("")
        if data.get("questions"):
            body += ["[b $bright]The factory flagged[/]", *[f"  [$warning]?[/] {e(q)}" for q in data["questions"]], ""]
        nxt = {"spec": "the design starts", "design": "code starts"}.get(name, "the next stage starts")
        body.append(f"[$dim]Approve: {nxt}. Reject: the {name} runs again with your feedback.[/]")
        return Detail(d.title, f"checkpoint {cp} of 3", top, "\n".join(body),
                      [(f"Approve {name}", "a", True), ("Reject with feedback…", "r", False)], "",
                      [("a", "approve"), ("r", "reject"), ("esc", "back")],
                      text_hint=f"reruns the {name}")

    if d.kind == "release":
        data = d.data
        story = data.get("story_obj")
        notes = data.get("release") or {}
        top = "Approving releases this reviewed change. Deploying is a separate step."
        body = []
        if story:
            body.append(f"[b $bright]Includes[/]  #{story.n} {e(story.title)}")
        if notes.get("summary"):
            body.append(f"[b $bright]What ships[/]  {e(notes['summary'])}")
        if data.get("checks"):
            body.append("[b $bright]Checks[/]    " + "   ".join(
                f"[$success]✓[/] {e(c)}" if ok else f"[$error]✗[/] {e(c)}" for c, ok in data["checks"]))
        if story and story.spend:
            body.append(f"[b $bright]Spend[/]     {money(story.spend, cap)}")
        if notes.get("how_to_verify"):
            body += ["", "[b $bright]How to verify[/]", *[f"  • {e(s)}" for s in notes["how_to_verify"]]]
        body += ["", "[$dim]After release, the story moves to Done. Deploy from your own pipeline.[/]"]
        right = f"verified {local(d.created_at)}"
        return Detail(d.title, right, top, "\n".join(body),
                      [("Approve release", "a", True), ("Request changes…", "c", False)], "",
                      [("a", "approve"), ("c", "changes"), ("esc", "back")],
                      text_hint="sends it back for another pass")

    # fail
    job, retryable = d.data["job"], d.data["retryable"]
    error = (job.get("error") or "").strip()
    first = error.split("\n", 1)[0]
    step = d.sub.lower()
    top = (f"[$error]✗ Failed[/] at {local(d.created_at)} · {e(step)}\n"
           "Nothing was changed. " + ("The story stays not ready until refinement finishes."
                                      if d.story is not None else "What was saved stays saved."))
    body = ["[b $bright]What happened[/]", *_wrap(_plain_error(first), width), ""]
    if retryable:
        body += ["[b $bright]Your options[/]",
                 "  [b]Retry[/]               Run this step again with the same inputs.",
                 "  [b]Retry with a note[/]   Add guidance first, e.g. “assume a relay server”.",
                 "  [b]Dismiss[/]             Close this item. Refine the story later from Stories.", ""]
        buttons = [("Retry", "r", True), ("Retry with a note…", "m", False), ("Dismiss", "x", False)]
        keys = [("r", "retry"), ("m", "with note"), ("x", "dismiss"), ("d", "details"), ("esc", "back")]
    else:
        body += ["[b $bright]Your options[/]",
                 "  [b]Reconcile[/]   Settle the stopped step from what was recorded; nothing is repeated.",
                 "  [b]Dismiss[/]     Close this item.", ""]
        buttons = [("Reconcile", "r", True), ("Dismiss", "x", False)]
        keys = [("r", "reconcile"), ("x", "dismiss"), ("d", "details"), ("esc", "back")]
    body.append(f"[$dim]{e(error)}  d hides[/]" if show_error
                else "[$dim]Technical details for a bug report:[/] [b $accent]d[/]")
    return Detail(d.title, d.sub.lower(), top, "\n".join(body), buttons, "", keys,
                  text_label="Note for the agent", text_hint="retries this step")


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[:width - 1] + "…"


def _why(d: Decision) -> str:
    phase = d.data["pending"][0]["context"].get("phase", "")
    if d.story is not None:
        return "This story can't be planned until this is settled; the brief leaves it open."
    return {"technical": "A technical choice the project spec leaves open.",
            "execution": "This sets how much the factory may do without asking you.",
            "amend": "Your amendment to the brief raises this."}.get(
        phase, "The product brief needs this before anything can be planned.")


def _plain_error(text: str) -> str:
    import re
    text = re.sub(r"\s*\(exit -?\d+\)", "", text)
    return text.strip() or "The step stopped without saying why."


def _brief_sections(markdown: str, width: int) -> list[str]:
    """The brief's markdown as the board's sections: `## X` → bold heading, items indented."""
    out: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            if out:
                out.append("")
            out.append(f"[b $bright]{e(line[3:])}[/]")
        elif line.strip():
            text = line.strip().lstrip("-* ").replace("**", "")
            out += _wrap(text, width)
    return out


def parts(modules: list[str], repo: Path, others: dict[str, int]) -> list[tuple[str, str, int | None]]:
    """(New | Changes, path, another story touching it) for a design's files."""
    return [("Changes" if (repo / m).exists() else "New", m, others.get(m)) for m in modules]


def row_title(d: Decision, width: int, now: datetime) -> str:
    left = f"{kind_icon(d)} {e(d.title)}"
    right = f"[$dim]{age(d.created_at, now)}[/]"
    return _pad(left, right, width)


def _pad(left: str, right: str, width: int) -> str:
    from textual.content import Content
    gap = width - Content.from_markup(left).cell_length - Content.from_markup(right).cell_length
    return left + " " * max(1, gap) + right


# ── Overview ──────────────────────────────────────────────────────────
STALLED_MINUTES = 10  # after this long without progress, the design shows it in yellow


def working_lines(model: Board, now: datetime) -> str:
    out: list[str] = []
    for s in model.stories:
        if s.state == "refining":
            out += [f"[$accent]◌[/] [b $bright]Story #{s.n} {e(s.title)}[/] · refining",
                    f"  {e(s.doing or 'Refining')}"]
        elif s.state in ("working", "merging"):
            waiting = "wait" in s.stage
            if waiting:
                doing = f"[$warning]◆ {e(s.doing)}[/]"
            else:
                doing = e(s.doing or "Starting")
                if s.last_progress:
                    seconds = (now - datetime.fromisoformat(s.last_progress)).total_seconds()
                    doing += (f" · [$warning]no progress for {int(seconds // 60)} min[/]"
                              if seconds >= STALLED_MINUTES * 60
                              else f" · last progress {age(s.last_progress, now)}")
            spend = (f"{money(s.spend, model.budget_usd)}" if s.spend else "just started")
            out += [f"[$accent]●[/] [b $bright]Story #{s.n} {e(s.title)}[/]", f"  {doing}",
                    f"  {stage_strip(s.stage)}      [$dim]Spend[/] {spend}"]
    for job in model.jobs:
        out += [f"[$accent]◌[/] {e(job['text'])}",
                f"  [$dim]started {local(job['at'])} · you can leave; results arrive in Needs you[/]"]
    return "\n".join(out) or "[$dim]Nothing running.[/]"


def activity_lines(activity: list[tuple[str, str]]) -> str:
    lines = []
    for stamp, text in activity:
        body = f"[$error]✗[/] {e(text[2:])}" if text.startswith("✗ ") else e(text)
        lines.append(f"[$dim]{local(stamp)}[/]  {body}")
    return "\n".join(lines)


_COUNTED = (("ready", "ready"), ("working", "working"), ("release", "waiting for release"),
            ("done", "done"))


def story_counts(stories: list[Story]) -> str:
    if not stories:
        return "[$dim]No stories yet.[/]"
    counts = {state: sum(s.state == state for s in stories) for state, _ in _COUNTED}
    parts = [f"{counts[k]} {label}" for k, label in _COUNTED if counts[k]]
    return ("[$dim]Stories:[/] " + " · ".join(parts) if parts else "[$dim]Stories:[/] none started"
            ) + "   [$dim]t to see all[/]"


# ── Stories ───────────────────────────────────────────────────────────
STORY_ORDER = ("needs", "notready", "refining", "ready", "working", "stopped", "release",
               "draft", "done")
STORY_GROUP = {"needs": "NEEDS ANSWERS", "notready": "NOT READY", "refining": "REFINING",
               "ready": "READY", "working": "WORKING", "stopped": "STOPPED",
               "release": "WAITING FOR RELEASE", "draft": "DRAFTS", "done": "DONE"}
STORY_ICON = {"needs": "[$warning]?[/]", "notready": "[$warning]![/]", "refining": "[$dim]◌[/]",
              "ready": "[$success]○[/]", "working": "[$accent]●[/]", "stopped": "[$warning]⏸[/]",
              "release": "[$accent]◆[/]", "merging": "[$accent]◆[/]", "draft": "[$dim]·[/]",
              "done": "[$success]✓[/]"}
STORY_LABEL = {"needs": "[$warning]? Needs answers[/]", "notready": "[$warning]! Not ready[/]",
               "refining": "[$dim]◌ Refining[/]", "ready": "[$success]○ Ready[/]",
               "working": "[$accent]● Working[/]", "stopped": "[$warning]⏸ Stopped[/]",
               "release": "[$accent]◆ Waiting for release[/]", "merging": "[$accent]◆ Merging[/]",
               "draft": "[$dim]· Draft[/]", "done": "[$success]✓ Done[/]"}


def story_group(state: str) -> str:
    return "release" if state == "merging" else state


def ordered_stories(stories: list[Story]) -> list[Story]:
    return [s for k in STORY_ORDER for s in stories if story_group(s.state) == k]


def story_row(s: Story, width: int) -> str:
    return _pad(f"{STORY_ICON.get(s.state, '·')} #{s.n} {e(s.title)}",
                f"[$dim]{e(s.meta)}[/]" if s.meta else "", width)


def story_detail(s: Story, model: Board, *, width: int = 90) -> Detail:
    decision = next((d for d in model.decisions if d.story == s.n), None)
    failed = next((d for d in model.decisions if d.story == s.n and d.kind == "fail"), None)
    top = (STORY_LABEL.get(s.state, s.state) + (f" · {e(s.meta)}" if s.meta else "") + "\n"
           f"[$dim]Stage[/]  {stage_strip(s.stage)}      [$dim]Spend[/] {money(s.spend, model.budget_usd)}")
    plan = s.plan or {}
    body: list[str] = []
    if s.state == "notready":
        body.append("[b $bright]Why it isn’t ready[/]")
        body += [f"  [$warning]![/] {e(u)}" for u in plan.get("uncertainties") or []]
        body += [f"  [$dim]→[/] Needs #{n} first." for n in plan.get("dependencies") or []]
        if failed:
            body.append(f"  [$error]✗[/] Refinement failed at {local(failed.created_at)}.  "
                        "[b $accent]r[/] retry")
        if s.meta == "last run failed":
            body.append("  [$error]✗[/] Its last run failed: see All runs.")
        body.append("")
    if s.doing:
        body += [f"[b $bright]Now[/]  {e(s.doing)}", ""]
    body += ["[b $bright]Request[/]", *_wrap(s.request, width), ""]
    spec = plan.get("spec") or {}
    criteria = spec.get("acceptance_criteria") or []
    if criteria:
        body += [f"[b $bright]Requirements[/]  [$dim]{len(criteria)}[/]", *[f"  • {e(c)}" for c in criteria], ""]
    if s.files:
        others = {f: o.n for o in model.stories if o.n != s.n and o.state != "done" for f in o.files}
        body.append("[b $bright]What it changes[/]")
        body += [f"  {e(f)}" + (f" [$dim](also #{others[f]})[/]" if f in others else "") for f in s.files]
        body.append("")
    body.append("[b $bright]Assumptions[/]  " + ("" if s.assumptions else "[$dim]none[/]"))
    body += [f"  • {e(a)}" for a in s.assumptions]
    buttons: list[tuple[str, str, bool]] = []
    keys: list[tuple[str, str]] = [("↓", "scroll")]
    if s.state == "notready":
        buttons = [("Retry refinement", "r", True)] + ([("Refine with a note…", "m", False)] if failed else [])
        keys += [("r", "retry")] + ([("m", "note")] if failed else [])
    elif s.state == "draft":
        buttons, keys = [("Refine story", "r", True)], keys + [("r", "refine")]
    elif s.state == "ready":
        buttons, keys = [("Propose batch", "b", True)], keys + [("b", "batch")]
    elif decision is not None and s.state in ("needs", "working", "release"):
        label = "Answer questions" if s.state == "needs" else "Open checkpoint" if s.state == "working" else "Open release"
        buttons, keys = [(label, "enter", True)], keys + [("enter", "open")]
    elif s.state == "working":
        buttons, keys = [("Stop at a safe point", "S", False)], keys + [("S", "stop")]
    right = {"needs": "needs answers", "notready": "not ready", "release": "waiting for release"}.get(s.state, s.state)
    return Detail(f"#{s.n} {s.title}", right, top, "\n".join(body), buttons, "", keys + [("esc", "back")])


BOARD_COLUMNS = (("TO REFINE", ("draft", "needs", "notready", "refining")), ("READY", ("ready",)),
                 ("SPEC", 0), ("DESIGN", 1), ("CODE", 2), ("TEST", 3),
                 ("DONE", ("release", "merging", "done")))
CARD_STATUS = {"needs": "[$warning]? answers[/]", "notready": "[$warning]! blocked[/]",
               "refining": "[$dim]◌ refining[/]", "ready": "[$success]○ ready[/]",
               "release": "[$accent]◆ release[/]", "merging": "[$accent]◆ merging[/]",
               "draft": "[$dim]· draft[/]", "done": "[$success]✓ done[/]", "stopped": "[$warning]⏸ stopped[/]"}


def _stage_column(s: Story) -> int:
    if s.state not in ("working", "stopped"):
        return -1
    pending = [i for i, x in enumerate(s.stage) if x != "done"]
    return pending[0] if pending else 3


def board_columns(stories: list[Story]) -> list[tuple[str, list[Story]]]:
    columns = []
    for name, rule in BOARD_COLUMNS:
        if isinstance(rule, int):
            columns.append((name, [s for s in stories if _stage_column(s) == rule]))
        else:
            columns.append((name, [s for s in stories if s.state in rule]))
    return columns


def card_status(s: Story) -> str:
    if s.state == "working":
        return "[$warning]◆ you[/]" if "wait" in s.stage else "[$accent]● running[/]"
    return CARD_STATUS.get(s.state, "")


def board_text(stories: list[Story], col: int, row: int, width: int, height: int) -> str:
    """The stories as 7 columns of 7-line cards; the selected card has an accent border."""
    from textual.content import Content
    columns = board_columns(stories)
    cw = max(12, width // 7)
    widths = [cw] * 6 + [max(12, width - cw * 6)]
    per_column = max(1, (height - 4) // 7)
    cells: list[list[str]] = []
    for i, (_name, items) in enumerate(columns):
        w = widths[i] - 1
        inner = w - 2
        lines: list[str] = []
        shown = items[:per_column - 1] if len(items) > per_column else items[:per_column]
        for j, s in enumerate(shown):
            colour = "$accent" if (i, j) == (col, row) else "$dim"
            words = textwrap.wrap(f"#{s.n} {s.title}", max(4, inner)) or [""]
            words = (words + ["", "", ""])[:3]
            words = [_clip(x, inner) for x in words]
            lines.append(f"[{colour}]┌{'─' * (w - 2)}┐[/]")
            for k, text in enumerate(words):
                if k == 0 and text.startswith(f"#{s.n}"):
                    text_m = f"[b]#{s.n}[/]" + e(text[len(f'#{s.n}'):])
                else:
                    text_m = e(text)
                lines.append(f"[{colour}]│[/]" + text_m + " " * (inner - len(text)) + f"[{colour}]│[/]")
            status = card_status(s)
            plain = Content.from_markup(status).plain
            lines.append(f"[{colour}]│[/]" + status + " " * max(0, inner - len(plain)) + f"[{colour}]│[/]")
            lines.append(f"[{colour}]└{'─' * (w - 2)}┘[/]")
        if len(items) > per_column:
            lines.append(f"[$dim]+{len(items) - per_column + 1} more[/]")
        if not items:
            lines.append("[$dim]—[/]")
        cells.append(lines)
    head = "".join(
        _padm(f"[$accent]{n}[/] [$dim]{len(items)}[/]" if i == col else f"[$dim]{n} {len(items)}[/]", widths[i])
        for i, (n, items) in enumerate(columns))
    rule = "".join(f"[$dim]{'─' * (w - 1)}[/] " for w in widths)
    out = [head, rule]
    for r in range(max(len(c) for c in cells)):
        out.append("".join(_padm(c[r] if r < len(c) else "", widths[i] - 1) + " "
                           for i, c in enumerate(cells)))
    return "\n".join(out)


def _padm(markup: str, width: int) -> str:
    from textual.content import Content
    return markup + " " * max(0, width - Content.from_markup(markup).cell_length)
