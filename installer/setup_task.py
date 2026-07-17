"""
Run this ONCE, after building the exe with PyInstaller, as the account
that will actually use the computer day to day (not a separate admin
account) -- schtasks needs to register the task "as" that user.

    python installer\\setup_task.py "C:\\path\\to\\WildlifeTagger\\WildlifeTagger.exe"

What this does:
  1. Registers the nightly Windows Scheduled Task at the default time
     (23:00) -- changeable later from the tray icon's Settings window.
  2. Creates a Startup shortcut so the tray icon appears automatically
     every time the computer is logged into, so people always have the
     "Upload photos" / "Process now" menu available.

You'll be prompted for admin rights (a Windows popup) when this runs --
that's required once, to register the scheduled task. After this, no
further admin rights are needed for day-to-day use.
"""

import sys
import winshell  # pip install winshell pywin32
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import scheduler, config


def main():
    if len(sys.argv) < 2:
        print("Usage: python setup_task.py <path to WildlifeTagger.exe>")
        sys.exit(1)

    exe_path = str(Path(sys.argv[1]).resolve())
    settings = config.load_settings()

    ok, msg = scheduler.register_nightly_task(exe_path, settings["nightly_run_time"])
    print(f"Scheduled task: {msg}" if ok else f"Scheduled task FAILED: {msg}")

    startup_dir = Path(winshell.startup())
    shortcut_path = startup_dir / "Wildlife Tagger.lnk"
    with winshell.shortcut(str(shortcut_path)) as link:
        link.path = exe_path
        link.description = "Wildlife Tagger tray app"
        link.working_directory = str(Path(exe_path).parent)
    print(f"Startup shortcut created: {shortcut_path}")

    print("\nSetup complete. Launch WildlifeTagger.exe now to see the tray icon.")


if __name__ == "__main__":
    main()
