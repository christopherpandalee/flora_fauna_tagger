"""
Ties together classify.py, metadata.py, and hashing.py into one pass over
the inbox folder. This is what both the "Process now" button and the
nightly scheduled task call -- same code path either way, so behavior
never drifts between manual and automatic runs.

Photographer names: captured at upload time by the GUI's upload tab into
a small `.manifest.json` file sitting in the inbox folder (filename ->
photographer). Photos placed into the inbox folder directly (bypassing
the app's upload tab) simply won't have a photographer name recorded,
and the name is left off the filename for those, consistent with it
being an optional field.

Photos are MOVED (not copied) from the inbox straight to their final
renamed location by default -- there is exactly one copy of each photo
at any time, never a duplicate original left behind. If settings
["keep_backup"] is True, the original is preserved untouched in
inbox/processed/<date>/<original_filename> first, and it's that
already-backed-up file which then gets renamed and moved -- so with
backups on, two copies exist (the untouched original, and the
processed/tagged version); with backups off (the default), only one
copy ever exists.

Routing:
  - A confidently-identified flora/fauna species with a usable date ->
    output_folder/YYYY/MM/renamed_file
  - Everything else -> a subfolder of output_folder/review/:
    - human photos -> output_folder/review/human/
    - scenery photos -> output_folder/review/scenery/
    - everything else needing review (flora/fauna below the confidence
      threshold, or any category with no usable date) ->
      output_folder/review/ directly, flat, no further subfolders
"""

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import classify, metadata
from .hashing import file_hash6, photo_date

logger = logging.getLogger("wildlifetagger.pipeline")

MANIFEST_NAME = ".manifest.json"

ProgressCallback = Optional[Callable[[int, int, str], None]]


@dataclass
class ProcessResult:
    original_path: Path
    output_path: Optional[Path]
    category: str
    species_name: str
    needs_review: bool
    ok: bool
    error: str = ""


