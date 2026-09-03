"""Spell-checking, the custom dictionary and British/Australian tolerance."""

import pytest

from burninghouse_qc.config import SpellingConfig
from burninghouse_qc.spelling import Speller, load_custom_words, normalise
from burninghouse_qc.variants import us_variants


@pytest.fixture(scope="module")
def speller():
    return Speller(SpellingConfig(custom_dictionary="dictionary/custom_words.txt"))


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Hello,", "Hello"),
        ('"Quality"', "Quality"),
        ("client's", "client"),
        ("client’s", "client"),
        ("—Grading—", "Grading"),
        ("...", ""),
    ],
)
def test_normalise_strips_ocr_punctuation(raw, expected):
    assert normalise(raw) == expected


def test_custom_dictionary_indexes_multi_word_entries(tmp_path):
    path = tmp_path / "words.txt"
    path.write_text("# a comment\nBurning House\nProRes  # inline comment\n\n")
    words = load_custom_words(path)
    assert {"burning house", "burning", "house", "prores"} <= words


def test_missing_dictionary_is_not_fatal(tmp_path):
    assert load_custom_words(tmp_path / "nope.txt") == set()


@pytest.mark.parametrize("word", ["recieve", "Acheiving", "seperate", "definately", "occurence"])
def test_real_typos_are_flagged(speller, word):
    assert speller.is_misspelled(word)


@pytest.mark.parametrize(
    "word",
    ["colour", "colours", "organise", "organisation", "centre", "metres",
     "behaviour", "programme", "analysed", "defence", "aluminium"],
)
def test_australian_spellings_are_accepted(speller, word):
    """The bundled dictionary is US English; variants.py bridges the gap."""
    assert not speller.is_misspelled(word)


@pytest.mark.parametrize("word", ["Burninghouse", "ProRes", "timecode", "Resolve"])
def test_custom_dictionary_words_are_accepted(speller, word):
    assert not speller.is_misspelled(word)


def test_variant_transform_does_not_rescue_a_genuine_typo():
    """'coulour' -> 'coulor' is still not a word, so it must stay flagged."""
    assert "coulor" in us_variants("coulour")
    assert "color" not in us_variants("coulour")


@pytest.mark.parametrize(
    "token,checkable",
    [
        ("Grading", True),
        ("the", False),          # under min_word_length
        ("l1ght", False),        # digits: OCR noise, not a word
        ("HDMI", False),         # short all-caps acronym
        ("XVIII", False),        # roman numeral
        ("PROFESSIONAL", True),  # long all-caps is real on-screen copy
    ],
)
def test_is_checkable_filters_ocr_noise(speller, token, checkable):
    assert speller.is_checkable(token, min_length=4) is checkable


def test_suggestions_offer_the_intended_word(speller):
    assert "achieving" in speller.suggestions("Acheiving")


# -- OCR misread signatures ----------------------------------------------

@pytest.mark.parametrize(
    "token,why",
    [
        ("gOLOUR", "a misread C at the head of COLOUR"),
        ("PROFESSlONAL", "a capital I misread as lowercase l"),
        ("AchieVing", "a stray capital mid-word"),
        ("cOMPANY", "misread C"),
        ("GRADlNG", "misread I"),
    ],
)
def test_ocr_misread_case_shapes_are_never_flagged(speller, token, why):
    """People do not change case halfway through a word, so an odd case shape
    means a misread character, not a misspelling. This was a real false positive
    on the first macOS run: "gOLOUR" flagged on a clip with no errors in it."""
    assert speller.is_checkable(token, min_length=4) is False, why


@pytest.mark.parametrize(
    "token",
    ["Acheiving", "recieve", "SEPERATE", "definately", "Occurence", "MISPELED"],
)
def test_real_typos_survive_the_case_filter(speller, token):
    """The filter must not become an excuse to miss actual errors."""
    assert speller.is_checkable(token, min_length=4)
    assert speller.is_misspelled(token)


@pytest.mark.parametrize("token", ["colour", "Colour", "COLOUR", "grading", "Grading"])
def test_normal_case_shapes_are_still_checked(speller, token):
    assert speller.is_checkable(token, min_length=4)


def test_the_case_filter_can_be_turned_off(tmp_path):
    from burninghouse_qc.config import SpellingConfig
    from burninghouse_qc.spelling import Speller

    relaxed = Speller(SpellingConfig(require_normal_case=False))
    assert relaxed.is_checkable("gOLOUR", min_length=4)
