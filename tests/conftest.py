from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from toop.db import get_connection, init_db


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
