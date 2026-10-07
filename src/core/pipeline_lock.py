"""
Single-flight lock for run_task.sh / run_quick.sh / run_linkedin.sh.

A second pipeline must not launch another debug Chrome with the same
--user-data-dir: that kills the first Chrome and every in-flight scraper
tab (Playwright TargetClosedError).
"""
from __future__ import annotations

import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LOGS_DIR = _PROJECT_ROOT / "logs"
PIDFILE = _LOGS_DIR / "pipeline.pid"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def pipeline_holder_pid() -> int | None:
    """Return the live pipeline PID, or None if no run is active."""
    try:
        raw = PIDFILE.read_text(encoding="utf-8").strip().split()[0]
        pid = int(raw)
    except (OSError, ValueError, IndexError):
        return None
    if _pid_alive(pid):
        return pid
    return None


def pipeline_is_running() -> bool:
    return pipeline_holder_pid() is not None


def acquire_pipeline_lock(pid: int | None = None) -> tuple[bool, str]:
    """
    Exclusive pidfile lock.

    Pass pid= the long-lived owner (the shell). Default is this process.
    Stale files (dead PID) are removed. Returns (ok, message).
    """
    owner = int(pid) if pid is not None else os.getpid()
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    holder = pipeline_holder_pid()
    if holder is not None:
        return False, f"Pipeline already running (PID {holder})"

    try:
        PIDFILE.unlink(missing_ok=True)
    except OSError as exc:
        return False, f"Could not clear stale pipeline lock: {exc}"

    try:
        fd = os.open(str(PIDFILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"{owner}\n")
    except FileExistsError:
        holder = pipeline_holder_pid()
        if holder is not None:
            return False, f"Pipeline already running (PID {holder})"
        return False, "Pipeline lock exists; try again in a moment"
    except OSError as exc:
        return False, f"Could not acquire pipeline lock: {exc}"

    return True, f"Pipeline lock acquired (PID {owner})"


def release_pipeline_lock(pid: int | None = None) -> None:
    """Release only if pid owns the pidfile (default: this process)."""
    owner = int(pid) if pid is not None else os.getpid()
    try:
        raw = PIDFILE.read_text(encoding="utf-8").strip().split()[0]
        file_pid = int(raw)
    except (OSError, ValueError, IndexError):
        return
    if file_pid != owner:
        return
    try:
        PIDFILE.unlink(missing_ok=True)
    except OSError:
        pass
