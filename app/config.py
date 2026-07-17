"""
Settings for the Wildlife Photo Tagger app.

All user-editable settings live in a single JSON file so the settings
window and the background/scheduled run both read the same source of
truth. On Windows this file lives under %APPDATA%\\WildlifeTagger\\settings.json
so it survives reinstalls and doesn't need admin rights to edit.
"""

import json
import os
from pathlib import Path

APP_NAME = "WildlifeTagger"

DEFAULT_SETTINGS = {
    # Folder people drop/upload photos into.
    "inbox_folder": str(Path.home() / "Pictures" / "WildlifeTagger" / "inbox"),
    # Folder where renamed copies land. Fixed, set once in settings.
    # Confidently-identified flora/fauna go to output_folder/YYYY/MM/photo.
    # Everything else (see review folder note below) goes to
    # output_folder/review/, with human and scenery photos further split
    # into output_folder/review/human/ and output_folder/review/scenery/.
    "output_folder": str(Path.home() / "Pictures" / "WildlifeTagger" / "output"),
    # Time the automatic nightly run fires, 24h "HH:MM".
    "nightly_run_time": "23:00",
    # BioCLIP species-level confidence needed to trust the name outright.
    "species_confidence_threshold": 0.20,
    # Broad categories CLIP sorts every photo into. Edit/extend this list
    # later to add specific scenery classes (e.g. "a photo of a forest").
    "clip_categories": {
        "flora": "a photo of a plant",
        "fauna": "a photo of an animal",
        "human": "a photo of a person",
        "scenery": "a photo of a landscape or scenery",
    },
    # Fallback strings used in filenames when data is missing.
    "missing_date_token": "nodate",
    "missing_species_token": "unknown",
    # If True, the original uploaded file is preserved untouched in
    # inbox/processed/<date>/ before a copy of it goes through
    # classification, renaming, and moving to output/review. If False
    # (the default), the original file itself is what gets renamed and
    # moved -- no duplicate is kept anywhere.
    "keep_backup": False,
    # Image file extensions the pipeline will pick up from the inbox.
    "allowed_extensions": [".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic"],
}


def _settings_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".wildlifetagger"
    return base / APP_NAME


def settings_path() -> Path:
    return _settings_dir() / "settings.json"


def load_settings() -> dict:
    """Load settings, creating the file with defaults on first run."""
    path = settings_path()
    if not path.exists():
        save_settings(DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS)

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Backfill any keys added in newer versions of the app without
    # clobbering values the person already customized.
    merged = dict(DEFAULT_SETTINGS)
    merged.update(data)
    return merged


def save_settings(settings: dict) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


def ensure_folders(settings: dict) -> None:
    """Make sure inbox/output/review folders exist so the pipeline never fails on a missing dir."""
    Path(settings["inbox_folder"]).mkdir(parents=True, exist_ok=True)
    output = Path(settings["output_folder"])
    output.mkdir(parents=True, exist_ok=True)
    (output / "review").mkdir(parents=True, exist_ok=True)
    (output / "review" / "human").mkdir(parents=True, exist_ok=True)
    (output / "review" / "scenery").mkdir(parents=True, exist_ok=True)
