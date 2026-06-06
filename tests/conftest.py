from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from toop.config import settings
from toop.db import get_connection, init_db


@pytest.fixture(autouse=True)
def _force_english(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default tests to English so the (mostly English) assertions stay stable.

    Production defaults to Persian (config.BOT_LANG="fa"); tests that exercise
    the Persian catalog set settings.BOT_LANG="fa" explicitly. t() reads
    settings.BOT_LANG from its own module, so this one override governs every
    call site.
    """
    monkeypatch.setattr(settings, "BOT_LANG", "en")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "toop.db"


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    c = get_connection(db_path)
    init_db(c)
    try:
        yield c
    finally:
        c.close()
