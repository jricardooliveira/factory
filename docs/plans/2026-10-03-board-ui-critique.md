# Board UI critique and improvement loop

Status: working document for the UI improvement loop (2026-10-03). Evidence: the board
driven headless (Textual pilot) on a copy of the operator's `$FACTORY_HOME` with the
model CLIs stubbed, screenshots at 140×45 and 90×40, plus the operator's own session
(a backlog proposal and a story refinement on checkers, both left in `needs_input`).

Operator decisions for this loop: **one board** (the workflow screen is home, a run is a
detail view opened from it), **answers are a pick list + Other** (like Claude Code's
AskUserQuestion), and **board tests simulate both sides** (scripted agents, scripted
operator, offline, in `make check`; never the coding stage).

## How it works today

`factory board` mounts the old run board (`FactoryBoard`: run table / kanban + approve /
reject pane) and immediately pushes `WorkflowScreen` on top of it. The workflow screen
has a project selector, a three-line summary, five tabs (Overview, Needs you, Stories,
Activity, Settings), a row of five always-visible buttons (Interview, Propose backlog,
Propose batch, Pause new starts, Start worker), one list and one detail pane with a
free-text box and three buttons. Every action writes a record (a job or an answer) and
starts a detached worker process; model calls happen in that worker; the screen polls
the database every 2 s.

The model is right: decisions are durable records, nothing blocks on a human, closing
the board loses nothing. The surface on top of it is where the problems are.

## Findings, worst first

### P0 — the board breaks

1. **Every action button crashed the board** (fixed): `WorkflowScreen._perform` called
   `self.call_from_thread`, an `App` method. Headless, the screen froze instead
   (`_acting` never reset, so refresh and every later click were ignored).
2. **Approve / reject / resume / release crashed** with `no such savepoint` (fixed): a
   `conn.commit()` inside `atomic()` in `resume_run`.
3. **Typing an answer writes to the DB on the UI thread, unguarded** (`draft_changed` →
   `save_draft` on every keystroke). A locked DB or a vanished decision kills the board.

### P1 — answering and approving

4. **Free text where the agent gave options.** All 5 questions in the operator's session
   carry 3 options with descriptions; the board prints them as text and asks the operator
   to type "an option number, your answer, or “you decide”".
5. **Backlog and brief approval are "type approve" over raw JSON.** The backlog decision
   shows `context_revision` hashes and the stories as a JSON dump; any other text is
   refused ("Type approve to accept…"), so there is no way to ask for changes.
6. **A question does not say what it is for.** No story title or number, no topic; the
   "why" is the same sentence for every question ("A material choice is not settled by
   the saved interview."). Four questions from one story appear as four unrelated rows.
7. **No next step after an answer.** "Action saved. The board will show the resulting
   state." stays on screen (in warning yellow) across tabs until the next action.

### P1 — navigation and information architecture

8. **Two boards stacked.** The footer shows the hidden run board's keys (`v` Table/Kanban,
   `←/→` col, `p` Project filter, `a` Approve, `x` Reject, `d` Dismiss, `i` Interview,
   `B` Brief) and they act on the hidden screen: `a` approves whatever run is selected
   underneath. Escape is labelled "Run details" and leaves home.
9. **The header lies.** It still says "Project: all · table" after a project is chosen.
10. **Project actions ignore project state.** Interview on a project with an approved brief
    starts a new product interview (a paid call); Propose backlog queues a second proposal
    while one waits in Needs you; Propose batch is offered with no ready story; "Start
    worker" is plumbing the operator should not need to know about.
11. **Overview and Needs you show the same list.** Settings is static prose.
12. **Disabled without a reason.** Refine story is greyed out on Draft stories (their
    backlog proposal is not approved yet) and nothing says so.

### P2 — status and feedback

13. **Counts are not what they say.** "Needs input: 1" (stories) while 4 decisions wait.
14. **Failures are JSON.** A failed job shows its row as a JSON dump with "Reconcile
    stopped job"; there is no Retry, and reconcile refuses while the worker is active.
15. **Times are UTC next to a local clock** (Activity 16:35, clock 17:35).
16. **Events are not readable.** "Decision answered" + `{"decision_id": "714f…"}`.
17. **Story detail dumps the plan as JSON.**

### P2 — layout

18. **Narrow (90 columns): the answer box and Submit are below the fold**; the promised
    list → detail view at narrow widths does not exist (only the button grid changes).
19. The project `Select` takes 3 rows; the summary 3–4 more; the list gets what is left.

### P3 — consistency

20. Two interview implementations: the modal `QuestionScreen` (old blocking flow, used by
    amend / story interview from the menu) and the durable decisions. Same question, two
    different answer UIs.
21. The command palette repeats the buttons with different behaviour ("Story: refine next
    from backlog" vs the Stories tab; "Backlog: propose" vs the button).

## Target

```
FACTORY  checkers ▾   ● worker running   ⏸ paused?                       17:35
Needs you (4) │ Stories (9) │ Runs (1) │ Activity
┌ list ─────────────────────────────┐┌ detail ──────────────────────────────┐
│ ? #2 International board  · 3 q   ││ Story #2 · International board       │
│ ? Backlog proposal · 9 stories    ││ Where should the square number sit?  │
│ ✗ backlog proposal failed  Retry  ││  ▸ 1. Small number in a corner  (rec)│
└───────────────────────────────────┘│    2. Large number in the middle     │
 Next: answer 3 questions on #2       │    3. You decide                     │
                                      │    4. Other…                         │
 [Interview] [Propose backlog] …      │ [Enter] Answer  [Esc] Later          │
 only the ones that apply now         └──────────────────────────────────────┘
```

## Loop backlog (each item: a pilot test that drives it, then the change)

1. [x] P0 crashes 1–2.
2. [ ] Simulated-flow harness: scripted agents at `run_agent`, jobs drained in-process,
   a scripted operator; one end-to-end test interview → brief → backlog → refine → ready.
3. [ ] Pick list + Other for questions (4, 6, 7); guarded draft saving (3).
4. [ ] Approve / Request changes for backlog and brief, rendered as text (5).
5. [ ] State-aware project actions, duplicate guards, reasons for disabled (10, 12).
6. [ ] One board: own bindings and footer, run detail view, honest header (8, 9, 11).
7. [ ] Status: counts, Retry for failed jobs, local times, readable events (13–17).
8. [ ] Narrow layout (18, 19).
9. [ ] Retire the duplicate paths (20, 21).
