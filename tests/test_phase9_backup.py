import json
import sqlite3

import pytest

from mizan.backup_encryption import generate_key

from mizan.accounting import AccountingService
from mizan.auth import PermissionDenied
from mizan.backup import BackupError, BackupService
from mizan.db import Database
from mizan.document_store import sha256_file
from mizan.documents import DocumentService
from phase6_support import build_shop


@pytest.fixture
def backup_shop(db, tmp_path):
    shop = build_shop(db)
    shop.purchase(10_000_000, 20_000_000)
    root = tmp_path / "documents"
    pdf = root / "quotations" / "test.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4\nbackup test document\n%%EOF")
    DocumentService(db).record_export(
        document_type="REPORT", document_id=1, export_type="PDF",
        relative_path="quotations/test.pdf", sha256=sha256_file(pdf), file_size=pdf.stat().st_size,
        template_version="TEST-1", renderer_version="TEST-1", generated_by=shop.owner,
    )
    return shop, BackupService(db, root), pdf


def test_live_backup_restore_documents_and_posting(backup_shop, tmp_path):
    shop, service, pdf = backup_shop
    # Keep an open WAL connection while backing up committed business data.
    live = shop.db.connect()
    try:
        bundle = service.create(tmp_path / "backup", actor_id=shop.owner)
    finally:
        live.close()
    manifest = service.verify(bundle)
    assert set(manifest["files"]) == {"mizan.db", "documents/quotations/test.pdf"}
    assert not manifest["encrypted"]
    # Later transactions must not change the historical snapshot.
    shop.purchase(1_000_000, 20_000_000)
    restored = service.restore(bundle, tmp_path / "restored", actor_id=shop.owner, reason="اختبار استعادة")
    assert (restored / "documents/quotations/test.pdf").read_bytes() == pdf.read_bytes()
    db = Database(restored / "mizan.db")
    db.initialize()
    assert db.scalar("SELECT COUNT(*) FROM purchase_invoices") == 1
    accounting = AccountingService(db)
    assert accounting.reconcile_inventory()["matched"]
    accounting.post_opening("2026-11-15", "1010", 1_000)
    assert accounting.account_balance("1010") == 1_000
    assert shop.accounting.account_balance("1010") == 0
    assert shop.db.scalar("SELECT COUNT(*) FROM audit_events WHERE event_type='DATABASE_RESTORED'") == 1
    service.verify(bundle)


def test_encrypted_recovery_offline_and_tamper_detection(backup_shop, tmp_path):
    shop, service, pdf = backup_shop
    key = generate_key()
    path = service.create(tmp_path / "manual.mizan", actor_id=shop.owner, key=key)
    encrypted = path.read_bytes()
    for private in (key, b"SQLite format 3", pdf.read_bytes(), b"quotations/test.pdf"):
        assert private not in encrypted
    manifest = service.verify(path, key=key)
    assert manifest["encrypted"] is True
    restored = service.restore(path, tmp_path / "recovered", key=key, actor_id=shop.owner, reason="اختبار")
    assert (restored / "documents/quotations/test.pdf").read_bytes() == pdf.read_bytes()
    assert AccountingService(Database(restored / "mizan.db")).reconcile_inventory()["matched"]

    for bad_key in (None, generate_key()):
        with pytest.raises(BackupError):
            service.restore(path, tmp_path / "rejected", actor_id=shop.owner, reason="اختبار", key=bad_key)
    damaged = bytearray(encrypted)
    damaged[len(damaged) // 2] ^= 1
    for payload in (damaged, encrypted[:-20]):
        path.write_bytes(payload)
        with pytest.raises(BackupError):
            service.restore(path, tmp_path / "rejected", actor_id=shop.owner, reason="اختبار", key=key)
    assert not (tmp_path / "rejected").exists()


@pytest.mark.parametrize("relative", ["mizan.db", "documents/quotations/test.pdf"])
def test_corruption_rejected_before_restore(backup_shop, tmp_path, relative):
    shop, service, _ = backup_shop
    bundle = service.create(tmp_path / "backup", actor_id=shop.owner)
    path = bundle / relative
    content = bytearray(path.read_bytes())
    content[-1] ^= 1  # Same size; hash verification is required.
    path.write_bytes(content)
    with pytest.raises(BackupError, match="hash mismatch"):
        service.restore(bundle, tmp_path / "restored", actor_id=shop.owner, reason="اختبار")
    assert not (tmp_path / "restored").exists()
    assert shop.accounting.reconcile_inventory()["matched"]


def test_failed_backup_preserves_previous_bundle(backup_shop, tmp_path):
    shop, service, pdf = backup_shop
    previous = service.create(tmp_path / "previous", actor_id=shop.owner)
    original = (previous / "manifest.json").read_bytes()
    pdf.write_bytes(b"corrupt")
    with pytest.raises(BackupError):
        service.create(tmp_path / "failed", actor_id=shop.owner)
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".mizan-backup-*"))
    assert (previous / "manifest.json").read_bytes() == original
    service.verify(previous)
    assert shop.db.scalar("SELECT COUNT(*) FROM audit_events WHERE event_type='BACKUP_CREATED'") == 1
    assert shop.db.scalar("SELECT COUNT(*) FROM audit_events WHERE event_type='BACKUP_FAILED'") == 1


