# Wildlife Tagger

Classifies photos (flora / fauna / human / scenery), identifies species on
flora and fauna shots, renames the file with a consistent naming pattern,
tags it so it's searchable in Windows Explorer, and files it into a
year/month folder structure -- either on demand or automatically every
night. Nothing is duplicated by default: each photo is renamed and moved directly
from the inbox to its final location, so there's exactly one copy at
all times -- unless the backup option is turned on (see Settings below),
in which case the original is preserved too.

## What each person sees day to day

Double-clicking the app opens a window with a "Process now" button always
visible at the top, and four tabs:

- **Upload** -- pick photos, optionally type your name, click Upload.
  Name is optional; if left blank, it's simply left out of the filename.
  Photos are processed tonight, or immediately if you click "Process now"
  at the top.
- **Search** -- find photos by tag. Defaults to searching the output
  folder, but you can point it at any folder (including the review
  folder). Type one or more words and it matches photos with ANY of those
  words in their tags (e.g. searching `fox winter` finds photos tagged
  with either). Select a result to open it, or to add more tags to it
  directly -- useful for adding a personal note or extra tag to a photo
  that's already been processed.
- **Settings** -- change the inbox/output/review folders, the nightly
  run time, the species confidence threshold, and whether to keep a
  backup copy of original uploads (off by default -- see below).
- **Log** -- see what happened on recent runs (useful if a photo didn't
  turn up where expected).

While processing, the top status line shows a running count (e.g.
"Processing... (5/10)"), and a line at the very bottom of the window
(visible no matter which tab is open) shows the specific photo currently
being worked on. The Upload tab additionally shows a live thumbnail
preview of that photo in the space below its controls.

Closing the window (the X button) quits the app. That's fine -- the
nightly automatic run doesn't depend on this window or the app being
open; it's a separate Windows Scheduled Task that runs the exe directly
at the scheduled time regardless. A small tray icon also appears while
the app is open, as a shortcut to "Process now" without switching
windows; its "Quit" option closes the app the same way the window's X
button does.

Photos can also be dropped directly into the inbox folder (Settings shows
where that is) -- those get processed too, just without a photographer
name attached, since there's no prompt for that outside the Upload tab.

## Tags

Every processed photo gets tags written into its IPTC Keywords / XMP
Subject fields (the same fields Windows Explorer's "Tags" column and
search index use), so they're searchable both from this app's Search tab
and from plain Windows Explorer search. Automatic tags include:

- Common name and scientific name (when confidently identified)
- Genus (e.g. "Vulpes"), whenever BioCLIP names one -- even for photos
  that land in the review folder
- A plain-language generic type where it applies (e.g. "bird", "mammal",
  "fish", "insect") based on the taxonomic class BioCLIP identifies --
  fauna only, since there isn't an equally reliable one-word mapping for
  plant classes
- The broad category (flora/fauna/human/scenery)
- "needs review" for anything routed to the review folder
- The year and the photographer's name

There's no batch-wide custom tag field anymore -- an earlier version had
one on the Upload tab, but since it applied the same tags to an entire
batch rather than per photo, it was removed. Individual tags can still be
added to any specific photo afterward from the Search tab.

## Keeping a backup of originals

Off by default. When turned on in Settings, the original uploaded file
is preserved untouched (original filename, original bytes) in
`inbox/processed/<date>/` before a copy of it goes through
classification, tagging, and renaming. With this on, two copies of each
photo exist afterward: the untouched original, and the fully processed
one in `output/` or `review/`. With it off, only the processed copy
exists -- the original is consumed by the move.

## The review folder

A photo goes to the flat `review` folder (no year/month subfolders,
so everything needing a look is in one place) instead of the normal
output structure whenever its filename would show `unknown` for species,
or `nodate` for date -- specifically:

- BioCLIP's species-level confidence was below the threshold (flora/fauna
  only) -- genus/generic type/category tags are still applied if available
- The photo is human or scenery -- these categories have no species to
  identify in the first place, so they always show `unknown` and always
  land in review rather than the year/month output structure
- The photo has no usable date at all (missing EXIF and no reliable file
  date), regardless of category

In short: only confidently-identified flora/fauna photos with a usable
date end up in the year/month output structure. Everything else goes to
review.

## Filename format

    YYYYMMDD_species_photographer_hash6.ext

- Date comes from the photo's EXIF data, or the file's date if that's
  missing, or `nodate` if neither is available.
