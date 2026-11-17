"""Focused T-QUO-019 / AC-QUO-003 logo snapshot stability tests.

The logo files here are temporary synthetic images.  No store logo, brand
asset, font, or UI choice is introduced.  The renderer still does not draw a
logo; this slice only guarantees that the immutable snapshot records the
content-addressed logo state used at issue time.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage

from mizan.document_store import sha256_file
from mizan.quotation_export import QuotationExportService
from mizan.rendering import ensure_qt_app
from phase7_support import QuoteShop, build_quote_shop


@pytest.fixture()
def logo_shop(db, tmp_path) -> tuple[QuoteShop, QuotationExportService, Path]:
    ensure_qt_app()
    shop = build_quote_shop(db)
    export = QuotationExportService(db, documents_root=tmp_path / "documents")
    return shop, export, tmp_path


def _write_logo(path: Path, color: str) -> Path:
    image = QImage(4, 4, QImage.Format_RGB32)
    image.fill(QColor(color))
    assert image.save(str(path), "PNG")
    return path


def _set_store_logo(shop: QuoteShop, path: Path) -> None:
    with shop.db.transaction() as conn:
        conn.execute(
            "UPDATE store_profile SET logo_path=? WHERE id=1", (str(path),)
        )


def _issue(shop: QuoteShop, export: QuotationExportService) -> dict:
    quote = shop.create_quote(lines=[shop.line(quantity_micros=1_000_000)])
    result = export.issue_official(quote, actor_id=shop.shop.manager)
    snapshot = shop.documents.snapshot("QUOTATION", quote)
    assert snapshot is not None
    return {
        "quote": quote,
        "result": result,
        "snapshot": snapshot,
        "pdf_path": export.store.resolve(result["relative_path"]),
    }


def test_same_logo_path_content_change_does_not_touch_issued_document(logo_shop):
    shop, export, tmp_path = logo_shop
    logo_path = _write_logo(tmp_path / "logo.png", "#cc2222")
    _set_store_logo(shop, logo_path)

    old = _issue(shop, export)
    old_snapshot = old["snapshot"]
    old_asset = old_snapshot["store"]["logo_asset"]
    assert old_asset is not None
    assert old_asset["sha256"] == sha256_file(logo_path)
    assert old_asset["relative_path"].startswith("assets/logos/")
    old_asset_path = export.store.resolve(old_asset["relative_path"])
    assert old_asset_path.is_file()
    assert sha256_file(old_asset_path) == old_asset["sha256"]
    old_pdf_bytes = old["pdf_path"].read_bytes()

    # Overwrite the source at the same configured path with different content.
    _write_logo(logo_path, "#2244cc")
    new = _issue(shop, export)
    new_asset = new["snapshot"]["store"]["logo_asset"]
    assert new_asset is not None
    assert new_asset["sha256"] == sha256_file(logo_path)
    assert new_asset["sha256"] != old_asset["sha256"]
    assert new_asset["relative_path"] != old_asset["relative_path"]

    # The old snapshot, official file, and reprint are unchanged.
    assert shop.documents.snapshot("QUOTATION", old["quote"]) == old_snapshot
    assert old["pdf_path"].read_bytes() == old_pdf_bytes
    old_export = shop.documents.official_export("QUOTATION", old["quote"])
    assert old_export is not None
    assert old_export["sha256"] == old["result"]["sha256"]
    reprint = export.reprint_pdf(old["quote"])
    assert reprint["path"] == old["pdf_path"]
    assert reprint["sha256"] == old["result"]["sha256"]
    assert old["pdf_path"].read_bytes() == old_pdf_bytes

    # Both content-addressed logo assets remain available.
    assert old_asset_path.is_file()
    assert sha256_file(old_asset_path) == old_asset["sha256"]
    new_asset_path = export.store.resolve(new_asset["relative_path"])
    assert new_asset_path.is_file()
    assert sha256_file(new_asset_path) == new_asset["sha256"]


def test_logo_path_change_affects_new_snapshot_only(logo_shop):
    shop, export, tmp_path = logo_shop
    first_logo = _write_logo(tmp_path / "first-logo.png", "#118844")
    _set_store_logo(shop, first_logo)
    old = _issue(shop, export)
    old_snapshot = old["snapshot"]
    old_asset = old_snapshot["store"]["logo_asset"]
    assert old_asset is not None
    old_pdf_bytes = old["pdf_path"].read_bytes()

    second_logo = _write_logo(tmp_path / "second-logo.png", "#884411")
    _set_store_logo(shop, second_logo)
    new = _issue(shop, export)
    new_asset = new["snapshot"]["store"]["logo_asset"]
    assert new_asset is not None
    assert new_asset["sha256"] == sha256_file(second_logo)
    assert new_asset["sha256"] != old_asset["sha256"]

    assert shop.documents.snapshot("QUOTATION", old["quote"]) == old_snapshot
    assert old["pdf_path"].read_bytes() == old_pdf_bytes
    reprint = export.reprint_pdf(old["quote"])
    assert reprint["sha256"] == old["result"]["sha256"]
    assert reprint["path"] == old["pdf_path"]
