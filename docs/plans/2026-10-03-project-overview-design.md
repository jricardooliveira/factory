# Factory Board Project Overview Design

## Problem

The board currently displays runs. A project with no runs has an empty table even
when the factory can say what phase it is in and what command should come next.
The board's `Project status` menu shows that information as text, while separate
menu items launch interview, backlog, and story actions.

## Approved direction

Add a dedicated project overview screen to the Textual board. It will show the
project lifecycle as four visual stages — Define, Plan, Build, Release — with
completed, current, and upcoming stages visually distinct. Under the stages it
will show the current phase detail, the lifecycle's recommended next action, and
a primary **Do next** button. A close button returns to the board.

The overview is for the currently filtered project. If the board is showing all
projects, the user first chooses one using the existing `p` project filter. Add
an `o` shortcut and a **Project overview** command-palette entry so the screen
is reachable both from the keyboard and the `m` menu. The Checkers case will show
Define complete, Plan current (“no backlog yet”), and **Propose backlog** as its
next action.

## Action behavior

The overview will use the existing `ProjectStatus` facts and lifecycle phases,
not derive project state a second time. Its primary action will route the
recommended `NextStep` to the board's existing operations: continue an interview,
propose and review a backlog, run the next approved story, or open the relevant
run review/approval flow. If a run is still working, the screen will explain that
and disable the action until a refresh shows a new next step. Alternative actions
remain available through the existing menu; the overview presents only the
primary next step to keep the view focused.

Long-running factory actions will continue to run through the board's worker
pattern. Errors will be shown as board notifications and will leave the screen
usable. After an action completes, refresh the overview from the status service
so the phases and primary action reflect the updated state.

## Components and data flow

`FactoryBoard` will load `project_status(project, db_path)` and present the
result in a new `ProjectOverviewScreen`. The screen will render the phases and
next-step explanation, and return a “do next” result when its primary button is
pressed. The board will dispatch that result using existing run services and
modal flows, then reload status. It will not shell out to the displayed command
string.

The new screen will follow the existing `ReviewScreen` and `PromptScreen`
interaction style. A small presentation helper may be added if needed to make
the phase states independently testable. Existing CLI status output and run
table behavior remain unchanged.

## Verification

- Add tests for rendering all lifecycle states, including the empty Checkers
  backlog case and a waiting-human run.
- Add board interaction coverage proving `o`/menu opens the overview for the
  filtered project and **Do next** dispatches the correct existing action.
- Run the focused board tests and the project status tests; then run the relevant
  test suite if those pass.
- Manually inspect the Textual screen at the board's normal terminal size and a
  narrow terminal size for readable phase labels and an accessible action.
