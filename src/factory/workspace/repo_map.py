"""Deterministic repo interface inventory — brownfield awareness for agents.

Gives the architect (and coder) a compact map of what already exists in a project
repo — module paths plus public function/class signatures — WITHOUT dumping file
contents. This lets the architect design against the real codebase instead of an
imagined one, and lets the coder integrate with existing interfaces. Pure `ast`,
no LLM, bounded in size.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_IGNORE_DIRS = {
    ".git", "__pycache__", ".venv", "venv", ".opencode", ".pytest_cache",
    "node_modules", ".sandbox", ".mypy_cache", ".ruff_cache", "dist", "build",
    ".egg-info", "vendor",
}
_CODE_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".go"}
_MAX_METHODS = 10


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        args = ast.unparse(node.args)
    except Exception:
        args = "..."
    sig = f"def {node.name}({args})"
    if node.returns is not None:
        try:
            sig += f" -> {ast.unparse(node.returns)}"
        except Exception:
            pass
    return sig


def _class_line(node: ast.ClassDef) -> str:
    bases = []
    for b in node.bases:
        try:
            bases.append(ast.unparse(b))
        except Exception:
            continue
    suffix = f"({', '.join(bases)})" if bases else ""
    return f"class {node.name}{suffix}"


def _public_methods(node: ast.ClassDef) -> list[str]:
    out: list[str] = []
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if item.name.startswith("_") and item.name != "__init__":
                continue
            try:
                out.append(f"{item.name}({ast.unparse(item.args)})")
            except Exception:
                out.append(f"{item.name}(...)")
        if len(out) >= _MAX_METHODS:
            break
    return out


def _py_interfaces(text: str) -> list[str] | None:
    """Top-level function/class interfaces for a Python module, or None if it
    doesn't parse (caller still lists the path so the gap is visible)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    lines: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines.append(f"  - {_signature(node)}")
        elif isinstance(node, ast.ClassDef):
            lines.append(f"  - {_class_line(node)}")
            for m in _public_methods(node):
                lines.append(f"    - {m}")
    return lines


# Exported (capitalised) top-level Go declarations: funcs, methods and types.
_GO_DECL = re.compile(
    r"^(?:func (?:\([^)]*\) )?[A-Z]\w*\(.*?(?=\s*\{\s*$|$)|type [A-Z]\w* \w+)",
    re.MULTILINE,
)


def _go_interfaces(text: str) -> list[str]:
    """Exported Go declarations as one-line signatures (no bodies). Go files were
    listed by name only, so a later story was designed blind to the existing
    backend's API (review task T05)."""
    out: list[str] = []
    for match in _GO_DECL.finditer(text):
        line = match.group(0).rstrip(" {")
        if line.startswith("type ") and not line.endswith(("struct", "interface")):
            line = line  # aliases / named types are kept as written
        out.append(f"    {line}")
        if len(out) >= _MAX_METHODS * 2:
            break
    return out


def _iter_code_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in _CODE_SUFFIXES:
            continue
        rel = path.relative_to(root)
        if any(part in _IGNORE_DIRS or part.endswith(".egg-info") for part in rel.parts):
            continue
        yield path, rel


def build_repo_inventory(root: Path, *, max_files: int = 60, max_chars: int = 6000) -> str:
    """A compact interface map of the repo, or "" if there is no code yet."""
    files = list(_iter_code_files(root))
    if not files:
        return ""

    blocks: list[str] = []
    for path, rel in files[:max_files]:
        header = str(rel).replace("\\", "/")
        if path.suffix == ".py":
            try:
                interfaces = _py_interfaces(path.read_text(encoding="utf-8"))
            except OSError:
                interfaces = None
            if interfaces is None:
                blocks.append(f"- {header}  (does not parse)")
            elif interfaces:
                blocks.append(f"- {header}\n" + "\n".join(interfaces))
            else:
                blocks.append(f"- {header}  (no public interfaces)")
        elif path.suffix == ".go":
            try:
                interfaces = _go_interfaces(path.read_text(encoding="utf-8"))
            except OSError:
                interfaces = []
            blocks.append(f"- {header}\n" + "\n".join(interfaces) if interfaces
                          else f"- {header}  (no exported declarations)")
        else:
            blocks.append(f"- {header}")

    if len(files) > max_files:
        blocks.append(f"- … and {len(files) - max_files} more file(s) (truncated)")

    body = "\n".join(blocks)
    if len(body) > max_chars:
        body = body[:max_chars] + "\n… (inventory truncated)"
    return body
