"""Factory-level project registry and scaffolding.

A project lives at ``$FACTORY_HOME/projects/<slug>/`` and that directory IS its
git repository: code at the root, the factory's evidence under ``docs/``,
``PROJECT_RULES.md`` and ``project-spec.json`` beside the code. It used to be
split in two — code in ``projects/<P>/repo/`` and its audit trail one level up,
outside that repository — so a product's history lived partly in the factory.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any

from factory.agent_config.location import checkout_root, product_skills_dir
from factory.state import projects as project_rows
from factory.state.db import get_db, init_db
from factory.workspace import layout
from factory.workspace.git import git_commit_paths, git_init
from factory.workspace.layout import normalize_slug
from factory.workspace.templates import create_project_spec, write_project_spec

RULES_FILENAME = "PROJECT_RULES.md"
SKILLS_RELDIR = ".claude/skills"
# Operator decision (2026-10-03): every product gets these, against AI slop and
# code nobody asked for. Copies of the MIT-licensed originals live in skills/.
PRODUCT_SKILLS = (
    "ponytail", "ponytail-review", "karpathy-guidelines",
    "test-driven-development", "systematic-debugging", "verification-before-completion",
)
CODE_DISCIPLINE_HEADING = "## Code discipline"
# Prompt text: PROJECT_RULES.md heads every agent's project memory. Short on purpose.
CODE_DISCIPLINE = f"""{CODE_DISCIPLINE_HEADING}

- Build only what the story's acceptance criteria require: no speculative features,
  options, or scaffolding "for later".
- Reuse before writing: this repo's code, then the standard library, then an installed
  dependency; add a dependency only when a few lines cannot do it.
- No abstraction with one implementation, no config for a value that never changes.
- Surgical changes: touch only what the task needs, match the surrounding style, never
  refactor or reformat unrelated code.
- Fix a bug at its root, where every caller benefits, and start with a test that
  reproduces it. Every behaviour change ships with the smallest test that fails if it
  breaks.
- Never trade away input validation at trust boundaries, error handling that prevents
  data loss, or security measures for brevity.
- Claim only what was verified.

