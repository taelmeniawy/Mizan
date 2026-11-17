from __future__ import annotations

import sqlite3

import pytest

from mizan.accounting import AccountingService
from mizan.auth import PermissionDenied
from mizan.sales import InsufficientStock
from phase6_support import Shop, build_shop


@pytest.fixture()
def shop(db) -> Shop:
    return build_shop(db)


def _line(shop: Shop, sale_id: int, line_no: int = 1):
    with shop.db.connect() as conn:
        return conn.execute(
            "SELECT * FROM sales_lines WHERE sales_id=? AND line_no=?", (sale_id, line_no)
        ).fetchone()


def _journal_lines(shop: Shop, entry_id: int):
    with shop.db.connect() as conn:
        return conn.execute(
            "SELECT a.code,l.debit_piastres,l.credit_piastres,l.party_type,l.party_id "
            "FROM journal_lines l JOIN accounts a ON a.id=l.account_id "
            "WHERE l.journal_entry_id=? ORDER BY l.line_no",
            (entry_id,),
        ).fetchall()


# ---------------------------------------------------------------------------
# Pricing and invoice construction
# ---------------------------------------------------------------------------

def test_price_priority_customer_then_list_then_default(shop: Shop):
    with shop.db.transaction() as conn:
        conn.execute(
            "INSERT INTO product_default_prices(product_id,unit_id,price_micros) VALUES(?,?,?)",
            (shop.lot_product, shop.meter, 30_000_000),
        )
        conn.execute(
            "INSERT INTO price_list_items(price_list_id,product_id,unit_id,price_micros) VALUES(?,?,?,?)",
            (shop.price_list, shop.lot_product, shop.meter, 28_000_000),
        )
        conn.execute(
            "INSERT INTO customer_prices(customer_id,product_id,unit_id,price_micros) VALUES(?,?,?,?)",
            (shop.wholesale, shop.lot_product, shop.meter, 25_000_000),
        )

    wholesale_invoice = shop.sale(1_000_000, None, customer=shop.wholesale,
                                  price_list_id=shop.price_list, actor=shop.manager)
    assert _line(shop, wholesale_invoice)["unit_price_micros"] == 25_000_000
    assert _line(shop, wholesale_invoice)["price_source"] == "CUSTOMER"

    retail_invoice = shop.sale(1_000_000, None, customer=shop.retail,
                               price_list_id=shop.price_list, actor=shop.manager)
    assert _line(shop, retail_invoice)["unit_price_micros"] == 28_000_000
    assert _line(shop, retail_invoice)["price_source"] == "PRICE_LIST"

    contractor_invoice = shop.sale(1_000_000, None, customer=shop.contractor,
                                   project_id=shop.project, actor=shop.manager)
    assert _line(shop, contractor_invoice)["unit_price_micros"] == 30_000_000
    assert _line(shop, contractor_invoice)["price_source"] == "DEFAULT"


def test_manual_price_requires_permission_and_reason(shop: Shop):
    with shop.db.transaction() as conn:
        conn.execute(
            "INSERT INTO product_default_prices(product_id,unit_id,price_micros) VALUES(?,?,?)",
            (shop.lot_product, shop.meter, 30_000_000),
        )
    lines = [{
        "product_id": shop.lot_product, "unit_id": shop.meter,
        "quantity_micros": 1_000_000, "unit_price_micros": 26_000_000,
    }]
    with pytest.raises(PermissionDenied):
        shop.sales.create_invoice(shop.retail, "2026-11-10", lines, actor_id=shop.cashier)
    with pytest.raises(ValueError):
        shop.sales.create_invoice(shop.retail, "2026-11-10", lines, actor_id=shop.manager)
    invoice = shop.sales.create_invoice(
        shop.retail, "2026-11-10",
        [{**lines[0], "price_adjustment_reason": "عرض خاص"}],
        actor_id=shop.manager,
    )
    line = _line(shop, invoice)
    assert line["price_source"] == "MANUAL"
    assert line["price_adjustment_reason"] == "عرض خاص"