- Species is the common name BioCLIP identified (only when confident --
  see "The review folder" above), or `unknown`.
- Photographer is left off if no name was recorded for that photo.
- The 6-character hash is generated from the photo's own data, purely to
  keep every filename unique.

## Output folder structure

    output_folder/
      2026/
        06/
          20260615_Red_Fox_Jordan_a1b2c3.jpg
        07/
          20260704_White_Oak_Jordan_9f8e7d.jpg
    review_folder/
      nodate_unknown_Jordan_3941d7.jpg
      20260704_unknown_Jordan_e8db10.jpg

## One-time setup (for whoever is installing this)


### 1. Install Python dependencies and build the exe

```
cd wildlife_tagger
pip install -r requirements.txt
pip install pyinstaller winshell pywin32
pyinstaller installer/wildlifetagger.spec
```

This produces `dist/WildlifeTagger/WildlifeTagger.exe`.

### 2. Add exiftool (for the searchable tags)

Download the Windows executable from https://exiftool.org, rename it
`exiftool.exe`, and place it directly in `dist/WildlifeTagger/` -- the
same folder as `WildlifeTagger.exe` itself, not inside `_internal/` or
any subfolder. If this step is skipped, everything else still works --
photos just won't get the Explorer-searchable tags, and the Log tab will
show a warning saying so after each run.

### 3. Register the nightly task and Startup shortcut

Run once, as the account that will actually use the machine day to day:

```
python installer/setup_task.py "C:\full\path\to\dist\WildlifeTagger\WildlifeTagger.exe"
```

You'll see a Windows admin prompt -- that's expected, and only needed this
one time to register the scheduled task.

### 4. Launch it

Double-click `WildlifeTagger.exe`, or just restart the computer -- the
Startup shortcut means it'll launch automatically from now on.

## Design notes / things to know

- **No GPU needed.** CLIP and BioCLIP both run fine on CPU; a batch of
  photos will just take a little while longer than it would on a GPU
  machine. The nightly schedule is meant to absorb that.
- **Species fallback uses GBIF, not iNaturalist's image-ID API.**
  iNaturalist's computer-vision endpoint requires a registered, approved
  OAuth application -- GBIF's species API is public and keyless. When
  BioCLIP's species-level confidence is below the threshold, the photo
  goes to the review folder rather than trusting an uncertain name, but
  we still ask GBIF for a common name for whatever genus BioCLIP *is*
  confident about, purely so the review-folder photo still gets a useful
  tag or two rather than none at all. If you'd rather pursue iNaturalist
  access later, `app/classify.py` is where that would plug in.
- **Adding scenery sub-classes later:** edit `clip_categories` in
  settings (`%APPDATA%\WildlifeTagger\settings.json`) -- add new
  category names mapped to short text prompts like
  `"forest": "a photo of a forest"`. No code changes needed for that part.
- **Confidence threshold** defaults to 20% and is editable from the
  Settings window without re-installing anything.
- **Three PyInstaller gotchas already fixed in this codebase, in case you
  ever rebuild from scratch or hit similar errors with a new dependency:**
  1. *"FileNotFoundError: ...bpe_simple_vocab_16e6.txt.gz"* -- `open_clip`
     ships a tokenizer vocab file as package data, not code, so
     PyInstaller doesn't grab it automatically. Fixed via
     `collect_data_files('open_clip')` in `installer/wildlifetagger.spec`.
  2. *"AttributeError: 'NoneType' object has no attribute 'write'"*
     during a model download -- because the exe is windowed
     (`console=False`), Windows gives it no stdout/stderr at all (they're
     `None`, not just hidden), and `tqdm`'s download progress bar crashes
     trying to write to it. Fixed at the very top of `main.py`, before any
     other imports, by giving `sys.stdout`/`sys.stderr` a real (if silent)
     file to write to.
  3. *Tags silently never get written* -- PyInstaller 6+ moved everything
     except the main exe into an `_internal/` folder for onedir builds,
     so a bundled `exiftool.exe` placed using an older module-relative
     path silently stops being found (no error, tags just don't appear).
     Fixed by looking for `exiftool.exe` next to the actual running exe
     (`sys.executable`'s folder) first, which isn't affected by
     PyInstaller's internal layout choices. `pipeline.py` also now logs a
     warning to the Log tab whenever tag-writing fails, so this kind of
     issue is visible instead of silent.

