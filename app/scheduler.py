"""
Registers, updates, and removes the Windows Scheduled Task that fires the
pipeline every night. Uses schtasks.exe (built into Windows, no extra
dependency) rather than pywin32's Task Scheduler COM API, since schtasks
is simpler to shell out to and doesn't require admin-only COM registration.

The task runs regardless of whether the tray app or anyone is logged in
(as long as the machine is on), by running as the current user with
"run whether user is logged on or not" -- which does require the account
to have a password and admin to register the first time. See installer
README for the one-time setup step.
"""

import subprocess
import sys

TASK_NAME = "WildlifeTagger_NightlyRun"

# Same reasoning as metadata.py's _NO_WINDOW -- this is a windowed app,
# so any subprocess it spawns (schtasks.exe here) would otherwise flash
# a console window on Windows.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _run(args) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, shell=False, creationflags=_NO_WINDOW
    )


def register_nightly_task(exe_path: str, run_time: str) -> tuple:
    """
    Create or replace the nightly scheduled task.

    `exe_path` is the full path to the packaged app executable; it will be
    called as `<exe_path> --run-once` at `run_time` ("HH:MM", 24h) every day.
    Returns (success, message).
    """
    remove_nightly_task()  # clear any existing registration first

    cmd = [
        "schtasks", "/Create",
        "/TN", TASK_NAME,
        "/TR", f'"{exe_path}" --run-once',
        "/SC", "DAILY",
        "/ST", run_time,
        "/RL", "LIMITED",
        "/F",  # overwrite without prompting
    ]
    result = _run(cmd)
    if result.returncode != 0:
        return False, result.stderr.strip() or "Failed to create scheduled task."
    return True, f"Nightly run scheduled for {run_time}."


def remove_nightly_task() -> tuple:
    cmd = ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]
    result = _run(cmd)
    # A "not found" failure here is fine -- it just means nothing was scheduled yet.
    return result.returncode == 0, result.stderr.strip()


def current_task_time() -> str:
    """Best-effort read of the currently scheduled time, for display in settings."""
    result = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"])
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        if line.strip().lower().startswith("start time"):
            return line.split(":", 1)[1].strip()
    return ""
