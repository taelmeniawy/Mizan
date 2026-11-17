"""Document export metadata and immutable document snapshots.

This module deliberately contains no renderer.  It owns the model half of the
A4/PDF requirement: what an export records, and the immutable snapshot taken
when a commercial document is issued.  Producing or validating a PDF/A4 page
remains blocked on ADR-0004.
"""

from __future__ import annotations

import json
import sqlite3
import string
from datetime import datetime, timezone

from .db import Database


DOCUMENT_TYPES = {"QUOTATION", "SALES_INVOICE", "PURCHASE_DOC", "STATEMENT", "REPORT"}
EXPORT_TYPES = {"PDF", "PRINT"}


class DocumentError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_sha256(value: str) -> str:
    value = (value or "").strip().lower()
    if len(value) != 64 or any(ch not in string.hexdigits for ch in value):
        raise DocumentError("sha256 must be 64 hexadecimal characters")
    return value


class DocumentService:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _validate_document(document_type: str, document_id: int) -> None:
        if document_type not in DOCUMENT_TYPES:
            raise DocumentError(f"unsupported document type: {document_type}")
        if int(document_id) <= 0:
            raise DocumentError("document_id must be positive")

    def record_export(self, *, document_type: str, document_id: int, export_type: str,
                      relative_path: str, sha256: str, file_size: int,
                      template_version: str, renderer_version: str,
                      generated_by: int, generated_at: str | None = None,
                      is_official: bool = False) -> int:
        """Record metadata for a file produced elsewhere (renderer/print layer).

        No file is opened or validated here.  The official-export uniqueness
        rule is enforced by the partial unique index in the database.
        """
        self._validate_document(document_type, document_id)
        if export_type not in EXPORT_TYPES:
            raise DocumentError(f"unsupported export type: {export_type}")
        relative_path = (relative_path or "").strip()
        if not relative_path:
            raise DocumentError("relative_path is required")
        sha256 = _validate_sha256(sha256)
        if file_size < 0:
            raise DocumentError("file_size cannot be negative")
        if not (template_version or "").strip() or not (renderer_version or "").strip():
            raise DocumentError("template_version and renderer_version are required")
        try:
            with self.db.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO document_exports(document_type,document_id,export_type,"
                    "relative_path,sha256,file_size,template_version,renderer_version,"
                    "generated_by,generated_at,is_official) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (document_type, document_id, export_type, relative_path, sha256,
                     file_size, template_version.strip(), renderer_version.strip(),
                     generated_by, generated_at or _now(), int(bool(is_official))),
                )
                return int(cur.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise DocumentError(f"cannot record document export: {exc}") from exc

    def official_export(self, document_type: str, document_id: int):
        with self.db.connect() as conn:
            return conn.execute(
                "SELECT * FROM document_exports WHERE document_type=? AND document_id=? "
                "AND is_official=1",
                (document_type, document_id),
            ).fetchone()

    def create_snapshot(self, *, document_type: str, document_id: int,
                        snapshot: dict, conn=None) -> int:
        self._validate_document(document_type, document_id)
        payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        if not payload.strip():
            raise DocumentError("snapshot cannot be empty")
        if conn is None:
            with self.db.transaction() as tx:
                return self._insert_snapshot(tx, document_type, document_id, payload)
        return self._insert_snapshot(conn, document_type, document_id, payload)

    @staticmethod
    def _insert_snapshot(conn, document_type: str, document_id: int, payload: str) -> int:
        cur = conn.execute(
            "INSERT INTO document_snapshots(document_type,document_id,snapshot_json,created_at) "
            "VALUES(?,?,?,?)",
            (document_type, document_id, payload, _now()),
        )
        return int(cur.lastrowid)

    def snapshot(self, document_type: str, document_id: int) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT snapshot_json FROM document_snapshots "
                "WHERE document_type=? AND document_id=?",
                (document_type, document_id),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["snapshot_json"])
