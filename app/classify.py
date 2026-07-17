"""
Classification pipeline: broad category (CLIP) -> species (BioCLIP), plus
genus and a friendly "generic type" (e.g. "bird", "mammal") pulled from
BioCLIP's taxonomy for tagging purposes.

Model loading is intentionally lazy (done once, on first use) because
importing torch/open_clip/bioclip is slow and this module gets imported
by the lightweight GUI too, which shouldn't pay that cost just to show a
window.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

GBIF_MATCH_URL = "https://api.gbif.org/v1/species/match"
GBIF_VERNACULAR_URL = "https://api.gbif.org/v1/species/{key}/vernacularNames"

# Maps BioCLIP's taxonomic "class" rank to a plain-language word for
# tagging (e.g. so "fish" or "bird" shows up as a searchable tag even
# without a confident species-level ID). Only fauna classes are listed
# here on purpose -- there's no similarly reliable one-word mapping from
# plant taxonomic classes to something like "tree" or "flower", so flora
# photos simply won't get a generic_type tag.
GENERIC_ANIMAL_TYPES = {
    "aves": "bird",
    "mammalia": "mammal",
    "reptilia": "reptile",
    "amphibia": "amphibian",
    "actinopterygii": "fish",
    "chondrichthyes": "fish",
    "insecta": "insect",
    "arachnida": "arachnid",
    "gastropoda": "mollusk",
    "bivalvia": "mollusk",
    "malacostraca": "crustacean",
}

_clip_model = None
_clip_preprocess = None
_clip_tokenizer = None
_bioclip_classifier = None


@dataclass
class ClassificationResult:
    common_name: Optional[str] = None
    scientific_name: Optional[str] = None  # species binomial, only set when confident
    species_confidence: float = 0.0
    below_threshold: bool = False  # True -> caller should route this photo to review
    genus: Optional[str] = None
    family: Optional[str] = None
    class_name: Optional[str] = None
    generic_type: Optional[str] = None  # e.g. "bird", "mammal"
    genus_common_name: Optional[str] = None  # only populated when below_threshold
    source: str = ""  # "bioclip" | "gbif_fallback" | ""
    rank_used: str = ""  # "species" if confident, "genus" if only that much is usable


def _load_clip():
    global _clip_model, _clip_preprocess, _clip_tokenizer
    if _clip_model is None:
        import open_clip

        _clip_model, _, _clip_preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="laion2b_s34b_b79k"
        )
        _clip_tokenizer = open_clip.get_tokenizer("ViT-B-32")
        _clip_model.eval()
    return _clip_model, _clip_preprocess, _clip_tokenizer


def _load_bioclip():
    global _bioclip_classifier
    if _bioclip_classifier is None:
        from bioclip import TreeOfLifeClassifier

        _bioclip_classifier = TreeOfLifeClassifier()
    return _bioclip_classifier


def _rank():
    """Imported lazily alongside bioclip itself, for the same reason _load_bioclip is lazy."""
    from bioclip import Rank

    return Rank


def _generic_type_for_class(class_name: Optional[str]) -> Optional[str]:
    if not class_name:
        return None
    return GENERIC_ANIMAL_TYPES.get(class_name.strip().lower())


def classify_category(image_path: Path, categories: dict) -> tuple:
    """
    Zero-shot classify the image into one of the broad categories using CLIP.

    `categories` maps category-name -> text prompt, e.g.
    {"flora": "a photo of a plant", ...}. Extend this dict in settings to
    add new scenery sub-classes later without touching this function.

    Returns (category_name, confidence_0_to_1).
    """
    import torch
    from PIL import Image

    model, preprocess, tokenizer = _load_clip()

    names = list(categories.keys())
    prompts = list(categories.values())

    image = preprocess(Image.open(image_path).convert("RGB")).unsqueeze(0)
    text = tokenizer(prompts)

    with torch.no_grad():
        image_features = model.encode_image(image)
        text_features = model.encode_text(text)
        image_features /= image_features.norm(dim=-1, keepdim=True)
        text_features /= text_features.norm(dim=-1, keepdim=True)
        similarity = (100.0 * image_features @ text_features.T).softmax(dim=-1)

    scores = similarity[0].tolist()
    best_idx = max(range(len(scores)), key=lambda i: scores[i])
    return names[best_idx], scores[best_idx]


def identify_species(image_path: Path, confidence_threshold: float) -> ClassificationResult:
    """
    Run BioCLIP on a flora/fauna photo.

    If species-level confidence is >= confidence_threshold, the result is
    treated as identified (common/scientific name set, below_threshold=False).

    Otherwise below_threshold=True and the caller (pipeline.py) routes the
    photo to the review folder instead of trusting a name. We still return
    whatever genus/family/class info BioCLIP has -- along with a GBIF
    genus-level common name where available -- purely so the review-folder
    photo still ends up with some useful tags rather than none at all.
    """
    classifier = _load_bioclip()
    Rank = _rank()
    predictions = classifier.predict(str(image_path), Rank.SPECIES)

    if not predictions:
        return ClassificationResult(below_threshold=True)

    top = predictions[0]
    species_conf = float(top.get("score", 0.0))
    genus = top.get("genus") or None
    family = top.get("family") or None
    class_name = top.get("class") or None
    generic_type = _generic_type_for_class(class_name)

    if species_conf >= confidence_threshold:
        return ClassificationResult(
            common_name=top.get("common_name") or top.get("species"),
            scientific_name=top.get("species"),
            species_confidence=species_conf,
            below_threshold=False,
            genus=genus,
            family=family,
            class_name=class_name,
            generic_type=generic_type,
            source="bioclip",
            rank_used="species",
        )

    genus_common_name = _gbif_common_name(genus) if genus else None
    return ClassificationResult(
        common_name=None,
        scientific_name=None,
        species_confidence=species_conf,
        below_threshold=True,
        genus=genus,
        family=family,
        class_name=class_name,
        generic_type=generic_type,
        genus_common_name=genus_common_name,
        source="gbif_fallback" if genus_common_name else "",
        rank_used="genus" if genus else "",
    )


def _gbif_common_name(taxon_name: str) -> Optional[str]:
    """Look up a common/vernacular name for a taxon via GBIF's public API (no key required)."""
    try:
        match = requests.get(GBIF_MATCH_URL, params={"name": taxon_name}, timeout=5)
        match.raise_for_status()
        key = match.json().get("usageKey")
        if not key:
            return None

        vernacular = requests.get(GBIF_VERNACULAR_URL.format(key=key), timeout=5)
        vernacular.raise_for_status()
        results = vernacular.json().get("results", [])
        for entry in results:
            if entry.get("language") in ("eng", "en", None):
                return entry.get("vernacularName")
        return results[0].get("vernacularName") if results else None
    except Exception:
        return None
