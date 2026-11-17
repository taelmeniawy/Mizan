"""Phase 7 renderer-independent quotation tests.

The official-export rows used here are test doubles for the future ADR-0004
renderer.  No PDF is generated, extracted, measured, or printed, so these tests
do not cover T-QUO-001/002/018, T-QUO-019, or any T-DOC-* acceptance case.
"""

from __future__ import annotations

import sqlite3

import pytest

from mizan.auth import PermissionDenied
from mizan.documents import DocumentError
from mizan.quotations import QuotationError
from mizan.sales import InsufficientStock, SalesError
from phase7_support import QuoteShop, build_quote_shop


@pytest.fixture()
def qs(db) -> QuoteShop:
    return build_quote_shop(db)


def _row(qs: QuoteShop, table: str, row_id: int):
    with qs.db.connect() as conn:
        return conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()


def _scalar(qs: QuoteShop, sql: str, params: tuple = ()):
    return qs.db.scalar(sql, params)


# ---------------------------------------------------------------------------
# Numbering, roots, drafts, and discounts
# ---------------------------------------------------------------------------

def test_quotation_numbering_and_root_links(qs: QuoteShop):
    first = qs.create_quote()
    second = qs.create_quote()
    first_row = _row(qs, "quotations", first)
    second_row = _row(qs, "quotations", second)

    assert first_row["document_no"] == "Q-2026-000001"
    assert second_row["document_no"] == "Q-2026-000002"
    assert first_row["root_quotation_id"] == first
    assert second_row["root_quotation_id"] == second
    assert first_row["version_no"] == 1
    assert first_row["status"] == "DRAFT"
    assert first_row["series_id"] != second_row["series_id"]
    assert first_row["subtotal_piastres"] == 3_000
    assert first_row["total_piastres"] == 3_000


def test_draft_edit_and_largest_remainder_discount(qs: QuoteShop):
    quote = qs.create_quote(lines=[
        qs.line(unit_price_micros=33_330_000, price_adjustment_reason="اختبار"),
        qs.line(unit_price_micros=33_330_000, price_adjustment_reason="اختبار"),
        qs.line(unit_price_micros=33_340_000, price_adjustment_reason="اختبار"),
    ], invoice_discount_piastres=10)
    row = _row(qs, "quotations", quote)
    with qs.db.connect() as conn:
        lines = conn.execute(
            "SELECT * FROM quotation_lines WHERE quotation_id=? ORDER BY line_no",
            (quote,),
        ).fetchall()

    assert [line["invoice_discount_alloc_piastres"] for line in lines] == [3, 3, 4]
    assert [line["net_piastres"] for line in lines] == [3_330, 3_330, 3_330]
    assert row["subtotal_piastres"] == 10_000
    assert row["invoice_discount_piastres"] == 10
    assert row["total_piastres"] == 9_990

    qs.quotes.update_draft(
        quote,
        [qs.line(quantity_micros=2_000_000)],
        invoice_discount_piastres=5,
        notes="ملاحظة محدثة",
        actor_id=qs.shop.manager,
    )
    row = _row(qs, "quotations", quote)
    with qs.db.connect() as conn:
        lines = conn.execute(
            "SELECT * FROM quotation_lines WHERE quotation_id=? ORDER BY line_no",
            (quote,),
        ).fetchall()
    assert len(lines) == 1
    assert row["subtotal_piastres"] == 6_000
    assert row["invoice_discount_piastres"] == 5
    assert row["total_piastres"] == 5_995
    assert row["notes"] == "ملاحظة محدثة"


def test_no_number_gap_when_a_draft_fails(qs: QuoteShop):
    with pytest.raises(QuotationError):
        qs.create_quote(lines=[qs.line(product_id=999_999)])
    quote = qs.create_quote()
    assert _row(qs, "quotations", quote)["document_no"] == "Q-2026-000001"


# ---------------------------------------------------------------------------
# Issue, snapshots, and lifecycle
# ---------------------------------------------------------------------------

