"""Spell-checking with an editable custom dictionary.

The custom list is a plain text file — one word per line, `#` for comments — so
whoever owns brand/client naming can maintain it without touching code
(SPEC.md §6).
"""

from __future__ import annotations

import re
from pathlib import Path

from spellchecker import SpellChecker

from .config import SpellingConfig
from .variants import is_known_variant

# Trailing possessives and smart quotes are stripped before lookup.
_STRIP_CHARS = "\"'“”‘’`.,:;!?()[]{}<>*_—–-…"
_POSSESSIVE = re.compile(r"[’']s$", re.IGNORECASE)
_ALPHA_ONLY = re.compile(r"^[A-Za-z]+$")
_ROMAN_NUMERAL = re.compile(r"^[IVXLCDM]+$")
# The three case shapes real words come in: lowercase, Title Case, ALL CAPS.
# Anything else — "gOLOUR", "PROFESSlONAL", "AchieVing" — is the signature of a
# misread character, not of a misspelling, because a person typing a word wrong
# does not change its case halfway through. Mixed-case brand names (ProRes,
# iPhone) belong in the custom dictionary; skipping them costs nothing, since
# they are spelled correctly anyway.
_NORMAL_CASE = re.compile(r"^(?:[a-z]+|[A-Z][a-z]+|[A-Z]+)$")
_TITLE_CASE = re.compile(r"^[A-Z][a-z]+$")

# Title-case words that do not imply a name when they sit next to one. Without
# these, "Achieving The Perfect Shot" would read as a proper noun and a real
# misspelling in a title-cased card would be missed.
_TITLE_STOPWORDS = frozenset(
    """a an the and or but of in on at to for from by with without into over under
    is are was were be been this that these those his her its their our your my""".split()
)


def is_title_case(token: str) -> bool:
    return bool(_TITLE_CASE.match(normalise(token)))


def looks_like_proper_noun(token: str, neighbours: list[str]) -> bool:
    """True if a Title-case token sits beside another Title-case word.

    Lower-third name supers are the most common on-screen text in interview
    work, and a spell-checker cannot possibly validate a surname — "Rothberg"
    and "Gullery" are not errors, and no dictionary will ever contain every
    name a client sends. Two capitalised words in a row is the giveaway.
    """
    if not is_title_case(token):
        return False
    for neighbour in neighbours:
        cleaned = normalise(neighbour)
        if not cleaned or cleaned.lower() in _TITLE_STOPWORDS:
            continue
        if is_title_case(neighbour):
            return True
    return False


def normalise(token: str) -> str:
    """Reduce an OCR token to the bare word to look up. May return ''."""
    cleaned = token.strip().strip(_STRIP_CHARS)
    cleaned = _POSSESSIVE.sub("", cleaned)
    return cleaned.strip(_STRIP_CHARS)


def load_custom_words(path: Path | None) -> set[str]:
    if path is None or not Path(path).exists():
        return set()
    words: set[str] = set()
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        # Allow multi-word entries like "Burning House" — index each part too.
        words.add(line.lower())
        for part in line.split():
            part = normalise(part).lower()
            if part:
                words.add(part)
    return words


class Speller:
    """Wraps pyspellchecker with the custom list and OCR-aware filtering."""

    def __init__(self, cfg: SpellingConfig, base_dir: Path | None = None):
        self.cfg = cfg
        dictionary_path = Path(cfg.custom_dictionary)
        if base_dir and not dictionary_path.is_absolute():
            dictionary_path = base_dir / dictionary_path
        self.custom_words = load_custom_words(dictionary_path)
        self.dictionary_path = dictionary_path
        self._checker = SpellChecker(language=cfg.language)
        if self.custom_words:
            self._checker.word_frequency.load_words(self.custom_words)

    def is_checkable(self, token: str, min_length: int) -> bool:
        """False for tokens that OCR routinely mangles or that aren't words."""
        word = normalise(token)
        if len(word) < min_length:
            return False
        if not _ALPHA_ONLY.match(word):
            return False
        if self.cfg.ignore_all_caps_acronyms and word.isupper() and len(word) <= 5:
            return False
        if _ROMAN_NUMERAL.match(word):
            return False
        if self.cfg.require_normal_case and not _NORMAL_CASE.match(word):
            return False
        return True

    def _known(self, words) -> bool:
        return not self._checker.unknown(list(words))

    def is_misspelled(self, token: str) -> bool:
        word = normalise(token).lower()
        if not word or word in self.custom_words:
            return False
        if self._known([word]):
            return False
        if self.cfg.accept_british_spellings and is_known_variant(word, self._known):
            return False
        return True

    def suggestions(self, token: str, limit: int = 3) -> list[str]:
        word = normalise(token).lower()
        if not word:
            return []
        candidates = self._checker.candidates(word) or set()
        ranked = sorted(
            (c for c in candidates if c != word),
            key=lambda c: -self._checker.word_frequency[c],
        )
        return ranked[:limit]
