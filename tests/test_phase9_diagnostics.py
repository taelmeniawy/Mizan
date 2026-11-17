"""Phase 9 — startup database/document check (no silent regeneration)."""

import sqlite3

import pytest

from mizan import migrations
from mizan.db import Database
from mizan.diagnostics import startup_check
from mizan.document_store import sha256_file
from mizan.documents import DocumentService
from mizan.migrations import MIGRATIONS
from phase6_support import build_shop


@pytest.fixture()
def shop_with_documents(db, tmp_path):
    shop = build_shop(db)
    root = tmp_path / "documents"
    pdf = root / "quotations" / "test.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4\nstartup check document\n%%EOF")
    documents = DocumentService(db)
    documents.record_export(
        document_type="REPORT", document_id=1, export_type="PDF",
        relative_path="quotations/test.pdf", sha256=sha256_file(pdf),
        file_size=pdf.stat().st_size, template_version="TEST-1",
        renderer_version="TEST-1", generated_by=shop.owner,
    )
    logo = root / "assets" / "logo.png"
    logo.parent.mkdir(parents=True)
    logo.write_bytes(b"content-addressed logo")
    documents.create_snapshot(
        document_type="REPORT", document_id=1,
        snapshot={"store": {"logo_asset": {
            "relative_path": "assets/logo.png", "sha256": sha256_file(logo),
            "file_size": logo.stat().st_size}}},
    )
    return shop, root, pdf, logo


def test_startup_check_accepts_a_healthy_database(shop_with_documents):
    shop, root, _, _ = shop_with_documents
    report = startup_check(shop.db, root)
    assert report["ok"]
    assert report["errors"] == []
    assert report["database"]["integrity"] == "ok"
    assert report["database"]["foreign_keys_ok"] is True
    assert report["database"]["schema_state"] == "current"
    assert report["documents"]["checked"] == 2
    assert report["documents"]["ok"]


def test_missing_document_is_reported_and_never_regenerated(shop_with_documents):
    shop, root, pdf, _ = shop_with_documents
    pdf.unlink()
    report = startup_check(shop.db, root)
    assert not report["ok"]
    assert report["documents"]["missing"] == [{"relative_path": "quotations/test.pdf"}]
    assert not pdf.exists()
    assert any("missing official document" in error for error in report["errors"])


def test_tampered_document_is_reported_as_a_hash_mismatch(shop_with_documents):
    shop, root, pdf, _ = shop_with_documents
    original = shop.db.scalar(
        "SELECT sha256 FROM document_exports WHERE relative_path='quotations/test.pdf'"
    )
    content = bytearray(pdf.read_bytes())
    content[-1] ^= 1  # Same size, different bytes.
    pdf.write_bytes(content)
    report = startup_check(shop.db, root)
    assert not report["ok"]
    assert report["documents"]["mismatched"] == [{
        "relative_path": "quotations/test.pdf",
        "expected_sha256": original,
        "reason": "hash mismatch",
    }]
    assert report["documents"]["checked"] == 1


def test_missing_documents_root_and_leftover_temporary_files(shop_with_documents, tmp_path):
    shop, root, _, _ = shop_with_documents
    report = startup_check(shop.db, tmp_path / "absent")
    assert not report["ok"]
    assert report["documents"]["skipped"] == "managed documents root is missing"

    temporary = root / ".tmp"
    temporary.mkdir()
    (temporary / "unfinished.pdf").write_bytes(b"partial render")
    report = startup_check(shop.db, root)
    assert report["ok"]
    assert report["documents"]["temporary_files"] == 1
    assert report["warnings"]


def test_outdated_and_newer_schemas_are_refused(shop_with_documents, tmp_path, monkeypatch):
    shop, root, _, _ = shop_with_documents
    with shop.db.transaction() as conn:
        conn.execute("INSERT INTO schema_migrations(version,name) VALUES(99,'future')")
    report = startup_check(shop.db, root)
    assert not report["ok"]
    assert report["database"]["schema_state"] == "newer"
    assert report["documents"]["skipped"] == "database schema does not match this version"

    old = Database(tmp_path / "old.db")
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", [m for m in MIGRATIONS if m.version < 10])
        old.initialize()
    report = startup_check(old, None)
    assert not report["ok"]
    assert report["database"]["schema_state"] == "outdated"


def test_missing_database_and_corrupt_database_are_reported(tmp_path):
    report = startup_check(Database(tmp_path / "absent.db"), None)
    assert not report["ok"]
    assert report["database"]["present"] is False
    assert report["errors"] == ["database file is missing"]
    assert report["documents"]["skipped"] == "database is unavailable"

    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a sqlite database" * 200)
    report = startup_check(Database(bad), None)
    assert not report["ok"]
    assert report["database"]["present"] is True
    assert report["database"]["error"]


def test_foreign_key_violation_is_reported(shop_with_documents):
    shop, root, _, _ = shop_with_documents
    with sqlite3.connect(shop.db.path) as raw:  # A connection with FK checks off.
        raw.execute("PRAGMA foreign_keys = OFF")
        raw.execute("INSERT INTO user_roles(user_id,role_id) VALUES(?,999)", (shop.owner,))
    report = startup_check(shop.db, root)
    assert not report["ok"]
    assert report["database"]["foreign_keys_ok"] is False
    assert "database foreign key check failed" in report["errors"]


def test_document_references_without_a_root_are_not_checked(shop_with_documents):
    shop, _, _, _ = shop_with_documents
    report = startup_check(shop.db, None)
    assert report["ok"]
    assert report["documents"]["checked"] == 0
    assert report["documents"]["skipped"] == "no managed documents root was provided"