def test_issue_requires_official_export_and_captures_snapshot(qs: QuoteShop):
    quote = qs.create_quote(notes="عرض تجريبي", terms="صالح 14 يومًا")
    with pytest.raises(QuotationError, match="ADR-0004"):
        qs.quotes.issue_quotation(quote, actor_id=qs.shop.manager)

    export_id = qs.issue(quote)
    row = _row(qs, "quotations", quote)
    assert row["status"] == "ISSUED"
    assert row["issued_at"] is not None
    assert row["official_export_id"] == export_id

    snapshot = qs.documents.snapshot("QUOTATION", quote)
    assert snapshot is not None
    assert snapshot["document_no"] == "Q-2026-000001"
    assert snapshot["status"] == "ISSUED"
    assert snapshot["store"]["name_ar"] == "متجر ميزان"
    assert snapshot["customer"]["name_ar"] == "تجزئة"
    assert snapshot["customer"]["phone"] == "01111111111"
    assert snapshot["lines"][0]["product_code"] == "WIRE-LOT"
    assert snapshot["lines"][0]["price_source"] == "DEFAULT"
    assert snapshot["lines"][0]["final_unit_price_micros"] == 30_000_000
    assert snapshot["totals"]["total_piastres"] == 3_000
    assert snapshot["template_version"] == "TEST_DOUBLE_TEMPLATE"
    assert snapshot["renderer_version"] == "TEST_DOUBLE_NO_RENDERER"
    assert snapshot["official_export"]["sha256"] == "a" * 64

    history = qs.quotes.status_history(quote)
    assert [(item["from_status"], item["to_status"]) for item in history] == [
        (None, "DRAFT"), ("DRAFT", "ISSUED"),
    ]

    with pytest.raises(sqlite3.IntegrityError):
        with qs.db.transaction() as conn:
            conn.execute(
                "UPDATE document_snapshots SET snapshot_json='{}' "
                "WHERE document_type='QUOTATION' AND document_id=?",
                (quote,),
            )
    with pytest.raises(sqlite3.IntegrityError):
        with qs.db.transaction() as conn:
            conn.execute(
                "DELETE FROM document_snapshots WHERE document_type='QUOTATION' "
                "AND document_id=?",
                (quote,),
            )


def test_issue_rejects_invalid_dates_and_foreign_export(qs: QuoteShop):
    with pytest.raises(QuotationError):
        qs.create_quote(valid_until="2026-11-01")

    quote = qs.create_quote()
    export_id = qs.issue(quote)
    with pytest.raises(QuotationError):
        qs.quotes.issue_quotation(
            quote, official_export_id=export_id, actor_id=qs.shop.manager
        )

    other = qs.create_quote()
    other_export = qs.export_for(other)
    with pytest.raises(QuotationError):
        qs.quotes.issue_quotation(
            other, official_export_id=qs.export_for(other, is_official=False),
            actor_id=qs.shop.manager,
        )
    with pytest.raises(QuotationError):
        qs.quotes.issue_quotation(
            quote, official_export_id=other_export, actor_id=qs.shop.manager,
        )


def test_accept_reject_and_cancel_policy(qs: QuoteShop):
    accepted = qs.create_quote()
    qs.issue(accepted)
    qs.quotes.accept_quotation(accepted, actor_id=qs.shop.manager)
    assert _row(qs, "quotations", accepted)["status"] == "ACCEPTED"
    with pytest.raises(QuotationError, match="OQ-QUO-005"):
        qs.quotes.cancel_quotation(accepted, reason="محاولة", actor_id=qs.shop.manager)
    with pytest.raises(QuotationError):
        qs.quotes.accept_quotation(accepted, actor_id=qs.shop.manager)

    rejected = qs.create_quote()
    qs.issue(rejected)
    qs.quotes.reject_quotation(rejected, reason="السعر مرتفع", actor_id=qs.shop.manager)
    assert _row(qs, "quotations", rejected)["status"] == "REJECTED"

    cancelled_draft = qs.create_quote()
    qs.quotes.cancel_quotation(cancelled_draft, reason="خطأ إدخال",
                               actor_id=qs.shop.manager)
    assert _row(qs, "quotations", cancelled_draft)["status"] == "CANCELLED"
    with pytest.raises(QuotationError):
        qs.quotes.cancel_quotation(cancelled_draft, reason="مرة أخرى",
                                   actor_id=qs.shop.manager)

    cancelled_issued = qs.create_quote()
    qs.issue(cancelled_issued)
    qs.quotes.cancel_quotation(cancelled_issued, reason="طلب العميل",
                               actor_id=qs.shop.manager)
    assert _row(qs, "quotations", cancelled_issued)["status"] == "CANCELLED"

    issued = qs.create_quote()
    qs.issue(issued)
    with pytest.raises(QuotationError):
        qs.quotes.cancel_quotation(issued, reason="   ", actor_id=qs.shop.manager)
    assert _row(qs, "quotations", issued)["status"] == "ISSUED"


