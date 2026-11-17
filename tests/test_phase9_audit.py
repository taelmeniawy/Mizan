"""Phase 9 — audit trail access and append-only protection (T-SEC-008)."""

import sqlite3

import pytest

from mizan import migrations
from mizan.audit import AuditQueryError, AuditService
from mizan.auth import PermissionDenied
from mizan.db import Database
from mizan.migrations import MIGRATIONS
from phase6_support import build_shop


@pytest.fixture()
def shop(db):
    return build_shop(db)


def _insert(db, event_type="TEST_EVENT", *, entity_type="thing", entity_id=1,
            actor=None, occurred_at="2026-11-10T10:00:00+00:00",
            before=None, after=None, reason=None):
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO audit_events(event_type,entity_type,entity_id,actor_user_id,"
            "occurred_at,before_json,after_json,reason) VALUES(?,?,?,?,?,?,?,?)",
            (event_type, entity_type, entity_id, actor, occurred_at, before, after, reason),
        )


def test_T_SEC_008_audit_log_permissions(shop):
    audit = AuditService(shop.db)
    assert audit.query(shop.owner)["total"] == 0
    audit.query(shop.manager)  # Manager may read the trail.
    with pytest.raises(PermissionDenied):
        audit.query(shop.cashier)


def test_audit_query_filters_ordering_and_paging(shop):
    audit = AuditService(shop.db)
    _insert(shop.db, "BACKUP_CREATED", entity_type="BACKUP", occurred_at="2026-11-01T09:00:00+00:00")
    _insert(shop.db, "BACKUP_FAILED", entity_type="BACKUP", occurred_at="2026-11-02T09:00:00+00:00",
            reason="disk full")
    _insert(shop.db, "PERIOD_CLOSED", entity_type="ACCOUNTING_PERIOD", occurred_at="2026-11-03T09:00:00+00:00",
            actor=shop.owner)
    _insert(shop.db, "LOGIN", entity_type="user", entity_id=shop.cashier,
            occurred_at="2026-11-04T09:00:00+00:00", actor=shop.cashier)

    page = audit.query(shop.owner)
    assert [event["event_type"] for event in page["events"]] == [
        "LOGIN", "PERIOD_CLOSED", "BACKUP_FAILED", "BACKUP_CREATED",
    ]
    assert page["total"] == 4

    assert [event["event_type"] for event in audit.query(
        shop.owner, event_type="BACKUP_FAILED")["events"]] == ["BACKUP_FAILED"]
    assert audit.query(shop.owner, entity_type="BACKUP")["total"] == 2
    assert audit.query(shop.owner, entity_id=shop.cashier)["total"] == 1
    assert audit.query(shop.owner, actor_user_id=shop.owner)["total"] == 1
    assert audit.query(shop.owner, from_date="2026-11-02",
                       through_date="2026-11-03")["total"] == 2

    first = audit.query(shop.owner, limit=2)
    assert len(first["events"]) == 2 and first["total"] == 4
    second = audit.query(shop.owner, limit=2, offset=2)
    assert [event["event_type"] for event in second["events"]] == [
        "BACKUP_FAILED", "BACKUP_CREATED",
    ]


def test_audit_rows_expose_actor_and_decoded_payloads(shop):
    shop.auth.login("owner", "owner-pass")
    _insert(shop.db, "USER_ROLE_CHANGED", entity_type="user", entity_id=shop.manager,
            actor=shop.owner, before='{"role": "CASHIER"}', after='{"role": "MANAGER"}')
    events = AuditService(shop.db).query(shop.owner, event_type="USER_ROLE_CHANGED")["events"]
    event = events[0]
    assert event["actor_username"] == "owner"
    assert event["before"] == {"role": "CASHIER"}
    assert event["after"] == {"role": "MANAGER"}


def test_audit_query_validation(shop):
    audit = AuditService(shop.db)
    for kwargs in (
        {"limit": 0}, {"limit": 501}, {"offset": -1},
        {"from_date": "2026-13-01"}, {"from_date": "2026-11-02", "through_date": "2026-11-01"},
    ):
        with pytest.raises(AuditQueryError):
            audit.query(shop.owner, **kwargs)


def test_audit_events_reject_update_and_delete(shop):
    _insert(shop.db)
    for statement in (
        "UPDATE audit_events SET reason='changed'",
        "DELETE FROM audit_events",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            with shop.db.transaction() as conn:
                conn.execute(statement)
    # The trail is still append-only writable: a real operation records normally.
    shop.auth.login("owner", "owner-pass")
    assert AuditService(shop.db).query(shop.owner, event_type="LOGIN")["total"] == 1


def test_audit_protection_migration_upgrades_an_existing_database(tmp_path, monkeypatch):
    db = Database(tmp_path / "mizan-v9.db")
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", [m for m in MIGRATIONS if m.version < 10])
        db.initialize()
    _insert(db, "OLD_EVENT")

    db.initialize()
    assert db.scalar("SELECT name FROM schema_migrations WHERE version=10") == "audit_log_protection"
    assert db.scalar("SELECT COUNT(*) FROM audit_events WHERE event_type='OLD_EVENT'") == 1
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with db.transaction() as conn:
            conn.execute("DELETE FROM audit_events")