def test_retail_wholesale_contractor_cash_and_credit(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    # Retail / credit
    retail_invoice = shop.sale(10_000_000, 30_000_000, customer=shop.retail, actor=shop.cashier)
    retail = shop.sales.post_invoice(retail_invoice, actor_id=shop.cashier)
    assert shop.db.scalar("SELECT sale_type FROM sales WHERE id=?", (retail_invoice,)) == "RETAIL"
    assert shop.db.scalar("SELECT payment_type FROM sales WHERE id=?", (retail_invoice,)) == "CREDIT"
    assert shop.accounting.account_balance("1100") == 30_000
    assert shop.accounting.account_balance("4010") == -30_000

    # Wholesale / cash
    wholesale_invoice = shop.sale(10_000_000, 25_000_000, customer=shop.wholesale,
                                  payment_type="CASH", actor=shop.cashier)
    shop.sales.post_invoice(wholesale_invoice, actor_id=shop.cashier)
    assert shop.db.scalar("SELECT sale_type FROM sales WHERE id=?", (wholesale_invoice,)) == "WHOLESALE"
    assert shop.accounting.account_balance("1010") == 25_000

    # Contractor / credit with project
    contractor_invoice = shop.sale(10_000_000, 27_000_000, customer=shop.contractor,
                                   project_id=shop.project, actor=shop.manager)
    shop.sales.post_invoice(contractor_invoice, actor_id=shop.manager)
    assert shop.db.scalar("SELECT sale_type FROM sales WHERE id=?", (contractor_invoice,)) == "CONTRACTOR"
    assert shop.db.scalar("SELECT project_id FROM sales WHERE id=?", (contractor_invoice,)) == shop.project
    assert shop.accounting.account_balance("1100") == 57_000


def test_sale_type_must_match_customer_type(shop: Shop):
    with pytest.raises(ValueError):
        shop.sales.create_invoice(
            shop.retail, "2026-11-10",
            [{"product_id": shop.lot_product, "unit_id": shop.meter, "quantity_micros": 1_000_000}],
            sale_type="WHOLESALE",
        )


def test_item_and_invoice_discounts_use_largest_remainder(shop: Shop):
    shop.purchase(100_000_000, 5_000_000)
    lines = [
        {"product_id": shop.lot_product, "unit_id": shop.meter, "quantity_micros": 1_000_000,
         "unit_price_micros": 10_000_000, "item_discount_piastres": 100},
        {"product_id": shop.lot_product, "unit_id": shop.meter, "quantity_micros": 2_000_000,
         "unit_price_micros": 10_000_000},
    ]
    invoice = shop.sales.create_invoice(
        shop.retail, "2026-11-10", lines, invoice_discount_piastres=1, actor_id=shop.manager,
    )
    one = _line(shop, invoice, 1)
    two = _line(shop, invoice, 2)
    assert one["gross_piastres"] == 1_000
    assert one["item_discount_piastres"] == 100
    assert one["net_piastres"] == 900
    assert two["gross_piastres"] == 2_000
    assert two["net_piastres"] == 1_999
    # 1 piastre split over weights 900/2000 -> remainder to the second line.
    assert (one["invoice_discount_alloc_piastres"], two["invoice_discount_alloc_piastres"]) == (0, 1)
    header = shop.db.scalar("SELECT subtotal_piastres FROM sales WHERE id=?", (invoice,))
    assert header == 3_000
    with shop.db.connect() as conn:
        row = conn.execute(
            "SELECT subtotal_piastres,item_discount_piastres,invoice_discount_piastres,total_piastres "
            "FROM sales WHERE id=?", (invoice,)
        ).fetchone()
    assert tuple(row) == (3_000, 100, 1, 2_899)


def test_discount_cannot_exceed_eligible_amount(shop: Shop):
    lines = [{"product_id": shop.lot_product, "unit_id": shop.meter,
              "quantity_micros": 1_000_000, "unit_price_micros": 10_000_000,
              "item_discount_piastres": 1_001}]
    with pytest.raises(ValueError):
        shop.sales.create_invoice(shop.retail, "2026-11-10", lines, actor_id=shop.manager)
    lines[0]["item_discount_piastres"] = 0
    with pytest.raises(ValueError):
        shop.sales.create_invoice(shop.retail, "2026-11-10", lines,
                                  invoice_discount_piastres=1_001, actor_id=shop.manager)


def test_draft_is_editable_and_cancel_has_no_effect(shop: Shop):
    shop.purchase(100_000_000, 20_000_000)
    invoice = shop.sale(5_000_000, 30_000_000, actor=shop.manager)
    assert shop.db.scalar("SELECT total_piastres FROM sales WHERE id=?", (invoice,)) == 15_000
    shop.sales.update_draft(
        invoice,
        [{"product_id": shop.lot_product, "unit_id": shop.meter,
          "quantity_micros": 10_000_000, "unit_price_micros": 30_000_000}],
        actor_id=shop.manager,
    )
    assert shop.db.scalar("SELECT total_piastres FROM sales WHERE id=?", (invoice,)) == 30_000
    shop.sales.update_draft(
        invoice,
        [{"product_id": shop.lot_product, "unit_id": shop.meter,
          "quantity_micros": 10_000_000, "unit_price_micros": 30_000_000}],
        invoice_discount_piastres=1_000,
        actor_id=shop.manager,
    )
    with shop.db.connect() as conn:
        header = conn.execute(
            "SELECT subtotal_piastres,item_discount_piastres,invoice_discount_piastres,total_piastres "
            "FROM sales WHERE id=?", (invoice,)
        ).fetchone()
    assert tuple(header) == (30_000, 0, 1_000, 29_000)
    shop.sales.cancel_draft(invoice, "إلغاء مسودة", actor_id=shop.manager)
    assert shop.db.scalar("SELECT status FROM sales WHERE id=?", (invoice,)) == "CANCELLED"
    assert shop.db.scalar("SELECT cancellation_reason FROM sales WHERE id=?", (invoice,)) == "إلغاء مسودة"
    assert shop.db.scalar("SELECT COUNT(*) FROM stock_movements WHERE source_type='SALES_INVOICE'") == 0
    assert shop.db.scalar("SELECT COUNT(*) FROM journal_entries WHERE source_type='SALES_INVOICE'") == 0
    with pytest.raises(ValueError):
        shop.sales.post_invoice(invoice, actor_id=shop.manager)


# ---------------------------------------------------------------------------
# Posting: atomicity, LCP/FIFO, reels, below cost, immutability
# ---------------------------------------------------------------------------

def test_multi_line_shortage_rolls_back_all_lines(shop: Shop):
    shop.purchase(10_000_000, 22_000_000)
    invoice = shop.sale(0, None, lines=[
        {"product_id": shop.lot_product, "unit_id": shop.meter,
         "quantity_micros": 5_000_000, "unit_price_micros": 30_000_000},
        {"product_id": shop.lot_product, "unit_id": shop.meter,
         "quantity_micros": 6_000_000, "unit_price_micros": 30_000_000},
    ], actor=shop.manager)
    with pytest.raises(InsufficientStock):
        shop.sales.post_invoice(invoice, actor_id=shop.manager)
    assert shop.db.scalar("SELECT status FROM sales WHERE id=?", (invoice,)) == "DRAFT"
    assert shop.db.scalar("SELECT COUNT(*) FROM stock_movements WHERE source_type='SALES_INVOICE'") == 0
    assert shop.db.scalar("SELECT SUM(cost_piastres) FROM sales_lines WHERE sales_id=?", (invoice,)) == 0
    assert shop.purchasing.lot_remaining(1) == (10_000_000, 22_000)


def test_shortage_reports_cumulative_demand(shop: Shop):
    shop.purchase(10_000_000, 22_000_000)
    invoice = shop.sale(0, None, lines=[
        {"product_id": shop.lot_product, "unit_id": shop.meter,
         "quantity_micros": 5_000_000, "unit_price_micros": 30_000_000},
        {"product_id": shop.lot_product, "unit_id": shop.meter,
         "quantity_micros": 6_000_000, "unit_price_micros": 30_000_000},
    ], actor=shop.manager)
    with pytest.raises(InsufficientStock) as excinfo:
        shop.sales.post_invoice(invoice, actor_id=shop.manager)
    assert excinfo.value.shortages == [{
        "product_id": shop.lot_product,
        "requested": 11_000_000,
        "available": 10_000_000,
        "shortage": 1_000_000,
    }]
    assert shop.db.scalar("SELECT COUNT(*) FROM stock_movements WHERE source_type='SALES_INVOICE'") == 0


def test_reel_fifo_spans_multiple_reels(shop: Shop):
    shop.purchase(100_000_000, 30_000_000, product=shop.reel_product,
                  reel_lengths=[60_000_000, 40_000_000])
    invoice = shop.sale(70_000_000, 40_000_000, product=shop.reel_product, actor=shop.manager)
    result = shop.sales.post_invoice(invoice, actor_id=shop.manager)
    assert result["cost"] == 210_000
    with shop.db.connect() as conn:
        allocs = conn.execute(
            "SELECT ra.reel_id,ra.quantity_micros,ra.cost_piastres FROM reel_allocations ra "
            "JOIN sales_lines sl ON sl.id=ra.sales_line_id WHERE sl.sales_id=? "
            "AND ra.allocation_role='SALE_ISSUE' ORDER BY ra.id", (invoice,)
        ).fetchall()
        reels = conn.execute("SELECT id FROM reels ORDER BY id").fetchall()
    assert [(row["quantity_micros"], row["cost_piastres"]) for row in allocs] == [
        (60_000_000, 180_000), (10_000_000, 30_000),
    ]
    assert shop.purchasing.reel_remaining(reels[0]["id"]) == (0, 0)
    assert shop.purchasing.reel_remaining(reels[1]["id"]) == (30_000_000, 90_000)
    assert shop.db.scalar("SELECT status FROM reels WHERE id=?", (reels[0]["id"],)) == "EMPTY"
    assert shop.db.scalar("SELECT status FROM reels WHERE id=?", (reels[1]["id"],)) == "OPENED"


def test_posting_creates_balanced_journal_and_freezes_cost(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = shop.sale(20_000_000, 30_000_000, actor=shop.manager)
    result = shop.sales.post_invoice(invoice, actor_id=shop.manager)
    assert result["total"] == 60_000
    assert result["cost"] == 44_000
    assert shop.accounting.account_balance("1100") == 60_000
    assert shop.accounting.account_balance("4010") == -60_000
    assert shop.accounting.account_balance("5010") == 44_000
    assert shop.accounting.account_balance("1200") == 176_000
    lines = _journal_lines(shop, result["journal_id"])
    assert sum(line["debit_piastres"] for line in lines) == sum(line["credit_piastres"] for line in lines)
    sale_line = _line(shop, invoice)
    assert sale_line["cost_piastres"] == 44_000
    assert shop.db.scalar("SELECT status FROM sales WHERE id=?", (invoice,)) == "POSTED"
    with shop.db.connect() as conn:
        movement = conn.execute(
            "SELECT * FROM stock_movements WHERE source_type='SALES_INVOICE' AND source_id=?", (invoice,)
        ).fetchone()
    assert movement["movement_type"] == "SALE_ISSUE"
    assert movement["direction"] == "OUT"
    assert movement["quantity_micros"] == 20_000_000
    assert movement["cost_piastres"] == 44_000


def test_fifo_spans_lots_and_keeps_no_negative_stock(shop: Shop):
    shop.purchase(100_000_000, 20_000_000)
    shop.purchase(100_000_000, 22_000_000)
    invoice = shop.sale(120_000_000, 30_000_000, actor=shop.manager)
    result = shop.sales.post_invoice(invoice, actor_id=shop.manager)
    assert result["cost"] == 244_000
    with shop.db.connect() as conn:
        allocs = conn.execute(
            "SELECT la.quantity_micros,la.cost_piastres FROM lot_allocations la "
            "JOIN sales_lines sl ON sl.id=la.sales_line_id WHERE sl.sales_id=? "
            "AND la.allocation_role='SALE_ISSUE' ORDER BY la.id", (invoice,)
        ).fetchall()
    assert [tuple(row) for row in allocs] == [(100_000_000, 200_000), (20_000_000, 44_000)]

    # Requesting more than the remaining 80m fails and must not partially issue.
    too_big = shop.sale(90_000_000, 30_000_000, actor=shop.manager)
    with pytest.raises(InsufficientStock):
        shop.sales.post_invoice(too_big, actor_id=shop.manager)
    assert shop.db.scalar("SELECT status FROM sales WHERE id=?", (too_big,)) == "DRAFT"
    assert shop.db.scalar(
        "SELECT COUNT(*) FROM stock_movements WHERE source_type='SALES_INVOICE' AND source_id=?", (too_big,)
    ) == 0


def test_reel_sale_uses_reel_allocations_and_cutting(shop: Shop):
    shop.purchase(100_000_000, 30_000_000, product=shop.reel_product,
                  reel_lengths=[100_000_000])
    invoice = shop.sale(37_500_000, 40_000_000, product=shop.reel_product, actor=shop.manager)
    result = shop.sales.post_invoice(invoice, actor_id=shop.manager)
    assert result["cost"] == 112_500
    with shop.db.connect() as conn:
        allocs = conn.execute(
            "SELECT ra.reel_id,ra.quantity_micros,ra.cost_piastres FROM reel_allocations ra "
            "JOIN sales_lines sl ON sl.id=ra.sales_line_id WHERE sl.sales_id=? "
            "AND ra.allocation_role='SALE_ISSUE'", (invoice,)
        ).fetchall()
        assert conn.execute("SELECT COUNT(*) FROM lot_allocations WHERE sales_line_id IN "
                            "(SELECT id FROM sales_lines WHERE sales_id=?)", (invoice,)).fetchone()[0] == 0
    assert len(allocs) == 1
    assert allocs[0]["quantity_micros"] == 37_500_000
    assert allocs[0]["cost_piastres"] == 112_500
    reel = allocs[0]["reel_id"]
    assert shop.purchasing.reel_remaining(reel) == (62_500_000, 187_500)
    assert shop.db.scalar("SELECT status FROM reels WHERE id=?", (reel,)) == "OPENED"


def test_posting_rolls_back_on_journal_failure(shop: Shop, monkeypatch):
    shop.purchase(100_000_000, 22_000_000)
    invoice = shop.sale(20_000_000, 30_000_000, actor=shop.manager)

    def boom(*args, **kwargs):
        raise RuntimeError("forced journal failure")

    monkeypatch.setattr(AccountingService, "post_journal", boom)
    with pytest.raises(RuntimeError):
        shop.sales.post_invoice(invoice, actor_id=shop.manager)
    assert shop.db.scalar("SELECT status FROM sales WHERE id=?", (invoice,)) == "DRAFT"
    assert shop.db.scalar("SELECT COUNT(*) FROM stock_movements WHERE source_type='SALES_INVOICE'") == 0
    assert shop.db.scalar("SELECT COUNT(*) FROM journal_entries WHERE source_type='SALES_INVOICE'") == 0
    assert shop.db.scalar("SELECT position_micros FROM inventory_cost_layers") == 0
    assert shop.purchasing.lot_remaining(1) == (100_000_000, 220_000)


def test_below_cost_requires_permission_and_reason(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = shop.sale(10_000_000, 10_000_000, actor=shop.manager)
    with pytest.raises(PermissionDenied):
        shop.sales.post_invoice(invoice, actor_id=shop.cashier, below_cost_reason="تصفية")
    # Cashier lacks approve_below_cost; the whole posting rolled back.
    assert shop.db.scalar("SELECT status FROM sales WHERE id=?", (invoice,)) == "DRAFT"
    assert shop.db.scalar("SELECT COUNT(*) FROM stock_movements WHERE source_type='SALES_INVOICE'") == 0
    with pytest.raises(ValueError):
        shop.sales.post_invoice(invoice, actor_id=shop.owner)
    assert shop.db.scalar("SELECT status FROM sales WHERE id=?", (invoice,)) == "DRAFT"
    result = shop.sales.post_invoice(
        invoice, actor_id=shop.owner, below_cost_reason="تصفية معتمدة",
    )
    assert result["cost"] == 22_000
    with shop.db.connect() as conn:
        row = conn.execute(
            "SELECT below_cost_reason,below_cost_approved_by FROM sales WHERE id=?", (invoice,)
        ).fetchone()
    assert row["below_cost_reason"] == "تصفية معتمدة"
    assert row["below_cost_approved_by"] == shop.owner


def test_posted_invoice_is_immutable(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = shop.sale(20_000_000, 30_000_000, actor=shop.manager)
    shop.sales.post_invoice(invoice, actor_id=shop.manager)
    with pytest.raises(ValueError):
        shop.sales.update_draft(
            invoice,
            [{"product_id": shop.lot_product, "unit_id": shop.meter,
              "quantity_micros": 1_000_000, "unit_price_micros": 1_000_000}],
            actor_id=shop.manager,
        )
    with pytest.raises(sqlite3.IntegrityError):
        with shop.db.transaction() as conn:
            conn.execute("UPDATE sales SET total_piastres=1 WHERE id=?", (invoice,))
    with pytest.raises(sqlite3.IntegrityError):
        with shop.db.transaction() as conn:
            conn.execute("UPDATE sales_lines SET unit_price_micros=1 WHERE sales_id=?", (invoice,))
    with pytest.raises(sqlite3.IntegrityError):
        with shop.db.transaction() as conn:
            conn.execute("DELETE FROM sales WHERE id=?", (invoice,))
    with pytest.raises(sqlite3.IntegrityError):
        with shop.db.transaction() as conn:
            conn.execute("UPDATE sales SET status='CANCELLED' WHERE id=?", (invoice,))
    with pytest.raises(sqlite3.IntegrityError):
        with shop.db.transaction() as conn:
            conn.execute("UPDATE sales SET cancellation_reason='تعديل' WHERE id=?", (invoice,))


def test_historical_price_and_cost_do_not_change_after_posting(shop: Shop):
    with shop.db.transaction() as conn:
        conn.execute(
            "INSERT INTO product_default_prices(product_id,unit_id,price_micros) VALUES(?,?,?)",
            (shop.lot_product, shop.meter, 30_000_000),
        )
    shop.purchase(100_000_000, 22_000_000)
    invoice = shop.sale(20_000_000, None, actor=shop.manager)
    result = shop.sales.post_invoice(invoice, actor_id=shop.manager)
    assert result["cost"] == 44_000

    # Change both the selling price and the purchase cost after posting.
    with shop.db.transaction() as conn:
        conn.execute("UPDATE product_default_prices SET price_micros=50_000_000")
    shop.purchase(100_000_000, 30_000_000, date="2026-11-20")

    line = _line(shop, invoice)
    assert line["unit_price_micros"] == 30_000_000
    assert line["net_piastres"] == 60_000
    assert line["cost_piastres"] == 44_000


# ---------------------------------------------------------------------------
# Reversal
# ---------------------------------------------------------------------------

def test_posted_invoice_reversal_restores_stock_and_marks_journal(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = shop.sale(20_000_000, 30_000_000, actor=shop.manager)
    posted = shop.sales.post_invoice(invoice, actor_id=shop.manager)
    reversal = shop.sales.reverse_invoice(
        invoice, "خطأ إدخال", reversal_date="2026-11-12", actor_id=shop.manager,
    )
    assert reversal["reversal_journal_id"] != posted["journal_id"]
    with shop.db.connect() as conn:
        original_entry = conn.execute(
            "SELECT status,reversed_by_id FROM journal_entries WHERE id=?", (posted["journal_id"],)
        ).fetchone()
    assert original_entry["status"] == "REVERSED"
    assert original_entry["reversed_by_id"] == reversal["reversal_journal_id"]
    assert shop.accounting.account_balance("1100") == 0
    assert shop.accounting.account_balance("4010") == 0
    assert shop.accounting.account_balance("5010") == 0
    assert shop.accounting.account_balance("1200") == 220_000
    assert shop.sales.invoice_balance(invoice) == 0
    with shop.db.connect() as conn:
        row = conn.execute(
            "SELECT reversal_journal_entry_id,reversal_reason FROM sales WHERE id=?", (invoice,)
        ).fetchone()
    assert row["reversal_journal_entry_id"] == reversal["reversal_journal_id"]
    assert row["reversal_reason"] == "خطأ إدخال"
    with pytest.raises(sqlite3.IntegrityError):
        with shop.db.transaction() as conn:
            conn.execute("UPDATE sales SET reversal_reason='تعديل' WHERE id=?", (invoice,))
    assert shop.purchasing.lot_remaining(1) == (100_000_000, 220_000)
    with shop.db.connect() as conn:
        journal = conn.execute(
            "SELECT COALESCE(SUM(debit_piastres),0) AS debit,"
            "COALESCE(SUM(credit_piastres),0) AS credit FROM journal_lines "
            "WHERE journal_entry_id=?", (reversal["reversal_journal_id"],)
        ).fetchone()
    assert journal["debit"] == journal["credit"] > 0
    statement = shop.sales.customer_statement(shop.retail)
    assert statement[-1]["entry_kind"] == "REVERSAL"
    assert statement[-1]["account_code"] == "1100"
    assert statement[-1]["ar_balance"] == 0
    assert all(row["ar_balance"] >= 0 for row in statement)


def test_reel_invoice_reversal_creates_new_returned_reel(shop: Shop):
    shop.purchase(100_000_000, 30_000_000, product=shop.reel_product,
                  reel_lengths=[100_000_000])
    invoice = shop.sale(10_000_000, 40_000_000, product=shop.reel_product, actor=shop.manager)
    posted = shop.sales.post_invoice(invoice, actor_id=shop.manager)
    line_id = shop.db.scalar("SELECT id FROM sales_lines WHERE sales_id=?", (invoice,))
    with shop.db.connect() as conn:
        original_alloc = conn.execute(
            "SELECT * FROM reel_allocations WHERE sales_line_id=? AND allocation_role='SALE_ISSUE'",
            (line_id,),
        ).fetchone()
    original_reel_id = original_alloc["reel_id"]
    assert shop.purchasing.reel_remaining(original_reel_id) == (90_000_000, 270_000)

    reversal = shop.sales.reverse_invoice(
        invoice, "عكس بكرة", reversal_date="2026-11-12", actor_id=shop.manager,
    )
    with shop.db.connect() as conn:
        new_reel = conn.execute(
            "SELECT * FROM reels WHERE source_reel_id=? AND source_issue_allocation_id=?",
            (original_reel_id, original_alloc["id"]),
        ).fetchone()
        layer = conn.execute(
            "SELECT * FROM inventory_cost_layers WHERE reel_id=?", (new_reel["id"],)
        ).fetchone()
        movement = conn.execute(
            "SELECT * FROM stock_movements WHERE movement_type='SALE_RETURN_IN' "
            "AND source_type='SALES_REVERSAL' AND source_id=?", (invoice,),
        ).fetchone()
        journal = conn.execute(
            "SELECT COALESCE(SUM(debit_piastres),0) AS debit,"
            "COALESCE(SUM(credit_piastres),0) AS credit FROM journal_lines "
            "WHERE journal_entry_id=?", (reversal["reversal_journal_id"],)
        ).fetchone()
    assert new_reel["id"] != original_reel_id
    assert new_reel["original_length_micros"] == 10_000_000
    assert new_reel["original_cost_piastres"] == 30_000
    assert layer["parent_issue_allocation_id"] == original_alloc["id"]
    assert movement["reel_id"] == new_reel["id"]
    assert journal["debit"] == journal["credit"] > 0
    assert shop.purchasing.reel_remaining(original_reel_id) == (90_000_000, 270_000)
    assert shop.purchasing.reel_remaining(new_reel["id"]) == (10_000_000, 30_000)
    assert shop.accounting.account_balance("1200") == 300_000
    assert posted["journal_id"] != reversal["reversal_journal_id"]


def test_reversal_rejected_after_posted_return(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = shop.sale(20_000_000, 30_000_000, actor=shop.manager)
    shop.sales.post_invoice(invoice, actor_id=shop.manager)
    line_id = shop.db.scalar("SELECT id FROM sales_lines WHERE sales_id=?", (invoice,))
    return_id = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 10_000_000, "condition": "SELLABLE"}],
        "2026-11-12", "مرتجع", actor_id=shop.manager,
    )
    shop.sales.post_return(return_id, actor_id=shop.manager)
    with pytest.raises(ValueError):
        shop.sales.reverse_invoice(invoice, "محاولة عكس", actor_id=shop.manager)
    assert shop.db.scalar("SELECT reversal_journal_entry_id FROM sales WHERE id=?", (invoice,)) is None
    assert shop.db.scalar("SELECT status FROM journal_entries WHERE id="
                          "(SELECT journal_entry_id FROM sales WHERE id=?)", (invoice,)) == "POSTED"


def test_reversal_rejected_after_return_or_receipt(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = shop.sale(20_000_000, 30_000_000, actor=shop.manager)
    shop.sales.post_invoice(invoice, actor_id=shop.manager)
    receipt = shop.sales.create_receipt(
        shop.retail, shop.cash, 10_000, "2026-11-11",
        allocations=[{"sales_id": invoice, "amount_piastres": 10_000}],
        actor_id=shop.manager,
    )
    shop.sales.post_receipt(receipt, actor_id=shop.manager)
    with pytest.raises(ValueError):
        shop.sales.reverse_invoice(invoice, "محاولة عكس", actor_id=shop.manager)


def test_cash_reversal_requires_refund_permission_and_restores_books(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = shop.sale(20_000_000, 30_000_000, payment_type="CASH", actor=shop.cashier)
    shop.sales.post_invoice(invoice, actor_id=shop.cashier)
    with pytest.raises(PermissionDenied):
        shop.sales.reverse_invoice(invoice, "عكس نقدي", actor_id=shop.cashier)
    assert shop.db.scalar("SELECT status FROM sales WHERE id=?", (invoice,)) == "POSTED"
    assert shop.db.scalar("SELECT reversal_journal_entry_id FROM sales WHERE id=?", (invoice,)) is None
    assert shop.accounting.account_balance("1010") == 60_000
    assert shop.purchasing.lot_remaining(1) == (80_000_000, 176_000)

    shop.sales.reverse_invoice(invoice, "عكس نقدي", actor_id=shop.manager)
    assert shop.accounting.account_balance("1010") == 0
    assert shop.accounting.account_balance("4010") == 0
    assert shop.accounting.account_balance("5010") == 0
    assert shop.accounting.account_balance("1200") == 220_000
    assert shop.sales.invoice_balance(invoice) == 0
    assert shop.purchasing.lot_remaining(1) == (100_000_000, 220_000)


def test_cashier_cannot_post_sales_return(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = shop.sale(20_000_000, 30_000_000, actor=shop.manager)
    shop.sales.post_invoice(invoice, actor_id=shop.manager)
    line_id = shop.db.scalar("SELECT id FROM sales_lines WHERE sales_id=?", (invoice,))
    with pytest.raises(PermissionDenied):
        shop.sales.create_return(
            invoice,
            [{"sales_line_id": line_id, "quantity_micros": 10_000_000, "condition": "SELLABLE"}],
            "2026-11-12", "مرتجع", actor_id=shop.cashier,
        )
    assert shop.db.scalar("SELECT COUNT(*) FROM sales_returns") == 0


def test_cashier_cannot_apply_invoice_discount(shop: Shop):
    lines = [{
        "product_id": shop.lot_product, "unit_id": shop.meter,
        "quantity_micros": 1_000_000, "unit_price_micros": 30_000_000,
    }]
    with pytest.raises(PermissionDenied):
        shop.sales.create_invoice(
            shop.retail, "2026-11-10", lines,
            invoice_discount_piastres=1, actor_id=shop.cashier,
        )
    assert shop.db.scalar("SELECT COUNT(*) FROM sales") == 0


def test_cashier_cannot_cancel_draft(shop: Shop):
    invoice = shop.sale(5_000_000, 30_000_000, actor=shop.cashier)
    with pytest.raises(PermissionDenied):
        shop.sales.cancel_draft(invoice, "إلغاء", actor_id=shop.cashier)
    assert shop.db.scalar("SELECT status FROM sales WHERE id=?", (invoice,)) == "DRAFT"


def test_user_without_post_sales_permission_cannot_post(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = shop.sale(20_000_000, 30_000_000, actor=shop.manager)
    viewer = shop.auth.create_user("viewer", "Viewer", "viewer-pass", [])
    with pytest.raises(PermissionDenied):
        shop.sales.post_invoice(invoice, actor_id=viewer)
    assert shop.db.scalar("SELECT status FROM sales WHERE id=?", (invoice,)) == "DRAFT"
    assert shop.db.scalar("SELECT COUNT(*) FROM stock_movements WHERE source_type='SALES_INVOICE'") == 0


# ---------------------------------------------------------------------------
# Customer receipts, credits and statements
# ---------------------------------------------------------------------------

def test_customer_receipt_allocation_and_statement(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = shop.sale(20_000_000, 30_000_000, actor=shop.manager)
    shop.sales.post_invoice(invoice, actor_id=shop.manager)
    receipt = shop.sales.create_receipt(
        shop.retail, shop.cash, 10_000, "2026-11-11",
        allocations=[{"sales_id": invoice, "amount_piastres": 10_000}],
        actor_id=shop.cashier,
    )
    result = shop.sales.post_receipt(receipt, actor_id=shop.cashier)
    assert result["allocated"] == 10_000
    assert result["customer_credit"] == 0
    assert shop.accounting.account_balance("1010") == 10_000
    assert shop.accounting.account_balance("1100") == 50_000
    assert shop.sales.invoice_balance(invoice) == 50_000
    assert shop.sales.customer_balance(shop.retail) == 50_000
    statement = shop.sales.customer_statement(shop.retail)
    assert [row["source_type"] for row in statement] == ["SALES_INVOICE", "CUSTOMER_RECEIPT"]
    assert statement[-1]["balance"] == 50_000
    assert all(row["entry_no"] for row in statement)
    sale_doc = shop.db.scalar("SELECT document_no FROM sales WHERE id=?", (invoice,))
    receipt_doc = shop.db.scalar("SELECT document_no FROM customer_receipts WHERE id=?", (receipt,))
    assert statement[0]["account_code"] == "1100"
    assert statement[0]["kind"] == "AR"
    assert statement[0]["affects_ar"] is True
    assert statement[0]["affects_credit"] is False
    assert statement[0]["document_no"] == sale_doc
    assert statement[0]["ar_balance"] == 60_000
    assert statement[0]["credit_balance"] == 0
    assert statement[-1]["account_code"] == "1100"
    assert statement[-1]["document_no"] == receipt_doc
    assert statement[-1]["ar_balance"] == 50_000
    assert shop.sales.customer_credit_balance(shop.retail) == 0
    assert shop.sales.customer_account_summary(shop.retail) == {
        "ar_balance": 50_000, "credit_balance": 0,
    }
    assert all(row["ar_balance"] >= 0 for row in statement)


def test_customer_statement_shows_credits_without_negative_ar(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = shop.sale(20_000_000, 30_000_000, actor=shop.manager)
    shop.sales.post_invoice(invoice, actor_id=shop.manager)
    receipt = shop.sales.create_receipt(
        shop.retail, shop.cash, 70_000, "2026-11-11",
        allocations=[{"sales_id": invoice, "amount_piastres": 60_000}],
        actor_id=shop.manager,
    )
    shop.sales.post_receipt(receipt, actor_id=shop.manager)

    second = shop.sale(10_000_000, 30_000_000, date="2026-11-12", actor=shop.manager)
    shop.sales.post_invoice(second, actor_id=shop.manager)
    credit_id = shop.db.scalar("SELECT id FROM customer_credits WHERE customer_id=?", (shop.retail,))
    shop.sales.apply_customer_credit(
        credit_id, second, 10_000, actor_id=shop.manager, application_date="2026-11-13",
    )

    statement = shop.sales.customer_statement(shop.retail)
    credit_rows = [row for row in statement if row["kind"] == "CUSTOMER_CREDIT"]
    assert len(credit_rows) == 2
    assert all(row["account_code"] == "2020" for row in credit_rows)
    assert all(row["affects_ar"] is False for row in credit_rows)
    assert all(row["affects_credit"] is True for row in credit_rows)
    assert credit_rows[0]["source_type"] == "CUSTOMER_RECEIPT"
    assert credit_rows[0]["credit"] == 10_000
    assert credit_rows[0]["credit_balance"] == 10_000
    assert credit_rows[0]["ar_balance"] == 0
    assert credit_rows[1]["source_type"] == "CUSTOMER_CREDIT_APPLICATION"
    assert credit_rows[1]["debit"] == 10_000
    assert credit_rows[1]["credit_balance"] == 0
    assert credit_rows[1]["ar_balance"] == 30_000  # application debit does not alter AR
    assert shop.sales.customer_balance(shop.retail) == 20_000
    assert shop.sales.customer_credit_balance(shop.retail) == 0
    assert shop.sales.customer_account_summary(shop.retail) == {
        "ar_balance": 20_000, "credit_balance": 0,
    }
    assert all(row["ar_balance"] >= 0 for row in statement)
    assert all(row["credit_balance"] >= 0 for row in statement)


def test_T_BAL_006_ar_never_negative_after_receipts_and_returns(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = shop.sale(20_000_000, 30_000_000, actor=shop.manager)
    shop.sales.post_invoice(invoice, actor_id=shop.manager)
    line_id = shop.db.scalar("SELECT id FROM sales_lines WHERE sales_id=?", (invoice,))
    receipt = shop.sales.create_receipt(
        shop.retail, shop.cash, 20_000, "2026-11-11",
        allocations=[{"sales_id": invoice, "amount_piastres": 20_000}],
        actor_id=shop.manager,
    )
    shop.sales.post_receipt(receipt, actor_id=shop.manager)

    first = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 10_000_000, "condition": "SELLABLE"}],
        "2026-11-12", "مرتجع أول", actor_id=shop.manager,
    )
    first_result = shop.sales.post_return(first, actor_id=shop.manager)
    assert first_result["ar_reduction"] == 30_000
    assert first_result["customer_credit"] == 0

    second = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 10_000_000, "condition": "SELLABLE"}],
        "2026-11-13", "مرتجع ثانٍ", actor_id=shop.manager,
    )
    second_result = shop.sales.post_return(second, actor_id=shop.manager)
    assert second_result["ar_reduction"] == 10_000
    assert second_result["customer_credit"] == 20_000
    assert shop.sales.invoice_balance(invoice) == 0
    assert shop.sales.customer_balance(shop.retail) == 0
    assert shop.sales.customer_credit_balance(shop.retail) == 20_000
    assert shop.sales.customer_account_summary(shop.retail) == {
        "ar_balance": 0, "credit_balance": 20_000,
    }

    statement = shop.sales.customer_statement(shop.retail)
    ar_rows = [row for row in statement if row["kind"] == "AR"]
    credit_rows = [row for row in statement if row["kind"] == "CUSTOMER_CREDIT"]
    assert [row["source_type"] for row in ar_rows] == [
        "SALES_INVOICE", "CUSTOMER_RECEIPT", "SALES_RETURN", "SALES_RETURN",
    ]
    assert [row["ar_balance"] for row in ar_rows] == [60_000, 40_000, 10_000, 0]
    assert len(credit_rows) == 1
    assert credit_rows[0]["source_type"] == "SALES_RETURN"
    assert credit_rows[0]["account_code"] == "2020"
    assert credit_rows[0]["credit"] == 20_000
    assert credit_rows[0]["credit_balance"] == 20_000
    assert credit_rows[0]["ar_balance"] == 0
    assert all(row["ar_balance"] >= 0 for row in statement)
    assert all(row["credit_balance"] >= 0 for row in statement)
    for row in statement:
        assert row["document_no"]


def test_receipt_over_allocation_is_rejected(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = shop.sale(20_000_000, 30_000_000, actor=shop.manager)
    shop.sales.post_invoice(invoice, actor_id=shop.manager)
    with pytest.raises(ValueError):
        shop.sales.create_receipt(
            shop.retail, shop.cash, 60_001, "2026-11-11",
            allocations=[{"sales_id": invoice, "amount_piastres": 60_001}],
            actor_id=shop.manager,
        )
    receipt = shop.sales.create_receipt(
        shop.retail, shop.cash, 60_000, "2026-11-11",
        allocations=[{"sales_id": invoice, "amount_piastres": 60_000}],
        actor_id=shop.manager,
    )
    shop.sales.post_receipt(receipt, actor_id=shop.manager)
    with pytest.raises(ValueError):
        shop.sales.create_receipt(
            shop.retail, shop.cash, 1, "2026-11-12",
            allocations=[{"sales_id": invoice, "amount_piastres": 1}],
            actor_id=shop.manager,
        )


def test_receipt_excess_becomes_customer_credit_and_can_be_applied(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = shop.sale(20_000_000, 30_000_000, actor=shop.manager)
    shop.sales.post_invoice(invoice, actor_id=shop.manager)
    receipt = shop.sales.create_receipt(
        shop.retail, shop.cash, 70_000, "2026-11-11",
        allocations=[{"sales_id": invoice, "amount_piastres": 60_000}],
        actor_id=shop.manager,
    )
    result = shop.sales.post_receipt(receipt, actor_id=shop.manager)
    assert result["allocated"] == 60_000
    assert result["customer_credit"] == 10_000
    assert shop.accounting.account_balance("2020") == -10_000
    assert shop.sales.invoice_balance(invoice) == 0

    second = shop.sale(10_000_000, 30_000_000, actor=shop.manager)
    shop.sales.post_invoice(second, actor_id=shop.manager)
    credit_id = shop.db.scalar("SELECT id FROM customer_credits WHERE customer_id=?", (shop.retail,))
    shop.sales.apply_customer_credit(credit_id, second, 10_000, actor_id=shop.manager)
    assert shop.accounting.account_balance("2020") == 0
    assert shop.sales.invoice_balance(second) == 20_000
    assert shop.sales.customer_balance(shop.retail) == 20_000


def test_receipt_against_cash_invoice_is_rejected(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = shop.sale(20_000_000, 30_000_000, payment_type="CASH", actor=shop.cashier)
    shop.sales.post_invoice(invoice, actor_id=shop.cashier)
    with pytest.raises(ValueError):
        shop.sales.create_receipt(
            shop.retail, shop.cash, 1_000, "2026-11-11",
            allocations=[{"sales_id": invoice, "amount_piastres": 1_000}],
            actor_id=shop.manager,
        )