def test_cashier_can_create_but_cannot_issue_cancel_or_convert(qs: QuoteShop):
    quote = qs.create_quote(actor_id=qs.shop.cashier)
    export_id = qs.export_for(quote)
    with pytest.raises(PermissionDenied):
        qs.quotes.issue_quotation(
            quote, official_export_id=export_id, actor_id=qs.shop.cashier,
        )
    qs.quotes.issue_quotation(
        quote, official_export_id=export_id, actor_id=qs.shop.manager,
    )
    with pytest.raises(PermissionDenied):
        qs.quotes.convert_to_sale(
            quote, invoice_date="2026-11-17", as_of="2026-11-17",
            actor_id=qs.shop.cashier,
        )
    with pytest.raises(PermissionDenied):
        qs.quotes.cancel_quotation(quote, reason="لا", actor_id=qs.shop.cashier)


def test_expiry_is_computed_and_last_day_is_valid(qs: QuoteShop):
    quote = qs.create_quote(valid_until="2026-11-17")
    qs.issue(quote)
    assert qs.quotes.is_expired(quote, as_of="2026-11-17") is False
    assert qs.quotes.effective_status(quote, as_of="2026-11-17") == "ISSUED"
    assert qs.quotes.is_expired(quote, as_of="2026-11-18") is True
    assert qs.quotes.effective_status(quote, as_of="2026-11-18") == "EXPIRED"


def test_quotation_status_history_is_immutable(qs: QuoteShop):
    quote = qs.create_quote()
    with pytest.raises(sqlite3.IntegrityError):
        with qs.db.transaction() as conn:
            conn.execute(
                "UPDATE quotation_status_history SET to_status='ISSUED' WHERE quotation_id=?",
                (quote,),
            )
    with pytest.raises(sqlite3.IntegrityError):
        with qs.db.transaction() as conn:
            conn.execute(
                "DELETE FROM quotation_status_history WHERE quotation_id=?", (quote,)
            )


# ---------------------------------------------------------------------------
# Revisions
# ---------------------------------------------------------------------------

def test_revision_root_and_supersede(qs: QuoteShop):
    source = qs.create_quote()
    qs.issue(source)
    revision = qs.quotes.create_revision(source, actor_id=qs.shop.manager)

    source_row = _row(qs, "quotations", source)
    revision_row = _row(qs, "quotations", revision)
    assert source_row["status"] == "ISSUED"
    assert revision_row["status"] == "DRAFT"
    assert revision_row["version_no"] == 2
    assert revision_row["document_no"] == "Q-2026-000001-R2"
    assert revision_row["root_quotation_id"] == source
    with qs.db.connect() as conn:
        copied = conn.execute(
            "SELECT * FROM quotation_lines WHERE quotation_id=?", (revision,)
        ).fetchall()
    assert len(copied) == 1
    assert copied[0]["final_unit_price_micros"] == 30_000_000

    qs.issue(revision)
    assert _row(qs, "quotations", source)["status"] == "SUPERSEDED"
    assert _row(qs, "quotations", revision)["status"] == "ISSUED"
    with pytest.raises(QuotationError):
        qs.convert(source)
    converted = qs.convert(revision)
    assert _row(qs, "sales", converted["sales_id"])["status"] == "DRAFT"


def test_revision_blocked_while_active_sale_draft(qs: QuoteShop):
    quote = qs.create_quote()
    qs.issue(quote)
    sale = qs.convert(quote)
    with pytest.raises(QuotationError, match="active sale draft"):
        qs.quotes.create_revision(quote, actor_id=qs.shop.manager)

    qs.shop.sales.cancel_draft(
        sale["sales_id"], "إلغاء مسودة التحويل", actor_id=qs.shop.manager
    )
    revision = qs.quotes.create_revision(quote, actor_id=qs.shop.manager)
    assert _row(qs, "quotations", revision)["version_no"] == 2


# ---------------------------------------------------------------------------
# Conversion to a sales DRAFT
# ---------------------------------------------------------------------------

