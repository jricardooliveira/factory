"""Best-effort desktop notifications (macOS).

OPT-IN: disabled unless FACTORY_NOTIFY is set (e.g. FACTORY_NOTIFY=1). The
osascript route was observed launching Script Editor on some setups, and the
queue/board/timeline already provide visibility — so pinging is off by default
and the operator can re-enable it explicitly. No-op on non-macOS or without
osascript; never raises, never blocks the pipeline.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

# Notifications are short; sanitize hard to avoid AppleScript injection from
# agent/user-supplied text (story titles, gate reasons).
_SAFE = re.compile(r"[^\w \-.#:/]")


def _clean(text: str, maxlen: int = 180) -> str:
    return _SAFE.sub("", text)[:maxlen]


def enabled() -> bool:
    return os.environ.get("FACTORY_NOTIFY", "").strip().lower() in ("1", "true", "yes", "on")


def notify(title: str, message: str) -> bool:
    """Show a desktop notification when opted in. Returns True if dispatched."""
    if not enabled():
        return False
    if sys.platform != "darwin" or shutil.which("osascript") is None:
        return False
    script = f'display notification "{_clean(message)}" with title "{_clean(title)}"'
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
        return True
    except (subprocess.SubprocessError, OSError):
        return False
