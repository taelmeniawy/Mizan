"""Verified local backup bundles; restore only into a new directory.

Bundles contain only the database snapshot and files
referenced by its exports/snapshots, never temporary renders or unrelated files.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .auth import PermissionService
from .backup_encryption import EncryptionError, seal, unseal
from .db import Database
from .document_store import sha256_file
from .documents import referenced_files
from .migrations import MIGRATIONS


class BackupError(ValueError):
    pass


def _notify(progress, stage: str) -> None:
    if progress is not None:
        progress(stage)


def _publish(stage: Path, destination: Path) -> None:
    # Windows scanners/sync clients can briefly hold a freshly verified file.
    for attempt in range(4):
        if destination.exists():
            raise BackupError("destination already exists; choose a new directory")
        try:
            stage.rename(destination)
            return
        except PermissionError:
            if os.name != "nt" or attempt == 3:
                raise
            time.sleep(0.1 * (attempt + 1))


def _path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise BackupError("invalid bundle path")
    parts = PurePosixPath(relative)
    if parts.is_absolute() or any(p in (".", "..") for p in relative.split("/")):
        raise BackupError("invalid bundle path")
    target = (root / relative).resolve()
    if root.resolve() not in target.parents:
        raise BackupError("bundle path escapes its root")
    return target


def _read_db(path: Path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def _check_database(path: Path) -> None:
    with closing(_read_db(path)) as conn:
        if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise BackupError("database integrity check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise BackupError("database foreign key check failed")
        versions = conn.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()
        if versions != [(m.version, m.name) for m in MIGRATIONS]:
            raise BackupError("unsupported database schema version")


def _references(path: Path) -> dict[str, dict]:
    with closing(_read_db(path)) as conn:
        files = referenced_files(conn, BackupError)
    return {"documents/" + relative: metadata for relative, metadata in files.items()}


def _check_file(path: Path, metadata: dict) -> None:
    if not path.is_file() or path.stat().st_size != metadata["size"]:
        raise BackupError(f"missing file or size mismatch: {path.name}")
    if sha256_file(path) != metadata["sha256"]:
        raise BackupError(f"file hash mismatch: {path.name}")


class BackupService:
    def __init__(self, db: Database, documents_root: str | Path):
        self.db = db
        self.documents_root = Path(documents_root).resolve()

    def _destination(self, destination: str | Path) -> Path:
        destination = Path(destination).resolve()
        if destination.exists():
            raise BackupError("destination already exists; choose a new directory")
        if destination == self.documents_root or self.documents_root in destination.parents:
            raise BackupError("destination must be outside managed documents")
        if destination == self.db.path.resolve() or destination in self.db.path.resolve().parents:
            raise BackupError("destination overlaps the active database")
        destination.parent.mkdir(parents=True, exist_ok=True)
        return destination

    def create(self, destination: str | Path, *, actor_id: int, key: bytes | None = None,
               progress=None) -> Path:
        PermissionService(self.db).require(actor_id, "backup_database")
        try:
            if key is None:
                result = self._create(destination, progress=progress)
            else:
                result = self._destination(destination)
                with tempfile.TemporaryDirectory() as local, tempfile.TemporaryDirectory(
                    prefix=".mizan-encrypted-", dir=result.parent
                ) as staging:
                    snapshot = self._create(Path(local) / "snapshot", progress=progress)
                    _notify(progress, "encrypt")
                    encrypted = Path(staging) / "backup.mizan"
                    seal(snapshot, encrypted, key)
                    _notify(progress, "verify")
                    self.verify(encrypted, key=key)
                    _notify(progress, "publish")
                    _publish(encrypted, result)
        except (OSError, sqlite3.Error, BackupError, EncryptionError) as exc:
            self._audit("BACKUP_FAILED", actor_id, str(exc), destination)
            raise
        self._audit("BACKUP_CREATED", actor_id, "Verified local backup", result)
        return result

    def audit_event(self, event: str, actor_id: int, reason: str, destination) -> None:
        """Record a backup-lifecycle event in the audit trail."""
        self._audit(event, actor_id, reason, destination)

    def copy_to(self, bundle: str | Path, destination: str | Path, *, actor_id: int,
                key: bytes | None = None, progress=None) -> Path:
        """Copy an already verified bundle to a second (off-device) destination.

        The copy is verified again before publication, so a failed copy never
        leaves a usable-looking backup behind.  The source bundle is never used
        as a live database copy: it is the verified artifact produced by
        :meth:`create`.
        """
        PermissionService(self.db).require(actor_id, "backup_database")
        source = Path(bundle).resolve()
        try:
            self.verify(source, key=key)
            _notify(progress, "source")
            result = self._destination(destination)
            if result == source or source in result.parents or result in source.parents:
                raise BackupError("destination overlaps the source bundle")
            with tempfile.TemporaryDirectory(
                prefix=".mizan-copy-", dir=result.parent
            ) as temporary:
                stage = Path(temporary) / "copy"
                if source.is_dir():
                    shutil.copytree(source, stage)
                else:
                    shutil.copyfile(source, stage)
                _notify(progress, "copy")
                if source.is_dir():
                    self.verify(stage)
                else:
                    self.verify(stage, key=key)
                _notify(progress, "verify")
                _publish(stage, result)
        except (OSError, sqlite3.Error, BackupError, EncryptionError) as exc:
            self._audit("BACKUP_COPY_FAILED", actor_id, str(exc), destination)
            raise
        self._audit("BACKUP_COPIED", actor_id, "Verified off-device copy", result)
        return result

    def _audit(self, event: str, actor_id: int, reason: str, destination: str | Path) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO audit_events(event_type,entity_type,actor_user_id,reason,after_json) "
                "VALUES(?,'BACKUP',?,?,?)",
                (event, actor_id, reason, json.dumps({"destination": str(destination)})),
            )

    def _create(self, destination: str | Path, *, progress=None) -> Path:
        destination = self._destination(destination)
        # A sibling stage ensures the final rename stays on the same filesystem.
        with tempfile.TemporaryDirectory(prefix=".mizan-backup-", dir=destination.parent) as temporary:
            stage = Path(temporary) / "bundle"
            stage.mkdir()
            snapshot = stage / "mizan.db"
            _notify(progress, "database")
            with closing(self.db.connect()) as source, closing(sqlite3.connect(snapshot)) as target:
                source.backup(target, pages=256)
                target.execute("PRAGMA journal_mode=DELETE")
            _notify(progress, "verify")
            _check_database(snapshot)
            files = _references(snapshot)
            _notify(progress, "documents")
            for relative, metadata in files.items():
                source = _path(self.documents_root, relative.removeprefix("documents/"))
                target = _path(stage, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                _check_file(target, metadata)
            files["mizan.db"] = {"sha256": sha256_file(snapshot), "size": snapshot.stat().st_size}
            manifest = {"format": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                        "encrypted": False, "files": files}
            (stage / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            self.verify(stage)
            _notify(progress, "publish")
            _publish(stage, destination)
        return destination

    @staticmethod
    def verify(bundle: str | Path, *, key: bytes | None = None) -> dict:
        root = Path(bundle).resolve()
        if root.is_file() or key is not None:
            if not root.is_file():
                raise BackupError("expected an encrypted backup file")
            with tempfile.TemporaryDirectory() as local:
                try:
                    unseal(root, Path(local), key)
                except EncryptionError as exc:
                    raise BackupError(str(exc)) from exc
                return BackupService.verify(local) | {"encrypted": True}
        try:
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            if manifest["format"] != 1 or manifest["encrypted"] is not False:
                raise BackupError("unsupported backup format")
            files = manifest["files"]
            if not isinstance(files, dict) or "mizan.db" not in files:
                raise BackupError("database missing from manifest")
            for relative, metadata in files.items():
                _check_file(_path(root, relative), metadata)
            snapshot = _path(root, "mizan.db")
            _check_database(snapshot)
            references = _references(snapshot)
            if set(files) != {"mizan.db", *references}:
                raise BackupError("manifest does not match database document references")
            for relative, metadata in references.items():
                if files[relative] != metadata:
                    raise BackupError("manifest does not match document metadata")
            return manifest
        except (OSError, sqlite3.Error, KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
            raise BackupError(f"invalid backup: {exc}") from exc

    def restore(self, bundle: str | Path, destination: str | Path, *, actor_id: int, reason: str,
                key: bytes | None = None) -> Path:
        PermissionService(self.db).require(actor_id, "restore_database")
        if not reason.strip():
            raise BackupError("restore requires a reason")
        if Path(bundle).is_file() or key is not None:
            if not Path(bundle).is_file():
                raise BackupError("expected an encrypted backup file")
            with tempfile.TemporaryDirectory() as local:
                try:
                    unseal(Path(bundle), Path(local), key)
                except EncryptionError as exc:
                    raise BackupError(str(exc)) from exc
                return self._restore_directory(local, destination, actor_id, reason)
        return self._restore_directory(bundle, destination, actor_id, reason)

    def _restore_directory(self, bundle, destination, actor_id, reason):
        manifest = self.verify(bundle)
        destination = self._destination(destination)
        with tempfile.TemporaryDirectory(prefix=".mizan-restore-", dir=destination.parent) as temporary:
            stage = Path(temporary) / "bundle"
            stage.mkdir()
            for relative in manifest["files"]:
                target = _path(stage, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(_path(Path(bundle).resolve(), relative), target)
            (stage / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.verify(stage)  # Recheck copied bytes before publishing the restored directory.
            (stage / "manifest.json").unlink()  # A restored working DB will change after opening.
            (stage / "documents").mkdir(exist_ok=True)
            _publish(stage, destination)
        self._audit("DATABASE_RESTORED", actor_id, reason.strip(), destination)
        return destination
