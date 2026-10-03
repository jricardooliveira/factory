# Board Project Overview Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Give the Factory Board a visual project lifecycle overview with a primary action that starts the lifecycle's recommended next step.

**Architecture:** Add a focused Textual screen that renders the existing `ProjectStatus` phases and next-step recommendation. Expose it from the board shortcut and command palette; dispatch its action through the board's existing run services and approval modals, never through a shell command. Refresh status after actions and use existing worker/error-notification patterns for long work.

**Tech Stack:** Python 3.12+, Textual, Rich, unittest, pytest.

---

### Task 1: Build and test the project overview screen

**Files:**
- Create: `src/factory/interfaces/board/overview_screen.py`
- Test: `tests/interfaces/board/test_overview_screen.py`
- Reference: `src/factory/runs/status.py` (`ProjectStatus`)
- Reference: `src/factory/domain/lifecycle.py` (`Phase`, `NextStep`)

**Step 1: Write the failing screen tests**

Cover a four-phase project with Define done, Plan current, and Build/Release upcoming; assert phase names, details, next-step reason and action label are present. Also cover a waiting-human project and assert the approval action is represented distinctly.

**Step 2: Run the focused tests**

Run: `uv run pytest tests/interfaces/board/test_overview_screen.py -q`
Expected: FAIL because `ProjectOverviewScreen` is not implemented.

**Step 3: Implement the screen**

Create a Textual screen receiving a `ProjectStatus` and returning a “do next” result when the primary button is pressed. Render each phase with a state-specific icon/style (`done`, `now`, `todo`), show the active phase detail and next-step explanation, and provide a close action. Keep layout readable at narrow terminal widths by stacking the phase rows vertically.

**Step 4: Run the focused tests**

Run: `uv run pytest tests/interfaces/board/test_overview_screen.py -q`
Expected: PASS.

### Task 2: Add board entry points and next-step dispatch

**Files:**
- Modify: `src/factory/interfaces/board/tui.py`
- Modify: `src/factory/domain/lifecycle.py`
- Test: `tests/interfaces/board/test_board_menu.py`
- Test: `tests/interfaces/board/test_tui.py`
- Test: `tests/domain/test_lifecycle.py`

**Step 1: Write failing board interaction tests**

Test that `o` opens the overview for the filtered project, that the command palette contains **Project overview**, and that Checkers-style state dispatches backlog proposal from **Do next**. Add coverage for the next approved story and for a waiting-human run so the dispatcher does not treat a displayed command as shell input.

**Step 2: Run the focused tests**

Run: `uv run pytest tests/interfaces/board/test_board_menu.py tests/interfaces/board/test_tui.py tests/domain/test_lifecycle.py -q`
Expected: FAIL because the overview entry point and action dispatch are absent.

**Step 3: Implement board navigation and semantic next actions**

Add an `o` binding and palette entry. Require the existing project filter; when “all” is selected, notify the user to choose a project with `p`. Load `ProjectStatus` through `project_status`, push the overview screen, and dispatch its result using a semantic action from lifecycle data. Reuse `_backlog_text`, `_next_text`, interview, and run-resume/review flows as appropriate. Disable the primary action while the next step is “wait for running run”. Run long actions in the existing worker pattern, surface exceptions through `notify`, and refresh the overview state after completion.

Avoid parsing or executing `NextStep.command`; if the current lifecycle model lacks a stable action identifier, add an explicit action kind and optional run ID to `NextStep` and populate them in `next_step`.

**Step 4: Run the focused tests**

Run: `uv run pytest tests/interfaces/board/test_board_menu.py tests/interfaces/board/test_tui.py tests/domain/test_lifecycle.py -q`
Expected: PASS.

### Task 3: Check CLI and board behavior together

**Files:**
- Test: `tests/runs/test_status.py`
- Test: `tests/interfaces/board/test_overview_screen.py`
- Test: `tests/interfaces/board/test_board_menu.py`

**Step 1: Run project status and board tests**

Run: `uv run pytest tests/runs/test_status.py tests/interfaces/board/test_overview_screen.py tests/interfaces/board/test_board_menu.py tests/interfaces/board/test_tui.py -q`
Expected: PASS; the CLI lifecycle facts remain unchanged and the board action follows the same `NextStep` decision.

**Step 2: Check patch formatting**

Run: `git diff --check`
Expected: no output and exit status 0.

**Step 3: Inspect the interactive layout**

Launch `uv run factory board`, choose a project with `p`, open the overview with `o`, and check the Checkers empty-backlog state. Confirm the primary action opens backlog proposal and its existing approve/reject review flow. Resize to a narrow terminal and confirm labels and buttons remain usable.

**Step 4: Commit the feature**

```bash
git add src/factory/interfaces/board/overview_screen.py src/factory/interfaces/board/tui.py src/factory/domain/lifecycle.py tests/interfaces/board/test_overview_screen.py tests/interfaces/board/test_board_menu.py tests/interfaces/board/test_tui.py tests/domain/test_lifecycle.py
git commit -m "feat: add project overview to factory board"
```