def test_snapshot_assets_are_required_and_temporary_files_are_excluded(backup_shop, tmp_path):
    shop, service, _ = backup_shop
    logo = service.documents_root / "assets" / "logo.png"
    logo.parent.mkdir()
    logo.write_bytes(b"historical logo asset")
    DocumentService(shop.db).create_snapshot(
        document_type="REPORT", document_id=1,
        snapshot={"store": {"logo_asset": {"relative_path": "assets/logo.png",
                    "sha256": sha256_file(logo), "file_size": logo.stat().st_size}}},
    )
    temporary = service.documents_root / ".tmp"
    temporary.mkdir()
    (temporary / "unfinished.pdf").write_bytes(b"partial render")
    bundle = service.create(tmp_path / "backup", actor_id=shop.owner)
    assert "documents/assets/logo.png" in service.verify(bundle)["files"]
    assert not (bundle / "documents/.tmp").exists()
    logo.unlink()
    with pytest.raises(OSError):
        service.create(tmp_path / "failed", actor_id=shop.owner)
    assert not (tmp_path / "failed").exists()


def test_permissions_destinations_and_manifest_paths(backup_shop, tmp_path):
    shop, service, _ = backup_shop
    with pytest.raises(PermissionDenied):
        service.create(tmp_path / "forbidden", actor_id=shop.cashier)
    bundle = service.create(tmp_path / "backup", actor_id=shop.owner)
    with pytest.raises(PermissionDenied):
        service.restore(bundle, tmp_path / "forbidden", actor_id=shop.manager, reason="اختبار")
    with pytest.raises(BackupError, match="already exists"):
        service.restore(bundle, bundle, actor_id=shop.owner, reason="اختبار")
    with pytest.raises(BackupError, match="outside managed"):
        service.create(service.documents_root / "nested", actor_id=shop.owner)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["../escape"] = manifest["files"]["mizan.db"]
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(BackupError, match="invalid bundle path"):
        service.verify(bundle)


@pytest.mark.parametrize("sql, message", [
    ("UPDATE schema_migrations SET version=999 WHERE version=9", "unsupported database schema"),
    ("UPDATE user_roles SET role_id=999", "foreign key check"),
])
def test_database_checks_even_when_file_hash_is_updated(backup_shop, tmp_path, sql, message):
    shop, service, _ = backup_shop
    bundle = service.create(tmp_path / "backup", actor_id=shop.owner)
    snapshot = bundle / "mizan.db"
    with sqlite3.connect(snapshot) as conn:
        conn.execute(sql)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["mizan.db"] = {"sha256": sha256_file(snapshot), "size": snapshot.stat().st_size}
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(BackupError, match=message):
        service.verify(bundle)


def test_restore_rechecks_staged_files_before_publishing(backup_shop, tmp_path, monkeypatch):
    import mizan.backup as backup_module

    shop, service, _ = backup_shop
    bundle = service.create(tmp_path / "backup", actor_id=shop.owner)
    original_copy = backup_module.shutil.copyfile

    def corrupted_copy(source, destination):
        result = original_copy(source, destination)
        if destination.suffix == ".pdf":
            destination.write_bytes(b"interrupted copy")
        return result

    monkeypatch.setattr(backup_module.shutil, "copyfile", corrupted_copy)
    with pytest.raises(BackupError):
        service.restore(bundle, tmp_path / "restored", actor_id=shop.owner, reason="اختبار")
    assert not (tmp_path / "restored").exists()
    assert not list(tmp_path.glob(".mizan-restore-*"))
    assert shop.db.scalar("SELECT COUNT(*) FROM audit_events WHERE event_type='DATABASE_RESTORED'") == 0


@pytest.mark.parametrize("failures", [1, 4])
def test_windows_publish_retries_are_bounded(tmp_path, monkeypatch, failures):
    import mizan.backup as backup_module
    from pathlib import Path

    if backup_module.os.name != "nt":
        pytest.skip("Windows sharing violation retry")
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "data").write_bytes(b"verified data")
    destination = tmp_path / "published"
    original = Path.rename
    calls = []

    def rename(path, target):
        calls.append(target)
        if len(calls) <= failures:
            raise PermissionError("temporary sharing violation")
        return original(path, target)

    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(backup_module.time, "sleep", lambda seconds: None)
    if failures == 4:
        with pytest.raises(PermissionError):
            backup_module._publish(stage, destination)
        assert stage.exists() and not destination.exists()
        assert len(calls) == 4
    else:
        backup_module._publish(stage, destination)
        assert (destination / "data").read_bytes() == b"verified data"
        assert len(calls) == 2
