"""Phase 7 A4/PDF rendering, export, reprint, and shared printer-path tests.

The printer-path test writes through QPrinter's PDF output mode to verify that
PDF and printer use the same page composer.  It is not a physical A4 printer
test and does not cover T-DOC-009.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QPageLayout, QPageSize
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo

from mizan.document_store import sha256_file
from mizan.documents import DocumentError
from mizan.quotation_export import QuotationExportService, QuotationExportError
from mizan.rendering import default_platform_font_family, ensure_qt_app
from pdf_probe import normalize_arabic, read_pdf
from phase7_support import QuoteShop, build_quote_shop


ARABIC_DESCRIPTION = "سلك كهربائي عربي طويل للاختبار"


@pytest.fixture()
def render_shop(db, tmp_path) -> tuple[QuoteShop, QuotationExportService]:
    shop = build_quote_shop(db)
    export = QuotationExportService(db, documents_root=tmp_path / "documents")
    return shop, export


def _lines(shop: QuoteShop, count: int, *, description: str = ARABIC_DESCRIPTION) -> list[dict]:
    return [
        shop.line(description=f"{description} {index}", quantity_micros=1_000_000)
        for index in range(1, count + 1)
    ]


def _assert_a4(report) -> None:
    assert report.page_count >= 1
    for width, height in report.media_boxes:
        assert abs(width - 595.0) <= 1.0
        assert abs(height - 842.0) <= 1.0


def _pdf_printer(path: Path) -> QPrinter:
    printer = QPrinter(QPrinter.PrinterMode.ScreenResolution)
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(str(path))
    printer.setPageSize(QPageSize(QPageSize.A4))
    printer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Millimeter)
    printer.setResolution(300)
    return printer


def test_official_pdf_export_records_snapshot_hash_and_status(render_shop):
    shop, export = render_shop
    quote = shop.create_quote(lines=_lines(shop, 2), notes="ملاحظات", terms="شروط")
    result = export.issue_official(quote, actor_id=shop.shop.manager)

    quotation = shop.quotes.get_quotation(quote)
    assert quotation["status"] == "ISSUED"
    assert quotation["official_export_id"] == result["export_id"]
    export_row = shop.documents.official_export("QUOTATION", quote)
    assert export_row is not None
    assert export_row["is_official"] == 1
    assert export_row["sha256"] == result["sha256"]
    assert export_row["relative_path"] == result["relative_path"]
    assert export_row["template_version"] == "A4-QUOTATION-V1"
    assert export_row["renderer_version"].startswith("PySide6-QTextDocument")

    path = export.store.resolve(result["relative_path"])
    assert path.is_file()
    assert sha256_file(path) == result["sha256"]
    assert path.stat().st_size == result["file_size"]

    snapshot = shop.documents.snapshot("QUOTATION", quote)
    assert snapshot is not None
    assert snapshot["official_export"]["sha256"] == result["sha256"]
    assert snapshot["lines"][0]["description"].startswith(ARABIC_DESCRIPTION)
    assert snapshot["template_version"] == "A4-QUOTATION-V1"

    report = read_pdf(path)
    _assert_a4(report)
    assert report.page_count == result["pages"]

    original_bytes = path.read_bytes()
    reprint = export.reprint_pdf(quote)
    assert reprint["path"] == path
    assert reprint["sha256"] == result["sha256"]
    assert path.read_bytes() == original_bytes


def test_pdf_text_extraction_and_arabic_shaping(render_shop):
    shop, export = render_shop
    quote = shop.create_quote(
        lines=_lines(shop, 1), notes="ملاحظة تجريبية", terms="شروط تجريبية",
    )
    result = export.issue_official(quote, actor_id=shop.shop.manager)
    report = read_pdf(export.store.resolve(result["relative_path"]))
    text = report.normalized_text

    assert "عرض سعر" in text
    assert "متجر ميزان" in text
    assert "تجزئة" in text
    assert ARABIC_DESCRIPTION in text
    assert "ملاحظات" in text
    assert "الشروط" in text
    assert "الإجمالي المستحق" in text
    assert "EGP" in text
    assert result["document_no"] in text
    assert "صفحة" in text
    assert "1 من 1" in text
    assert default_platform_font_family() == "Noto Naskh Arabic"


def test_multipage_repeated_headers_and_row_pagination(render_shop):
    shop, export = render_shop
    quote = shop.create_quote(lines=_lines(shop, 60))
    result = export.issue_official(quote, actor_id=shop.shop.manager)
    report = read_pdf(export.store.resolve(result["relative_path"]))
    snapshot = shop.documents.snapshot("QUOTATION", quote)
    plans = export.renderer.paginate(snapshot)

    assert report.page_count == result["pages"] == len(plans) >= 2
    _assert_a4(report)
    assert sum(len(plan.rows) for plan in plans) == 60
    seen = [row["line_no"] for plan in plans for row in plan.rows]
    assert seen == list(range(1, 61))
    assert all(plan.rows for plan in plans)
    assert [plan.include_summary for plan in plans] == [False] * (len(plans) - 1) + [True]

    # The table header is rebuilt on every page; the footer is page-numbered.
    assert report.normalized_text.count("الوصف") == len(plans)
    assert report.normalized_text.count("صفحة ") == len(plans)
    for index, page_text in enumerate(report.page_texts):
        normalized = normalize_arabic(page_text)
        assert "الوصف" in normalized
        if index < len(plans) - 1:
            assert "الإجمالي المستحق" not in normalized
        else:
            assert "الإجمالي المستحق" in normalized


def test_100_plus_line_document_has_no_row_loss(render_shop):
    shop, export = render_shop
    line_count = 120
    quote = shop.create_quote(lines=_lines(shop, line_count))
    result = export.issue_official(quote, actor_id=shop.shop.manager)
    report = read_pdf(export.store.resolve(result["relative_path"]))
    snapshot = shop.documents.snapshot("QUOTATION", quote)
    plans = export.renderer.paginate(snapshot)

    _assert_a4(report)
    assert report.page_count == result["pages"] == len(plans) >= 4
    assert [plan.include_summary for plan in plans] == [False] * (len(plans) - 1) + [True]
    assert all(plan.rows for plan in plans)

    seen = [row["line_no"] for plan in plans for row in plan.rows]
    assert len(seen) == line_count
    assert len(set(seen)) == line_count
    assert seen == list(range(1, line_count + 1))

    # Every page carries the repeated table header and a page-numbered footer.
    assert report.normalized_text.count("الوصف") == len(plans)
    assert report.normalized_text.count("صفحة ") == len(plans)


def test_stable_pagination_and_snapshot(render_shop, tmp_path):
    shop, export = render_shop
    quote = shop.create_quote(lines=_lines(shop, 40))
    export.issue_official(quote, actor_id=shop.shop.manager)
    snapshot = shop.documents.snapshot("QUOTATION", quote)
    plans_one = export.renderer.paginate(snapshot)
    plans_two = export.renderer.paginate(snapshot)

    def shape(plans):
        return [
            (tuple(row["line_no"] for row in plan.rows), plan.include_summary)
            for plan in plans
        ]

    assert shape(plans_one) == shape(plans_two)
    assert snapshot == shop.documents.snapshot("QUOTATION", quote)

    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    export.renderer.render_pdf(snapshot, first)
    export.renderer.render_pdf(snapshot, second)
    report_one = read_pdf(first)
    report_two = read_pdf(second)
    assert report_one.page_count == report_two.page_count
    assert report_one.media_boxes == report_two.media_boxes
    assert report_one.normalized_text == report_two.normalized_text


def test_revision_official_export_supersedes_previous(render_shop):
    shop, export = render_shop
    source = shop.create_quote(lines=_lines(shop, 1))
    source_result = export.issue_official(source, actor_id=shop.shop.manager)
    revision = shop.quotes.create_revision(source, actor_id=shop.shop.manager)
    revision_result = export.issue_official(revision, actor_id=shop.shop.manager)

    assert shop.quotes.get_quotation(source)["status"] == "SUPERSEDED"
    assert shop.quotes.get_quotation(revision)["status"] == "ISSUED"
    assert revision_result["relative_path"] != source_result["relative_path"]
    assert export.store.resolve(source_result["relative_path"]).is_file()
    assert export.store.resolve(revision_result["relative_path"]).is_file()
    assert shop.documents.official_export("QUOTATION", source) is not None
    assert shop.documents.official_export("QUOTATION", revision) is not None
    report = read_pdf(export.store.resolve(revision_result["relative_path"]))
    assert revision_result["document_no"] in report.normalized_text


def test_missing_official_pdf_is_never_regenerated(render_shop, monkeypatch):
    shop, export = render_shop
    quote = shop.create_quote(lines=_lines(shop, 1))
    result = export.issue_official(quote, actor_id=shop.shop.manager)
    path = export.store.resolve(result["relative_path"])
    path.unlink()

    def forbidden(*args, **kwargs):
        raise AssertionError("reprint/print must not regenerate an official PDF")

    monkeypatch.setattr(export.renderer, "render_pdf", forbidden)
    with pytest.raises(DocumentError):
        export.reprint_pdf(quote)
    with pytest.raises(DocumentError):
        export.print_official(quote, _pdf_printer(Path("unused.pdf")))
    assert not path.exists()


def test_official_issue_requires_an_authorized_actor(render_shop, tmp_path):
    shop, export = render_shop
    quote = shop.create_quote(lines=_lines(shop, 1))
    with pytest.raises(QuotationExportError):
        export.issue_official(quote, actor_id=None)
    assert not list((tmp_path / "documents").glob("quotations/*.pdf"))
    assert shop.documents.official_export("QUOTATION", quote) is None


def test_issue_failure_cleans_file_and_does_not_leave_export(render_shop, monkeypatch):
    shop, export = render_shop
    quote = shop.create_quote(lines=_lines(shop, 1))

    def fail(*args, **kwargs):
        raise RuntimeError("simulated issue failure")

    monkeypatch.setattr(export.quotes, "issue_quotation", fail)
    with pytest.raises(RuntimeError):
        export.issue_official(quote, actor_id=shop.shop.manager)
    assert not list(export.store.root.glob("quotations/*.pdf"))
    assert shop.documents.official_export("QUOTATION", quote) is None


def test_printer_path_uses_the_same_page_layout(render_shop, tmp_path):
    shop, export = render_shop
    quote = shop.create_quote(lines=_lines(shop, 30))
    result = export.issue_official(quote, actor_id=shop.shop.manager)
    printer_path = tmp_path / "shared-layout.pdf"
    printer = _pdf_printer(printer_path)

    printed = export.print_official(quote, printer)
    report = read_pdf(printer_path)
    assert printed["pages"] == report.page_count == result["pages"]
    _assert_a4(report)
    assert ARABIC_DESCRIPTION in report.normalized_text
    assert "الإجمالي المستحق" in report.normalized_text
    assert any("NotoNaskhArabic" in name for name in report.font_names)


def test_real_a4_printer_is_not_available_here():
    ensure_qt_app()
    physical = [
        printer for printer in QPrinterInfo.availablePrinters()
        if not any(marker in printer.printerName().lower() for marker in (
            "print to pdf", "xps", "fax", "onenote",
        ))
    ]
    if not physical:
        pytest.skip(
            "no physical A4 printer is available; T-DOC-009 remains open "
            "and Microsoft Print to PDF is not a substitute"
        )
    # A physical printer exists, but this suite does not send an unattended
    # print job. The manual T-DOC-009 acceptance check remains required.
    pytest.skip("physical printer detected; run the manual T-DOC-009 check")
