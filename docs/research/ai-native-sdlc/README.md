# AI-native SDLC: source notes and factory assessment

Research date: **2026-10-02**. This directory records an evaluation, not an implementation change.

Start with the [repository assessment](assessment.md) for the verdict and proposed priorities. Read [verification evidence](evidence.md) for the exact revision, checks, reproductions, and limitations. The notes below explain the source material in plain language.

## Sources and scope

I read the [AI-native SDLC playbook article](https://claude.com/blog/the-ai-native-sdlc-playbook), the [Academy course index](https://academy.claude.com/courses/ai-native-sdlc-playbook), and all 14 linked lesson pages. These are concise paraphrases and interpretations, not a copy of the course. The assessment uses repository code and independent local experiments as its evidence.

The article and course describe a development process spanning planning, design, building, testing, deployment, and maintenance. Their enterprise examples need adapting to this factory's single-operator context. The article also currently includes recurring codebase scans and channel-driven incident handling. Those are extensions to the loop, not prerequisites for this project's next improvement.

The pages are living documents. These notes describe what was accessible on the research date; they do not certify completion of an Academy account, exercises, or unexposed course media. Vendor-specific example settings were not tested here.

## Plain-language vocabulary

| Term | Meaning here |
|---|---|
| SDLC | Software development lifecycle: deciding what to build, building it, checking it, releasing it, and maintaining it. |
| Intent | The problem someone wants solved and the outcome they want. |
| Spec | A clearer description of the required behavior and how to recognize success. |
| Plan | The proposed files, tasks, order, and checks for implementing the spec. |
| Gate | A rule that decides whether work may continue. It can allow, stop, or ask a person. |
| Artifact | A saved result, such as a spec, test report, diff, or approval record. |
| Eval | A repeatable task used to measure an AI system's behavior against explicit checks. |
| Replay | Running saved answers through the factory again to check its orchestration. |
| ADR | Architecture decision record: why a design choice was made and its tradeoffs. |
| Sandbox | An enforced boundary around what a process can read, write, execute, or reach over the network. A separate directory alone does not provide all of this. |
| Leading indicator | An early signal, such as successful first verification attempts. |
| Lagging indicator | An eventual outcome, such as defects discovered after release. |

## What each lesson teaches

### 1. Introduction

Faster code generation moves pressure to deciding what should be built, reviewing changes, and releasing them safely. Improve the whole process rather than counting generated code. Make handoffs carry understandable evidence, with people retaining decisions that need judgment.

**Factory interpretation:** judge success by trustworthy completed changes and the review effort they require. A high agent-call count or green orchestration run is not enough.

[Source: Introduction](https://academy.claude.com/courses/ai-native-sdlc-playbook/introduction)

### 2. Capture intent

Turn a request into a durable description of the problem, desired outcome, constraints, affected systems, and unanswered questions. Let a person correct the interpretation before it becomes the basis for more work. Keep the accepted result with the project.

**Factory interpretation:** the original request and the approved interpretation should remain distinguishable. An agent's assumption should never quietly become an operator decision.

[Source: Capture intent](https://academy.claude.com/courses/ai-native-sdlc-playbook/capture-intent)

### 3. Requirements and design

Apply the project's rules while forming requirements and the design. Identify conflicts and tradeoffs early. Produce an accepted specification before implementation, with enough detail to review whether the result satisfies the request.

**Factory interpretation:** project rules, acceptance criteria, design decisions, and approval status need one clear home each. Saving a proposed ADR is useful; it does not make its decision approved.

[Source: Requirements and design](https://academy.claude.com/courses/ai-native-sdlc-playbook/requirements-and-design)

### 4. Plan mode

Inspect and plan before making edits. Name the files, task order, risks, and verification steps. Let the reviewer improve the plan while changes are still cheap. Keep the plan and implementation aligned when new information changes the approach.

**Factory interpretation:** a task should say what it may change and what evidence will demonstrate completion. Approval needs to identify the particular plan being approved.

[Source: Plan mode](https://academy.claude.com/courses/ai-native-sdlc-playbook/plan-mode)

### 5. CLAUDE.md

Maintain concise repository instructions containing useful commands, architecture boundaries, and recurring lessons. Keep them current as the project changes. Repeated mistakes are candidates for explicit instructions; stale or excessive material weakens the signal.

**Factory interpretation:** separate instructions for developing the factory from instructions sent to agents building products. Each must match the tools and commands used in that environment.

[Source: CLAUDE.md](https://academy.claude.com/courses/ai-native-sdlc-playbook/claude-md)

### 6. Skills as institutional knowledge

Package repeatable procedures as versioned skills, with clear triggers and expected results. Test whether they activate in the right situations. Instructions guide behavior; mandatory rules require deterministic enforcement. Put expensive checks at suitable checkpoints rather than slowing every small edit.

**Factory interpretation:** a reusable review or testing procedure is useful, but a prompt saying “always” is not proof that it happened.

[Source: Skills as institutional knowledge](https://academy.claude.com/courses/ai-native-sdlc-playbook/skills-as-institutional-knowledge)

### 7. Parallel sessions and subagents

Separate independent work into isolated worktrees. Keep edits to shared files sequential. Use scoped helpers for recurring jobs, including independent verification. Increase concurrency only as far as a person can review the results effectively.

**Factory interpretation:** more simultaneous coders should follow workspace isolation and reliable review. A separate tester context is useful even while implementation remains sequential.

[Source: Parallel sessions and subagents](https://academy.claude.com/courses/ai-native-sdlc-playbook/parallel-sessions-and-subagents)

### 8. Give Claude a feedback loop

Give the agent commands or visual checks that reveal whether its work actually behaves correctly. Use that feedback during implementation. For a bug, first demonstrate failure with a regression test, then preserve that check while fixing the code. A fresh final verifier complements the repeated inner checks.

**Factory interpretation:** distinguish syntax checks, test discovery, executed tests, and a model's review. Protect the test that proves a bug; permit new tests without automatically permitting old expectations to be weakened.

[Source: Give Claude a feedback loop](https://academy.claude.com/courses/ai-native-sdlc-playbook/give-claude-a-feedback-loop)

### 9. Continuous evals in CI

Exercise representative tasks against the current agent configuration and evaluate the results. The course suggests building a corpus of 20–50 real tasks, running it on configuration changes or a schedule, and reviewing regressions before merging. Add incidents as lasting cases and refresh cases as capabilities change.

**Factory interpretation:** preserve cheap configuration checks and frozen-output replay, then add a separately budgeted layer that asks the current model to solve tasks. Only the latter measures changes in answer quality.

[Source: Continuous evals in CI](https://academy.claude.com/courses/ai-native-sdlc-playbook/continuous-evals-in-ci)

### 10. AI in the PR review loop

Use a versioned review policy with clear severity levels and a limit on low-value comments. Review correctness, security, and compliance with the accepted design. Keep approval separate from authorship and enforce merge policy through repository controls. Feed repeated findings back into instructions.

**Factory interpretation:** the tester's policy is a useful start. A product PR should eventually contain the specific candidate diff, verification results, unresolved findings, and a separate approval decision.

[Source: AI in the PR review loop](https://academy.claude.com/courses/ai-native-sdlc-playbook/ai-in-the-pr-review-loop)

### 11. Hooks as approval gates

Express required approvals at the boundary before an action occurs. Allow routine work, ask for decisions that need a person, and block forbidden actions with an explanation. Organization-managed controls matter where developers must not be able to override policy. Tool permissions and operating-system isolation address different boundaries.

**Factory interpretation:** enforce product constraints where the factory invokes a process or writes files. Hooks belonging to the tool used to develop the factory do not automatically govern its runtime adapter.

[Source: Hooks as approval gates](https://academy.claude.com/courses/ai-native-sdlc-playbook/hooks-as-approval-gates)

### 12. CI/CD integration and deployment

Begin with read-only diagnosis in CI. Add controlled write actions through reviewed changes, isolated execution, and scoped credentials. Make permissions depend on the environment. Preserve a production approval boundary and rehearse rollback before relying on autonomous recovery.

**Factory interpretation:** first establish a trustworthy release candidate and PR handoff. Add deployment later, with an explicit release target, approval record, and tested recovery path.

[Source: CI/CD integration and deployment](https://academy.claude.com/courses/ai-native-sdlc-playbook/ci-cd-integration-and-deployment)

### 13. Closing the loop on metrics

Use deterministic monitoring to trigger bounded diagnosis or action. Convert findings into new intent, route them through review, record triage decisions, and add a regression eval when the issue is fixed. The detector and the response permissions should be explicit rather than improvised by the model.

**Factory interpretation:** with little operational history, begin with simple thresholds and a proposal queue. Statistical control bands and autonomous rollback can wait for reliable data and a deployment system.

[Source: Closing the loop on metrics](https://academy.claude.com/courses/ai-native-sdlc-playbook/closing-the-loop-on-metrics)

### 14. Closing thoughts and resources

Treat the practices as a connected operating model. Human judgment remains responsible for the decisions while automation performs bounded work and carries evidence between stages. Supporting documentation helps tailor the controls to the environment.

**Factory interpretation:** adopting the principles does not require replacing OpenCode with Claude Code. Compare observable guarantees—permissions, verification, review, provenance, and recovery—before considering a runtime replacement.

[Source: Closing thoughts and resources](https://academy.claude.com/courses/ai-native-sdlc-playbook/closing-thoughts-and-resources)

## Suggested adoption order for this factory

This order is my repository-specific recommendation, not the course's lesson order:

1. Make completion and release evidence accurate, current, and enforceable.
2. Run product verification in isolation, with protected regression checks.
3. Give agents adequate existing-code context and enforce approved change scope.
4. Measure current agent behavior with bounded live evals and honest usage accounting.
5. Add story branches, PR review, and release authorization.
6. Connect real operational findings back to new work and permanent evals.

The [assessment](assessment.md) explains why these steps address the code that exists today.
