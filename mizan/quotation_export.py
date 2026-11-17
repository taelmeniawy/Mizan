"""Official quotation PDF export, reprint, and print services.

The immutable snapshot is the source of truth.  A PDF is rendered once from a
content snapshot, installed atomically, hashed, and registered together with
the quotation's ISSUED status.  Reprints verify the stored file/hash and never
regenerate an official document.  Printing uses the same snapshot/layout path.
"""

from __future__ import annotations

import os
from pathlib import Path

from .db import Database
from .document_store import DocumentStore, sha256_file
from .documents import DocumentService
from .quotations import QuotationService
from .rendering import (
    A4QuotationRenderer,
    RENDERER_VERSION,
    TEMPLATE_VERSION,
    RendererError,
)


class QuotationExportError(ValueError):
    pass


def _default_documents_root(db: Database) -> Path:
    configured = os.environ.get("MIZAN_DOCUMENTS_DIR")
    if configured:
        return Path(configured)
    return db.path.parent / "documents"


class QuotationExportService:
    def __init__(self, db: Database, *, documents_root: str | Path | None = None,
                 renderer: A4QuotationRenderer | None = None):
        self.db = db
        self.documents = DocumentService(db)
        self.quotes = QuotationService(db)
        self.store = DocumentStore(documents_root or _default_documents_root(db))
        self.renderer = renderer or A4QuotationRenderer()

    def issue_official(self, quotation_id: int, *, actor_id: int | None = None) -> dict:
        """Render and issue the official quotation PDF atomically with status."""
        if actor_id is None:
            raise QuotationExportError(
                "an authorized user is required to issue an official document"
            )
        try:
            current = self.quotes.get_quotation(quotation_id)
            if current["status"] != "DRAFT":
                raise QuotationExportError("only a draft quotation can be issued")
            content = self.quotes.build_snapshot_content(
                quotation_id,
                template_version=TEMPLATE_VERSION,
                renderer_version=RENDERER_VERSION,
            )
        except QuotationExportError:
            raise
        except ValueError as exc:
            raise QuotationExportError(str(exc)) from exc

        logo_asset = None
        if content.get("store"):
            logo_asset = self.store.capture_logo(content["store"].get("logo_path"))
            content["store"]["logo_asset"] = logo_asset

        temp_path = self.store.new_temp_path()
        installed_relative: str | None = None
        try:
            render_result = self.renderer.render_pdf(content, temp_path)
            digest = sha256_file(temp_path)
            file_size = temp_path.stat().st_size
            relative_path = self.store.relative_path(
                "QUOTATION", content["document_no"]
            )
            self.store.install(temp_path, relative_path)
            installed_relative = relative_path
            export_id = self.quotes.issue_quotation(
                quotation_id,
                official_export={
                    "relative_path": relative_path,
                    "sha256": digest,
                    "file_size": file_size,
                    "template_version": TEMPLATE_VERSION,
                    "renderer_version": RENDERER_VERSION,
                    "generated_by": actor_id,
                },
                expected_snapshot=content,
                logo_asset=logo_asset,
                actor_id=actor_id,
            )
            return {
                "quotation_id": quotation_id,
                "document_no": content["document_no"],
                "export_id": export_id,
                "relative_path": relative_path,
                "sha256": digest,
                "file_size": file_size,
                "pages": render_result.pages,
                "page_width_pt": render_result.page_width_pt,
                "page_height_pt": render_result.page_height_pt,
                "template_version": TEMPLATE_VERSION,
                "renderer_version": RENDERER_VERSION,
            }
        except Exception:
            if installed_relative is not None:
                self.store.remove(installed_relative)
            raise
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def reprint_pdf(self, quotation_id: int) -> dict:
        """Return the existing official file after verifying its hash."""
        export = self.documents.official_export("QUOTATION", quotation_id)
        if export is None:
            raise QuotationExportError("quotation has no official PDF export")
        path = self.store.verify(export["relative_path"], export["sha256"])
        return {
            "quotation_id": quotation_id,
            "relative_path": export["relative_path"],
            "path": path,
            "sha256": export["sha256"],
            "file_size": export["file_size"],
            "template_version": export["template_version"],
            "renderer_version": export["renderer_version"],
        }

    def print_official(self, quotation_id: int, printer) -> dict:
        """Print from the immutable snapshot using the shared A4 layout.

        The official file must still exist and match its hash; a missing file
        is reported instead of silently regenerating it.
        """
        export = self.documents.official_export("QUOTATION", quotation_id)
        if export is None:
            raise QuotationExportError("quotation has no official PDF export")
        self.store.verify(export["relative_path"], export["sha256"])
        snapshot = self.documents.snapshot("QUOTATION", quotation_id)
        if snapshot is None:
            raise QuotationExportError("quotation has no document snapshot")
        try:
            result = self.renderer.print_document(snapshot, printer)
        except RendererError as exc:
            raise QuotationExportError(str(exc)) from exc
        return {
            "quotation_id": quotation_id,
            "pages": result.pages,
            "page_width_pt": result.page_width_pt,
            "page_height_pt": result.page_height_pt,
        }
