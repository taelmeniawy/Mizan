import sqlite3
from pathlib import Path

import pytest

from mizan.db import Database


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "mizan.db")
    database.initialize()
    return database


@pytest.fixture()
def conn(db: Database):
    connection = db.connect()
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture()
def uow(db: Database):
    return db
