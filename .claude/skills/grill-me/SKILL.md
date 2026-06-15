---
name: grill-me
description: Use BEFORE writing a plan, design, workflow, or spec — when the user says "design X", "create a workflow/plan for X", "let's build X", or "how should we approach X". Interview relentlessly to reach shared understanding before proposing anything.
---

# Grill Me

Before you write a single line of plan, design, or code, **interview the user relentlessly** about the idea until you both reach genuine shared understanding. Do not jump to a plan. The conversation is the work.

**Why:** jumping to an early plan bakes in unexamined assumptions. Most bad output traces back to a decision nobody actually made on purpose. Surfacing the design tree first is cheaper than rewriting later.

## When to use

- The user asks you to design, plan, spec, or scope something non-trivial.
- The user describes an idea and seems to expect you to start building.
- You notice yourself about to produce a multi-step plan from a one-line request.

Skip for: trivial, fully-specified, single-step tasks where there is genuinely nothing to resolve.

## How to grill

1. **Map the design tree.** Identify the major decision points implied by the idea. Walk down each branch; decisions often depend on each other — resolve dependencies one at a time, parents before children.
2. **Ask one cluster at a time.** Group related questions; don't dump 30 at once. Use `AskUserQuestion` for genuine forks with discrete options; use plain prose for open-ended ones.
3. **Be relentless but useful.** Expect to ask many questions (real sessions run 16–50). Each must change what gets built — no questions for their own sake.
4. **Verify claims against reality.** Before accepting an assertion about the codebase ("we already have X"), check it. Don't interview in a vacuum.
5. **Surface tradeoffs, don't hide them.** When a choice has real downsides, name them and give a recommendation the user can override.
6. **Reflect back.** Periodically summarize the shared understanding so far so drift is caught early.

## Question targets

Probe these until each is pinned down or explicitly deferred:
- **Goal & success:** what does "done" look like; how do we know it worked?
- **Scope & non-goals:** what are we explicitly NOT doing?
- **Users & context:** who uses this, in what situation, with what constraints?
- **Boundaries:** what existing systems/contracts must it respect?
- **Failure & edges:** what happens when it breaks, when input is bad, at scale?
- **Tradeoffs:** what are we optimizing for, and what are we willing to give up?
- **Sequencing:** what must come first; what unblocks what?

## Stop condition

Stop grilling when the remaining open questions are deferrable and you could write a plan that the user would recognize as *theirs*, not yours. Then — and only then — propose the plan and confirm before building.

## Red flags

- Producing a plan after one or two questions.
- Asking questions whose answers wouldn't change the design.
- Accepting vague answers ("make it good", "the usual") without a follow-up.
- Interviewing about things you could simply check in the code.
