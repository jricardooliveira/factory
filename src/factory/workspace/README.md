# workspace

## Responsibility

The product workspace: where the factory's state lives (`$FACTORY_HOME`), how a product project is
laid out and registered, the git plumbing that commits and measures it, and how coder output
reaches disk. A project directory IS its git repository; the factory's own evidence is committed
into it as it is produced, and is kept out of every measurement of the agents' code.

## Modules

| Module | What it does |
|---|---|
| `__init__.py` | Re-exports only `home`, `db_path`, `projects_dir`. |
| `layout.py` | `$FACTORY_HOME` resolver (`home`, `db_path`, `projects_dir`, `project_dir_for`, `replays_dir`, `replay_dir`); `EVIDENCE_PATHS` and `is_evidence_path`; `store_location` / `resolve_location` (DB-relative project paths); `normalize_slug`. |
| `projects.py` | `create_project` (scaffold repo + register in DB), `get_project`, `list_projects`, `project_repository`; `link_opencode_agents`, `agents_link_problem`; `render_project_rules`. |
| `templates.py` | Project-spec templates: `get_stack_template` / `create_project_spec` (only `fastapi`), `write_project_spec`. |
| `git.py` | Best-effort git plumbing, never raises: `git_init`, `git_commit_all`, `git_commit_paths`, `git_head`, `git_changed_paths`, `git_changed_files`, `collect_repo_diff`, `code_changed_since`, `_factory_baseline`. |
| `materialize.py` | `materialize_code_blocks` (writes `code_blocks` to disk, all-or-nothing), `normalize_block_path`, credential/VCS path refusal. |
| `sandbox.py` | Scratch clones for `factory replay`: `replay_sandbox`, `prepare_replay_sandbox`, `run_repository`. |
| `repo_map.py` | `build_repo_inventory(root)`: bounded interface map of existing code for the architect/coder prompts. |
| `legacy.py` | One-off `import_legacy` / `plan_legacy_import` behind `factory workspace import-legacy`. |

## How it works

- **Layout.** `home()` is `$FACTORY_HOME` (default `~/.factory`, resolved absolute at call time).
  It holds `factory.db` and `projects/<slug>/`. `db_path()` is THE database. `store_location`
  writes `projects.repo_path` / `spec_path` relative to the DB's directory when inside it, so a
  copied home stays self-contained; `resolve_location` re-anchors old absolute paths naming a
  `projects/<slug>` that exists in this home.
- **Project dir == repo.** `create_project` refuses a taken slug or non-empty dir before touching
  disk, then: `git_init`, writes `PROJECT_RULES.md`, links `.opencode` to the checkout's agents,
  copies the given spec (or writes the stack template) to `project-spec.json`, and commits both as
  the first `factory: scaffold ...` commit. `git_init` adds `/.opencode` to `.git/info/exclude`
  (not the product's `.gitignore`).
- **Evidence.** `EVIDENCE_PATHS` = `docs/work/`, `docs/architecture/adr/`, `docs/releases/`,
  `PROJECT_RULES.md`, `project-spec.json`. The factory commits them with `git_commit_paths`, which
  stages only the named paths (never `git add -A`, which would launder an out-of-band agent
  write). `materialize_code_blocks(reserved=...)` refuses them as coder output, and
  `git_changed_paths` / `git_changed_files` / `collect_repo_diff` / `code_changed_since` take
  `exclude=` so paperwork is never counted as code. Any new code measurement must pass `exclude`.
- **Materialize.** Validates every block first (action in `create`/`modify`, not evidence, not
  `.git/`, `.env*`, key files and similar, not escaping the root), then writes; one bad block
  writes nothing. `normalize_block_path` strips a leading `repo/` when the root is named `repo` or
  `repo_root=True`.
- **Baselines.** `_factory_baseline` is the parent of the oldest commit whose subject starts with
  `factory:` (empty tree if none), so that prefix is load-bearing. Diffs prefer the run's own
  `base_commit`; `git_changed_files(end=...)` pins a change set to a candidate commit.
- **Sandbox.** Despite the name, `sandbox.py` is not a code-execution sandbox. A replay works in
  `<home>/replays/run-<id>/`, cloned from the product at the replayed run's baseline commit, so the
  product's HEAD and history are never touched. Non-project runs get an empty directory.
- **repo_map.** Pure `ast` for `.py` (top-level signatures, up to 10 public methods per class),
  regex for exported Go declarations, file names only for js/ts. Defaults: 60 files, 6000 chars.
- **Legacy import.** Plans every move and refusal first (`LegacyImportError` leaves both sides
  untouched), then moves each old `repo/` to `<home>/projects/<slug>/`, merges its outside-repo
  evidence in as `factory: import legacy evidence (<id>)`, copies the DB (WAL-safe) with rewritten
  paths and removes the old one. `--dry-run` returns the plan only.

## Imports / imported by

- Imports: `factory.domain` (`project_spec`), `factory.state` (`db`, `projects`, `reports`, used by
  `projects.py` and `legacy.py`), `factory.agent_config.location` (`checkout_root`).
  `layout`, `git`, `materialize`, `repo_map`, `sandbox`, `templates` are leaves within the package.
- Imported by: `pipeline` (coder, gates, boss, prompts, evidence_writers, state), `runs`
  (service, queries), `evidence/trust_package.py`, `preflight/doctor.py`, `selftest`
  (`git_init`), and `interfaces/cli` (common, project, workspace). `verification` only mentions it.

## Gotchas / adding things

- State never lives in the working directory; tests point `FACTORY_HOME` at a tmp dir.
- Products are factory output: never hand-edit `$FACTORY_HOME/projects/*`.
- New factory-owned file in a product: add it to `EVIDENCE_PATHS` and commit it with
  `git_commit_paths`; otherwise it shows up as a coder change.
- New stack template: add it in `templates.py` and `SUPPORTED_STACKS`.
- New credential-shaped path to refuse: `_FORBIDDEN_*` in `materialize.py`.
- Git helpers return `None`/`False` when git is absent or the call fails; callers must handle that.
- `git_commit_all` (checkpoint after each task) does use `add -A`; evidence commits must not.
- Tests: `tests/workspace/`, plus `tests/integration/test_layout.py` for paths and layering.
