"""Phase 9 — scheduled backups, retention and off-device copies."""

import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

import mizan.backup as backup_module
from mizan.auth import PermissionDenied
from mizan.backup import BackupError, BackupService
from mizan.backup_encryption import generate_key
from mizan.backup_policy import RetentionPolicy, ScheduledBackups
from phase6_support import build_shop


@pytest.fixture()
def scheduled(db, tmp_path):
    shop = build_shop(db)
    documents = tmp_path / "documents"
    documents.mkdir()
    service = BackupService(db, documents)
    return shop, service, ScheduledBackups(service, tmp_path / "backups"), tmp_path / "backups"


def _clone_bundles(source: Path, directory: Path, names: list[str]) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    copies = []
    for name in names:
        target = directory / name
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copyfile(source, target)
        copies.append(target)
    return copies


def _days(count: int, start: date = date(2026, 9, 1)) -> list[str]:
    return [(start + timedelta(days=offset)).isoformat() for offset in range(count)]


def test_daily_and_monthly_run_is_idempotent(scheduled):
    shop, service, backups, root = scheduled
    result = backups.run(actor_id=shop.owner, today=date(2026, 11, 18))
    assert result["created"] == [str(root / "daily" / "2026-11-18")]
    assert result["copied"] == [str(root / "monthly" / "2026-11")]
    service.verify(root / "daily" / "2026-11-18")
    service.verify(root / "monthly" / "2026-11")

    again = backups.run(actor_id=shop.owner, today=date(2026, 11, 18))
    assert again["created"] == []
    assert again["copied"] == []
    assert len(again["skipped"]) == 2
    assert [path.name for path in backups.bundles("daily")] == ["2026-11-18"]
    assert [path.name for path in backups.bundles("monthly")] == ["2026-11"]


def test_daily_retention_keeps_the_newest_verified_bundles(scheduled):
    shop, service, backups, root = scheduled
    base = service.create(root / "daily" / "2026-08-01", actor_id=shop.owner)
    names = _days(35)
    _clone_bundles(base, root / "daily", names)

    assert len(backups.bundles("daily")) == 36
    removed = backups.prune()
    assert [path.name for path in removed] == ["2026-08-01", *names[:5]]
    remaining = [path.name for path in backups.bundles("daily")]
    assert len(remaining) == 30
    assert remaining[0] == names[5]
    assert remaining[-1] == "2026-10-05"
    assert "2026-08-01" not in remaining
    for name in remaining:
        service.verify(root / "daily" / name)


def test_monthly_retention_and_pre_upgrade_bundles_are_kept(scheduled):
    shop, service, backups, root = scheduled
    base = service.create(root / "daily" / "2026-08-01", actor_id=shop.owner)
    _clone_bundles(base, root / "monthly", [f"2025-{month:02d}" for month in range(1, 13)]
                   + ["2026-01"])
    pre = backups.create_pre_upgrade(actor_id=shop.owner, label="schema 10",
                                     now=datetime(2026, 11, 18, 3, 4, 5))
    assert pre == root / "pre-upgrade" / "20261118T030405-schema-10"
    service.verify(pre)

    backups.run(actor_id=shop.owner, today=date(2026, 11, 18))
    assert len(backups.bundles("monthly")) == 12
    assert backups.bundles("monthly")[-1].name == "2026-11"
    assert pre.is_dir() and (pre / "manifest.json").is_file()


def test_pruning_refuses_when_the_newest_bundle_is_corrupt(scheduled):
    shop, service, backups, root = scheduled
    base = service.create(root / "daily" / "2026-08-01", actor_id=shop.owner)
    names = _days(31)
    _clone_bundles(base, root / "daily", names)
    newest = root / "daily" / names[-1]
    (newest / "mizan.db").write_bytes(b"damaged backup")

    with pytest.raises(BackupError, match="mismatch"):
        backups.prune()
    assert len(backups.bundles("daily")) == 32  # base + 31 copies: nothing was deleted


