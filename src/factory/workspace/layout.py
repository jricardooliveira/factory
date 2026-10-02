"""Where the factory keeps its state, and which paths inside a product it owns.

`$FACTORY_HOME` (default ``~/.factory``) holds ``factory.db`` and every product
under ``projects/<slug>/``. Every reader and writer of factory state resolves the
location here: the DB path used to be a bare ``factory.db`` relative to whatever
directory `factory` was run from, which scattered the run history across four
stray databases on one machine.

A product directory IS its git repository: code at its root, the factory's
evidence beside it under ``docs/`` (see `EVIDENCE_PATHS`).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

FACTORY_HOME_ENV = "FACTORY_HOME"
DB_FILENAME = "factory.db"

# Paths inside a product repository that the FACTORY writes and owns: the
# artifact chain, the decision memory, the trust packages, and the two
# operator-authored inputs. They are committed by the factory as they are
# produced (subject prefix ``factory:``), so they are never an agent's change:
#   - the coder may not write them (gate-1 reads PROJECT_RULES.md as
#     operator-authored, so a coder able to rewrite it could settle its own
#     ambiguity questions),
#   - and they are excluded from every CODE measurement — the coder's scope /
#     out-of-band check, the trust package's change set, the tester's diff.
# An entry ending in "/" is a directory prefix; any other entry is one file.
EVIDENCE_PATHS: tuple[str, ...] = (
    "docs/work/",
    "docs/architecture/adr/",
    "docs/releases/",
    "PROJECT_RULES.md",
    "project-spec.json",
)


def home() -> Path:
    """The factory's state directory: $FACTORY_HOME, else ~/.factory (absolute)."""
    raw = os.environ.get(FACTORY_HOME_ENV, "").strip()
    base = Path(raw).expanduser() if raw else Path.home() / ".factory"
    return base.resolve()


def db_path() -> Path:
    """The ONE factory database: <home>/factory.db."""
    return home() / DB_FILENAME


def projects_dir() -> Path:
    """Where products live: <home>/projects/."""
    return home() / "projects"


def project_dir_for(slug: str) -> Path:
    """A product's directory, which is also its git repository."""
    return projects_dir() / slug


def replays_dir(base: Path | None = None) -> Path:
    """Where replays run: scratch clones under <home>/replays/, never the product."""
    return (base if base is not None else home()) / "replays"


def replay_dir(run_id: int, base: Path | None = None) -> Path:
    """The scratch repository a replay run (and any resume of it) works in."""
    return replays_dir(base) / f"run-{run_id}"


def store_location(path: Path | str, db: Path) -> str:
    """How a product path is written to factory.db.

    Relative to the directory holding the DB (the home) when it lives inside it,
    absolute otherwise. A home is then self-contained: copy or move it and its
    database still points at ITS products — with absolute paths, a run in a copied
    home silently wrote into the original's repositories.
    """
    base = Path(db).resolve().parent
    target = Path(path).expanduser().resolve()
    try:
        return target.relative_to(base).as_posix()
    except ValueError:
        return str(target)


def resolve_location(stored: str | None, db: Path) -> Path | None:
    """The absolute path a stored product location means for THIS database.

    Relative paths resolve against the DB's directory. An absolute path recorded
    before locations were stored relatively is re-anchored when it names a
    ``projects/<slug>`` that this home also has: the DB was copied or moved along
    with its products, and the copy's repositories are the ones it governs.
    """
    if not stored:
        return None
    # Anchor on the DB path as the caller spelled it (no symlink resolution), so
    # a relative location comes back in the same form the caller uses.
    base = Path(db).expanduser().absolute().parent
    path = Path(stored)
    if not path.is_absolute():
        return base / path
    for anchor in (base, base.resolve()):
        if path.is_relative_to(anchor):
            return path
    parts = path.parts
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] == "projects":
            local = base.joinpath(*parts[i:])
            if (base / "projects" / parts[i + 1]).is_dir():
                return local
            break
    return path


def normalize_slug(value: str) -> str:
    """Return a filesystem and CLI friendly project slug (the ``projects/<slug>`` name)."""

    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise ValueError("Project slug must contain at least one letter or number")
    return slug


def is_evidence_path(path: str, evidence: tuple[str, ...] = EVIDENCE_PATHS) -> bool:
    """Whether a repo-relative path is factory-owned evidence."""
    rel = path.strip().strip('"')
    while rel.startswith("./"):
        rel = rel[2:]
    for entry in evidence:
        if entry.endswith("/"):
            if rel.startswith(entry) or rel == entry.rstrip("/"):
                return True
        elif rel == entry:
            return True
    return False
