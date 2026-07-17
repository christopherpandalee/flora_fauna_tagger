"""
Writes and reads searchable tags on photo files.

Windows Explorer's "Tags" column and its search index both read from the
IPTC Keywords / XMP dc:subject fields, not plain EXIF -- so we shell out to
exiftool (https://exiftool.org), which reads/writes both consistently
across JPEG/TIFF/HEIC. exiftool.exe must ship alongside the app (see
README for where to drop it); this module looks for it on PATH, next to
the running .exe, or (for running from source) next to this file.

If exiftool isn't found, tagging/reading is skipped -- the rename/move
still succeeds, since tagging is an enhancement, not a blocker -- but
pipeline.py logs a warning so this doesn't fail silently.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

# Suppresses the console window Windows would otherwise flash every time
# this windowed (no-console) app spawns a console subprocess like
# exiftool.exe. subprocess.CREATE_NO_WINDOW only exists on Windows, so
# this falls back to 0 (no special flags) on other platforms.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _exiftool_path() -> str:
    found = shutil.which("exiftool")
    if found:
        return found

    candidates = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        # Primary: right next to WildlifeTagger.exe. This is deliberately
        # NOT inside _internal/ -- PyInstaller's onedir builds moved
        # everything except the main exe into an _internal/ folder as of
        # PyInstaller 6, and that internal layout isn't something this
        # app should depend on staying the same across versions.
        candidates.append(exe_dir / "exiftool.exe")
        # Back-compat: older setup instructions said to place it under
        # _internal/app/bin/ -- still checked so existing installs keep
        # working without having to move the file.
        candidates.append(exe_dir / "_internal" / "app" / "bin" / "exiftool.exe")
        candidates.append(exe_dir / "app" / "bin" / "exiftool.exe")
    else:
        candidates.append(Path(__file__).parent / "bin" / "exiftool.exe")

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return ""


def build_tags(
    category: str,
    common_name: str,
    scientific_name: str,
    genus: str,
    generic_type: str,
    genus_common_name: str,
    photographer: str,
    date_str: str,
    needs_review: bool,
) -> List[str]:
    """
    Assemble the automatic tag list for a photo. Includes the species
    names, genus, a plain-language generic type (e.g. "bird"), and
    category, plus a few extras chosen specifically to make the photo
    turn up in a plain Windows Explorer search:
      - year (e.g. "2026") since people often search by year
      - "wildlife photo" as a generic catch-all
      - photographer's name, so "find everything Jordan shot" works
      - "needs review" for photos routed to the review folder, so they're
        still findable by search even though nothing else got automated

    More tags can always be added to an individual photo afterward from
    the app's Search tab.
    """
    tags = []

    if common_name:
        tags.append(common_name)
    if scientific_name and scientific_name != common_name:
        tags.append(scientific_name)
    if genus:
        tags.append(genus)
    if genus_common_name and genus_common_name != common_name:
        tags.append(genus_common_name)
    if generic_type:
        tags.append(generic_type)
    if category:
        tags.append(category)

    tags.append("wildlife photo")

    if needs_review:
        tags.append("needs review")

    if photographer:
        tags.append(photographer)

    if date_str and date_str != "nodate" and len(date_str) >= 4:
        tags.append(date_str[:4])  # year

    return _dedupe(tags)


def _dedupe(tags: List[str]) -> List[str]:
    seen = set()
    unique_tags = []
    for t in tags:
        key = t.strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique_tags.append(t.strip())
    return unique_tags


def write_tags(image_path: Path, tags: List[str]) -> bool:
    """
    Set `tags` as the complete IPTC Keywords / XMP Subject list on the
    file (replacing whatever was there, not appending -- callers that
    want to add to existing tags should use append_tags instead, which
    reads first and merges).
    """
    exiftool = _exiftool_path()
    if not exiftool or not tags:
        return False

    cmd = [exiftool, "-overwrite_original", "-IPTC:Keywords=", "-XMP-dc:Subject="]
    for tag in tags:
        cmd.append(f"-IPTC:Keywords+={tag}")
        cmd.append(f"-XMP-dc:Subject+={tag}")
    cmd.append(str(image_path))

    result = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW)
    return result.returncode == 0


def read_tags(image_path: Path) -> List[str]:
    """Read the current IPTC Keywords / XMP Subject tags off a single file."""
    exiftool = _exiftool_path()
    if not exiftool:
        return []

    cmd = [exiftool, "-j", "-Keywords", "-Subject", str(image_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW)
    if result.returncode != 0 or not result.stdout.strip():
        return []

    try:
        data = json.loads(result.stdout)[0]
    except (json.JSONDecodeError, IndexError):
        return []

    return _dedupe(_flatten_tag_fields(data))


def _flatten_tag_fields(data: dict) -> List[str]:
    tags = []
    for field in ("Keywords", "Subject"):
        value = data.get(field)
        if isinstance(value, list):
            tags.extend(str(v) for v in value)
        elif value is not None:
            tags.append(str(value))
    return tags


def append_tags(image_path: Path, new_tags: List[str]) -> bool:
    """Add `new_tags` to whatever tags a file already has, without duplicating."""
    existing = read_tags(image_path)
    merged = _dedupe(existing + list(new_tags))
    return write_tags(image_path, merged)


def read_tags_for_folder(folder: Path) -> Dict[str, List[str]]:
    """
    Read tags for every image in `folder` (and subfolders) in a single
    exiftool call -- much faster than shelling out once per file when
    searching a folder that might have hundreds of photos.

    Returns {absolute_file_path_str: [tags]}.
    """
    exiftool = _exiftool_path()
    if not exiftool or not folder.exists():
        return {}

    cmd = [exiftool, "-j", "-r", "-Keywords", "-Subject", "-FileName", "-Directory", str(folder)]
    result = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW)
    if result.returncode != 0 or not result.stdout.strip():
        return {}

    try:
        entries = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}

    tags_by_path = {}
    for entry in entries:
        directory = entry.get("Directory", "")
        filename = entry.get("FileName", "")
        if not filename:
            continue
        full_path = str(Path(directory) / filename)
        tags_by_path[full_path] = _dedupe(_flatten_tag_fields(entry))
    return tags_by_path