def test_encrypted_schedule_verifies_before_pruning(scheduled):
    shop, service, backups, root = scheduled
    key = generate_key()
    result = backups.run(actor_id=shop.owner, today=date(2026, 11, 18), key=key)
    assert result["daily"].endswith(".mizan")
    assert Path(result["daily"]).is_file()
    assert service.verify(Path(result["daily"]), key=key)["encrypted"] is True

    base = Path(result["daily"])
    names = [name + ".mizan" for name in _days(31)]
    _clone_bundles(base, root / "daily", names)
    with pytest.raises(BackupError, match="recovery key"):
        backups.prune()
    assert len(backups.bundles("daily")) == 32
    removed = backups.prune(key=key)
    assert len(removed) == 2
    assert len(backups.bundles("daily")) == 30


def test_offsite_copy_is_verified_and_off_device(scheduled, tmp_path):
    shop, service, _, root = scheduled
    bundle = service.create(root / "daily" / "2026-11-18", actor_id=shop.owner)
    offsite = tmp_path / "external" / "offsite-2026-11-18"
    copied = service.copy_to(bundle, offsite, actor_id=shop.owner)
    assert copied == offsite.resolve()
    assert service.verify(copied)["files"] == service.verify(bundle)["files"]
    assert offsite.is_dir() and (offsite / "mizan.db").is_file()
    with pytest.raises(BackupError, match="already exists"):
        service.copy_to(bundle, offsite, actor_id=shop.owner)


def test_failed_offsite_copy_leaves_no_destination(scheduled, tmp_path, monkeypatch):
    shop, service, _, root = scheduled
    bundle = service.create(root / "daily" / "2026-11-18", actor_id=shop.owner)
    destination = tmp_path / "external" / "interrupted"

    def broken_copy(source, target, **kwargs):
        raise OSError("external drive disconnected")

    monkeypatch.setattr(backup_module.shutil, "copytree", broken_copy)
    with pytest.raises(OSError):
        service.copy_to(bundle, destination, actor_id=shop.owner)
    assert not destination.exists()
    assert not list(destination.parent.glob(".mizan-copy-*"))
    assert shop.db.scalar(
        "SELECT COUNT(*) FROM audit_events WHERE event_type='BACKUP_COPY_FAILED'"
    ) == 1
    assert shop.db.scalar(
        "SELECT COUNT(*) FROM audit_events WHERE event_type='BACKUP_COPIED'"
    ) == 0


def test_encrypted_offsite_copy_requires_the_right_key(scheduled, tmp_path):
    shop, service, _, _ = scheduled
    key = generate_key()
    bundle = service.create(tmp_path / "manual.mizan", actor_id=shop.owner, key=key)
    with pytest.raises(BackupError):
        service.copy_to(bundle, tmp_path / "wrong-key.mizan", actor_id=shop.owner,
                        key=generate_key())
    assert not (tmp_path / "wrong-key.mizan").exists()

    copied = service.copy_to(bundle, tmp_path / "copy.mizan", actor_id=shop.owner, key=key)
    assert service.verify(copied, key=key)["encrypted"] is True


def test_progress_reports_stages_and_supports_cancellation(scheduled, tmp_path):
    shop, service, _, root = scheduled
    stages = []
    bundle = service.create(root / "daily" / "2026-11-18", actor_id=shop.owner,
                            progress=stages.append)
    assert stages == ["database", "verify", "documents", "publish"]
    service.verify(bundle)

    def cancel(stage):
        if stage == "documents":
            raise RuntimeError("cancelled by the user")

    with pytest.raises(RuntimeError, match="cancelled"):
        service.create(root / "daily" / "2026-11-19", actor_id=shop.owner, progress=cancel)
    assert not (root / "daily" / "2026-11-19").exists()
    assert not list((root / "daily").glob(".mizan-backup-*"))
    assert shop.db.scalar(
        "SELECT COUNT(*) FROM audit_events WHERE event_type='BACKUP_CREATED'"
    ) == 1


def test_schedule_permissions_and_root_validation(scheduled, tmp_path):
    shop, service, backups, root = scheduled
    with pytest.raises(PermissionDenied):
        backups.run(actor_id=shop.cashier, today=date(2026, 11, 18))
    assert not root.exists()

    with pytest.raises(BackupError, match="outside managed documents"):
        ScheduledBackups(service, service.documents_root / "backups")
    with pytest.raises(BackupError, match="active database"):
        ScheduledBackups(service, service.db.path.parent)
    for policy in ({"daily": 0}, {"monthly": -1}, {"daily": "30"}):
        with pytest.raises(ValueError):
            RetentionPolicy(**policy)
