"""
Small, focused helpers: content hashing and EXIF date extraction.

Kept separate from the classification/pipeline code because they're pure,
easy-to-test functions with no model dependencies.
"""

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image
from PIL.ExifTags import TAGS


def file_hash6(path: Path) -> str:
    """
    6-character hash derived from the file's actual bytes.

    EXIF metadata doesn't reliably contain a ready-made unique ID, so we
    hash the file content itself -- this guarantees no collisions between
    different photos and is stable (same file always gets the same hash).
    """
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()[:6]


def photo_date(path: Path, missing_token: str = "nodate") -> str:
    """
    Return YYYYMMDD pulled from EXIF DateTimeOriginal, falling back to the
    file's modified time, falling back to `missing_token`.
    """
    exif_date = _exif_date(path)
    if exif_date:
        return exif_date

    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        return mtime.strftime("%Y%m%d")
    except OSError:
        return missing_token


def _exif_date(path: Path) -> Optional[str]:
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            tag_map = {TAGS.get(k, k): v for k, v in exif.items()}
            raw = tag_map.get("DateTimeOriginal") or tag_map.get("DateTime")
            if not raw:
                return None
            # EXIF dates look like "2024:06:15 08:30:00"
            dt = datetime.strptime(raw.split(" ")[0], "%Y:%m:%d")
            return dt.strftime("%Y%m%d")
    except Exception:
        return None
