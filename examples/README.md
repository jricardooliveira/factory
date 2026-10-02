# Example specifications

The files in `specs/` are sample `project-spec.json` files. A project spec tells
the agents what kind of product they are working on and which technical rules
they must follow. It can name the language, framework, data store, architecture,
conventions, and things the project must not use.

These examples show different project shapes:

- `tic-tac-toe.json` — a small browser game with no server or database.
- `taskflow.json` — a task management application with an API and database.
- `supportflow.json` — a support ticket application with explicit workflow rules.

Use an example to understand the format or as a starting point for your own
project. Review and edit it to match your actual codebase and decisions; the
examples are not automatically applied to every project.

To create a project with the built-in FastAPI template, run
`factory project create <slug> --stack fastapi`. To create or update its spec,
use `factory spec init <slug> --stack fastapi`, then review the resulting
`project-spec.json` before asking the factory to implement a task.
