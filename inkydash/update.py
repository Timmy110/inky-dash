"""`inky-dash update`: git fetch → checkout → pip install → validate → smoke render →
restart, rolling back (code and dependencies) on failure (DESIGN.md §7.4)."""

from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[1]


def _git(*args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def _pip_install() -> bool:
    target = f"{REPO}[inky]" if _has_inky() else str(REPO)
    return subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-e", target]).returncode == 0


def _has_inky() -> bool:
    try:
        import inky  # type: ignore[import-not-found]  # noqa: F401
        return True
    except ImportError:
        return False


def _check(config: str | None) -> bool:
    base = [sys.executable, "-m", "inkydash"]
    cfg = ["--config", config] if config else []
    if subprocess.run([*base, "validate", *cfg]).returncode:
        print("✗ the current config doesn't validate against the new version")
        return False
    with tempfile.TemporaryDirectory() as tmp:
        if subprocess.run([*base, "render", "--smoke", "--out", f"{tmp}/smoke.png", *cfg]).returncode:
            print("✗ smoke render failed")
            return False
    return True


def update(to: str | None, config: str | None, restart: bool = True) -> int:
    if not (REPO / ".git").exists():
        print(f"{REPO} is not a git checkout; update with pip instead")
        return 1
    if _git("status", "--porcelain", "--untracked-files=no"):
        print("✗ local changes in the checkout; commit or stash them first")
        return 1
    prev = _git("rev-parse", "HEAD")
    _git("fetch", "--tags", "--prune", "origin")
    if to in (None, "latest"):
        tags = [t for t in _git("tag", "--sort=-v:refname").splitlines() if t.startswith("v")]
        target = tags[0] if tags else "origin/main"
    elif to == "main":
        target = "origin/main"
    else:
        target = to
    new = _git("rev-parse", f"{target}^{{commit}}")
    if new == prev:
        print(f"already at {target} ({new[:8]})")
        return 0
    print(f"updating {prev[:8]} → {target} ({new[:8]})")
    _git("checkout", "--detach", new)
    ok = _pip_install() and _check(config)
    if not ok:
        print(f"rolling back to {prev[:8]}")
        _git("checkout", "--detach", prev)
        if not _pip_install():
            print("⚠ reinstalling the old dependencies failed; run pip install -e . by hand")
        return 1
    print("✓ new version validated")
    if restart:
        r = subprocess.run(["sudo", "-n", "systemctl", "restart", "inky-dash"])
        if r.returncode:
            print("⚠ couldn't restart the service; run: sudo systemctl restart inky-dash")
            return 1
        print("✓ service restarted")
    return 0
