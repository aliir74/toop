from __future__ import annotations

from pathlib import Path

import pytest

from toop import photos


@pytest.fixture
def photos_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "photos"
    monkeypatch.setattr(photos.settings, "PHOTOS_DIR", str(d))
    return d


def test_save_photo_bytes_writes_and_creates_dir(photos_dir: Path) -> None:
    path = photos.save_photo_bytes(7, b"JPEGDATA")
    assert path == photos_dir / "7.jpg"
    assert path.read_bytes() == b"JPEGDATA"


def test_save_photo_bytes_overwrites_on_resend(photos_dir: Path) -> None:
    photos.save_photo_bytes(7, b"first")
    path = photos.save_photo_bytes(7, b"second")
    assert path.read_bytes() == b"second"


def test_save_photo_bytes_accepts_negative_ghost_id(photos_dir: Path) -> None:
    path = photos.save_photo_bytes(-3, b"x")
    assert path.name == "-3.jpg"


def test_delete_photo_bytes_removes_file(photos_dir: Path) -> None:
    photos.save_photo_bytes(7, b"x")
    photos.delete_photo_bytes(7)
    assert not (photos_dir / "7.jpg").exists()


def test_delete_photo_bytes_absent_is_noop(photos_dir: Path) -> None:
    photos.delete_photo_bytes(999)  # must not raise
