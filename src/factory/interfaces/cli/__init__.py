"""The `factory` command line.

`main.py` parses argv and dispatches; each command group lives in its own module
(`run`, `review`, `project`, `selftest`, `board`) and renders through
`factory.interfaces.render`. Run orchestration is `factory.runs`, never here.

Deliberately re-exports nothing: `factory.interfaces.cli.main` must stay the
MODULE (the console-script target is `factory.interfaces.cli.main:main`), so
`patch("factory.interfaces.cli.main.<name>")` resolves the way it reads.
"""
