"""Best-effort desktop notifications (macOS osascript, Linux notify-send).

OPT-IN: disabled unless FACTORY_NOTIFY is set (e.g. FACTORY_NOTIFY=1). The
osascript route was observed launching Script Editor on some setups, and the
queue/board/timeline already provide visibility — so pinging is off by default
and the operator can re-enable it explicitly. No-op without osascript (macOS) or
notify-send (Linux); never raises, never blocks the pipeline.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys

from factory.agent_config.settings import settings

# Notifications are short; sanitize hard to avoid AppleScript injection from
# agent/user-supplied text (story titles, gate reasons).
_SAFE = re.compile(r"[^\w \-.#:/]")


def _clean(text: str, maxlen: int = 180) -> str:
    return _SAFE.sub("", text)[:maxlen]


def enabled() -> bool:
    return settings().features.notify


def notify(title: str, message: str) -> bool:
    """Show a desktop notification when opted in. Returns True if dispatched."""
    if not enabled():
        return False
    if sys.platform.startswith("linux") and shutil.which("notify-send"):
        # An argv, not a script: nothing to inject into, but keep it short and clean.
        cmd = ["notify-send", _clean(title), _clean(message)]
        try:
            subprocess.run(cmd, capture_output=True, timeout=10, stdin=subprocess.DEVNULL)
            return True
        except (subprocess.SubprocessError, OSError):
            return False
    if sys.platform != "darwin" or shutil.which("osascript") is None:
        return False
    script = f'display notification "{_clean(message)}" with title "{_clean(title)}"'
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
        return True
    except (subprocess.SubprocessError, OSError):
        return False
