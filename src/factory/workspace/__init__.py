"""The product workspace: where state lives, projects, spec templates, git plumbing,
materialization.

`home()` / `db_path()` / `projects_dir()` are the single resolver for the
factory's state ($FACTORY_HOME, default ~/.factory) — see `workspace.layout`.
"""

from factory.workspace.layout import db_path, home, projects_dir

__all__ = ["db_path", "home", "projects_dir"]
