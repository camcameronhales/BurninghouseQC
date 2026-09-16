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


# -- configs outlive the code that read them -----------------------------

def test_an_obsolete_key_is_ignored_not_fatal(tmp_path, capsys):
    """config.toml is hand-maintained on each machine and outlives the app.

    The launchd agent restarts on crash, so a removed option raising here is a
    crash loop rather than a message anyone reads.
    """
    target = tmp_path / "config.toml"
    target.write_text('[routing]\nmode = "alongside"\nverify_hash = false\n')

    cfg = Config.load(target)

    assert cfg.routing.mode == "alongside"
    assert "obsolete config key 'routing.verify_hash'" in capsys.readouterr().err


def test_a_genuinely_unknown_key_is_still_rejected(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("[routing]\nnonsense_key = 1\n")
    with pytest.raises(ValueError, match="Unknown config key"):
        Config.load(target)


def test_every_obsolete_key_names_a_section_that_exists(tmp_path):
    """An entry like "routng.verify_hash" would silently never match."""
    from burninghouse_qc.config import OBSOLETE_KEYS

    cfg = Config()
    for qualified in OBSOLETE_KEYS:
        section, _, _ = qualified.rpartition(".")
        assert section, f"{qualified!r} is not section-qualified"
        target = cfg
        for part in section.split("."):
            assert hasattr(target, part), f"{qualified!r} names no such section"
            target = getattr(target, part)
