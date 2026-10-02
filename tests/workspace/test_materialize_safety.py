"""Materialization is the factory's single write chokepoint — it must hold.

Agents have write/edit/bash/patch disabled, so `materialize_code_blocks` is the
ONLY path from an agent's output to the filesystem. That makes it the right place
for two guarantees it did not have:

1. **No credential-shaped writes.** `_safe_target` blocks path traversal outside
   the repo root, but nothing stopped an agent writing `.env`, `.git/config`, or
   `.secrets/token` INSIDE it. The playbook's Stage-3 hook play is exactly this
   ("keep credentials out of diffs"), and for a factory whose agents cannot be
   trusted with tools it belongs at the chokepoint, not in a hook.

2. **All-or-nothing.** Blocks were written one at a time, so an invalid third
   block left the first two on disk — a half-materialized task that the scope
   check then reports as unclaimed out-of-band writes, blaming the agent for the
   orchestrator's partial failure.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.workspace.materialize import materialize_code_blocks


def _block(path: str, content: str = "x = 1\n", action: str = "create") -> dict:
    return {"path": path, "content": content, "action": action}


class CredentialPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_dotenv_is_refused(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            materialize_code_blocks([_block(".env", "API_KEY=sk-live-abc\n")], root=self.root)
        self.assertIn(".env", str(ctx.exception))
        self.assertFalse((self.root / ".env").exists())

    def test_env_variants_are_refused(self) -> None:
        for path in (".env.local", "config/.env.production", "app/.env"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    materialize_code_blocks([_block(path)], root=self.root)

    def test_writing_into_dot_git_is_refused(self) -> None:
        """A write into .git/ could repoint the very baseline the trust package
        measures the diff against."""
        with self.assertRaises(ValueError):
            materialize_code_blocks([_block(".git/config", "[core]\n")], root=self.root)
        with self.assertRaises(ValueError):
            materialize_code_blocks([_block(".git/hooks/pre-commit", "#!/bin/sh\n")],
                                    root=self.root)

    def test_secrets_and_key_material_are_refused(self) -> None:
        for path in (".secrets/token", "id_rsa", "certs/server.key", "creds.pem",
                     ".npmrc", ".pypirc", ".aws/credentials"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    materialize_code_blocks([_block(path)], root=self.root)

    def test_ordinary_source_and_config_still_write(self) -> None:
        written = materialize_code_blocks(
            [_block("src/app.py"), _block("pyproject.toml", "[project]\n"),
             _block("tests/test_app.py"), _block(".gitignore", "*.pyc\n"),
             _block("docs/environment.md", "# env notes\n")],
            root=self.root,
        )
        self.assertEqual(len(written), 5)
        self.assertTrue((self.root / "src" / "app.py").is_file())
        self.assertTrue((self.root / "docs" / "environment.md").is_file())


class AtomicityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_bad_block_writes_nothing_at_all(self) -> None:
        with self.assertRaises(ValueError):
            materialize_code_blocks(
                [_block("src/good.py"), _block("src/also_good.py"), _block(".env")],
                root=self.root,
            )
        self.assertFalse((self.root / "src" / "good.py").exists(),
                         "an invalid later block must not leave earlier blocks on disk")
        self.assertFalse((self.root / "src" / "also_good.py").exists())

    def test_traversal_outside_the_root_still_writes_nothing(self) -> None:
        with self.assertRaises(ValueError):
            materialize_code_blocks(
                [_block("src/good.py"), _block("../escape.py")], root=self.root
            )
        self.assertFalse((self.root / "src" / "good.py").exists())

    def test_an_unsupported_action_writes_nothing(self) -> None:
        with self.assertRaises(ValueError):
            materialize_code_blocks(
                [_block("src/good.py"), _block("src/bad.py", action="delete")],
                root=self.root,
            )
        self.assertFalse((self.root / "src" / "good.py").exists())


if __name__ == "__main__":
    unittest.main()
