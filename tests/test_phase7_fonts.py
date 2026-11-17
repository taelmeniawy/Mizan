"""Focused verification of the owner-approved Noto Naskh Arabic bundle.

This checks the pinned package assets, OFL license retention, Qt font loading,
and actual PDF embedding/text extraction.  It does not create golden images or
approve a template, and it does not exercise a physical printer.
"""

from __future__ import annotations

import hashlib

import pytest
from PySide6.QtGui import QFontDatabase

from mizan.quotation_export import QuotationExportService
from mizan.rendering import (
    A4QuotationRenderer,
    BUNDLED_FONT_FAMILY,
    BUNDLED_FONT_LICENSE,
    BUNDLED_FONT_VERSION,
    RendererError,
    bundled_font_dir,
    load_bundled_fonts,
)
from pdf_probe import read_pdf
from phase7_support import build_quote_shop


PINNED_COMMIT = "8386d579f0fc5064dc8b66c6b5a9382f3822b82d"
FONT_VERSION = "2.000"
EXPECTED_FILES = {
    "NotoNaskhArabic-Regular.ttf": "d96f0f0f0a3f2a5ea091106c248170bbeac23380ff7f64c38621ddd4cd21c05a",
    "NotoNaskhArabic-Bold.ttf": "90e63a98ae4011c98531f8c752138a25ea1268dba679691b665c6dc12229828c",
    "LICENCE": "6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2",
}
ARABIC_TEXT = "سلك كهربائي عربي طويل للاختبار"


@pytest.fixture()
def render_shop(db, tmp_path):
    shop = build_quote_shop(db)
    export = QuotationExportService(db, documents_root=tmp_path / "documents")
    return shop, export


def test_pinned_font_package_contains_only_regular_bold_and_license():
    assert BUNDLED_FONT_VERSION == FONT_VERSION
    assert BUNDLED_FONT_LICENSE == "OFL-1.1"
    font_dir = bundled_font_dir()
    assert font_dir.is_dir()
    names = {entry.name for entry in font_dir.iterdir()}
    assert names == set(EXPECTED_FILES) | {"SOURCE.md"}

    for name, expected_hash in EXPECTED_FILES.items():
        data = font_dir.joinpath(name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected_hash

    license_text = font_dir.joinpath("LICENCE").read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE Version 1.1" in license_text

    source_text = font_dir.joinpath("SOURCE.md").read_text(encoding="utf-8")
    assert PINNED_COMMIT in source_text
    assert FONT_VERSION in source_text
    assert "github.com/notofonts/NotoNaskhArabic" in source_text


def test_qt_loads_the_bundled_noto_naskh_arabic_family():
    family = load_bundled_fonts()
    assert family == BUNDLED_FONT_FAMILY == "Noto Naskh Arabic"
    assert family in QFontDatabase.families()
    assert A4QuotationRenderer()._font_family == family
    with pytest.raises(RendererError):
        A4QuotationRenderer(font_family="Arial")


def test_pdf_embeds_approved_font_and_extracts_complete_arabic(render_shop):
    shop, export = render_shop
    quote = shop.create_quote(
        lines=[shop.line(description=ARABIC_TEXT)],
        notes="ملاحظة تجريبية",
        terms="شروط تجريبية",
    )
    result = export.issue_official(quote, actor_id=shop.shop.manager)
    report = read_pdf(export.store.resolve(result["relative_path"]))
    text = report.normalized_text

    assert any("NotoNaskhArabic-0-400" in name for name in report.font_names)
    assert any("NotoNaskhArabic-0-700" in name for name in report.font_names)
    assert report.embedded_font_count >= 1
    assert report.has_to_unicode
    assert ARABIC_TEXT in text
    assert "الإجمالي المستحق" in text
    assert "ملاحظات" in text
    assert "الشروط" in text
    assert "عرض سعر" in text
