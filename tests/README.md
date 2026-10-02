# Tests

These tests check the factory itself: its command-line interface, agent
configuration, pipeline, database, verification rules, and saved eval cases.
They do not test product projects created by the factory; those live outside
this repository under `$FACTORY_HOME/projects/`.

## Run the checks

From the repository root:

```bash
make test       # Python test suite
make evals      # agent configuration and saved replay cases
make sim        # offline sample runs through the pipeline
make check      # all of the above, plus lint when ruff is installed
```

These checks run locally and do not call AI models. To run an individual test
file, use the project's Python environment, for example:

```bash
.venv/bin/python -m pytest tests/pipeline/test_public_api.py -q
```

## Where tests live

Most test folders mirror a package under `src/factory/`, such as `pipeline/`,
`workspace/`, and `verification/`. `integration/` contains checks that cover
more than one package. `fixtures/` contains saved agent responses and prompts
used by the tests. `selftest/` checks the offline simulations and eval runner.

Use a focused test while changing one area, then run `make check` before relying
on the full factory verification result. See [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md)
for how the packages relate to each other.
