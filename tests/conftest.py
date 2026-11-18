import os
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


@pytest.fixture(scope="session")
def qt_app():
    """One configured QApplication for the whole session.

    It must exist before any QGuiApplication is created, because Qt cannot
    upgrade a QGuiApplication to the QApplication that widgets require.
    """
    if os.name != "nt" and not os.environ.get("QT_QPA_PLATFORM"):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication

    from mizan.ui.theme import configure_application

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return configure_application(app)
