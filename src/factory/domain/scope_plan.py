"""Reconcile concrete design files with a uniquely identifiable owning task."""
from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath

from factory.domain.contracts import ArchitectOutput, SpecOutput


def _family(path: str) -> str:
    name = PurePosixPath(path).name
    for family in ('prettier', 'eslint', 'vite', 'vitest', 'tsconfig'):
        if family in name:
            return family
    return PurePosixPath(name).stem


def reconcile_scope(spec: SpecOutput, arch: ArchitectOutput) -> tuple[SpecOutput, list[str]]:
    """Only a concrete design file and unique tool/directory owner can extend a task.

    The resulting plan still passes gate 2. No global exemption is introduced, and
    no prose module labels, broad patterns, protected evidence or unsafe paths grant writes.
    """
    revised = spec.model_copy(deep=True)
    changes = []
    for raw in arch.modules_affected:
        path = raw.removeprefix('./').removeprefix('repo/')
        parts = PurePosixPath(path).parts
        if (not parts or path.startswith('/') or '..' in parts or any(c in path for c in '*?[] :')
                or parts[0] in ('.git', '.claude', '.opencode')
                or path.startswith(('docs/work/', 'docs/releases/', 'docs/architecture/adr/'))
                or path in ('PROJECT_RULES.md', 'project-spec.json')
                or not (PurePosixPath(path).suffix or PurePosixPath(path).name.startswith('.'))):
            continue
        scopes = [s.removeprefix('./').removeprefix('repo/') for t in revised.tasks for s in t.scope]
        if any(path == s or path.startswith(s.rstrip('/') + '/') or fnmatch.fnmatchcase(path, s)
               for s in scopes):
            continue
        owners = [t for t in revised.tasks if any(_family(s) == _family(path) for s in t.scope)]
        if not owners and str(PurePosixPath(path).parent) != '.':
            owners = [t for t in revised.tasks if any(PurePosixPath(s).parent == PurePosixPath(path).parent
                                                       for s in t.scope)]
        if len(owners) == 1:
            owners[0].scope.append(path)
            changes.append(f'{owners[0].id}: {path} (required by the design)')
    return revised, changes
