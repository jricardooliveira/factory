"""One-off import of the pre-$FACTORY_HOME layout (`factory workspace import-legacy`).

The old layout kept the run history in ``<old_dir>/factory.db`` and each product
in ``<old_dir>/projects/PROJ-NNN-<slug>/``, split in two: the code in ``repo/``
(its own git repository) and the product's audit trail — the artifact chain
under ``docs/work/``, ADRs, trust packages, ``PROJECT_RULES.md``,
``project-spec.json`` — one level up, OUTSIDE that repository. So a product's
evidence was never versioned with its code.

The import MOVES both into $FACTORY_HOME:

  - every registered project's ``repo/`` becomes ``<home>/projects/<slug>/``
    (history intact), its outside-repo evidence is merged into it and committed
    as ``factory: import legacy evidence (<id>)``;
  - ``factory.db`` is copied (sqlite backup, WAL-safe) to ``<home>/factory.db``
    with ``projects.repo_path`` / ``spec_path`` rewritten, then the old file is
    removed.

Everything is validated BEFORE anything moves (`plan_legacy_import`); a
refusal raises `LegacyImportError` and leaves both sides untouched. Anything in
an old project folder that is not evidence (``.secrets/``, stray state) stays
where it is and is reported — the import never decides what an unknown file is.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from factory.state.db import get_db
from factory.state.projects import relocate_project
from factory.state.reports import copy_database, has_history, read_only_projects
from factory.workspace import layout
from factory.workspace.git import git_commit_paths, git_init, is_git_repo
from factory.agent_config.location import checkout_root
from factory.workspace.layout import normalize_slug
from factory.workspace.projects import (
    RULES_FILENAME,
    SPEC_FILENAME,
    link_opencode_agents,
    render_project_rules,
)

_DB_SIDECARS = ("-wal", "-shm")
# Files at an old project's top level that are evidence and move INTO the repo.
_TOP_LEVEL_EVIDENCE = (RULES_FILENAME, SPEC_FILENAME)
# Old-scaffold lines in PROJECT_RULES.md that are now wrong. Agents read this
# file; "Source root: repo/" is what taught them to prefix every path with repo/.
_STALE_RULE_LINES = {
    "- Source root: repo/",
    "- Pipeline artifacts: docs/pipeline/",
    "- Project tasks: docs/work/tasks/",
}


class LegacyImportError(Exception):
    """The import was refused; nothing was moved."""


@dataclass
class ProjectMove:
    project_id: str
    slug: str
    source: Path  # the old projects/PROJ-NNN-<slug>/ folder
    target: Path  # <home>/projects/<slug>/
    repo_source: Path | None  # the old repo/ (None: no code yet)
    evidence: list[str] = field(default_factory=list)  # target-relative paths merged in
    left_behind: list[str] = field(default_factory=list)  # non-evidence, not moved
    spec_path: str | None = None  # the rewritten projects.spec_path
    spec_source: Path | None = None  # an external spec COPIED in as project-spec.json
    notes: list[str] = field(default_factory=list)  # warnings for the operator
    committed: bool = False


@dataclass
class LegacyImport:
    old_dir: Path
    home: Path
    dry_run: bool
    projects: list[ProjectMove] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # registered, folder not found
    unregistered: list[Path] = field(default_factory=list)  # folders with no DB row
    problems: list[str] = field(default_factory=list)

    @property
    def db_source(self) -> Path:
        return self.old_dir / layout.DB_FILENAME

    @property
    def db_target(self) -> Path:
        return self.home / layout.DB_FILENAME


# ── planning (read-only) ──────────────────────────────────────────


def _find_project_folder(old_dir: Path, project_id: str, repo_path: str) -> Path | None:
    """The old folder for a registered project.

    The DB stored absolute paths, which go stale when the folder moves (the
    factory itself moved from mvp/ to the repo root), so match by folder NAME
    under old_dir/projects first, then by the PROJ-NNN- prefix.
    """
    projects = old_dir / "projects"
    recorded = Path(repo_path)
    name = recorded.parent.name if recorded.name == "repo" else recorded.name
    if name and (projects / name).is_dir():
        return projects / name
    matches = sorted(projects.glob(f"{project_id}-*")) if projects.is_dir() else []
    matches = [m for m in matches if m.is_dir()]
    return matches[0] if len(matches) == 1 else None


def _relative_to_folder(path: Path, folder: Path) -> Path | None:
    """`path` relative to an old project folder, tolerating a since-moved prefix."""
    try:
        return path.resolve().relative_to(folder.resolve())
    except (ValueError, OSError):
        pass
    parts = path.parts
    for i in range(len(parts) - 1):
        if parts[i] == "projects" and parts[i + 1] == folder.name:
            return Path(*parts[i + 2:]) if len(parts) > i + 2 else Path()
    return None


def _evidence_files(folder: Path) -> list[str]:
    """Folder-relative evidence files: everything under docs/, plus rules + spec."""
    found: list[str] = []
    docs = folder / "docs"
    if docs.is_dir():
        found += sorted(
            p.relative_to(folder).as_posix() for p in docs.rglob("*") if p.is_file()
        )
    found += [name for name in _TOP_LEVEL_EVIDENCE if (folder / name).is_file()]
    return found


def _is_empty_tree(path: Path) -> bool:
    return path.is_dir() and not any(p.is_file() or p.is_symlink() for p in path.rglob("*"))


def _left_behind(folder: Path) -> list[str]:
    keep = {"repo", "docs", *_TOP_LEVEL_EVIDENCE}
    return sorted(
        child.name for child in folder.iterdir()
        if child.name not in keep and not _is_empty_tree(child)
    )


def _same_bytes(a: Path, b: Path) -> bool:
    try:
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def plan_legacy_import(old_dir: Path, *, home: Path | None = None) -> LegacyImport:
    """Work out every move and every refusal without touching anything."""
    old_dir = Path(old_dir).expanduser().resolve()
    home = (Path(home).expanduser() if home is not None else layout.home()).resolve()
    plan = LegacyImport(old_dir=old_dir, home=home, dry_run=True)

    if old_dir == home:
        plan.problems.append(f"old_dir and $FACTORY_HOME are the same directory: {home}")
        return plan
    if not plan.db_source.is_file():
        plan.problems.append(f"no legacy factory.db in {old_dir}")
        return plan
    if has_history(plan.db_target):
        plan.problems.append(
            f"{plan.db_target} already has run history; refusing to merge two databases"
        )

    # Read-only: planning (and so --dry-run) must not so much as checkpoint the WAL.
    rows: list[dict] = []
    try:
        rows = read_only_projects(plan.db_source)
    except sqlite3.Error as exc:
        plan.problems.append(f"cannot read projects from {plan.db_source}: {exc}")

    claimed: set[Path] = set()
    for row in rows:
        folder = _find_project_folder(old_dir, row["id"], row.get("repo_path") or "")
        if folder is None:
            plan.missing.append(f"{row['id']} ({row['slug']}): no folder under {old_dir}/projects")
            continue
        claimed.add(folder.resolve())
        target = home / "projects" / normalize_slug(row["slug"])
        repo_source = folder / "repo" if (folder / "repo").is_dir() else None
        move = ProjectMove(
            project_id=row["id"], slug=row["slug"], source=folder, target=target,
            repo_source=repo_source, evidence=_evidence_files(folder),
            left_behind=_left_behind(folder),
        )
        if target.exists() and any(target.iterdir()):
            plan.problems.append(f"{row['id']}: target {target} already exists ({row['slug']})")
        if repo_source is not None:
            for rel in move.evidence:
                inside = repo_source / rel
                if inside.exists() and not _same_bytes(inside, folder / rel):
                    plan.problems.append(
                        f"{row['id']}: {rel} exists both inside repo/ and beside it with "
                        f"different content — merge it by hand first"
                    )
        _plan_spec(move, row.get("spec_path"), old_dir)
        plan.projects.append(move)

    projects_dir = old_dir / "projects"
    if projects_dir.is_dir():
        plan.unregistered = sorted(
            p for p in projects_dir.iterdir()
            if p.is_dir() and p.resolve() not in claimed and not _is_empty_tree(p)
        )
    return plan


def _external_spec_candidates(recorded: Path, old_dir: Path) -> list[Path]:
    """Where an external spec may be now. The DB recorded absolute paths that the
    restructure invalidated (<factory>/mvp/specs/x.json is now
    <factory>/examples/specs/x.json), so after the recorded path, look by NAME in
    the old dir's specs/ and in this factory's examples/specs/."""
    return [
        recorded,
        old_dir / "specs" / recorded.name,
        checkout_root() / "examples" / "specs" / recorded.name,
    ]


def _plan_spec(move: ProjectMove, spec_path: str | None, old_dir: Path) -> None:
    """Decide projects.spec_path after the move (and whether to copy a spec in).

    A spec inside the old project folder moves with the evidence. An EXTERNAL spec
    is copied into the repo as project-spec.json, so the product carries the spec
    it was designed against; the operator's original is left where it is.
    """
    move.spec_path = spec_path
    if not spec_path:
        return
    rel = _relative_to_folder(Path(spec_path), move.source)
    if rel is not None:
        parts = rel.parts
        if parts and parts[0] == "repo":
            rel = Path(*parts[1:]) if len(parts) > 1 else Path()
        move.spec_path = str(move.target / rel)
        return
    if SPEC_FILENAME in move.evidence or (
        move.repo_source is not None and (move.repo_source / SPEC_FILENAME).exists()
    ):
        move.notes.append(
            f"spec_path points outside the project ({spec_path}) but the project already "
            f"has its own {SPEC_FILENAME}; the row is kept as-is"
        )
        return
    found = next((p for p in _external_spec_candidates(Path(spec_path), old_dir)
                  if p.is_file()), None)
    if found is None:
        move.notes.append(
            f"spec not found at {spec_path} (nor by name in specs/ or examples/specs/); "
            f"the row is kept — fix it before the next run"
        )
        return
    move.spec_source = found
    move.spec_path = str(move.target / SPEC_FILENAME)


# ── execution ─────────────────────────────────────────────────────


def migrate_rules_text(text: str) -> str:
    """Drop the old scaffold's now-wrong path lines; keep every operator line."""
    lines = text.splitlines()
    if not any(line.strip() in _STALE_RULE_LINES for line in lines):
        return text
    fresh = render_project_rules("", "", "").splitlines()
    new_lines = [ln for ln in fresh if ln.startswith(("- Source root:", "- Factory-owned"))]
    out: list[str] = []
    inserted = False
    for line in lines:
        if line.strip() in _STALE_RULE_LINES:
            if not inserted:
                out.extend(new_lines)
                inserted = True
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _move_project(move: ProjectMove) -> None:
    move.target.parent.mkdir(parents=True, exist_ok=True)
    if move.target.exists():
        move.target.rmdir()  # validated empty during planning
    if move.repo_source is not None:
        shutil.move(str(move.repo_source), str(move.target))
    else:
        move.target.mkdir(parents=True)
    git_init(move.target)

    for rel in move.evidence:
        src, dst = move.source / rel, move.target / rel
        if dst.exists():
            src.unlink()  # identical copy already in the repo (checked in planning)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))

    to_commit = list(move.evidence)
    if move.spec_source is not None:
        shutil.copyfile(move.spec_source, move.target / SPEC_FILENAME)
        to_commit.append(SPEC_FILENAME)

    rules = move.target / RULES_FILENAME
    if rules.is_file():
        text = rules.read_text(encoding="utf-8")
        migrated = migrate_rules_text(text)
        if migrated != text:
            rules.write_text(migrated, encoding="utf-8")

    link_opencode_agents(move.target)
    if is_git_repo(move.target):
        move.committed = git_commit_paths(
            move.target, to_commit,
            f"factory: import legacy evidence ({move.project_id})",
        )

    # Tidy the old folder: drop emptied scaffolding, keep anything unknown.
    for child in sorted(move.source.iterdir()) if move.source.is_dir() else []:
        if _is_empty_tree(child):
            shutil.rmtree(child)
    if move.source.is_dir() and not any(move.source.iterdir()):
        move.source.rmdir()


