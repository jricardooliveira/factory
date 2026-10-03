"""Git plumbing for a product workspace (best-effort, never raises).

Split out of the old ``verify.py``: committing a task checkpoint, finding the
pre-factory baseline and measuring the real change set are workspace concerns,
not verification checks. The trust package and the pipeline both read from here.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path

from factory.workspace.layout import is_evidence_path

_TIMEOUT = 60


def _run(
    cmd: list[str], cwd: Path, timeout: int = _TIMEOUT
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        # Never inherit stdin: a git hook or credential prompt reading it would
        # block the pipeline. See adapters.opencode.run_agent.
        stdin=subprocess.DEVNULL,
    )


def is_git_repo(root: Path) -> bool:
    return (root / ".git").exists()


def git_init(root: Path) -> None:
    """Initialize a git repo at root if not already one (best-effort)."""
    if is_git_repo(root) or shutil.which("git") is None:
        return
    try:
        _run(["git", "init", "-q"], cwd=root)
    except (subprocess.SubprocessError, OSError):
        return
    _exclude_factory_infra(root)


# Factory plumbing that lives inside a product repo but is not part of the product.
# `.opencode` is the symlink `projects.link_opencode_agents` creates so opencode can
# resolve the agent definitions; it is an absolute path to the operator's machine.
# Bytecode and test caches are what verification and test runs leave behind, and a
# product `.venv` is the operator's test environment: `git add -A` committed them all
# (the same set `_is_noise` already keeps out of every code measurement).
_INFRA_EXCLUDES = ("/.opencode", "__pycache__/", "*.pyc", ".pytest_cache/", "/.venv/")


def _exclude_factory_infra(root: Path) -> None:
    """Keep factory plumbing out of the product's git history (idempotent).

    Uses `.git/info/exclude`, not the product's `.gitignore`: the factory has no
    business editing the product's own files to hide its own. Re-asserted on every
    commit because `git_init` is a no-op on a repo that already exists.

    Without it, `git add -A` committed the `.opencode` symlink into the generated
    app; it then appeared in the diff the tester and remediation coder review, the
    coder echoed it back as a code block, and the run failed.
    """
    exclude = root / ".git" / "info" / "exclude"
    try:
        existing = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
        missing = [e for e in _INFRA_EXCLUDES if e not in existing.splitlines()]
        if missing:
            exclude.parent.mkdir(parents=True, exist_ok=True)
            sep = "" if not existing or existing.endswith("\n") else "\n"
            exclude.write_text(existing + sep + "\n".join(missing) + "\n", encoding="utf-8")
    except OSError:
        return


def git_commit_all(root: Path, message: str) -> bool:
    """Commit all current changes in the repo (best-effort).

    Used to checkpoint each completed task so the NEXT task's `git status` shows
    only its own new files — otherwise the governance/scope check would see prior
    tasks' files as undeclared out-of-band writes. Returns True if a commit was made.
    """
    if not is_git_repo(root) or shutil.which("git") is None:
        return False
    _exclude_factory_infra(root)
    try:
        _run(["git", "add", "-A"], root)
        proc = _run(
            ["git", "-c", "user.name=factory", "-c", "user.email=factory@local",
             "commit", "-m", message, "--no-gpg-sign"],
            root,
        )
        return proc.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def git_commit_paths(root: Path, paths: Iterable[str | Path], message: str) -> bool:
    """Commit ONLY `paths` (relative to root, or absolute under it); best-effort.

    How the factory commits the evidence it writes into a product repo (the
    artifact chain, ADRs, trust packages, rules, spec) the moment it is produced.
    Deliberately not `git add -A`: sweeping the whole tree into an evidence commit
    would also commit any file an agent wrote out of band, hiding it from the
    coder's governance check. Returns True if a commit was made.
    """
    if not is_git_repo(root) or shutil.which("git") is None:
        return False
    rels: list[str] = []
    root_resolved = root.resolve()
    for raw in paths:
        path = Path(raw)
        if path.is_absolute():
            try:
                path = path.resolve().relative_to(root_resolved)
            except ValueError:
                continue  # outside this repo: not ours to commit
        if (root / path).exists():
            rels.append(path.as_posix())
    if not rels:
        return False
    _exclude_factory_infra(root)
    try:
        _run(["git", "add", "--", *rels], root)
        staged = _run(["git", "diff", "--cached", "--quiet", "--", *rels], root)
        if staged.returncode == 0:
            return False  # already committed as-is
        proc = _run(
            ["git", "-c", "user.name=factory", "-c", "user.email=factory@local",
             "commit", "-m", message, "--no-gpg-sign", "--", *rels],
            root,
        )
        return proc.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def git_discard_paths(root: Path, paths: Iterable[str | Path]) -> list[str]:
    """Undo the factory's own UNCOMMITTED writes at `paths` (best-effort).

    For a run that stops mid-coding: what it materialized is only committed when a
    task passes, so a failed attempt's files would otherwise be read as the NEXT
    story's out-of-band writes. A path tracked at HEAD is restored from HEAD; an
    untracked one is deleted, with the parent dirs that become empty. Deliberately
    path-by-path, never `git clean`: the operator's untracked files (a product
    `.venv`, notes) are not the factory's to delete. Evidence, `.git` and anything
    outside the repo are never touched. The attempt's code stays in agent_logs.
    Returns the repo-relative paths discarded.
    """
    if not is_git_repo(root) or shutil.which("git") is None:
        return []
    root_resolved = root.resolve()
    discarded: list[str] = []
    for raw in sorted({str(p) for p in paths if str(p).strip()}):
        target = (root_resolved / raw).resolve()  # absolute `raw` wins in the join
        try:
            rel = target.relative_to(root_resolved).as_posix()
        except ValueError:
            continue  # outside this repo (incl. a symlink pointing out): not ours
        if rel == "." or rel.split("/", 1)[0] == ".git" or is_evidence_path(rel):
            continue
        try:
            if _run(["git", "cat-file", "-e", f"HEAD:{rel}"], root).returncode == 0:
                if _run(["git", "checkout", "HEAD", "--", rel], root).returncode == 0:
                    discarded.append(rel)
                continue
            if not target.is_file():
                continue
            target.unlink()
            discarded.append(rel)
            if target.suffix == ".py":  # verification compiled it: that bytecode is ours too
                cache = target.parent / "__pycache__"
                for pyc in cache.glob(f"{target.stem}.*.pyc"):
                    pyc.unlink()
                if cache.is_dir() and not any(cache.iterdir()):
                    cache.rmdir()
            # ponytail: removes every parent left empty up to the root; an empty dir
            # the operator made before the run would go too (git can't track it anyway).
            parent = target.parent
            while parent != root_resolved:
                parent.rmdir()  # OSError once a parent is not empty: stop there
                parent = parent.parent
        except (subprocess.SubprocessError, OSError):
            continue
    return discarded


def _excluded(path: str, exclude: tuple[str, ...]) -> bool:
    return bool(exclude) and is_evidence_path(path, exclude)


def _exclude_pathspecs(exclude: tuple[str, ...]) -> list[str]:
    """`-- . :(exclude)<p>...` so git itself leaves the excluded paths out of a diff."""
    if not exclude:
        return []
    return ["--", ".", *(f":(exclude){e.rstrip('/')}" for e in exclude)]


# Git's well-known empty-tree object — a valid "diff from nothing" baseline.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_DIFF_MAX_CHARS = 16000


def _git_resolve(root: Path, ref: str) -> str | None:
    """Resolve a git ref to a sha, or None if it doesn't exist."""
    try:
        proc = _run(["git", "rev-parse", "--verify", "--quiet", ref], root)
    except (subprocess.SubprocessError, OSError):
        return None
    sha = proc.stdout.strip()
    return sha if sha else None


