"""Transfer safety: the source file must survive everything."""

import os
from pathlib import Path

import pytest

from burninghouse_qc import transfer
from burninghouse_qc.transfer import (
    FileSnapshot,
    TransferError,
    check_space,
    file_digest,
    safe_copy,
    safe_move,
)


def make_file(path: Path, payload: bytes = b"a render, pretend") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_safe_copy_reproduces_the_file_exactly(tmp_path):
    source = make_file(tmp_path / "src" / "a.mov", b"x" * 5000)
    target = safe_copy(source, tmp_path / "dst" / "a.mov")
    assert target.read_bytes() == source.read_bytes()
    assert source.exists()


def test_safe_copy_leaves_no_partial_file_behind(tmp_path):
    source = make_file(tmp_path / "a.mov")
    target = safe_copy(source, tmp_path / "dst" / "a.mov")
    leftovers = list(target.parent.glob(f"*{transfer.PARTIAL_SUFFIX}"))
    assert leftovers == []


def test_a_truncated_copy_is_detected_and_discarded(tmp_path, monkeypatch):
    """The partial must be removed and the source left alone."""
    source = make_file(tmp_path / "a.mov", b"y" * 4096)
    target = tmp_path / "dst" / "a.mov"

    def truncating_copy(src, dst, **kwargs):
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_bytes(b"y" * 100)      # short write

    monkeypatch.setattr(transfer.shutil, "copy2", truncating_copy)

    with pytest.raises(TransferError, match="truncated"):
        safe_copy(source, target)

    assert source.exists() and source.stat().st_size == 4096
    assert not target.exists()
    assert list(target.parent.glob(f"*{transfer.PARTIAL_SUFFIX}")) == []


def test_hash_verification_catches_silent_corruption(tmp_path, monkeypatch):
    source = make_file(tmp_path / "a.mov", b"z" * 2048)
    target = tmp_path / "dst" / "a.mov"

    def corrupting_copy(src, dst, **kwargs):
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_bytes(b"q" * 2048)     # right size, wrong bytes

    monkeypatch.setattr(transfer.shutil, "copy2", corrupting_copy)

    with pytest.raises(TransferError, match="does not match"):
        safe_copy(source, target, verify_hash=True)
    assert source.exists()
    assert not target.exists()


def test_copy_refuses_to_start_without_room(tmp_path, monkeypatch):
    """Filling a shared volume is its own kind of outage."""
    source = make_file(tmp_path / "a.mov", b"w" * 10_000)
    monkeypatch.setattr(transfer, "free_space", lambda _d: 1000)
    with pytest.raises(TransferError, match="Not enough room"):
        check_space(source, tmp_path)


def test_same_filesystem_move_is_atomic_and_complete(tmp_path):
    source = make_file(tmp_path / "src" / "a.mov", b"payload")
    target = safe_move(source, tmp_path / "dst" / "a.mov")
    assert target.read_bytes() == b"payload"
    assert not source.exists()


def test_cross_filesystem_move_verifies_before_deleting(tmp_path, monkeypatch):
    """The delete must happen only after a verified copy exists."""
    source = make_file(tmp_path / "src" / "a.mov", b"payload")
    target = tmp_path / "dst" / "a.mov"
    order: list[str] = []

    monkeypatch.setattr(transfer, "same_filesystem", lambda a, b: False)
    real_copy = transfer.shutil.copy2

    def tracked_copy(src, dst, **kwargs):
        order.append("copy")
        return real_copy(src, dst, **kwargs)

    real_unlink = Path.unlink

    def tracked_unlink(self, *args, **kwargs):
        if self == source:
            order.append("delete")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(transfer.shutil, "copy2", tracked_copy)
    monkeypatch.setattr(Path, "unlink", tracked_unlink)

    safe_move(source, target)
    assert order == ["copy", "delete"]
    assert target.read_bytes() == b"payload"
    assert not source.exists()


