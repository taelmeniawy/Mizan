"""Startup integrity checks for the local database and managed documents.

Opening the application runs this check before any business screen is shown.
It never repairs or regenerates anything: it reports what is wrong so the owner
can restore verified data (see ``docs/BACKUP_RESTORE.md``).  A missing official
document stays missing; Mizan refuses to silently re-render it.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .document_store import DocumentStore, sha256_file
from .documents import DocumentError, referenced_files
from .migrations import MIGRATIONS


def _expected_schema() -> list[tuple[int, str]]:
    return [(migration.version, migration.name) for migration in MIGRATIONS]


def _schema_state(applied: list[tuple[int, str]], expected: list[tuple[int, str]]) -> str:
    if applied == expected:
        return "current"
    if len(applied) < len(expected) and applied == expected[: len(applied)]:
        return "outdated"
    if len(applied) > len(expected) and applied[: len(expected)] == expected:
        return "newer"
    return "unknown"


def startup_check(db, documents_root: str | Path | None = None) -> dict:
    """Verify the database and every referenced document without modifying them."""
    report: dict = {
        "ok": False,
        "database": {
            "path": str(Path(db.path)),
            "present": Path(db.path).is_file(),
            "integrity": None,
            "foreign_keys_ok": None,
            "schema": [],
            "schema_state": "unknown",
            "error": None,
        },
        "documents": {
            "root": str(documents_root) if documents_root is not None else None,
            "checked": 0,
            "ok": True,
            "skipped": None,
            "missing": [],
            "mismatched": [],
            "unreadable": [],
            "temporary_files": 0,
        },
        "warnings": [],
        "errors": [],
    }

    if not report["database"]["present"]:
        report["database"]["error"] = "database file is missing"
        report["errors"].append("database file is missing")
        report["documents"]["skipped"] = "database is unavailable"
        return report

    try:
        with closing(db.connect()) as conn:
            integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
            report["database"]["integrity"] = "ok" if integrity == ["ok"] else integrity
            if integrity != ["ok"]:
                report["errors"].append("database integrity check failed")

            foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchone()
            report["database"]["foreign_keys_ok"] = foreign_keys is None
            if foreign_keys is not None:
                report["errors"].append("database foreign key check failed")

            applied = [
                (row["version"], row["name"])
                for row in conn.execute(
                    "SELECT version,name FROM schema_migrations ORDER BY version"
                )
            ]
            report["database"]["schema"] = [list(item) for item in applied]
            state = _schema_state(applied, _expected_schema())
            report["database"]["schema_state"] = state
            if state == "outdated":
                report["errors"].append("database schema is outdated; migrations are required")
            elif state == "newer":
                report["errors"].append("database schema is newer than this application version")
            elif state == "unknown":
                report["errors"].append("unsupported database schema version")

            if state != "current":
                report["documents"]["skipped"] = "database schema does not match this version"
            else:
                _check_documents(conn, documents_root, report)
    except (OSError, sqlite3.Error) as exc:
        report["database"]["error"] = str(exc)
        report["errors"].append(f"cannot read the database: {exc}")
        report["documents"]["skipped"] = "database is unreadable"

    report["ok"] = not report["errors"]
    return report


def _check_documents(conn, documents_root, report: dict) -> None:
    documents = report["documents"]
    if documents_root is None:
        documents["skipped"] = "no managed documents root was provided"
        return
    root = Path(documents_root)
    documents["root"] = str(root)
    if not root.is_dir():
        documents["ok"] = False
        documents["skipped"] = "managed documents root is missing"
        report["errors"].append("managed documents root is missing")
        return

    try:
        references = referenced_files(conn)
    except (DocumentError, json.JSONDecodeError) as exc:
        documents["ok"] = False
        report["errors"].append(f"cannot read document references: {exc}")
        return

    store = DocumentStore(root)
    for relative, metadata in references.items():
        try:
            path = store.resolve(relative)
        except DocumentError:
            documents["unreadable"].append(
                {"relative_path": relative, "reason": "path escapes the managed root"}
            )
            continue
        if not path.is_file():
            documents["missing"].append({"relative_path": relative})
            continue
        try:
            size = path.stat().st_size
            digest = sha256_file(path)
        except OSError as exc:
            documents["unreadable"].append({"relative_path": relative, "reason": str(exc)})
            continue
        if size != metadata["size"]:
            documents["mismatched"].append(
                {"relative_path": relative, "expected_sha256": metadata["sha256"],
                 "reason": "size mismatch"}
            )
        elif digest != metadata["sha256"]:
            documents["mismatched"].append(
                {"relative_path": relative, "expected_sha256": metadata["sha256"],
                 "reason": "hash mismatch"}
            )
        else:
            documents["checked"] += 1

    for problem, label in (
        (documents["missing"], "missing official document"),
        (documents["mismatched"], "document hash/size mismatch"),
        (documents["unreadable"], "unreadable document"),
    ):
        if problem:
            report["errors"].append(f"{len(problem)} {label}(s)")
    documents["ok"] = not (
        documents["missing"] or documents["mismatched"] or documents["unreadable"]
    )

    temporary = root / ".tmp"
    if temporary.is_dir():
        documents["temporary_files"] = count = sum(
            1 for path in temporary.iterdir() if path.is_file()
        )
        if count:
            report["warnings"].append(
                f"{count} temporary file(s) left by an interrupted render"
            )
