"""British / Australian spelling tolerance.

pyspellchecker's `en` dictionary is US English, so `colour`, `organise` and
`centre` all come back as misspellings. Rather than maintaining a word list of
every -our/-ise word in the language, a candidate word is transformed into its
US equivalent and re-checked: if the transform lands on a real word, the
original is accepted.

This keeps genuine typos flagged — "coulour" transforms to "coulor", which is
not a word either, so it still fails.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable

# (pattern, replacement) applied to the lowercase word.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"our(s|ed|ing|ful|less|able|ite|ites)?$"), r"or\1"),
    (re.compile(r"is(e|es|ed|ing|er|ers|ation|ations|able)$"), r"iz\1"),
    (re.compile(r"ys(e|es|ed|ing|er|ers)$"), r"yz\1"),
    (re.compile(r"tre(s)?$"), r"ter\1"),
    (re.compile(r"bre(s)?$"), r"ber\1"),
    (re.compile(r"ogue(s)?$"), r"og\1"),
    (re.compile(r"([a-z])nce$"), r"\1nse"),          # defence -> defense
    (re.compile(r"^(a|o)e"), r"e"),                  # aetiology -> etiology
    (re.compile(r"ae"), "e"),                        # anaemia -> anemia
    (re.compile(r"oe"), "e"),                        # oesophagus -> esophagus
    (re.compile(r"ll(ed|ing|er|ers|ery|or|ors)$"), r"l\1"),  # travelled -> traveled
    (re.compile(r"ould$"), "old"),                   # mould -> mold
    (re.compile(r"mme$"), "m"),                      # programme -> program
    (re.compile(r"^grey"), "gray"),
    (re.compile(r"grey$"), "gray"),
]

# Words whose US form isn't reachable by a regular transform.
_IRREGULAR: dict[str, str] = {
    "aluminium": "aluminum",
    "cheque": "check",
    "cheques": "checks",
    "chequered": "checkered",
    "draught": "draft",
    "draughts": "drafts",
    "gaol": "jail",
    "jewellery": "jewelry",
    "kerb": "curb",
    "kerbs": "curbs",
    "manoeuvre": "maneuver",
    "manoeuvres": "maneuvers",
    "moustache": "mustache",
    "plough": "plow",
    "ploughed": "plowed",
    "practise": "practice",
    "practised": "practiced",
    "practising": "practicing",
    "pyjamas": "pajamas",
    "sceptic": "skeptic",
    "sceptical": "skeptical",
    "storey": "story",
    "storeys": "stories",
    "tyre": "tire",
    "tyres": "tires",
    "whilst": "while",
}


def us_variants(word: str) -> list[str]:
    """Every plausible US spelling of a British/Australian word."""
    lowered = word.lower()
    candidates: list[str] = []
    if lowered in _IRREGULAR:
        candidates.append(_IRREGULAR[lowered])
    for pattern, replacement in _PATTERNS:
        transformed = pattern.sub(replacement, lowered)
        if transformed != lowered:
            candidates.append(transformed)
    # Chained transforms cover words like "organisational centre-isms".
    for base in list(candidates):
        for pattern, replacement in _PATTERNS:
            transformed = pattern.sub(replacement, base)
            if transformed != base:
                candidates.append(transformed)
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if candidate != lowered and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def is_known_variant(word: str, known: Callable[[Iterable[str]], bool]) -> bool:
    """True if any US transform of `word` is a word `known` recognises."""
    for candidate in us_variants(word):
        if known([candidate]):
            return True
    return False