def test_conversion_creates_sales_draft_with_frozen_values(qs: QuoteShop):
    qs.shop.purchase(100_000_000, 22_000_000)
    quote = qs.create_quote(lines=[
        qs.line(product_id=qs.shop.lot_product, quantity_micros=10_000_000),
        qs.line(product_id=qs.shop.lot_product, unit_id=qs.shop.carton,
                quantity_micros=1_000_000),
    ])
    qs.issue(quote)
    with qs.db.transaction() as conn:
        conn.execute(
            "UPDATE product_default_prices SET price_micros=99_000_000 "
            "WHERE product_id=? AND unit_id=?",
            (qs.shop.lot_product, qs.shop.meter),
        )
    result = qs.convert(quote)
    sale = _row(qs, "sales", result["sales_id"])
    assert sale["status"] == "DRAFT"
    assert sale["source_quotation_id"] == quote
    assert sale["total_piastres"] == 30_000 + 36_000
    with qs.db.connect() as conn:
        lines = conn.execute(
            "SELECT * FROM sales_lines WHERE sales_id=? ORDER BY line_no",
            (result["sales_id"],),
        ).fetchall()
        quoted_line_ids = [
            row["id"] for row in conn.execute(
                "SELECT id FROM quotation_lines WHERE quotation_id=? ORDER BY line_no",
                (quote,),
            ).fetchall()
        ]
    assert [line["unit_price_micros"] for line in lines] == [30_000_000, 360_000_000]
    assert [line["net_piastres"] for line in lines] == [30_000, 36_000]
    assert [line["conversion_num"] for line in lines] == [1, 12]
    assert [line["quotation_line_id"] for line in lines] == quoted_line_ids
    assert _row(qs, "quotations", quote)["status"] == "ISSUED"
    snapshot = qs.documents.snapshot("QUOTATION", quote)
    assert snapshot["lines"][0]["conversion_num"] == 1
    assert snapshot["lines"][1]["conversion_num"] == 12
    assert snapshot["lines"][1]["quantity_micros"] == 1_000_000
    assert _scalar(qs, "SELECT COUNT(*) FROM journal_entries WHERE source_type='SALES_INVOICE'") == 0
    assert _scalar(qs, "SELECT COUNT(*) FROM stock_movements WHERE source_type='SALES_INVOICE'") == 0
    assert result["shortages"] == []


def test_conversion_reports_shortage_without_blocking(qs: QuoteShop):
    quote = qs.create_quote(quantity_micros=200_000_000)
    qs.issue(quote)
    result = qs.convert(quote)
    assert result["shortages"] == [{
        "product_id": qs.shop.lot_product,
        "requested": 200_000_000,
        "available": 0,
        "shortage": 200_000_000,
    }]
    assert _row(qs, "sales", result["sales_id"])["status"] == "DRAFT"


def test_duplicate_conversion_is_blocked_by_database(qs: QuoteShop):
    quote = qs.create_quote()
    qs.issue(quote)
    first = qs.convert(quote)
    with pytest.raises(QuotationError, match="active sale draft"):
        qs.convert(quote)

    with pytest.raises(sqlite3.IntegrityError):
        with qs.db.transaction() as conn:
            conn.execute(
                "INSERT INTO sales(document_no,customer_id,sale_type,payment_type,status,"
                "invoice_date,subtotal_piastres,item_discount_piastres,"
                "invoice_discount_piastres,total_piastres,source_quotation_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("SI-TEST-DUP", qs.shop.retail, "RETAIL", "CREDIT", "DRAFT",
                 "2026-11-17", 0, 0, 0, 0, quote),
            )

    qs.shop.sales.cancel_draft(
        first["sales_id"], "إلغاء للاختبار", actor_id=qs.shop.manager
    )
    assert _row(qs, "sales", first["sales_id"])["status"] == "CANCELLED"
    with qs.db.transaction() as conn:
        conn.execute(
            "INSERT INTO sales(document_no,customer_id,sale_type,payment_type,status,"
            "invoice_date,subtotal_piastres,item_discount_piastres,"
            "invoice_discount_piastres,total_piastres,source_quotation_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("SI-TEST-AFTER-CANCEL", qs.shop.retail, "RETAIL", "CREDIT", "DRAFT",
             "2026-11-17", 0, 0, 0, 0, quote),
        )
    assert _scalar(
        qs, "SELECT COUNT(*) FROM sales WHERE source_quotation_id=? AND status<>'CANCELLED'",
        (quote,),
    ) == 1