def _move_database(plan: LegacyImport) -> None:
    plan.home.mkdir(parents=True, exist_ok=True)
    for suffix in ("", *_DB_SIDECARS):
        stale = Path(f"{plan.db_target}{suffix}")
        if stale.exists():
            stale.unlink()  # an EMPTY home DB (checked in planning)
    copy_database(plan.db_source, plan.db_target)
    with get_db(plan.db_target) as conn:
        for move in plan.projects:
            relocate_project(
                conn,
                move.project_id,
                layout.store_location(move.target, plan.db_target),
                (
                    layout.store_location(Path(move.spec_path), plan.db_target)
                    if move.spec_path
                    else None
                ),
            )
    for suffix in ("", *_DB_SIDECARS):
        old = Path(f"{plan.db_source}{suffix}")
        if old.exists():
            old.unlink()


def import_legacy(
    old_dir: Path, *, home: Path | None = None, dry_run: bool = False
) -> LegacyImport:
    """Move a legacy factory.db + projects/ into $FACTORY_HOME (see module doc)."""
    plan = plan_legacy_import(old_dir, home=home)
    if plan.problems:
        raise LegacyImportError("; ".join(plan.problems))
    plan.dry_run = dry_run
    if dry_run:
        return plan
    for move in plan.projects:
        _move_project(move)
    _move_database(plan)
    old_projects = plan.old_dir / "projects"
    if old_projects.is_dir() and not any(old_projects.iterdir()):
        old_projects.rmdir()
    return plan