def _read_manifest(inbox: Path) -> dict:
    manifest_path = inbox / MANIFEST_NAME
    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _write_manifest(inbox: Path, manifest: dict) -> None:
    (inbox / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def register_upload(inbox: Path, filenames, photographer: str) -> None:
    """Called by the GUI's upload tab right after copying files into the inbox."""
    manifest = _read_manifest(inbox)
    for name in filenames:
        manifest[name] = photographer
    _write_manifest(inbox, manifest)


def _sanitize(text: str) -> str:
    """Make a string filesystem- and filename-safe."""
    keep = "-_"
    cleaned = "".join(c if c.isalnum() or c in keep else "_" for c in text)
    return cleaned.strip("_") or ""


def _backup_original(image_path: Path, inbox: Path, date_str: str) -> None:
    """
    Preserve an untouched copy of the original file, under its original
    name, in inbox/processed/<date>/ -- before the (still-original-until-
    this-copy-exists) file continues on through renaming and moving.
    """
    backup_dir = inbox / "processed" / date_str
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(image_path, backup_dir / image_path.name)


def process_one(image_path: Path, photographer: str, settings: dict) -> ProcessResult:
    try:
        category, cat_conf = classify.classify_category(image_path, settings["clip_categories"])

        common_name = ""
        scientific_name = ""
        genus = ""
        family = ""
        generic_type = ""
        genus_common_name = ""
        tag_category = category

        if category in ("flora", "fauna"):
            result = classify.identify_species(
                image_path, settings["species_confidence_threshold"]
            )
            common_name = result.common_name or ""
            scientific_name = result.scientific_name or ""
            genus = result.genus or ""
            family = result.family or ""
            generic_type = result.generic_type or ""
            genus_common_name = result.genus_common_name or ""

        date_str = photo_date(image_path, settings["missing_date_token"])
        missing_date = date_str == settings["missing_date_token"]

        species_for_filename = _sanitize(common_name) or settings["missing_species_token"]
        unknown_species = species_for_filename == settings["missing_species_token"]

        # Route to review whenever the filename would show 'unknown' for
        # species -- whether that's because BioCLIP wasn't confident
        # enough (flora/fauna) or simply because the category has no
        # species concept at all (human/scenery) -- or if there's no
        # usable date. Either way, something about this photo needs a
        # person to take a look, or it just doesn't have full metadata
        # for the normal output structure.
        needs_review = unknown_species or missing_date

        hash6 = file_hash6(image_path)
        photographer_clean = _sanitize(photographer)

        parts = [date_str, species_for_filename]
        if photographer_clean:
            parts.append(photographer_clean)
        parts.append(hash6)

        new_name = "_".join(parts) + image_path.suffix.lower()

        if settings.get("keep_backup", False):
            _backup_original(image_path, Path(settings["inbox_folder"]), date_str)

        if needs_review:
            review_base = Path(settings["output_folder"]) / "review"
            if tag_category == "human":
                output_dir = review_base / "human"
            elif tag_category == "scenery":
                output_dir = review_base / "scenery"
            else:
                output_dir = review_base
        else:
            year, month = date_str[:4], date_str[4:6]
            output_dir = Path(settings["output_folder"]) / year / month
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / new_name

        # Move -- if a backup was just made above, this moves that
        # redundant copy on to its final home; if backups are off, this
        # is the one and only copy of the photo from here on.
        shutil.move(str(image_path), str(output_path))

        tags = metadata.build_tags(
            category=tag_category,
            common_name=common_name,
            scientific_name=scientific_name,
            genus=genus,
            generic_type=generic_type,
            genus_common_name=genus_common_name,
            photographer=photographer,
            date_str=date_str,
            needs_review=needs_review,
        )
        tags_ok = metadata.write_tags(output_path, tags)
        if not tags_ok:
            logger.warning(
                "Could not write tags to %s -- exiftool.exe may be missing; "
                "the photo itself was still renamed and moved successfully.",
                output_path,
            )

        return ProcessResult(
            original_path=image_path,
            output_path=output_path,
            category=tag_category,
            species_name=common_name or settings["missing_species_token"],
            needs_review=needs_review,
            ok=True,
        )
    except Exception as exc:
        logger.exception("Failed to process %s", image_path)
        return ProcessResult(
            original_path=image_path,
            output_path=None,
            category="",
            species_name="",
            needs_review=False,
            ok=False,
            error=str(exc),
        )


def process_inbox(settings: dict, progress_callback: ProgressCallback = None) -> list:
    """
    Process every eligible photo currently sitting in the inbox.
    Returns a list of ProcessResult.

    `progress_callback`, if given, is called as
    `progress_callback(current_index_1_based, total_count, filename)`
    right before each photo is processed, so a caller (the GUI) can show
    something like "Processing photo 5/10 (fox1.jpg)".
    """
    inbox = Path(settings["inbox_folder"])
    output = Path(settings["output_folder"])
    output.mkdir(parents=True, exist_ok=True)
    (output / "review").mkdir(parents=True, exist_ok=True)
    (output / "review" / "human").mkdir(parents=True, exist_ok=True)
    (output / "review" / "scenery").mkdir(parents=True, exist_ok=True)

    manifest = _read_manifest(inbox)
    allowed = {ext.lower() for ext in settings["allowed_extensions"]}

    candidates = [
        p
        for p in inbox.iterdir()
        if p.is_file() and p.suffix.lower() in allowed
    ]
    total = len(candidates)

    results = []
    for index, image_path in enumerate(candidates, start=1):
        if progress_callback:
            progress_callback(index, total, image_path.name)

        photographer = manifest.get(image_path.name, "")
        result = process_one(image_path, photographer, settings)
        results.append(result)

        if result.ok:
            manifest.pop(image_path.name, None)

    _write_manifest(inbox, manifest)
    return results
