"""Shared A4 quotation renderer.

ADR-0004 Option B: QTextDocument + QTextTable with a focused paginator, using
Qt for both PDF output (QPdfWriter) and printing (QPrinter).  The same page
composer builds every page for both output paths.

No font is bundled and no brand font is selected.  The renderer uses the
platform's general system font unless a family is supplied by the caller;
font licensing/approval remains OQ-DOC-002 / T-DOC-005 work.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QMarginsF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QPageLayout,
    QPageSize,
    QPainter,
    QPdfWriter,
    QPen,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextLength,
    QTextTable,
    QTextTableCellFormat,
    QTextTableFormat,
)
from PySide6.QtPrintSupport import QPrinter


MM_TO_PT = 72.0 / 25.4
A4_WIDTH_PT = 210 * MM_TO_PT
A4_HEIGHT_PT = 297 * MM_TO_PT
MARGIN_PT = 15 * MM_TO_PT
CONTENT_WIDTH_PT = A4_WIDTH_PT - 2 * MARGIN_PT
CONTENT_HEIGHT_PT = A4_HEIGHT_PT - 2 * MARGIN_PT
FOOTER_HEIGHT_PT = 22.0
BODY_HEIGHT_PT = CONTENT_HEIGHT_PT - FOOTER_HEIGHT_PT - 4.0

TEMPLATE_VERSION = "A4-QUOTATION-V1"
RENDERER_VERSION = "PySide6-QTextDocument-QTextTable-Paginator/1"

_APP: QGuiApplication | None = None

_ALIGN_RIGHT = Qt.AlignRight | Qt.AlignAbsolute
_ALIGN_LEFT = Qt.AlignLeft | Qt.AlignAbsolute
_ALIGN_CENTER = Qt.AlignHCenter | Qt.AlignVCenter


class RendererError(ValueError):
    pass


def ensure_qt_app() -> QGuiApplication:
    """Create/reuse the Qt application and ensure a system font database."""
    global _APP
    if QGuiApplication.instance() is None:
        _APP = QGuiApplication([])
    app = QGuiApplication.instance()
    if not QFontDatabase.families():
        raise RendererError(
            "Qt has no system font database; Arabic rendering needs a Windows "
            "font database. Do not use QT_QPA_PLATFORM=offscreen in this environment."
        )
    return app


def format_egp(piastres: int) -> str:
    amount = Decimal(int(piastres)) / Decimal(100)
    return f"{amount:,.2f} EGP"


def format_quantity(micros: int) -> str:
    value = Decimal(int(micros)) / Decimal(1_000_000)
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_unit_price(micros: int) -> str:
    amount = Decimal(int(micros)) / Decimal(1_000_000)
    return f"{amount:,.2f}"


def normalize_arabic(text: str) -> str:
    """Normalize presentation forms and common Persian letter variants."""
    normalized = unicodedata.normalize("NFKC", text)
    replacements = {
        "\u06cc": "\u064a",  # Persian yeh -> Arabic yeh
        "\u06a9": "\u0643",  # Persian kaf -> Arabic kaf
        "\u06be": "\u0647",
        "\u06c1": "\u0647",
        "\u0640": "",        # tatweel
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    return normalized


def default_platform_font_family() -> str:
    """Pick an installed system font with reliable Arabic shaping/PDF text.

    This is a platform compatibility fallback, not an approved brand font or
    a bundled asset.  OQ-DOC-002 / T-DOC-005 remain open until a licensed font
    is approved.
    """
    ensure_qt_app()
    available = set(QFontDatabase.families())
    for candidate in ("Microsoft Sans Serif", "Tahoma", "Arial"):
        if candidate in available:
            return candidate
    return QFontDatabase.systemFont(QFontDatabase.GeneralFont).family()


@dataclass(frozen=True)
class RenderResult:
    pages: int
    page_width_pt: float
    page_height_pt: float


@dataclass(frozen=True)
class PagePlan:
    rows: tuple[dict, ...]
    include_summary: bool


class A4QuotationRenderer:
    def __init__(self, *, font_family: str | None = None, base_font_pt: float = 9.0):
        ensure_qt_app()
        self._font_family = font_family or default_platform_font_family()
        self._base_font_pt = base_font_pt
        self._table_headers = (
            "الصافي (EGP)", "خصم (EGP)", "سعر الوحدة (EGP)",
            "الكمية", "الوحدة", "الوصف", "م",
        )
        self._table_widths = (12, 10, 13, 9, 9, 40, 7)

    # ------------------------------------------------------------------
    # Fonts and text formats
    # ------------------------------------------------------------------

    def _font(self, size: float | None = None, *, bold: bool = False) -> QFont:
        if self._font_family:
            font = QFont(self._font_family)
        else:
            font = QFontDatabase.systemFont(QFontDatabase.GeneralFont)
        font.setPointSizeF(size if size is not None else self._base_font_pt)
        font.setBold(bold)
        return font

    def _char_format(self, size: float | None = None, *, bold: bool = False) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setFont(self._font(size, bold=bold))
        fmt.setForeground(QBrush(QColor("#111111")))
        return fmt

    @staticmethod
    def _block_format(alignment=_ALIGN_RIGHT, *, top: float = 0.0,
                      bottom: float = 0.0) -> QTextBlockFormat:
        fmt = QTextBlockFormat()
        fmt.setLayoutDirection(Qt.RightToLeft)
        fmt.setAlignment(alignment)
        fmt.setTopMargin(top)
        fmt.setBottomMargin(bottom)
        return fmt

    def _insert_paragraph(self, cursor: QTextCursor, text: str, *, size: float | None = None,
                          bold: bool = False, alignment=_ALIGN_RIGHT,
                          top: float = 0.0, bottom: float = 0.0,
                          first: bool = False) -> None:
        block = self._block_format(alignment, top=top, bottom=bottom)
        char = self._char_format(size, bold=bold)
        if first:
            cursor.setBlockFormat(block)
            cursor.setCharFormat(char)
            cursor.insertText(text, char)
        else:
            cursor.insertBlock(block, char)
            cursor.insertText(text, char)

    # ------------------------------------------------------------------
    # Page body construction
    # ------------------------------------------------------------------

    def _header(self, cursor: QTextCursor, snapshot: dict) -> None:
        store = snapshot.get("store") or {}
        store_name = (store.get("name_ar") or "Mizan").strip()
        self._insert_paragraph(
            cursor, store_name, size=15, bold=True, alignment=_ALIGN_CENTER, first=True,
        )
        contact = " — ".join(
            part for part in (store.get("address"), store.get("phone")) if part
        )
        if contact:
            self._insert_paragraph(cursor, contact, size=8, alignment=_ALIGN_CENTER,
                                   bottom=2)
        self._insert_paragraph(cursor, "عرض سعر", size=16, bold=True,
                               alignment=_ALIGN_CENTER, top=4, bottom=2)
        meta = f"رقم العرض: {snapshot.get('document_no', '')}"
        if snapshot.get("issue_date"):
            meta += f" — تاريخ الإصدار: {snapshot['issue_date']}"
        if snapshot.get("valid_until"):
            meta += f" — صالح حتى: {snapshot['valid_until']}"
        self._insert_paragraph(cursor, meta, size=8, alignment=_ALIGN_CENTER, bottom=3)

        customer = snapshot.get("customer") or {}
        customer_line = f"العميل: {customer.get('name_ar', '')}"
        if customer.get("code"):
            customer_line += f" ({customer['code']})"
        if customer.get("phone"):
            customer_line += f" — هاتف: {customer['phone']}"
        self._insert_paragraph(cursor, customer_line, size=8.5)
        if customer.get("address"):
            self._insert_paragraph(cursor, f"العنوان: {customer['address']}", size=8.5)
        project = snapshot.get("project") or {}
        if project:
            self._insert_paragraph(
                cursor,
                f"المشروع: {project.get('name_ar', '')} ({project.get('code', '')})",
                size=8.5, bottom=3,
            )

    def _table(self, cursor: QTextCursor, snapshot: dict, rows: tuple[dict, ...]) -> QTextTable:
        table_format = QTextTableFormat()
        table_format.setLayoutDirection(Qt.RightToLeft)
        table_format.setCellSpacing(0)
        table_format.setCellPadding(3)
        table_format.setBorder(0.5)
        table_format.setBorderBrush(QBrush(QColor("#999999")))
        table_format.setBorderStyle(QTextTableFormat.BorderStyle_Solid)
        table_format.setWidth(QTextLength(QTextLength.PercentageLength, 100))
        table_format.setHeaderRowCount(1)
        table_format.setColumnWidthConstraints([
            QTextLength(QTextLength.PercentageLength, width)
            for width in self._table_widths
        ])
        table = cursor.insertTable(len(rows) + 1, len(self._table_headers), table_format)

        header_cell_format = QTextTableCellFormat()
        header_cell_format.setBackground(QBrush(QColor("#EDEDED")))
        header_cell_format.setPadding(3)
        header_char = self._char_format(8.5, bold=True)
        header_block = self._block_format(_ALIGN_CENTER)
        for column, title in enumerate(self._table_headers):
            cell = table.cellAt(0, column)
            cell.setFormat(header_cell_format)
            cell_cursor = cell.firstCursorPosition()
            cell_cursor.setBlockFormat(header_block)
            cell_cursor.insertText(title, header_char)

        body_char = self._char_format(8.2)
        text_block = self._block_format(_ALIGN_RIGHT)
        number_block = self._block_format(_ALIGN_LEFT)
        center_block = self._block_format(_ALIGN_CENTER)
        for row_index, row in enumerate(rows, start=1):
            logical_values = (
                str(row.get("line_no", row_index)),
                str(row.get("description") or row.get("product_name") or ""),
                str(row.get("unit_name") or ""),
                format_quantity(int(row.get("quantity_micros", 0))),
                format_unit_price(int(row.get("final_unit_price_micros", 0))),
                format_unit_price(int(row.get("item_discount_piastres", 0))),
                format_unit_price(int(row.get("net_piastres", 0))),
            )
            values = tuple(reversed(logical_values))
            for column, value in enumerate(values):
                cell = table.cellAt(row_index, column)
                cell_cursor = cell.firstCursorPosition()
                if column in (4, 5):
                    block = text_block
                elif column == 6:
                    block = center_block
                else:
                    block = number_block
                cell_cursor.setBlockFormat(block)
                cell_cursor.insertText(value, body_char)
        return table

    def _summary(self, cursor: QTextCursor, snapshot: dict) -> None:
        totals = snapshot.get("totals") or {}
        cursor.movePosition(QTextCursor.End)
        self._insert_paragraph(cursor, "", size=4, top=4)
        lines = (
            ("الإجمالي قبل الخصم", int(totals.get("subtotal_piastres", 0))),
            ("خصم البنود", int(totals.get("item_discount_piastres", 0))),
            ("خصم العرض", int(totals.get("invoice_discount_piastres", 0))),
        )
        for label, value in lines:
            self._insert_paragraph(
                cursor, f"{label}: {format_egp(value)}", size=8.5,
                alignment=_ALIGN_RIGHT,
            )
        self._insert_paragraph(
            cursor,
            f"الإجمالي المستحق: {format_egp(int(totals.get('total_piastres', 0)))}",
            size=10, bold=True, alignment=_ALIGN_RIGHT, top=2, bottom=2,
        )
        if snapshot.get("notes"):
            self._insert_paragraph(cursor, f"ملاحظات: {snapshot['notes']}", size=8.5,
                                   top=3)
        if snapshot.get("terms"):
            self._insert_paragraph(cursor, f"الشروط: {snapshot['terms']}", size=8.5)

    def _body_document(self, snapshot: dict, rows: tuple[dict, ...],
                       include_summary: bool) -> QTextDocument:
        document = QTextDocument()
        document.setDefaultFont(self._font())
        document.setTextWidth(CONTENT_WIDTH_PT)
        cursor = QTextCursor(document)
        cursor.movePosition(QTextCursor.Start)
        self._header(cursor, snapshot)
        self._table(cursor, snapshot, rows)
        if include_summary:
            self._summary(cursor, snapshot)
        document.adjustSize()
        return document

    @staticmethod
    def _body_height(document: QTextDocument) -> float:
        return float(document.size().height())

    def _fits(self, document: QTextDocument) -> bool:
        return self._body_height(document) <= BODY_HEIGHT_PT + 0.5

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------

    def _layout_pages(self, snapshot: dict) -> list[tuple[PagePlan, QTextDocument]]:
        """Build page plans and their body documents once.

        The first page uses a measured row-height estimate, then each accepted
        page updates the estimate.  A narrower binary search is used only when
        the estimate does not fit, which keeps long documents from rebuilding
        the whole remaining document on every page.
        """
        rows = tuple(snapshot.get("lines") or ())
        if not rows:
            document = self._body_document(snapshot, (), True)
            return [(PagePlan((), True), document)]

        shell_height = self._body_height(self._body_document(snapshot, (), False))
        probe_count = min(5, len(rows))
        probe_height = self._body_height(
            self._body_document(snapshot, rows[:probe_count], False)
        )
        average_row = max(1.0, (probe_height - shell_height) / probe_count)
        estimate = max(
            1, int(max(0.0, BODY_HEIGHT_PT - shell_height) / average_row)
        )
        estimate = min(estimate, len(rows))

        pages: list[tuple[PagePlan, QTextDocument]] = []
        remaining = list(rows)
        while remaining:
            if len(remaining) <= estimate:
                last_document = self._body_document(snapshot, tuple(remaining), True)
                if self._fits(last_document):
                    pages.append((PagePlan(tuple(remaining), True), last_document))
                    remaining = []
                    break

            candidate_count = min(estimate, len(remaining))
            document = self._body_document(
                snapshot, tuple(remaining[:candidate_count]), False
            )
            if not self._fits(document):
                low, high = 1, candidate_count - 1
                best = 0
                best_document = None
                while low <= high:
                    middle = (low + high) // 2
                    candidate = self._body_document(
                        snapshot, tuple(remaining[:middle]), False
                    )
                    if self._fits(candidate):
                        best = middle
                        best_document = candidate
                        low = middle + 1
                    else:
                        high = middle - 1
                if best == 0:
                    candidate_count = 1
                    document = self._body_document(
                        snapshot, tuple(remaining[:1]), False
                    )
                else:
                    candidate_count = best
                    document = best_document
            pages.append(
                (PagePlan(tuple(remaining[:candidate_count]), False), document)
            )
            remaining = remaining[candidate_count:]

            height = self._body_height(document)
            average = max(1.0, (height - shell_height) / candidate_count)
            estimate = max(
                1, int(max(0.0, BODY_HEIGHT_PT - shell_height) / average)
            )
            estimate = min(estimate, max(1, len(remaining)))

        if pages and not pages[-1][0].include_summary:
            plan, document = pages[-1]
            merged = self._body_document(snapshot, plan.rows, True)
            if self._fits(merged):
                pages[-1] = (PagePlan(plan.rows, True), merged)
            elif len(plan.rows) > 1:
                # Keep the summary on the final page and move one row there;
                # this avoids an otherwise blank summary-only page.
                kept_rows = plan.rows[:-1]
                moved_rows = plan.rows[-1:]
                kept_document = self._body_document(snapshot, kept_rows, False)
                moved_document = self._body_document(snapshot, moved_rows, True)
                if self._fits(kept_document) and self._fits(moved_document):
                    pages[-1] = (PagePlan(kept_rows, False), kept_document)
                    pages.append((PagePlan(moved_rows, True), moved_document))
                else:
                    self._append_summary_only_page(snapshot, pages)
            else:
                self._append_summary_only_page(snapshot, pages)
        return pages

    def _append_summary_only_page(self, snapshot: dict,
                                  pages: list[tuple[PagePlan, QTextDocument]]) -> None:
        summary_only = self._body_document(snapshot, (), True)
        if not self._fits(summary_only):
            raise RendererError(
                "quotation summary is taller than one A4 page; "
                "shorten notes/terms or split the document"
            )
        pages.append((PagePlan((), True), summary_only))

    def paginate(self, snapshot: dict) -> list[PagePlan]:
        return [plan for plan, _document in self._layout_pages(snapshot)]

    # ------------------------------------------------------------------
    # Shared page composition
    # ------------------------------------------------------------------

    def _draw_page(self, painter: QPainter, snapshot: dict, plan: PagePlan,
                   document: QTextDocument, *, page_number: int,
                   total_pages: int) -> None:
        painter.save()
        painter.setClipRect(QRectF(MARGIN_PT, MARGIN_PT, CONTENT_WIDTH_PT, BODY_HEIGHT_PT))
        painter.translate(MARGIN_PT, MARGIN_PT)
        document.drawContents(
            painter, QRectF(0, 0, CONTENT_WIDTH_PT, BODY_HEIGHT_PT)
        )
        painter.restore()

        footer_top = MARGIN_PT + CONTENT_HEIGHT_PT - FOOTER_HEIGHT_PT
        painter.setPen(QPen(QColor("#777777"), 0.5))
        painter.drawLine(
            int(MARGIN_PT), int(footer_top),
            int(MARGIN_PT + CONTENT_WIDTH_PT), int(footer_top),
        )
        footer_font = self._font(7.5)
        painter.setFont(footer_font)
        painter.setPen(QPen(QColor("#555555")))
        footer = f"صفحة {page_number} من {total_pages} — {snapshot.get('document_no', '')}"
        painter.drawText(
            QRectF(MARGIN_PT, footer_top + 4, CONTENT_WIDTH_PT, FOOTER_HEIGHT_PT - 4),
            int(Qt.AlignCenter), footer,
        )

    # ------------------------------------------------------------------
    # PDF / printer outputs
    # ------------------------------------------------------------------

    def render_pdf(self, snapshot: dict, output_path: str | Path) -> RenderResult:
        ensure_qt_app()
        pages = self._layout_pages(snapshot)
        writer = QPdfWriter(str(output_path))
        writer.setPageSize(QPageSize(QPageSize.A4))
        writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Millimeter)
        writer.setResolution(72)
        writer.setTitle(f"عرض سعر {snapshot.get('document_no', '')}")
        writer.setCreator("Mizan")
        writer.setAuthor("Mizan")
        painter = QPainter(writer)
        if not painter.isActive():
            raise RendererError("could not start the PDF painter")
        try:
            for index, (plan, document) in enumerate(pages):
                if index:
                    writer.newPage()
                self._draw_page(
                    painter, snapshot, plan, document,
                    page_number=index + 1, total_pages=len(pages),
                )
        finally:
            painter.end()
        return RenderResult(len(pages), A4_WIDTH_PT, A4_HEIGHT_PT)

    def print_document(self, snapshot: dict, printer: QPrinter) -> RenderResult:
        ensure_qt_app()
        pages = self._layout_pages(snapshot)
        printer.setPageSize(QPageSize(QPageSize.A4))
        printer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Millimeter)
        printer.setFullPage(True)
        painter = QPainter(printer)
        if not painter.isActive():
            raise RendererError("could not start the printer painter")
        scale = 72.0 / float(printer.resolution() or 72)
        try:
            for index, (plan, document) in enumerate(pages):
                if index:
                    printer.newPage()
                painter.save()
                painter.scale(scale, scale)
                self._draw_page(
                    painter, snapshot, plan, document,
                    page_number=index + 1, total_pages=len(pages),
                )
                painter.restore()
        finally:
            painter.end()
        return RenderResult(len(pages), A4_WIDTH_PT, A4_HEIGHT_PT)


def page_rects_pt():
    """Expose the A4 geometry used by tests/documentation."""
    return {
        "page_width_pt": A4_WIDTH_PT,
        "page_height_pt": A4_HEIGHT_PT,
        "margin_pt": MARGIN_PT,
        "content_width_pt": CONTENT_WIDTH_PT,
        "content_height_pt": CONTENT_HEIGHT_PT,
        "body_height_pt": BODY_HEIGHT_PT,
        "footer_height_pt": FOOTER_HEIGHT_PT,
    }
