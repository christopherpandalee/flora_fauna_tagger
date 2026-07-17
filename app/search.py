"""
Tag-based photo search. Reads every photo's IPTC/XMP tags in a chosen
folder (one exiftool call for the whole folder, not one per file) and
matches them against whatever the person typed in the search box.
"""

from pathlib import Path
from typing import List

from . import metadata

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic"}


def parse_query(raw_query: str) -> List[str]:
    """Split a search box string into individual lowercase terms."""
    return [term.strip().lower() for term in raw_query.split() if term.strip()]


def search_folder(folder: Path, raw_query: str, match_all: bool = False) -> List[Path]:
    """
    Return image files in `folder` (including subfolders) whose tags
    match the search terms.

    match_all=False (the default): a photo matches if ANY search term is
    found in ANY of its tags (substring match, e.g. "fox" matches the tag
    "Red Fox"). match_all=True requires every search term to be found in
    at least one of the photo's tags.

    An empty query returns every image file in the folder, tagged or not.
    """
    terms = parse_query(raw_query)
    tags_by_path = metadata.read_tags_for_folder(folder)

    if not terms:
        return sorted(
            p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS
        )

    matches = []
    for path_str, tags in tags_by_path.items():
        path = Path(path_str)
        if path.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue

        lowered_tags = [t.lower() for t in tags]
        term_hits = [
            any(term in tag for tag in lowered_tags) for term in terms
        ]

        if (all(term_hits) if match_all else any(term_hits)):
            matches.append(path)

    return sorted(matches)
