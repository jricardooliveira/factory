"""Combined-candidate checks: missing tools/suites never count as release evidence."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from factory.verification import VerifyCheck, VerifyResult, tests_enabled, verify_changes
from factory.verification.base import build_timeout, suite_timeout


def verify_candidate(root: Path, paths: list[str]) -> VerifyResult:
    result = verify_changes([root / p for p in paths if (root / p).is_file()], root=root)
    if not tests_enabled():
        result.checks.append(VerifyCheck('executed-tests', 'fail',
                            'Enable FACTORY_RUN_TESTS=1 in the worker environment to execute checks'))
        return result
    package = root / 'package.json'
    if package.is_file():
        scripts = json.loads(package.read_text()).get('scripts', {})
        for name in ('build', 'test'):
            if not scripts.get(name):
                result.checks.append(VerifyCheck(f'npm-{name}', 'fail', f'Missing {name} script'))
                continue
            try:
                proc = subprocess.run(['npm', 'run', name], cwd=root, stdin=subprocess.DEVNULL,
                                      capture_output=True, text=True, env={**os.environ, 'CI': 'true'},
                                      timeout=suite_timeout() if name == 'test' else build_timeout())
                result.checks.append(VerifyCheck(f'npm-{name}', 'pass' if proc.returncode == 0 else 'fail',
                                                (proc.stdout + proc.stderr)[-6000:]))
            except (OSError, subprocess.SubprocessError) as exc:
                result.checks.append(VerifyCheck(f'npm-{name}', 'fail', str(exc)))
    if not any('test' in c.name and c.status == 'pass' for c in result.checks):
        result.checks.append(VerifyCheck('executed-tests', 'fail', 'No passing executed test suite'))
    # warn and skip signal incomplete verification, never a verified combined candidate.
    if any(c.status != 'pass' for c in result.checks):
        result.checks.append(VerifyCheck('complete-evidence', 'fail', 'Some required checks are missing or failed'))
    return result
