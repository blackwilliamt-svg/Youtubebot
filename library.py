"""
Scans the manually-curated library/ folders (sfx, reactions, music) that
you drop files into yourself. No auto-tagging beyond a simple filename
hint for sfx (e.g. "fail_boing.mp3" hints the "fails" category);
everything else is just random selection from whatever's present.

    library/
      sfx/                    any mp3/wav/ogg/m4a -- compiler picks one per cut
      reactions/<category>/   short reaction clips, manually inserted from
                               the build screen (never auto-inserted)
      music/                  royalty-free/CC background beds, ducked under
                               silent segments (images/gifs/muted clips)
"""
import random
import re
from pathlib import Path

import config
from scraper.subreddits import CATEGORIES

AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".gif"}


def ensure_dirs():
    config.SFX_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    config.MUSIC_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    for cat in CATEGORIES:
        (config.REACTIONS_LIBRARY_DIR / cat).mkdir(parents=True, exist_ok=True)


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _hinted_category(stem: str) -> str | None:
    norm = _normalize(stem)
    for cat in CATEGORIES:
        if _normalize(cat) in norm:
            return cat
    return None


# --- sfx --------------------------------------------------------------

def list_library_sfx() -> list[Path]:
    if not config.SFX_LIBRARY_DIR.exists():
        return []
    return sorted(p for p in config.SFX_LIBRARY_DIR.iterdir() if p.suffix.lower() in AUDIO_EXTS)


def pick_library_sfx(category: str | None = None) -> Path | None:
    """
    Random pick from library/sfx/, preferring files whose name hints the
    given category, falling back to unhinted (generic) files, excluding
    files hinted for a *different* category. Returns None if the library
    is empty or nothing qualifies (caller should fall back to the
    synthesized sfx in compiler/sfx_gen.py).
    """
    files = list_library_sfx()
    if not files:
        return None
    if category is None:
        return random.choice(files)

    hinted = [f for f in files if _hinted_category(f.stem) == category]
    if hinted:
        return random.choice(hinted)
    generic = [f for f in files if _hinted_category(f.stem) is None]
    if generic:
        return random.choice(generic)
    return None  # every library file is hinted for some *other* category


# --- reactions ----------------------------------------------------------

def list_reaction_clips(category: str | None = None) -> dict[str, list[Path]]:
    """{category: [clip paths]} -- restricted to one category if given."""
    cats = [category] if category else CATEGORIES
    result = {}
    for cat in cats:
        d = config.REACTIONS_LIBRARY_DIR / cat
        if d.exists():
            result[cat] = sorted(p for p in d.iterdir() if p.suffix.lower() in VIDEO_EXTS)
        else:
            result[cat] = []
    return result


# --- music --------------------------------------------------------------

def list_library_music() -> list[Path]:
    if not config.MUSIC_LIBRARY_DIR.exists():
        return []
    return sorted(p for p in config.MUSIC_LIBRARY_DIR.iterdir() if p.suffix.lower() in AUDIO_EXTS)


def pick_library_music() -> Path | None:
    files = list_library_music()
    return random.choice(files) if files else None