def _factory_baseline(root: Path) -> str:
    """The pre-factory commit to diff against: the parent of the OLDEST commit the
    factory authored (message prefix ``factory:``). Falls back to the empty tree
    when there is no such parent (factory commit is the repo root) or no factory
    commit at all."""
    try:
        proc = _run(["git", "log", "--reverse", "--format=%H%x1f%s"], root)
    except (subprocess.SubprocessError, OSError):
        return _EMPTY_TREE
    if proc.returncode != 0:
        return _EMPTY_TREE
    for line in proc.stdout.splitlines():
        sha, _, subject = line.partition("\x1f")
        if subject.startswith("factory:"):
            return _git_resolve(root, f"{sha}^") or _EMPTY_TREE
    return _EMPTY_TREE


def collect_repo_diff(
    root: Path,
    *,
    max_chars: int = _DIFF_MAX_CHARS,
    exclude: tuple[str, ...] = (),
    base: str | None = None,
) -> str | None:
    """Real cumulative diff of the factory's changes, for the tester to review.

    Returns the git diff from `base` (the run's starting commit; else the
    pre-factory baseline) to the current repo state
    (committed + any working-tree changes), so the tester sees EVERY task's change
    — not just the last one's self-report. None when not a git repo (caller then
    falls back to the agent's self-reported implementation); ``""`` when the repo
    is clean. Truncated to ``max_chars`` to bound prompt cost. Paths matching
    `exclude` (a project's factory-owned evidence) are left out: the tester
    reviews code, not the factory's paperwork.
    """
    if not is_git_repo(root) or shutil.which("git") is None:
        return None
    if _git_resolve(root, "HEAD") is None:
        return None  # no commits yet — nothing to diff
    # `base` = the run's own starting commit, so a review sees THIS story's change.
    # Diffing from the first factory commit made story N's review carry stories
    # 1..N-1, which consumed its character budget (review task T05).
    # `^{commit}`: `rev-parse --verify` accepts ANY well-formed full sha without
    # checking that the object exists; a stale base must fall back, not be used.
    if not base or _git_resolve(root, f"{base}^{{commit}}") is None:
        base = _factory_baseline(root)
    try:
        spec = _exclude_pathspecs(exclude)
        committed = _run(["git", "diff", base, "HEAD", *spec], root)
        working = _run(["git", "diff", *spec], root)
    except (subprocess.SubprocessError, OSError):
        return None
    parts = [p.stdout for p in (committed, working) if p.returncode == 0 and p.stdout.strip()]
    diff = "\n".join(parts).strip()
    if len(diff) > max_chars:
        diff = diff[:max_chars] + f"\n... [diff truncated at {max_chars} chars]"
    return diff


