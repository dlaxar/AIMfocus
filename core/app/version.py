"""
app/version.py – the checked-out commit and its tags for /healthz. Read from git on every
call, so tags added after the server started show up without a restart.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

_REPO_DIR = Path(__file__).resolve().parent.parent.parent


def _git(*args: str) -> str:
    """Output of a git command in the repo, '' when git or the repo is unavailable."""
    try:
        return subprocess.run(
            ["git", *args], cwd=_REPO_DIR, capture_output=True, text=True, timeout=2, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def collect() -> dict[str, Any]:
    """{hash, tags}: HEAD and the tags pointing at it (newest first); hash is None without git."""
    head = _git("rev-parse", "HEAD")
    tags = _git("tag", "--points-at", "HEAD", "--sort=-creatordate").splitlines() if head else []
    return {"hash": head or None, "tags": tags}