def test_conversion_rejects_non_issued_and_is_full_only(qs: QuoteShop):
    draft = qs.create_quote()
    with pytest.raises(QuotationError):
        qs.convert(draft)

    cancelled = qs.create_quote()
    qs.quotes.cancel_quotation(cancelled, reason="إلغاء", actor_id=qs.shop.manager)
    with pytest.raises(QuotationError):
        qs.convert(cancelled)

    rejected = qs.create_quote()
    qs.issue(rejected)
    qs.quotes.reject_quotation(rejected, reason="رفض", actor_id=qs.shop.manager)
    with pytest.raises(QuotationError):
        qs.convert(rejected)

    accepted = qs.create_quote(lines=[qs.line(), qs.line(quantity_micros=2_000_000)])
    qs.issue(accepted)
    qs.quotes.accept_quotation(accepted, actor_id=qs.shop.manager)
    sale = qs.convert(accepted)
    with qs.db.connect() as conn:
        quote_count = conn.execute(
            "SELECT COUNT(*) FROM quotation_lines WHERE quotation_id=?", (accepted,)
        ).fetchone()[0]
        sale_count = conn.execute(
            "SELECT COUNT(*) FROM sales_lines WHERE sales_id=?", (sale["sales_id"],)
        ).fetchone()[0]
    assert quote_count == sale_count == 2


def test_cancel_conversion_draft_restores_accepted_status(qs: QuoteShop):
    quote = qs.create_quote()
    qs.issue(quote)
    qs.quotes.accept_quotation(quote, actor_id=qs.shop.manager)
    sale = qs.convert(quote)
    qs.shop.sales.cancel_draft(
        sale["sales_id"], "إلغاء تحويل", actor_id=qs.shop.manager
    )
    assert _row(qs, "quotations", quote)["status"] == "ACCEPTED"
    history = qs.quotes.status_history(quote)
    assert any(item["metadata"].get("event") == "CONVERSION_DRAFT_CANCELLED"
               for item in history)
    assert _scalar(
        qs, "SELECT COUNT(*) FROM audit_events WHERE event_type='QUOTATION_CONVERSION_DRAFT_CANCELLED'"
    ) == 1
    again = qs.convert(quote)
    assert _row(qs, "sales", again["sales_id"])["status"] == "DRAFT"


def test_cancel_conversion_draft_uses_saved_status_when_status_changed(qs: QuoteShop):
    quote = qs.create_quote()
    qs.issue(quote)
    sale = qs.convert(quote)
    qs.quotes.accept_quotation(quote, actor_id=qs.shop.manager)
    assert _row(qs, "quotations", quote)["status"] == "ACCEPTED"
    qs.shop.sales.cancel_draft(
        sale["sales_id"], "إلغاء التحويل", actor_id=qs.shop.manager
    )
    # OQ-QUO-001 restores the status saved before conversion (ISSUED here).
    assert _row(qs, "quotations", quote)["status"] == "ISSUED"


def test_cancel_conversion_draft_uses_saved_status_and_respects_expiry(qs: QuoteShop):
    quote = qs.create_quote(issue_date="2026-10-01", valid_until="2026-10-15")
    qs.issue(quote)
    sale = qs.convert(
        quote, override_expired=True, override_reason="موافقة المالك",
        as_of="2026-11-17",
    )
    with pytest.raises(QuotationError):
        qs.convert(quote)
    assert qs.quotes.effective_status(quote, as_of="2026-11-17") == "EXPIRED"
    qs.shop.sales.cancel_draft(
        sale["sales_id"], "إلغاء التحويل", actor_id=qs.shop.manager
    )
    assert qs.quotes.effective_status(quote, as_of="2026-11-17") == "EXPIRED"
    assert _row(qs, "quotations", quote)["status"] == "ISSUED"
    history = qs.quotes.status_history(quote)
    cancel_events = [item for item in history
                     if item["metadata"].get("event") == "CONVERSION_DRAFT_CANCELLED"]
    assert cancel_events and cancel_events[-1]["metadata"]["expired"] is True


def test_expired_conversion_requires_override_permission_and_reason(qs: QuoteShop):
    quote = qs.create_quote(issue_date="2026-10-01", valid_until="2026-10-15")
    qs.issue(quote)
    with pytest.raises(QuotationError):
        qs.convert(quote, as_of="2026-11-17")
    with pytest.raises(QuotationError):
        qs.convert(
            quote, override_expired=True, override_reason="   ",
            as_of="2026-11-17",
        )
    sale = qs.convert(
        quote, override_expired=True, override_reason="موافقة المدراء",
        as_of="2026-11-17",
    )
    assert sale["expired_override"] is True
    assert _row(qs, "sales", sale["sales_id"])["status"] == "DRAFT"
    history = qs.quotes.status_history(quote)
    created = [item for item in history
               if item["metadata"].get("event") == "CONVERSION_DRAFT_CREATED"]
    assert created[-1]["metadata"]["expired_override"] is True
    assert created[-1]["metadata"]["override_reason"] == "موافقة المدراء"