def git_head(root: Path) -> str | None:
    """The repo's current commit sha, or None if not a git repo / no commits.

    Captured at run start as the run's own baseline, so the trust package can
    measure THIS run's change set instead of every factory commit ever made.
    """
    if not is_git_repo(root) or shutil.which("git") is None:
        return None
    return _git_resolve(root, "HEAD")


# git --name-status letters → the schema's change vocabulary.
_STATUS_MAP = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "added"}


def git_changed_files(
    root: Path, base: str | None, *, exclude: tuple[str, ...] = (), end: str | None = None
) -> list[dict[str, str]] | None:
    """Real per-file change set from `base` to the current state, or None if it
    cannot be measured from git.

    Returns ``[{"path": ..., "change": "added|modified|deleted|renamed"}]``. This
    is the trust package's Evidence 2: what git saw, not what the agent claimed.
    A file both committed and then edited appears once, with its committed status.
    Paths matching `exclude` (a project's factory-owned evidence) are not part of
    the change set: the factory wrote them, no agent did.

    `end` pins the change set to a commit (the candidate reviewed at Checkpoint 3):
    then nothing after it — a later story, a working-tree edit — can change what
    an old package says it measured.
    """
    if not is_git_repo(root) or shutil.which("git") is None:
        return None
    if _git_resolve(root, "HEAD") is None:
        return None
    baseline = base or _factory_baseline(root)
    try:
        if end and _git_resolve(root, f"{end}^{{commit}}"):
            committed = _run(["git", "diff", "--name-status", baseline, end], root)
            working = untracked = subprocess.CompletedProcess([], 1, "", "")
        else:
            committed = _run(["git", "diff", "--name-status", baseline, "HEAD"], root)
            working = _run(["git", "diff", "--name-status"], root)
            untracked = _run(["git", "ls-files", "--others", "--exclude-standard"], root)
    except (subprocess.SubprocessError, OSError):
        return None
    if committed.returncode != 0:
        return None

    files: dict[str, str] = {}
    for proc in (committed, working):
        if proc.returncode != 0:
            continue
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            # Rename lines are "R100\told\tnew" — the new path is what changed.
            status, path = parts[0][:1], parts[-1].strip()
            if _is_noise(path) or _excluded(path, exclude):
                continue
            files.setdefault(path, _STATUS_MAP.get(status, "modified"))
    if untracked.returncode == 0:
        for path in untracked.stdout.splitlines():
            path = path.strip()
            if path and not _is_noise(path) and not _excluded(path, exclude):
                files.setdefault(path, "added")
    return [{"path": p, "change": c} for p, c in sorted(files.items())]


