"""Config loading and path resolution."""

import pytest

from burninghouse_qc.config import Config


def test_defaults_load_without_a_file():
    cfg = Config.load(None)
    assert cfg.text.enabled
    assert cfg.black.fail_duration == 0.5


def test_toml_overrides_are_applied(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[black]
fail_duration = 1.25

[text]
min_confidence = 60.0
tesseract_psm = 6
"""
    )
    cfg = Config.load(path)
    assert cfg.black.fail_duration == 1.25
    assert cfg.text.min_confidence == 60.0
    assert cfg.text.tesseract_psm == 6
    assert cfg.silence.fail_duration == 3.0, "untouched sections keep their defaults"


def test_relative_paths_resolve_against_the_config_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[paths]\ninput = "media/in"\n')
    cfg = Config.load(path)
    assert cfg.paths.input == tmp_path / "media" / "in"


def test_unknown_keys_are_rejected_loudly(tmp_path):
    """A typo'd threshold silently doing nothing is the worst outcome here."""
    path = tmp_path / "config.toml"
    path.write_text('[black]\nfail_duraton = 1.0\n')
    with pytest.raises(ValueError, match="fail_duraton"):
        Config.load(path)


def test_the_shipped_example_config_is_valid():
    cfg = Config.load("config.example.toml")
    assert cfg.text.fail_min_occurrences >= 1
