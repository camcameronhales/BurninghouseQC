"""`bhqc init` and `bhqc forget` — the two commands the local trial leans on."""

from pathlib import Path

import pytest

from burninghouse_qc.cli import main
from burninghouse_qc.config import Config
from burninghouse_qc.ledger import Ledger

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_init_creates_a_working_config_and_folders(tmp_path):
    config = tmp_path / "config.toml"
    assert main(["init", "-o", str(config)]) == 0

    assert config.exists()
    cfg = Config.load(config)
    for folder in (cfg.paths.input, cfg.paths.passed, cfg.paths.review, cfg.paths.error):
        assert folder.is_dir()


def test_init_writes_absolute_paths(tmp_path):
    """A launchd service has no working directory of yours — relative paths in
    a config are how it ends up silently watching the wrong folder."""
    config = tmp_path / "config.toml"
    main(["init", "-o", str(config)])

    cfg = Config.load(config)
    for path in (cfg.paths.input, cfg.paths.passed, cfg.paths.work, cfg.paths.ledger_file):
        assert path.is_absolute()
    assert 'root        = "/' in config.read_text()


def test_init_gives_the_install_its_own_dictionary(tmp_path):
    """It must live outside the repo so `git pull` can't overwrite added words."""
    config = tmp_path / "config.toml"
    main(["init", "-o", str(config)])

    dictionary = tmp_path / "dictionary" / "custom_words.txt"
    assert dictionary.exists()
    assert "Burninghouse" in dictionary.read_text()

    cfg = Config.load(config)
    assert Path(cfg.spelling.custom_dictionary) == dictionary
    assert Path(cfg.spelling.custom_dictionary).is_absolute()


def test_the_configured_dictionary_actually_resolves(tmp_path):
    """The bug this test exists for: a relative dictionary path resolves against
    the config file, so it broke the moment the config left the repo."""
    from burninghouse_qc.spelling import Speller

    config = tmp_path / "config.toml"
    main(["init", "-o", str(config)])
    cfg = Config.load(config)

    speller = Speller(cfg.spelling, base_dir=config.parent)
    assert speller.dictionary_path.exists()
    assert speller.custom_words, "the custom words must actually load"
    assert not speller.is_misspelled("Burninghouse")


def test_init_does_not_overwrite_an_edited_dictionary(tmp_path):
    config = tmp_path / "config.toml"
    main(["init", "-o", str(config)])
    dictionary = tmp_path / "dictionary" / "custom_words.txt"
    dictionary.write_text("Whitefox\nAcmeCorp\n")

    main(["init", "-o", str(config), "--force"])
    assert "AcmeCorp" in dictionary.read_text()


def test_init_refuses_to_clobber_a_config_without_force(tmp_path, capsys):
    config = tmp_path / "config.toml"
    main(["init", "-o", str(config)])
    config.write_text("# hand-tuned thresholds\n")

    main(["init", "-o", str(config)])
    assert config.read_text() == "# hand-tuned thresholds\n"


def test_forget_makes_a_file_eligible_again(tmp_path):
    config = tmp_path / "config.toml"
    main(["init", "-o", str(config)])
    cfg = Config.load(config)

    render = cfg.paths.input / "spot.mov"
    render.write_bytes(b"pretend render")
    ledger = Ledger(cfg.paths.ledger_file)
    ledger.record(render, "fail")
    assert Ledger(cfg.paths.ledger_file).seen(render)

    assert main(["-c", str(config), "forget", str(render)]) == 0
    assert not Ledger(cfg.paths.ledger_file).seen(render)


def test_forget_all_clears_the_whole_ledger(tmp_path):
    config = tmp_path / "config.toml"
    main(["init", "-o", str(config)])
    cfg = Config.load(config)

    for name in ("a.mov", "b.mov"):
        render = cfg.paths.input / name
        render.write_bytes(b"pretend render")
        Ledger(cfg.paths.ledger_file).record(render, "pass")

    assert main(["-c", str(config), "forget", "--all"]) == 0
    assert not cfg.paths.ledger_file.exists()


def test_forget_without_a_file_is_an_error(tmp_path):
    config = tmp_path / "config.toml"
    main(["init", "-o", str(config)])
    assert main(["-c", str(config), "forget"]) == 2
