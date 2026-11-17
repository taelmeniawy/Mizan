"""Regression tests for per-line money formatting in the A4 quotation table.

The table's unit price is micro-EGP, while line discounts and net amounts are
piastres.  These cases previously reached the unit-price formatter and rendered
as 0.00; the summary was correct.
"""

from __future__ import annotations

import re

import pytest
from PySide6.QtGui import QTextTable

from mizan.rendering import (
    A4QuotationRenderer,
    RENDERER_VERSION,
    TEMPLATE_VERSION,
    format_egp,
    format_egp_amount,
)
from pdf_probe import normalize_arabic, read_pdf
from phase7_support import build_quote_shop


def _quote_with_amount_cases(shop):
    """Create 42 lines with deterministic values for the preview regressions."""
    lines = []
    for index in range(42):
        if index == 0:
            quantity, price, discount = 1_000_000, 12_500_000, 0
        elif index == 1:
            quantity, price, discount = 1_500_000, 14_750_000, 100
        elif index in (20, 41):
            quantity, price, discount = 2_000_000, 26_000_000, 0
        else:
            quantity, price, discount = 1_000_000, 10_000_000, 0
        lines.append(shop.line(
            description=f"بند اختبار {index + 1:02d}",
            quantity_micros=quantity,
            unit_price_micros=price,
            item_discount_piastres=discount,
            price_adjustment_reason="سعر اختبار",
        ))
    return shop.create_quote(lines=lines, notes="ملاحظات", terms="شروط")


@pytest.fixture()
def amount_snapshot(db):
    shop = build_quote_shop(db)
    quote = _quote_with_amount_cases(shop)
    snapshot = shop.quotes.build_snapshot_content(
        quote,
        template_version=TEMPLATE_VERSION,
        renderer_version=RENDERER_VERSION,
    )
    return A4QuotationRenderer(), snapshot


def _document_and_table(renderer: A4QuotationRenderer, snapshot: dict):
    document = renderer._body_document(
        snapshot, tuple(snapshot["lines"]), False,
    )
    for frame in document.rootFrame().childFrames():
        if isinstance(frame, QTextTable):
            return document, frame
    raise AssertionError("quotation table was not found in the body document")


def _cell(table: QTextTable, row: int, column: int) -> str:
    return table.cellAt(row, column).firstCursorPosition().block().text()


def test_line_net_and_discount_use_piastre_formatter(amount_snapshot):
    renderer, snapshot = amount_snapshot
    _document, table = _document_and_table(renderer, snapshot)

    # The table is RTL: column 0 is net, column 1 is discount,
    # column 2 is unit price, column 3 is quantity.  The column headers already
    # say (EGP), so the cells use the piastre formatter without a suffix.
    assert _cell(table, 1, 0) == "12.50"
    assert _cell(table, 1, 1) == "0.00"
    assert _cell(table, 1, 2) == "12.50"
    assert _cell(table, 1, 3) == "1"

    assert _cell(table, 2, 0) == "21.13"
    assert _cell(table, 2, 1) == "1.00"
    assert _cell(table, 2, 2) == "14.75"
    assert _cell(table, 2, 3) == "1.5"

    assert _cell(table, 21, 0) == "52.00"
    assert _cell(table, 42, 0) == "52.00"

    # The same piastre values, with the currency suffix used in summaries.
    assert format_egp(1250) == "12.50 EGP"
    assert format_egp(2113) == "21.13 EGP"
    assert format_egp(5200) == "52.00 EGP"


def test_line_values_reconcile_with_invoice_summary(amount_snapshot):
    _renderer, snapshot = amount_snapshot
    lines = snapshot["lines"]
    totals = snapshot["totals"]

    assert sum(line["item_discount_piastres"] for line in lines) == (
        totals["item_discount_piastres"]
    )
    assert sum(line["invoice_discount_alloc_piastres"] for line in lines) == (
        totals["invoice_discount_piastres"]
    )
    assert sum(line["net_piastres"] for line in lines) == totals["total_piastres"]
    assert (
        totals["subtotal_piastres"]
        - totals["item_discount_piastres"]
        - totals["invoice_discount_piastres"]
    ) == totals["total_piastres"]
    assert format_egp(sum(line["net_piastres"] for line in lines)) == (
        format_egp(totals["total_piastres"])
    )