(The full practice: `.claude/skills/` — ponytail, karpathy-guidelines, superpowers TDD,
systematic debugging, verification before completion.)
"""
SPEC_FILENAME = "project-spec.json"


def _resolved(row: dict[str, Any] | None, db_path: Path) -> dict[str, Any] | None:
    """A projects row with its stored locations resolved for THIS database
    (see `layout.resolve_location`): callers always get absolute paths."""
    if row is None:
        return None
    project = dict(row)
    for key in ("repo_path", "spec_path"):
        location = layout.resolve_location(project.get(key), db_path)
        project[key] = str(location) if location is not None else None
    return project


def factory_opencode_dir() -> Path:
    """This checkout's ``.opencode/`` — what every product's ``.opencode`` links to."""
    return checkout_root() / ".opencode"


def link_opencode_agents(repo_path: Path) -> None:
    """Point <repo>/.opencode at the factory's agent definitions.

    An existing SYMLINK is re-pointed (a repo moved by `import-legacy` still
    links to wherever the factory used to live); a real directory is left alone.
    """
    agents_dir = factory_opencode_dir()
    target = repo_path / ".opencode"
    if not agents_dir.exists():
        return
    if target.is_symlink():
        if target.resolve() == agents_dir.resolve():
            return
        target.unlink()
    elif target.exists():
        return
    try:
        target.symlink_to(agents_dir, target_is_directory=True)
    except OSError:
        # The project can still be registered; users may copy/link agents manually.
        return


def agents_link_problem(repo_path: Path) -> str | None:
    """Why a LIVE run in `repo_path` would not drive this checkout's agents.

    None when it would. A missing link is created (what `create_project` does); a
    real ``.opencode`` directory is the operator's own choice and left alone; a
    symlink into ANOTHER checkout (a clone, a worktree, a moved factory) is the
    problem: opencode would load that checkout's agents, not the ones this
    checkout's `make evals` validated.
    """
    agents = factory_opencode_dir()
    link = Path(repo_path) / ".opencode"
    if not agents.exists():
        return None  # an installed wheel has no checkout agents to link (as before)
    if link.is_symlink():
        if link.resolve() == agents.resolve():
            return None
        return (
            f"{Path(repo_path).name}/.opencode points at {link.resolve()}, not this "
            f"factory's agents ({agents}): the run would drive another checkout's "
            f"agents. Re-link it if this checkout should drive the project: "
            f"ln -sfn {agents} {link}"
        )
    if not link.exists():
        link_opencode_agents(Path(repo_path))
    return None


def render_project_rules(project_id: str, slug: str, name: str) -> str:
    """The scaffolded PROJECT_RULES.md.

    Agents read this file (it heads the project-memory block), so what it says
    about paths is prompt text: "Source root: repo/" is what taught them to
    prefix every path with ``repo/``. It now names the repo root, and lists the
    paths the factory owns so no agent tries to author them.
    """
    owned = ", ".join(f"`{p}`" for p in layout.EVIDENCE_PATHS)
    return "\n".join(
        [
            f"# {name} Project Rules",
            "",
            f"- Project ID: {project_id}",
            f"- Slug: {slug}",
            "- Source root: the repository root (write repo-relative paths, e.g. `app/main.py`)",
            f"- Factory-owned evidence (never write these): {owned}",
            "",
            CODE_DISCIPLINE,
        ]
    )


def _write_project_rules(project_dir: Path, project_id: str, slug: str, name: str) -> None:
    rules_path = project_dir / RULES_FILENAME
    if rules_path.exists():
        return
    rules_path.write_text(render_project_rules(project_id, slug, name), encoding="utf-8")


def install_product_skills(project_dir: Path) -> list[str]:
    """Copy PRODUCT_SKILLS (+ their licenses) into <repo>/.claude/skills; the paths written."""
    source, target = product_skills_dir(), project_dir / SKILLS_RELDIR
    written: list[str] = []
    for name in (*PRODUCT_SKILLS, "licenses", "LICENSES.md"):
        src, dst = source / name, target / name
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        elif src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        else:
            continue
        written.append(f"{SKILLS_RELDIR}/{name}")
    return written


def refresh_project(db_path: Path, project_ref: str) -> bool:
    """Bring a product's factory-owned scaffold up to date: the skills, the code
    discipline in its rules, the .opencode link. True if a commit was made."""
    project = get_project(db_path, project_ref)
    repo = Path(project["repo_path"])
    link_opencode_agents(repo)
    paths = install_product_skills(repo)
    rules = repo / RULES_FILENAME
    text = rules.read_text(encoding="utf-8") if rules.is_file() else ""
    if CODE_DISCIPLINE_HEADING not in text:
        if not text:
            text = render_project_rules(project["id"], project["slug"],
                                        project.get("name") or project["slug"])
        else:
            text = text.rstrip("\n") + "\n\n" + CODE_DISCIPLINE
        rules.write_text(text, encoding="utf-8")
    return git_commit_paths(repo, [RULES_FILENAME, *paths],
                            f"factory: refresh scaffold {project['id']}")


def create_project(
    db_path: Path,
    *,
    home: Path | None = None,
    slug: str,
    name: str | None = None,
    spec_path: Path | None = None,
    stack: str | None = None,
) -> dict[str, Any]:
    """Create a project repository under <home>/projects/<slug>/ and register it.

    `home` defaults to $FACTORY_HOME. An explicit `spec_path` is COPIED into the
    repo as project-spec.json (so the spec the architect designed against is part
    of the product's history); otherwise `stack` writes the stack template there.
    Either way the scaffold is committed as the repo's first ``factory:`` commit.
    """

    init_db(db_path)
    normalized_slug = normalize_slug(slug)
    project_name = name or normalized_slug.replace("-", " ").title()
    root = Path(home) if home is not None else layout.home()
    project_dir = root / "projects" / normalized_slug

    with get_db(db_path) as conn:
        # Refuse BEFORE touching disk, so a refusal never leaves a half-made repo.
        if project_rows.slug_taken(conn, normalized_slug):
            raise ValueError(f"Project slug already exists: {normalized_slug}")
        if project_dir.exists() and any(project_dir.iterdir()):
            raise ValueError(f"Project directory already exists and is not empty: {project_dir}")
        if spec_path is not None and not Path(spec_path).is_file():
            raise ValueError(f"Project spec not found: {spec_path}")

        project_id = project_rows.next_project_id(conn)
        project_dir.mkdir(parents=True, exist_ok=True)

        git_init(project_dir)
        _write_project_rules(project_dir, project_id, normalized_slug, project_name)
        link_opencode_agents(project_dir)
        skills = install_product_skills(project_dir)

        resolved_spec_path: Path | None = None
        if spec_path is not None:
            resolved_spec_path = project_dir / SPEC_FILENAME
            shutil.copyfile(spec_path, resolved_spec_path)
        elif stack:
            resolved_spec_path = write_project_spec(
                project_dir / SPEC_FILENAME,
                create_project_spec(stack, name=project_name, slug=normalized_slug),
            )

        # The scaffold is the repo's first factory: commit — the baseline every
        # later measurement of the factory's CODE change is taken from.
        git_commit_paths(
            project_dir,
            [RULES_FILENAME, SPEC_FILENAME, *skills],
            f"factory: scaffold {project_id} {normalized_slug}",
        )

        try:
            project_rows.insert_project(
                conn,
                project_id=project_id,
                slug=normalized_slug,
                name=project_name,
                repo_path=layout.store_location(project_dir, db_path),
                spec_path=(
                    layout.store_location(resolved_spec_path, db_path)
                    if resolved_spec_path
                    else None
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Project slug already exists: {normalized_slug}") from exc

        project = _resolved(project_rows.get_project_row(conn, project_id), db_path)
        assert project is not None
        return project


def get_project(db_path: Path, ref: str) -> dict[str, Any]:
    """Resolve a project by id or slug."""

    init_db(db_path)
    normalized_ref = ref if ref.startswith("PROJ-") else normalize_slug(ref)
    with get_db(db_path) as conn:
        project = _resolved(project_rows.get_project_row(conn, normalized_ref), db_path)
    if not project:
        raise ValueError(f"Unknown project: {ref}")
    return project


def list_projects(db_path: Path) -> list[dict[str, Any]]:
    """Return all registered projects ordered by id."""

    init_db(db_path)
    with get_db(db_path) as conn:
        rows = project_rows.list_project_rows(conn)
    return [p for row in rows if (p := _resolved(row, db_path)) is not None]


def project_repository(project_row: dict[str, Any], db_path: Path) -> Path | None:
    """The absolute repository of a stored projects row (or a run joined with one)."""
    return layout.resolve_location(project_row.get("repo_path"), db_path)
