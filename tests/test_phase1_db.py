import sqlite3

import pytest

from mizan import migrations
from mizan.db import Database
from mizan.migrations import MIGRATIONS


def test_migrations_apply_from_empty_and_are_idempotent(db: Database):
    with db.connect() as conn:
        versions = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        assert [r["version"] for r in versions] == [m.version for m in MIGRATIONS]
    db.initialize()
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"] == len(MIGRATIONS)


def test_supplier_payment_migration_preserves_existing_database(tmp_path, monkeypatch):
    db = Database(tmp_path / "mizan-v7.db")
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", [m for m in MIGRATIONS if m.version < 8])
        db.initialize()
    with db.transaction() as conn:
        conn.execute("INSERT INTO store_profile(id,name_ar) VALUES(1,'متجر')")

    db.initialize()
    assert db.scalar("SELECT name_ar FROM store_profile WHERE id=1") == "متجر"
    assert db.scalar("SELECT name FROM schema_migrations WHERE version=8") == "supplier_payments"
    assert db.scalar("SELECT COUNT(*) FROM supplier_payments") == 0


def test_sqlite_pragmas_are_enabled(db: Database):
    with db.connect() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_T_DB_012_foreign_key_enforced(db: Database):
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO audit_events(event_type,entity_type,actor_user_id,occurred_at) VALUES(?,?,?,?)",
                ("TEST", "thing", 999, "2026-01-01T00:00:00Z"),
            )


def test_transaction_rolls_back_everything(db: Database):
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            conn.execute("INSERT INTO app_settings(key,value) VALUES('x','1')")
            raise RuntimeError("boom")
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM app_settings WHERE key='x'").fetchone()["n"] == 0


def test_store_profile_and_sequences_exist(db: Database):
    with db.transaction() as conn:
        conn.execute("INSERT INTO store_profile(id,name_ar) VALUES(1,'متجر')")
        conn.execute("INSERT INTO sequences(name,year,next_value,prefix) VALUES('SALES_INVOICE',2026,1,'S')")
    with db.connect() as conn:
        assert conn.execute("SELECT name_ar FROM store_profile WHERE id=1").fetchone()["name_ar"] == "متجر"
        assert conn.execute("SELECT next_value FROM sequences WHERE name='SALES_INVOICE'").fetchone()["next_value"] == 1