def test_pdf_amounts_render_and_keep_pagination(amount_snapshot, tmp_path):
    renderer, snapshot = amount_snapshot
    output = tmp_path / "line-amounts.pdf"
    result = renderer.render_pdf(snapshot, output)
    report = read_pdf(output)

    assert result.pages == 3
    assert report.page_count == 3
    assert "12.50" in report.text
    assert "1.00" in report.text
    assert "21.13" in report.text
    assert report.text.count("52.00") >= 2


def _summary_text(renderer: A4QuotationRenderer, snapshot: dict, tmp_path) -> str:
    output = tmp_path / "summary-format.pdf"
    renderer.render_pdf(snapshot, output)
    report = read_pdf(output)
    return normalize_arabic(report.page_texts[-1])


def test_summary_amounts_follow_labels_in_one_ltr_run(amount_snapshot, tmp_path):
    """The RTL paragraph must not reorder the totals or their currency.

    The amount is inserted with its currency as one direction-isolated LTR
    run, so extracted text keeps "<amount> EGP" and never "EGP <amount>".
    """
    renderer, snapshot = amount_snapshot
    text = _summary_text(renderer, snapshot, tmp_path)
    totals = snapshot["totals"]
    pairs = (
        ("الإجمالي قبل الخصم", totals["subtotal_piastres"]),
        ("خصم البنود", totals["item_discount_piastres"]),
        ("خصم العرض", totals["invoice_discount_piastres"]),
        ("الإجمالي المستحق", totals["total_piastres"]),
    )

    # Isolation must not leak bidi control characters into copyable text.
    assert "\u200e" not in text
    assert "\u200f" not in text
    for label, piastres in pairs:
        token = f"{format_egp_amount(piastres)} EGP"
        assert token in text, (label, token)
        assert f"EGP {token.split(' ')[0]}" not in text, (label, token)
        assert re.search(re.escape(token) + r"\s+" + re.escape(label), text), (
            label,
            token,
        )


def test_money_values_keep_two_ascii_decimals(amount_snapshot, tmp_path):
    renderer, snapshot = amount_snapshot
    output = tmp_path / "money-format.pdf"
    renderer.render_pdf(snapshot, output)
    text = normalize_arabic(read_pdf(output).text)

    # No Arabic separators and no comma used as a decimal separator.
    assert "\u066b" not in text
    assert "\u066c" not in text
    assert re.search(r"\d,\d{2}(?!\d)", text) is None
    # Every currency token is a two-decimal ASCII amount followed by EGP.
    tokens = re.findall(r"\d{1,3}(?:,\d{3})*\.\d{2} EGP", text)
    assert len(tokens) >= 4
    for token in tokens:
        assert re.fullmatch(r"\d{1,3}(?:,\d{3})*\.\d{2} EGP", token)
    # Per-line values are unchanged by the summary fix.
    for value in ("12.50", "21.13", "52.00"):
        assert value in text


def test_rendered_totals_still_reconcile_with_line_amounts(amount_snapshot, tmp_path):
    renderer, snapshot = amount_snapshot
    lines = snapshot["lines"]
    totals = snapshot["totals"]
    item_discount = sum(line["item_discount_piastres"] for line in lines)
    invoice_discount = sum(line["invoice_discount_alloc_piastres"] for line in lines)
    net = sum(line["net_piastres"] for line in lines)

    assert totals["item_discount_piastres"] == item_discount
    assert totals["invoice_discount_piastres"] == invoice_discount
    assert totals["total_piastres"] == net
    assert totals["subtotal_piastres"] == net + item_discount + invoice_discount

    text = _summary_text(renderer, snapshot, tmp_path)
    for key in (
        "subtotal_piastres",
        "item_discount_piastres",
        "invoice_discount_piastres",
        "total_piastres",
    ):
        assert f"{format_egp_amount(totals[key])} EGP" in text