def code_changed_since(
    root: Path, commit: str, *, exclude: tuple[str, ...] = ()
) -> bool | None:
    """Has any CODE changed since `commit` — committed, uncommitted or untracked?

    `exclude` is the factory's own evidence: it keeps committing PIPELINE.md and
    trust packages after a checkpoint, and that is not a change to what was
    reviewed. None when it cannot be measured (no git, unknown commit).
    """
    if not is_git_repo(root) or shutil.which("git") is None:
        return None
    if _git_resolve(root, f"{commit}^{{commit}}") is None:
        return None
    spec = _exclude_pathspecs(exclude) or ["--", "."]
    try:
        tracked = _run(["git", "diff", "--quiet", commit, *spec], root)
        untracked = _run(["git", "ls-files", "--others", "--exclude-standard", *spec], root)
    except (subprocess.SubprocessError, OSError):
        return None
    if tracked.returncode not in (0, 1):
        return None
    new_files = [p for p in untracked.stdout.splitlines() if p.strip() and not _is_noise(p)]
    return tracked.returncode == 1 or bool(new_files)


def _is_noise(path: str) -> bool:
    """Infra/tooling paths that are not application code the factory authored."""
    if not path or "__pycache__" in path or path.endswith((".pyc", ".pyo")):
        return True
    return path.split("/", 1)[0] in {".opencode", ".git", ".sandbox", ".venv"}


def git_changed_paths(root: Path, *, exclude: tuple[str, ...] = ()) -> list[str] | None:
    """Return changed paths from `git status --porcelain`, or None if not a repo.

    Paths matching `exclude` (a project's factory-owned evidence) are omitted, so
    the coder's out-of-band-write check never mistakes the factory's own evidence
    for an undeclared agent write.
    """
    if not is_git_repo(root) or shutil.which("git") is None:
        return None
    try:
        # --untracked-files=all lists individual files (not collapsed dirs) so the
        # diff is comparable to the agent's per-file claims. Project repos are tiny.
        proc = _run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root)
    except (subprocess.SubprocessError, OSError):
        return None
    paths: list[str] = []
    for line in proc.stdout.splitlines():
        # format: "XY <path>" (XY = 2-char status)
        path = line[3:].strip().strip('"')
        if not path or "__pycache__" in path or path.endswith((".pyc", ".pyo")):
            continue
        # Infra/tooling noise — not application code the agent claims to author.
        if path.split("/", 1)[0] in {".opencode", ".git", ".sandbox", ".venv"}:
            continue
        if _excluded(path, exclude):
            continue
        paths.append(path)
    return paths
