---
name: factory-intake
description: Use when the operator says "/factory-intake <project>" or wants to define a factory product (or one story of it) by being interviewed here in Claude Code instead of in the terminal. The second door to `factory interview`; it records answers through `factory interview <project> --import`.
---

# Factory intake (the second door to `factory interview`)

You interview the operator so the factory builds what they want instead of guessing. You only
ASK and RECORD; the factory decides coverage, writes `docs/work/BRIEF.md` and commits it. Never
edit a product repo by hand, and never call a model API yourself.

Two modes, chosen by the arguments:

- `/factory-intake <project>`: the **product interview** (project level, once).
- `/factory-intake <project> "story request"`: the **story interview** (gaps only, then run it).

`<project>` is a project id or slug (`.venv/bin/factory project list` shows them). All commands
run from the factory repo root.

## How to ask (both modes)

- Use `AskUserQuestion`. At most 4 questions per call (one cluster); each question has 2-4
  options, **recommended option first** with "(recommended)" in its label. The tool always lets
  the operator type free text; take that text as the answer verbatim.
- Broad to narrow: what and why first, then who, then behaviour, then edges. One cluster at a
  time; let earlier answers shape later questions. Skip questions an answer already settled.
- **"you decide"** (or "whatever you think", "pick one") is a valid answer: record the
  recommended option as the answer with `"assumed": true`. Never re-ask it.
- Ask only questions whose answer changes what gets built.

## Product interview: `/factory-intake <project>`

1. If `<repo>/docs/work/BRIEF.md` already exists (`.venv/bin/factory project show <project>`
   prints its repo), the brief is approved: stop and tell the operator to use
   `.venv/bin/factory interview <project> --amend "what changed"` instead.
2. Read `src/factory/domain/interview.py`. **Every topic in `REQUIRED_TOPICS` must end with a
   non-blank answer**; use those exact topic keys, and `TOPIC_TITLES` / `FALLBACK_QUESTIONS`
   for what each one means. Do not work from a remembered list; that file is the contract.
3. Interview in clusters until every required topic is answered. Extra answers may use any
   topic key (unknown ones land under "Other" in the brief).
4. **Stack cluster** (topic `stack`): propose a stack that fits the answers (language, framework,
   storage, how it runs), 2-4 options, recommended first.
5. **Summary + approval, before writing anything.** Show every answer grouped by
   `TOPIC_TITLES`, marking assumptions, then ask with `AskUserQuestion`: "Approve this brief?"
   with options "Approve (recommended)" / "Change something". On a change, ask what, update the
   answers and show the summary again. Do not continue without an explicit approval.
6. Write the answers to a temp file (e.g. `mktemp /tmp/factory-intake-XXXX.json`) as a JSON
   list in the shape `factory.domain.interview.ImportedAnswer` validates:

   ```json
   [
     {"topic": "goal", "question": "What is this product for?",
      "options": ["Track team expenses (recommended)", "Personal budgeting"],
      "answer": "Track team expenses", "assumed": false}
   ]
   ```

   `topic`, `question` and `answer` are required; `options` are the labels you offered (`[]`
   for an open question); `assumed` is `true` only for "you decide".
7. Run `.venv/bin/factory interview <project> --import <file>` and report what it printed. It
   records NOTHING unless every required topic is covered; if it names uncovered topics, ask
   about exactly those and import again. On success, tell the operator the next step is
   `.venv/bin/factory backlog <project>` (proposes the story list for approval).

## Story interview: `/factory-intake <project> "story request"`

1. Read the project's `docs/work/BRIEF.md`. If it does not exist, stop: run the product
   interview first.
2. Ask **only** about gaps the brief leaves open for this story: behaviour, data, errors or
   acceptance the brief does not settle. Nothing open means ask nothing. At most
   `MAX_STORY_INTERVIEW_QUESTIONS` (`src/factory/domain/gates.py`) questions in total.
3. Build the request exactly as `factory.domain.interview.with_clarifications` does. No
   questions asked means the request unchanged; otherwise:

   ```
   <story request>

   ## Operator clarifications
   - <question> — <answer>
   - <question> — <answer> (assumption — the operator said "you decide")
   ```

   (one line per answer, an em dash between question and answer, the suffix only on assumed
   answers).
4. Show that final request and get approval with `AskUserQuestion`, as in the product interview.
5. Run `.venv/bin/factory run --project <project> --no-interview "<final request>"` (quote it
   safely; a heredoc into a variable is fine) and report what it printed: run id, where it
   parked, and the next command it suggests. `--no-interview` is right here because you just
   held the story interview; Checkpoint 1 still parks on any open question.
