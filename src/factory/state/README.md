# factory.state

## Responsibility

Owns every SQL statement the factory runs. It defines the SQLite schema, opens connections, and
exposes plain accessor functions for projects, stories, runs, agent logs, gate results and boss
authorizations, plus read-only aggregate queries. The database holds the verbatim input/output of
every agent call and every gate verdict, which is what makes replay, resume and the metrics
possible. Nothing outside this package may call `.execute` (enforced by
`tests/integration/test_layout.py::test_sql_lives_only_in_state`).

## Modules

| Module | What it does |
|---|---|
| `db.py` | `SCHEMA` (tables `projects`, `stories`, `pipeline_runs`, `agent_logs`, `gate_results`, `authorizations`); `get_db` (context manager: WAL, FKs on, commit/rollback/close), `connect` (plain handle the caller closes), `init_db` (schema + additive migrations). Accessors: `create_story`, `next_story_id`, `start_run`, `finish_run`, `update_run_stage`, `update_story_status/_title`, `log_agent`, `log_gate`, `respond_to_gate`, `log_authorization`, `get_run`, `list_runs`, `get_runs_by_status`, `get_run_logs`, `get_run_gates`, `get_run_authorizations`, `get_agent_log`, `get_agent_log_by_stage`, `get_agent_logs_for`, `get_pending_human_gate`, `get_answered_human_gate`, `get_human_responses`, `get_run_cost`, `live_runs_in_project`, `set_candidate_commit`, `reopen_run`, `requeue_answered_gate`, `archive_run`, `reconcile_stale_runs`. |
| `projects.py` | Accessors for the `projects` table: `next_project_id` (`PROJ-001`...), `slug_taken`, `insert_project`, `get_project_row` (by id or slug), `list_project_rows`, `list_projects_with_run_counts`, `relocate_project`. |
| `reports.py` | Read-only aggregates: `run_status_counts`, `gate_tallies`, `coder_attempts`, `checkpoint_counts`, `usage_totals`, `replay_run_count` (metrics); `runs_overview`, `agent_logs_by_run`, `gate_results_by_run` (HTML report); `read_only_summary`, `read_only_projects`, `has_history`, `copy_database` (doctor and legacy import). |

## How it works

- **No default database path.** `get_db(path)` and `init_db(path)` require one; callers pass
  `factory.workspace.db_path()` (`$FACTORY_HOME/factory.db`). A bare `factory.db` default once
  resolved against the working directory and scattered history across stray databases.
- **Schema evolution is additive**: `init_db` runs `CREATE TABLE IF NOT EXISTS` then
  `_ensure_column` (checks `PRAGMA table_info`, `ALTER TABLE ... ADD COLUMN`) for columns added
  later (`base_commit`, `replay_of`, `candidate_commit`, `responded_at`, usage and hash columns).
  New columns go through `_ensure_column`, never a rewritten `CREATE TABLE`.
- **Gates vs authorizations are separate tables**: `gate_results` judges a stage's output;
  `authorizations` records whether the boss let a stage start (`missing` / `warnings` stored as JSON).
  Keeping them apart keeps the gate pass rates in `factory metrics` honest.
- **Checkpoints live in `gate_results`**: a parked gate has `needs_human=1` and
  `human_response IS NULL`; `respond_to_gate` stamps `responded_at`. `get_answered_human_gate`
  and `requeue_answered_gate` rescue a run that died after the operator answered.
- **Newest wins on resume.** `get_agent_log`, `get_agent_logs_for` and `get_answered_human_gate` order
  `id DESC`, whereas `get_run_logs` / `get_run_gates` are ascending. Resume code must use the
  newest-first accessors, or it rebuilds an earlier design than the one approved.
- **Replays are excluded from outcome metrics** (`reports._LIVE_RUNS` filters `replay_of IS NULL`)
  and from `live_runs_in_project`, since they re-log frozen outputs and run in a scratch clone.
- **`log_agent` stores a `prompt_hash`** (16-hex SHA-256 of the input) next to the optional
  `agent_prompt_hash` of the agent definition.
- **`reconcile_stale_runs`** only marks `running` rows older than a cutoff (default 3600s) as
  `failed`, conservatively, so long multi-task runs survive.
- **Read-only peeks** (`read_only_summary`, `read_only_projects`) open the file with `mode=ro` so
  the doctor and legacy import never create or migrate a database; `has_history` treats an
  unreadable DB as "has history" so callers refuse rather than overwrite it.
- **Path columns are stored as given**; `workspace.projects` decides relative vs absolute.

## Dependencies

Imports no other `factory` package (standard library only; the layering table allows `domain`).
Imported by `pipeline` (nodes, `boss`, `agent_calls`), `runs`, `evidence` (`trust_package`,
`progress`, `pipeline_record`, `metrics`), `workspace` (`projects`, `legacy`), `selftest`
(`simulate`, `evals/capture`) and `preflight/doctor.py`. `interfaces` does not import it directly.

## Where to add things

- New table or column: edit `SCHEMA` and, for an existing table, add an `_ensure_column` call in
  `init_db`; add the accessor here. Put a new reader that is an aggregate in `reports.py`, a
  `projects` table accessor in `projects.py`.
- Every accessor takes an open `sqlite3.Connection`; the caller owns the transaction (`get_db`).
- Every terminal run state must go through `finish_run`, or the run is stuck `running`.
- Never commit `*.db`; all state is regenerable and gitignored.