def test_a_failed_cross_filesystem_copy_never_deletes_the_source(tmp_path, monkeypatch):
    source = make_file(tmp_path / "src" / "a.mov", b"payload")

    monkeypatch.setattr(transfer, "same_filesystem", lambda a, b: False)

    def boom(*args, **kwargs):
        raise OSError("network went away mid-copy")

    monkeypatch.setattr(transfer.shutil, "copy2", boom)

    with pytest.raises(TransferError):
        safe_move(source, tmp_path / "dst" / "a.mov")
    assert source.exists(), "the render must survive a failed move"
    assert source.read_bytes() == b"payload"


def test_digest_distinguishes_different_content(tmp_path):
    a = make_file(tmp_path / "a.mov", b"one")
    b = make_file(tmp_path / "b.mov", b"two")
    assert file_digest(a) != file_digest(b)
    assert file_digest(a) == file_digest(a)


class TestSnapshot:
    def test_matches_an_unchanged_file(self, tmp_path):
        path = make_file(tmp_path / "a.mov")
        before = FileSnapshot.of(path)
        assert before.matches(FileSnapshot.of(path))

    def test_notices_a_rewrite(self, tmp_path):
        path = make_file(tmp_path / "a.mov", b"first")
        before = FileSnapshot.of(path)
        path.write_bytes(b"a completely different second render")
        assert not before.matches(FileSnapshot.of(path))

    def test_notices_a_same_size_rewrite_via_mtime(self, tmp_path):
        path = make_file(tmp_path / "a.mov", b"12345")
        before = FileSnapshot.of(path)
        path.write_bytes(b"54321")
        os.utime(path, (before.mtime + 60, before.mtime + 60))
        assert not before.matches(FileSnapshot.of(path))

    def test_tolerates_coarse_mtime_resolution(self, tmp_path):
        """SMB mtimes are often only good to a second or two."""
        path = make_file(tmp_path / "a.mov")
        before = FileSnapshot.of(path)
        jittered = FileSnapshot(size=before.size, mtime=before.mtime + 1.0)
        assert before.matches(jittered)

    def test_a_missing_file_never_matches(self, tmp_path):
        path = make_file(tmp_path / "a.mov")
        before = FileSnapshot.of(path)
        path.unlink()
        assert not before.matches(FileSnapshot.of(path))


# -- ffmpeg flag selection (no ffmpeg needed) ----------------------------

@pytest.mark.parametrize(
    "detected,expected",
    [
        ((6, 1), ["-fps_mode", "passthrough"]),
        ((9, 0), ["-fps_mode", "passthrough"]),
        ((5, 1), ["-fps_mode", "passthrough"]),
        ((5, 0), ["-vsync", "0"]),
        ((4, 3), ["-vsync", "0"]),
        (None, ["-fps_mode", "passthrough"]),   # unknown: assume modern
    ],
)
def test_passthrough_flag_matches_the_ffmpeg_version(monkeypatch, detected, expected):
    from burninghouse_qc import ffmpeg_tools

    monkeypatch.setattr(ffmpeg_tools, "version", lambda: detected)
    assert ffmpeg_tools.passthrough_args() == expected


@pytest.mark.parametrize(
    "banner,expected",
    [
        ("ffmpeg version 9.0.1 Copyright (c) 2000-2026", (9, 0)),
        ("ffmpeg version 6.1.1-3ubuntu5 Copyright (c) 2000-2023", (6, 1)),
        ("ffmpeg version n7.1 Copyright (c) 2000-2024", (7, 1)),
        ("ffmpeg version 2026-01-01-git-abc123", None),
        ("something else entirely", None),
    ],
)
def test_version_parsing(monkeypatch, banner, expected):
    from burninghouse_qc import ffmpeg_tools

    class FakeProc:
        stdout = banner

    ffmpeg_tools.version.cache_clear()
    monkeypatch.setattr(ffmpeg_tools, "run", lambda *a, **k: FakeProc())
    monkeypatch.setattr(ffmpeg_tools, "_binary", lambda name: f"/usr/bin/{name}")
    try:
        assert ffmpeg_tools.version() == expected
    finally:
        ffmpeg_tools.version.cache_clear()
