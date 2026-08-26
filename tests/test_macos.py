"""macOS-specific behaviour: network mounts and sleep prevention."""

from pathlib import Path

import pytest

from burninghouse_qc import mounts, power


@pytest.mark.parametrize(
    "device,expected",
    [
        ("/dev/disk3s1s1", False),          # local APFS volume
        ("/dev/vda", False),
        ("//cam@nas.local/renders", True),  # SMB, the usual edit-suite case
        ("//NAS/Video", True),
        ("afp://server/share", True),
        ("nas.local:/exports/renders", True),  # NFS
        ("map auto_home", False),
        ("", False),
    ],
)
def test_network_mounts_are_recognised(monkeypatch, device, expected):
    monkeypatch.setattr(mounts, "device_for", lambda _path: device)
    assert mounts.is_network_path(Path("/Volumes/renders")) is expected


@pytest.mark.parametrize("setting", ["true", "yes", "Always", "ON"])
def test_polling_can_be_forced_on(monkeypatch, setting):
    monkeypatch.setattr(mounts, "is_network_path", lambda _p: False)
    assert mounts.should_poll(Path("/tmp"), setting) is True


@pytest.mark.parametrize("setting", ["false", "no", "Never", "off"])
def test_polling_can_be_forced_off(monkeypatch, setting):
    """Even on a share — an escape hatch if the detection ever gets it wrong."""
    monkeypatch.setattr(mounts, "is_network_path", lambda _p: True)
    assert mounts.should_poll(Path("/Volumes/renders"), setting) is False


@pytest.mark.parametrize("setting", ["auto", "", None])
def test_auto_defers_to_mount_detection(monkeypatch, setting):
    monkeypatch.setattr(mounts, "is_network_path", lambda _p: True)
    assert mounts.should_poll(Path("/Volumes/renders"), setting) is True


def test_device_for_parses_df_output(monkeypatch):
    sample = (
        "Filesystem 512-blocks Used Available Capacity Mounted on\n"
        "//cam@nas.local/renders 1953125000 100 1953124900 1% /Volumes/renders\n"
    )

    class FakeProc:
        stdout = sample

    monkeypatch.setattr(mounts.subprocess, "run", lambda *a, **k: FakeProc())
    assert mounts.device_for(Path("/Volumes/renders")) == "//cam@nas.local/renders"


def test_device_for_survives_df_failing(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("df not available")

    monkeypatch.setattr(mounts.subprocess, "run", boom)
    assert mounts.device_for(Path("/tmp")) == ""


def test_keep_awake_is_a_no_op_off_macos(monkeypatch):
    monkeypatch.setattr(power.sys, "platform", "linux")
    with power.keep_awake():
        pass


def test_keep_awake_holds_and_releases_the_assertion(monkeypatch):
    """The assertion must be released even if the job raises."""
    calls = {"started": 0, "terminated": 0}

    class FakeProc:
        def terminate(self):
            calls["terminated"] += 1

        def wait(self, timeout=None):
            return 0

    def fake_popen(args, **kwargs):
        assert args[0].endswith("caffeinate")
        calls["started"] += 1
        return FakeProc()

    monkeypatch.setattr(power, "supported", lambda: True)
    monkeypatch.setattr(power.subprocess, "Popen", fake_popen)

    with pytest.raises(RuntimeError):
        with power.keep_awake():
            raise RuntimeError("job blew up")

    assert calls == {"started": 1, "terminated": 1}


def test_keep_awake_survives_a_missing_caffeinate(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no caffeinate here")

    monkeypatch.setattr(power, "supported", lambda: True)
    monkeypatch.setattr(power.subprocess, "Popen", boom)
    with power.keep_awake():
        pass
