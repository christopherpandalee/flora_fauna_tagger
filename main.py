"""
Entry point.

Two modes:
  - No arguments: opens the main window (what a person double-clicks, or
    what a Startup shortcut launches at login).
  - `--run-once`: does a single headless processing pass and exits. This
    is what the Windows Scheduled Task calls at the nightly run time --
    no window, just process and quit.

Both modes log to %USERPROFILE%\\WildlifeTagger.log so issues from an
unattended nightly run are still visible the next morning.

IMPORTANT -- this must run before any other imports:
PyInstaller builds this as a *windowed* app (no console), which means
Windows gives the process no stdout/stderr at all -- sys.stdout and
sys.stderr are literally None, not just redirected. Several libraries in
the dependency chain (tqdm's download progress bar inside huggingface_hub,
used when open_clip/BioCLIP fetch model weights) assume they can always
write to stdout, and crash with a confusing "AttributeError: 'NoneType'
object has no attribute 'write'" the first time they try. That crash then
gets reported by the calling code as if the download itself failed. The
fix is to give sys.stdout/stderr a harmless place to write before
anything else gets a chance to import and grab a reference to them.
"""

import os
import sys

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# Extra safety net: tell huggingface_hub not to render progress bars at
# all, so it never touches tqdm/stdout in the first place.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

import argparse
import logging
from pathlib import Path

from app import config, pipeline
from app.gui import LOG_FILE


def _setup_logging():
    logging.basicConfig(
        filename=str(LOG_FILE),
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def main():
    parser = argparse.ArgumentParser(description="Wildlife Tagger")
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Process the inbox once and exit (used by the nightly scheduled task).",
    )
    args = parser.parse_args()

    _setup_logging()
    logger = logging.getLogger("wildlifetagger.main")

    settings = config.load_settings()
    config.ensure_folders(settings)

    if args.run_once:
        logger.info("Starting scheduled nightly run.")
        results = pipeline.process_inbox(settings)
        ok = sum(1 for r in results if r.ok)
        failed = sum(1 for r in results if not r.ok)
        logger.info("Nightly run complete: %d processed, %d failed.", ok, failed)
        return

    from app.gui import run_app

    exe_path = sys.executable if getattr(sys, "frozen", False) else str(Path(__file__).resolve())
    logger.info("Starting main window.")
    run_app(exe_path)


if __name__ == "__main__":
    main()
