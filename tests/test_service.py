"""The launchd agent: it must point at the right Python and the right config.

Getting these wrong is the classic way a background service fails — it starts,
finds a different interpreter or an empty folder, and reports nothing.
"""

import plistlib
from pathlib import Path

import pytest

from burninghouse_qc import service
from burninghouse_qc.config import Config


@pytest.fixture
def cfg(tmp_path) -> Config:
    config = Config()
    config.paths.root = tmp_path / "qc_root"
    config.paths.input = tmp_path / "qc_root" / "input"
    config.paths.work = tmp_path / "qc_root" / "work"
    config.paths.log_file = tmp_path / "qc_root" / "burninghouse-qc.log"
    return config


@pytest.fixture
def plist(cfg, tmp_path) -> dict:
    xml = service.build_plist(
        tmp_path / "config.toml", Path("/Users/Shared/BurninghouseQC/.venv/bin/python"), cfg
    )
    return plistlib.loads(xml.encode())


def test_the_plist_is_valid(plist):
    assert plist["Label"] == service.LABEL


def test_it_runs_the_venv_python_not_the_system_one(plist):
    """The system Python is 3.9 and cannot run this at all."""
    assert plist["ProgramArguments"][0].endswith(".venv/bin/python")


def test_it_names_the_config_explicitly(plist, tmp_path):
    args = plist["ProgramArguments"]
    assert "-c" in args
    assert args[args.index("-c") + 1] == str(tmp_path / "config.toml")
    assert args[-1] == "watch"


def test_it_starts_at_login_and_restarts_on_crash(plist):
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True


def test_it_supplies_a_path_for_ffmpeg_and_tesseract(plist):
    """launchd inherits no shell PATH; without this the tools are invisible."""
    path = plist["EnvironmentVariables"]["PATH"]
    assert "/opt/homebrew/bin" in path, "Apple Silicon Homebrew"
    assert "/usr/local/bin" in path, "Intel Homebrew"


def test_it_runs_at_low_priority(plist):
    """It shares the machine with whoever is editing on it."""
    assert plist["ProcessType"] == "Background"
    assert plist["Nice"] > 0


def test_logs_go_somewhere_findable(plist, cfg):
    assert plist["StandardErrorPath"].startswith(str(cfg.paths.log_file.parent))


def test_install_writes_to_the_launch_agents_folder(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    written = service.install(tmp_path / "config.toml", cfg)
    assert written == tmp_path / "home" / "Library" / "LaunchAgents" / f"{service.LABEL}.plist"
    assert written.exists()
    plistlib.loads(written.read_bytes())


def test_the_commands_use_modern_launchctl(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cmds = service.commands()
    assert "bootstrap" in cmds["start"]
    assert "bootout" in cmds["stop"]
    assert "kickstart" in cmds["restart"]
    for command in cmds.values():
        assert "launchctl load" not in command, "load/unload are long deprecated"


class TestUpdateProtocol:
    """A launchd agent holds the code it started with, so `git pull` alone
    changes nothing about what is running — silently. `bhqc update` exists so
    the pull and the restart cannot be separated.
    """

    def test_kickstart_is_a_no_op_without_a_service(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        ok, detail = service.kickstart()
        assert ok is False
        assert detail == "no background service installed"

    def test_is_installed_tracks_the_agent_file(self, cfg, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
        assert service.is_installed() is False
        service.install(tmp_path / "config.toml", cfg)
        assert service.is_installed() is True


def test_install_output_tells_you_how_to_update(cfg, tmp_path, monkeypatch, capsys):
    """The instruction must be on screen at install time, not only in a doc."""
    import sys as _sys

    from burninghouse_qc.cli import main

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(_sys, "platform", "darwin")

    config = tmp_path / "config.toml"
    main(["init", "-o", str(config)])
    capsys.readouterr()

    main(["-c", str(config), "install-service"])
    printed = capsys.readouterr().out
    assert "update" in printed
    assert "git pull" in printed, "it must say why git pull is not enough"
