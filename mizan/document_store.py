"""Managed storage for official document files.

The database stores only metadata and a relative path.  Files live under a
managed documents root, are installed atomically, and are verified by SHA-256
before a reprint or print.  A missing file is an error, never an invitation to
silently regenerate an official document.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from pathlib import Path

from .documents import DocumentError


_FOLDER_BY_TYPE = {
    "QUOTATION": "quotations",
    "SALES_INVOICE": "sales",
    "PURCHASE_DOC": "purchases",
    "STATEMENT": "statements",
    "REPORT": "reports",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DocumentStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    # ------------------------------------------------------------------
    # Paths and installation
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_segment(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
        return cleaned or "document"

    def relative_path(self, document_type: str, document_no: str) -> str:
        folder = _FOLDER_BY_TYPE.get(document_type, self._safe_segment(document_type.lower()))
        return f"{folder}/{self._safe_segment(document_no)}.pdf"

    def resolve(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise DocumentError("document path escapes the managed documents root")
        return candidate

    def new_temp_path(self, suffix: str = ".pdf") -> Path:
        temp_dir = self.root / ".tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        return temp_dir / f"{uuid.uuid4().hex}{suffix}"

    def install(self, temp_path: str | Path, relative_path: str) -> Path:
        temp_path = Path(temp_path)
        if not temp_path.is_file():
            raise DocumentError("rendered document file is missing")
        final_path = self.resolve(relative_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                os.rename(temp_path, final_path)
            else:
                # The staging file is in this managed store, on the same filesystem.
                os.link(temp_path, final_path)
                try:
                    temp_path.unlink()
                except OSError:
                    final_path.unlink()
                    raise
        except FileExistsError as exc:
            raise DocumentError(
                f"official document file already exists: {relative_path}; "
                "refusing to overwrite a historical document"
            ) from exc
        return final_path

    def remove(self, relative_path: str) -> None:
        try:
            self.resolve(relative_path).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise DocumentError(f"cannot remove document file: {exc}") from exc

    # ------------------------------------------------------------------
    # Content-addressed assets (logos)
    # ------------------------------------------------------------------

    def capture_logo(self, source_path: str | None) -> dict | None:
        """Copy a configured logo into content-addressed managed storage.

        Returns ``None`` when no usable logo file is configured.  The stored
        asset path is derived from the SHA-256 of the file, so a later change to
        the source path or its content cannot alter an issued snapshot's logo.
        """
        if not source_path:
            return None
        source = Path(source_path).expanduser()
        if not source.is_file():
            return None
        digest = sha256_file(source)
        suffix = source.suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
            suffix = ""
        relative_path = f"assets/logos/{digest}{suffix}"
        final_path = self.resolve(relative_path)
        if final_path.exists():
            if sha256_file(final_path) != digest:
                raise DocumentError(
                    "managed logo asset hash does not match its content-addressed path"
                )
            return {
                "sha256": digest,
                "relative_path": relative_path,
                "file_size": final_path.stat().st_size,
            }

        temp_path = self.new_temp_path(suffix)
        try:
            shutil.copyfile(source, temp_path)
            if sha256_file(temp_path) != digest:
                raise DocumentError("logo file changed while it was being captured")
            final_path.parent.mkdir(parents=True, exist_ok=True)
            if final_path.exists():
                if sha256_file(final_path) != digest:
                    raise DocumentError(
                        "managed logo asset hash does not match its content-addressed path"
                    )
            else:
                os.replace(temp_path, final_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
        return {
            "sha256": digest,
            "relative_path": relative_path,
            "file_size": final_path.stat().st_size,
        }

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify(self, relative_path: str, expected_sha256: str) -> Path:
        path = self.resolve(relative_path)
        if not path.is_file():
            raise DocumentError(
                "official document file is missing; restore it from backup. "
                "Mizan refuses to silently regenerate an official document."
            )
        actual = sha256_file(path)
        if actual != (expected_sha256 or "").strip().lower():
            raise DocumentError(
                "official document file hash does not match its metadata; "
                "restore it from backup instead of regenerating it"
            )
        return path
