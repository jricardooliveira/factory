"""Live environment preflight (`factory doctor`): is this machine ready to run?

Unlike `selftest` — offline and zero-token by contract — the doctor checks the
real environment and spends one trivial probe per distinct tier model (skipped by
`--offline`). That is why it is its own package: `selftest` stays the part of the
factory the evals contract can run anywhere for free.
"""