def test_sale_posting_marks_quotation_converted(qs: QuoteShop):
    qs.shop.purchase(100_000_000, 22_000_000)
    quote = qs.create_quote(quantity_micros=10_000_000)
    qs.issue(quote)
    sale = qs.convert(quote)
    qs.shop.sales.post_invoice(sale["sales_id"], actor_id=qs.shop.manager)

    row = _row(qs, "quotations", quote)
    assert row["status"] == "CONVERTED"
    assert row["converted_at"] is not None
    history = qs.quotes.status_history(quote)
    assert any(item["metadata"].get("event") == "SALES_POSTED" for item in history)


def test_failed_sale_posting_does_not_change_quotation(qs: QuoteShop):
    quote = qs.create_quote(quantity_micros=200_000_000)
    qs.issue(quote)
    sale = qs.convert(quote)
    with pytest.raises(InsufficientStock):
        qs.shop.sales.post_invoice(sale["sales_id"], actor_id=qs.shop.manager)
    assert _row(qs, "quotations", quote)["status"] == "ISSUED"
    assert _row(qs, "sales", sale["sales_id"])["status"] == "DRAFT"


def test_generated_sale_draft_commercial_fields_are_locked(qs: QuoteShop):
    quote = qs.create_quote()
    qs.issue(quote)
    sale = qs.convert(quote)
    with pytest.raises(SalesError):
        qs.shop.sales.update_draft(
            sale["sales_id"],
            [qs.line(unit_price_micros=1_000_000, price_adjustment_reason="تغيير")],
            actor_id=qs.shop.manager,
        )
    qs.shop.sales.update_draft_header(
        sale["sales_id"], payment_type="CASH",
        financial_account_id=qs.shop.cash, due_date="2026-12-01",
        actor_id=qs.shop.manager,
    )
    row = _row(qs, "sales", sale["sales_id"])
    assert row["payment_type"] == "CASH"
    assert row["financial_account_id"] == qs.shop.cash
    assert row["due_date"] == "2026-12-01"

    qs.shop.sales.update_draft_header(
        sale["sales_id"], payment_type="CREDIT", actor_id=qs.shop.manager
    )
    row = _row(qs, "sales", sale["sales_id"])
    assert row["payment_type"] == "CREDIT"
    assert row["financial_account_id"] is None


# ---------------------------------------------------------------------------
# Snapshots and DB constraints
# ---------------------------------------------------------------------------

def test_issued_line_snapshot_survives_product_rename(qs: QuoteShop):
    quote = qs.create_quote()
    qs.issue(quote)
    snapshot_before = qs.documents.snapshot("QUOTATION", quote)
    with qs.db.transaction() as conn:
        conn.execute("UPDATE products SET name_ar='اسم جديد' WHERE id=?", (qs.shop.lot_product,))
    snapshot_after = qs.documents.snapshot("QUOTATION", quote)
    row = _row(qs, "quotations", quote)
    with qs.db.connect() as conn:
        line = conn.execute(
            "SELECT * FROM quotation_lines WHERE quotation_id=?", (quote,)
        ).fetchone()
    assert snapshot_before == snapshot_after
    assert line["product_name_snapshot"] == "سلك LOT"
    assert row["status"] == "ISSUED"


def test_official_export_and_series_version_are_unique(qs: QuoteShop):
    quote = qs.create_quote()
    qs.issue(quote)
    with pytest.raises(DocumentError):
        qs.export_for(quote)
    second = qs.create_quote()
    revision = qs.quotes.create_revision(quote, actor_id=qs.shop.manager)
    series_id = _row(qs, "quotations", revision)["series_id"]
    with pytest.raises(sqlite3.IntegrityError):
        with qs.db.transaction() as conn:
            conn.execute(
                "INSERT INTO quotations(document_no,series_id,version_no,customer_id,"
                "status,issue_date) VALUES(?,?,?,?,?,?)",
                ("Q-DUPLICATE-VERSION", series_id, 2, qs.shop.retail, "DRAFT", "2026-11-10"),
            )
    assert second != quote
