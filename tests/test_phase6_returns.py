from __future__ import annotations

import sqlite3

import pytest

from mizan.accounting import AccountingService
from mizan.auth import PermissionDenied
from mizan.reporting import ReportingService
from phase6_support import Shop, build_shop


@pytest.fixture()
def shop(db) -> Shop:
    return build_shop(db)


def _make_sale(shop: Shop, *, qty: int = 20_000_000, price: int = 30_000_000,
               payment_type: str = "CREDIT", actor: int | None = None) -> int:
    actor = actor or shop.manager
    invoice = shop.sale(qty, price, payment_type=payment_type, actor=actor)
    shop.sales.post_invoice(invoice, actor_id=actor)
    return invoice


def _sale_line_id(shop: Shop, invoice: int) -> int:
    return int(shop.db.scalar("SELECT id FROM sales_lines WHERE sales_id=?", (invoice,)))


def _return_line_id(shop: Shop, return_id: int) -> int:
    return int(shop.db.scalar("SELECT id FROM sales_return_lines WHERE sales_return_id=?", (return_id,)))


def _return_journal_accounts(shop: Shop, return_id: int) -> list[str]:
    with shop.db.connect() as conn:
        rows = conn.execute(
            "SELECT a.code FROM journal_lines l JOIN accounts a ON a.id=l.account_id "
            "JOIN journal_entries e ON e.id=l.journal_entry_id "
            "WHERE e.source_type='SALES_RETURN' AND e.source_id=? ORDER BY l.line_no",
            (return_id,),
        ).fetchall()
    return [row["code"] for row in rows]


def _assert_journal_balanced(shop: Shop, journal_id: int) -> None:
    with shop.db.connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(debit_piastres),0) AS debit,"
            "COALESCE(SUM(credit_piastres),0) AS credit FROM journal_lines "
            "WHERE journal_entry_id=?", (journal_id,)
        ).fetchone()
    assert row["debit"] == row["credit"] > 0


# ---------------------------------------------------------------------------
# Sellable and damaged returns
# ---------------------------------------------------------------------------

def test_T_RET_001_sellable_return_restores_inventory_and_cogs(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = _make_sale(shop)
    line_id = _sale_line_id(shop, invoice)
    return_id = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 20_000_000, "condition": "SELLABLE"}],
        "2026-11-12", "مرتجع عميل قابل للبيع", actor_id=shop.manager,
    )
    result = shop.sales.post_return(return_id, actor_id=shop.manager)
    _assert_journal_balanced(shop, result["journal_id"])
    assert result["commercial_total"] == 60_000
    assert result["inventory_cost"] == 44_000
    assert result["ar_reduction"] == 60_000
    assert result["customer_credit"] == 0
    assert result["refund"] == 0
    assert shop.accounting.account_balance("4020") == 60_000
    assert shop.accounting.account_balance("1100") == 0
    assert shop.accounting.account_balance("1200") == 220_000
    assert shop.accounting.account_balance("5010") == 0
    assert _return_journal_accounts(shop, return_id) == ["4020", "1100", "1200", "5010"]
    assert shop.purchasing.lot_remaining(1) == (100_000_000, 220_000)
    assert shop.sales.returnable_allocations(line_id) == []


def test_T_RET_002_damaged_return_direct_loss_accounting(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = _make_sale(shop)
    line_id = _sale_line_id(shop, invoice)
    return_id = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 20_000_000, "condition": "DAMAGED"}],
        "2026-11-12", "مرتجع تالف", actor_id=shop.manager,
    )
    result = shop.sales.post_return(return_id, actor_id=shop.manager)
    _assert_journal_balanced(shop, result["journal_id"])
    assert result["inventory_cost"] == 44_000
    # Approved OQ-INV-004: direct loss reclassification 5020/5010.
    assert _return_journal_accounts(shop, return_id) == ["4020", "1100", "5020", "5010"]
    assert shop.accounting.account_balance("4020") == 60_000
    assert shop.accounting.account_balance("1100") == 0
    assert shop.accounting.account_balance("1200") == 176_000
    assert shop.accounting.account_balance("5010") == 0
    assert shop.accounting.account_balance("5020") == 44_000
    with shop.db.connect() as conn:
        row = conn.execute(
            "SELECT commercial_total_piastres,inventory_cost_piastres,damaged_cost_accounting "
            "FROM sales_returns WHERE id=?", (return_id,)
        ).fetchone()
    assert tuple(row) == (60_000, 44_000, "POSTED_5020_5010")
    with shop.db.connect() as conn:
        damage = conn.execute(
            "SELECT * FROM stock_movements WHERE movement_type='DAMAGE' AND source_type='SALES_RETURN' "
            "AND source_id=?", (return_id,)
        ).fetchone()
        layer = conn.execute(
            "SELECT * FROM inventory_cost_layers WHERE layer_type='CUSTOMER_RETURN'"
        ).fetchone()
    assert damage["cost_piastres"] == 44_000
    assert damage["quantity_micros"] == 20_000_000
    assert layer["status"] == "DAMAGED"
    assert layer["remaining_qty_micros"] == 0
    assert shop.purchasing.lot_remaining(1) == (80_000_000, 176_000)


def test_T_RET_003_and_014_partial_return_cost_rounding(shop: Shop):
    shop.purchase(2_000_000, 11_665_000)  # layer 2m / 2,333 piastres
    invoice = _make_sale(shop, qty=2_000_000, price=20_000_000)
    line_id = _sale_line_id(shop, invoice)

    first = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 500_000, "condition": "SELLABLE"}],
        "2026-11-12", "مرتجع 0.5", actor_id=shop.manager,
    )
    first_result = shop.sales.post_return(first, actor_id=shop.manager)
    assert first_result["commercial_total"] == 1_000
    assert first_result["inventory_cost"] == 583

    second = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 500_000, "condition": "SELLABLE"}],
        "2026-11-13", "مرتجع 0.5 ثانٍ", actor_id=shop.manager,
    )
    second_result = shop.sales.post_return(second, actor_id=shop.manager)
    assert second_result["inventory_cost"] == 584

    remaining = shop.sales.returnable_allocations(line_id)
    assert len(remaining) == 1
    assert remaining[0]["remaining_qty_micros"] == 1_000_000
    assert remaining[0]["remaining_cost_piastres"] == 1_166

    third = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 1_000_000, "condition": "SELLABLE"}],
        "2026-11-14", "باقي المرتجع", actor_id=shop.manager,
    )
    third_result = shop.sales.post_return(third, actor_id=shop.manager)
    assert third_result["inventory_cost"] == 1_166
    assert shop.sales.returnable_allocations(line_id) == []


def test_T_RET_004_and_016_resell_returned_layer_at_original_cost(shop: Shop):
    shop.purchase(2_000_000, 11_665_000)
    invoice = _make_sale(shop, qty=2_000_000, price=20_000_000)
    line_id = _sale_line_id(shop, invoice)
    return_id = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 1_000_000, "condition": "SELLABLE"}],
        "2026-11-12", "مرتجع", actor_id=shop.manager,
    )
    first = shop.sales.post_return(return_id, actor_id=shop.manager)
    assert first["inventory_cost"] == 1_167

    resale = shop.sale(1_000_000, 20_000_000, actor=shop.manager)
    resale_result = shop.sales.post_invoice(resale, actor_id=shop.manager)
    assert resale_result["cost"] == 1_167
    assert shop.accounting.account_balance("1200") == 0


def test_T_RET_005_and_019_over_return_is_rejected_without_effects(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = _make_sale(shop)
    line_id = _sale_line_id(shop, invoice)
    with pytest.raises(ValueError):
        shop.sales.create_return(
            invoice,
            [{"sales_line_id": line_id, "quantity_micros": 30_000_000, "condition": "SELLABLE"}],
            "2026-11-12", "زيادة", actor_id=shop.manager,
        )
    assert shop.db.scalar("SELECT COUNT(*) FROM sales_returns") == 0
    assert shop.db.scalar("SELECT COUNT(*) FROM stock_movements WHERE movement_type='SALE_RETURN_IN'") == 0
    assert shop.db.scalar("SELECT COUNT(*) FROM journal_entries WHERE source_type='SALES_RETURN'") == 0


def test_T_RET_006_duplicate_return_is_rejected(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = _make_sale(shop)
    line_id = _sale_line_id(shop, invoice)
    return_id = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 20_000_000, "condition": "SELLABLE"}],
        "2026-11-12", "مرتجع", actor_id=shop.manager,
    )
    shop.sales.post_return(return_id, actor_id=shop.manager)
    with pytest.raises(ValueError):
        shop.sales.create_return(
            invoice,
            [{"sales_line_id": line_id, "quantity_micros": 20_000_000, "condition": "SELLABLE"}],
            "2026-11-13", "تكرار", actor_id=shop.manager,
        )
    with pytest.raises(ValueError):
        shop.sales.post_return(return_id, actor_id=shop.manager)
    assert shop.db.scalar("SELECT COUNT(*) FROM sales_returns WHERE status='POSTED'") == 1


# ---------------------------------------------------------------------------
# OQ-SAL-007 settlement
# ---------------------------------------------------------------------------

def test_T_RET_007_and_009_ar_is_reduced_before_credit(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = _make_sale(shop)
    line_id = _sale_line_id(shop, invoice)
    receipt = shop.sales.create_receipt(
        shop.retail, shop.cash, 10_000, "2026-11-11",
        allocations=[{"sales_id": invoice, "amount_piastres": 10_000}],
        actor_id=shop.manager,
    )
    shop.sales.post_receipt(receipt, actor_id=shop.manager)
    assert shop.sales.invoice_balance(invoice) == 50_000

    return_id = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 10_000_000, "condition": "SELLABLE"}],
        "2026-11-12", "مرتجع جزئي", actor_id=shop.manager,
    )
    result = shop.sales.post_return(return_id, actor_id=shop.manager)
    assert result["ar_reduction"] == 30_000
    assert result["customer_credit"] == 0
    assert result["refund"] == 0
    assert shop.sales.invoice_balance(invoice) == 20_000
    assert shop.accounting.account_balance("2020") == 0
    assert shop.accounting.account_balance("1100") == 20_000


def test_T_RET_010_fully_paid_return_credits_without_negative_ar(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = _make_sale(shop)
    line_id = _sale_line_id(shop, invoice)
    receipt = shop.sales.create_receipt(
        shop.retail, shop.cash, 60_000, "2026-11-11",
        allocations=[{"sales_id": invoice, "amount_piastres": 60_000}],
        actor_id=shop.manager,
    )
    shop.sales.post_receipt(receipt, actor_id=shop.manager)
    assert shop.sales.invoice_balance(invoice) == 0

    return_id = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 20_000_000, "condition": "SELLABLE"}],
        "2026-11-12", "مرتجع كامل", actor_id=shop.manager,
    )
    result = shop.sales.post_return(return_id, actor_id=shop.manager)
    _assert_journal_balanced(shop, result["journal_id"])
    assert result["ar_reduction"] == 0
    assert result["customer_credit"] == 60_000
    assert result["refund"] == 0
    assert shop.sales.invoice_balance(invoice) == 0
    assert shop.sales.customer_balance(shop.retail) == 0
    assert shop.accounting.account_balance("1100") == 0
    assert shop.accounting.account_balance("2020") == -60_000


def test_cash_refund_requires_permission_and_document(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = _make_sale(shop)
    line_id = _sale_line_id(shop, invoice)
    receipt = shop.sales.create_receipt(
        shop.retail, shop.cash, 60_000, "2026-11-11",
        allocations=[{"sales_id": invoice, "amount_piastres": 60_000}],
        actor_id=shop.manager,
    )
    shop.sales.post_receipt(receipt, actor_id=shop.manager)

    with pytest.raises(ValueError):
        shop.sales.create_return(
            invoice,
            [{"sales_line_id": line_id, "quantity_micros": 20_000_000, "condition": "SELLABLE"}],
            "2026-11-12", "رد نقدي", settlement_method="CASH_REFUND",
            refund_piastres=60_000, refund_reference=None,
            financial_account_id=shop.cash, actor_id=shop.manager,
        )

    refund_return = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 20_000_000, "condition": "SELLABLE"}],
        "2026-11-12", "رد نقدي", settlement_method="CASH_REFUND",
        refund_piastres=60_000, refund_reference="RF-1",
        financial_account_id=shop.cash, actor_id=shop.manager,
    )
    result = shop.sales.post_return(refund_return, actor_id=shop.manager)
    assert result["refund"] == 60_000
    assert result["customer_credit"] == 0
    assert shop.accounting.account_balance("1010") == 0
    assert shop.accounting.account_balance("2020") == 0

    # A return user without issue_customer_refund cannot post a refund.
    invoice2 = _make_sale(shop)
    line2 = _sale_line_id(shop, invoice2)
    receipt2 = shop.sales.create_receipt(
        shop.retail, shop.cash, 60_000, "2026-11-13",
        allocations=[{"sales_id": invoice2, "amount_piastres": 60_000}],
        actor_id=shop.manager,
    )
    shop.sales.post_receipt(receipt2, actor_id=shop.manager)
    refund2 = shop.sales.create_return(
        invoice2,
        [{"sales_line_id": line2, "quantity_micros": 20_000_000, "condition": "SELLABLE"}],
        "2026-11-14", "رد نقدي", settlement_method="CASH_REFUND",
        refund_piastres=60_000, refund_reference="RF-2",
        financial_account_id=shop.cash, actor_id=shop.manager,
    )
    returns_user = shop.db.scalar("SELECT id FROM users WHERE username='returns'")
    with pytest.raises(PermissionDenied):
        shop.sales.post_return(refund2, actor_id=returns_user)
    assert shop.db.scalar("SELECT status FROM sales_returns WHERE id=?", (refund2,)) == "DRAFT"
    assert shop.db.scalar("SELECT COUNT(*) FROM journal_entries WHERE source_type='SALES_RETURN' AND source_id=?", (refund2,)) == 0


def test_cash_sale_return_defaults_to_customer_credit(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = shop.sale(20_000_000, 30_000_000, payment_type="CASH", actor=shop.cashier)
    shop.sales.post_invoice(invoice, actor_id=shop.cashier)
    assert shop.accounting.account_balance("1010") == 60_000
    line_id = _sale_line_id(shop, invoice)
    return_id = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 20_000_000, "condition": "SELLABLE"}],
        "2026-11-12", "مرتجع نقدي", actor_id=shop.manager,
    )
    result = shop.sales.post_return(return_id, actor_id=shop.manager)
    _assert_journal_balanced(shop, result["journal_id"])
    assert result["ar_reduction"] == 0
    assert result["customer_credit"] == 60_000
    assert result["refund"] == 0
    assert shop.accounting.account_balance("1010") == 60_000
    assert shop.accounting.account_balance("2020") == -60_000
    assert shop.accounting.account_balance("4020") == 60_000
    statement = shop.sales.customer_statement(shop.retail)
    credit_rows = [row for row in statement if row["kind"] == "CUSTOMER_CREDIT"]
    assert len(credit_rows) == 1
    assert credit_rows[0]["account_code"] == "2020"
    assert credit_rows[0]["credit"] == 60_000
    assert credit_rows[0]["ar_balance"] == 0
    assert credit_rows[0]["credit_balance"] == 60_000
    assert shop.sales.customer_credit_balance(shop.retail) == 60_000


def test_credit_only_settlement_rejected_while_ar_outstanding(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = _make_sale(shop)
    line_id = _sale_line_id(shop, invoice)
    return_id = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 20_000_000, "condition": "SELLABLE"}],
        "2026-11-12", "رصيد فقط", settlement_method="CUSTOMER_CREDIT",
        actor_id=shop.manager,
    )
    with pytest.raises(ValueError):
        shop.sales.post_return(return_id, actor_id=shop.manager)
    assert shop.db.scalar("SELECT status FROM sales_returns WHERE id=?", (return_id,)) == "DRAFT"
    assert shop.db.scalar(
        "SELECT COUNT(*) FROM journal_entries WHERE source_type='SALES_RETURN' AND source_id=?",
        (return_id,),
    ) == 0
    assert shop.db.scalar("SELECT COUNT(*) FROM inventory_cost_layers WHERE layer_type='CUSTOMER_RETURN'") == 0


def test_cash_refund_requires_cash_account(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = _make_sale(shop)
    line_id = _sale_line_id(shop, invoice)
    receipt = shop.sales.create_receipt(
        shop.retail, shop.cash, 60_000, "2026-11-11",
        allocations=[{"sales_id": invoice, "amount_piastres": 60_000}],
        actor_id=shop.manager,
    )
    shop.sales.post_receipt(receipt, actor_id=shop.manager)
    return_id = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 20_000_000, "condition": "SELLABLE"}],
        "2026-11-12", "رد نقدي لحساب بنكي", settlement_method="CASH_REFUND",
        refund_piastres=60_000, refund_reference="RF-3",
        financial_account_id=shop.bank, actor_id=shop.manager,
    )
    with pytest.raises(ValueError):
        shop.sales.post_return(return_id, actor_id=shop.manager)
    assert shop.db.scalar("SELECT status FROM sales_returns WHERE id=?", (return_id,)) == "DRAFT"
    assert shop.db.scalar(
        "SELECT COUNT(*) FROM journal_entries WHERE source_type='SALES_RETURN' AND source_id=?",
        (return_id,),
    ) == 0


# ---------------------------------------------------------------------------
# OQ-INV-003 allocation choice
# ---------------------------------------------------------------------------

def test_return_defaults_to_most_recently_consumed_allocation(shop: Shop):
    shop.purchase(100_000_000, 20_000_000, date="2026-11-01")
    shop.purchase(100_000_000, 22_000_000, date="2026-11-02")
    invoice = _make_sale(shop, qty=120_000_000)
    line_id = _sale_line_id(shop, invoice)
    with shop.db.connect() as conn:
        allocs = conn.execute(
            "SELECT id,quantity_micros,cost_piastres FROM lot_allocations "
            "WHERE allocation_role='SALE_ISSUE' AND sales_line_id=? ORDER BY id",
            (line_id,),
        ).fetchall()
    assert [(row["quantity_micros"], row["cost_piastres"]) for row in allocs] == [
        (100_000_000, 200_000), (20_000_000, 44_000),
    ]
    first_alloc, second_alloc = allocs[0]["id"], allocs[1]["id"]

    returnable = shop.sales.returnable_allocations(line_id)
    assert returnable[0]["allocation_id"] == second_alloc
    assert returnable[0]["remaining_qty_micros"] == 20_000_000
    assert returnable[1]["allocation_id"] == first_alloc

    first = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 10_000_000, "condition": "SELLABLE"}],
        "2026-11-12", "آخر استهلاك أولًا", actor_id=shop.manager,
    )
    result = shop.sales.post_return(first, actor_id=shop.manager)
    assert result["inventory_cost"] == 22_000
    with shop.db.connect() as conn:
        layer = conn.execute(
            "SELECT parent_issue_allocation_id FROM inventory_cost_layers "
            "WHERE layer_type='CUSTOMER_RETURN'"
        ).fetchone()
    assert layer["parent_issue_allocation_id"] == second_alloc

    second = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 10_000_000, "condition": "SELLABLE"}],
        "2026-11-13", "آخر استهلاك أولًا", actor_id=shop.manager,
    )
    assert shop.sales.post_return(second, actor_id=shop.manager)["inventory_cost"] == 22_000

    third = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 10_000_000, "condition": "SELLABLE"}],
        "2026-11-14", "التالي", actor_id=shop.manager,
    )
    assert shop.sales.post_return(third, actor_id=shop.manager)["inventory_cost"] == 20_000


def test_return_can_explicitly_select_an_older_allocation(shop: Shop):
    shop.purchase(100_000_000, 20_000_000, date="2026-11-01")
    shop.purchase(100_000_000, 22_000_000, date="2026-11-02")
    invoice = _make_sale(shop, qty=120_000_000)
    line_id = _sale_line_id(shop, invoice)
    returnable = shop.sales.returnable_allocations(line_id)
    oldest = next(row for row in returnable if row["remaining_qty_micros"] == 100_000_000)

    return_id = shop.sales.create_return(
        invoice,
        [{
            "sales_line_id": line_id,
            "quantity_micros": 10_000_000,
            "condition": "SELLABLE",
            "allocation_selections": [{
                "kind": "LOT", "allocation_id": oldest["allocation_id"],
                "quantity_micros": 10_000_000,
            }],
        }],
        "2026-11-12", "اختيار يدوي", actor_id=shop.manager,
    )
    result = shop.sales.post_return(return_id, actor_id=shop.manager)
    assert result["inventory_cost"] == 20_000


def test_return_uses_original_price_and_cost_after_changes(shop: Shop):
    with shop.db.transaction() as conn:
        conn.execute(
            "INSERT INTO product_default_prices(product_id,unit_id,price_micros) VALUES(?,?,?)",
            (shop.lot_product, shop.meter, 30_000_000),
        )
    shop.purchase(100_000_000, 22_000_000)
    invoice = _make_sale(shop, qty=20_000_000)
    line_id = _sale_line_id(shop, invoice)
    with shop.db.transaction() as conn:
        conn.execute("UPDATE product_default_prices SET price_micros=50_000_000")
    shop.purchase(100_000_000, 30_000_000, date="2026-11-20")
    return_id = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 10_000_000, "condition": "SELLABLE"}],
        "2026-11-21", "بعد تغير السعر", actor_id=shop.manager,
    )
    result = shop.sales.post_return(return_id, actor_id=shop.manager)
    _assert_journal_balanced(shop, result["journal_id"])
    assert result["commercial_total"] == 30_000
    assert result["inventory_cost"] == 22_000


def test_return_selection_must_cover_exact_quantity(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = _make_sale(shop)
    line_id = _sale_line_id(shop, invoice)
    returnable = shop.sales.returnable_allocations(line_id)
    with pytest.raises(ValueError):
        shop.sales.create_return(
            invoice,
            [{
                "sales_line_id": line_id,
                "quantity_micros": 10_000_000,
                "condition": "SELLABLE",
                "allocation_selections": [{
                    "kind": "LOT", "allocation_id": returnable[0]["allocation_id"],
                    "quantity_micros": 5_000_000,
                }],
            }],
            "2026-11-12", "نقص اختيار", actor_id=shop.manager,
        )
    assert shop.db.scalar("SELECT COUNT(*) FROM sales_returns") == 0


def test_return_requires_reason(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = _make_sale(shop)
    line_id = _sale_line_id(shop, invoice)
    with pytest.raises(ValueError):
        shop.sales.create_return(
            invoice,
            [{"sales_line_id": line_id, "quantity_micros": 10_000_000, "condition": "SELLABLE"}],
            "2026-11-12", "   ", actor_id=shop.manager,
        )


def test_T_RET_009_unpaid_invoice_reduces_ar_only(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = _make_sale(shop)
    line_id = _sale_line_id(shop, invoice)
    return_id = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 10_000_000, "condition": "SELLABLE"}],
        "2026-11-12", "مرتجع على فاتورة غير مسددة", actor_id=shop.manager,
    )
    result = shop.sales.post_return(return_id, actor_id=shop.manager)
    assert result["ar_reduction"] == 30_000
    assert result["customer_credit"] == 0
    assert result["refund"] == 0
    assert shop.sales.invoice_balance(invoice) == 30_000
    assert shop.accounting.account_balance("1100") == 30_000
    assert shop.accounting.account_balance("2020") == 0


def test_customer_return_layer_requires_parent_allocation(shop: Shop):
    shop.purchase(3_000_000, 333_333)
    lot_id = shop.db.scalar("SELECT id FROM purchase_lots LIMIT 1")
    with pytest.raises(sqlite3.IntegrityError):
        with shop.db.transaction() as conn:
            conn.execute(
                "INSERT INTO inventory_cost_layers(lot_id,layer_type,original_qty_micros,"
                "original_cost_piastres,position_micros,remaining_qty_micros,"
                "remaining_cost_piastres,fifo_anchor_at) "
                "VALUES(?,'CUSTOMER_RETURN',1000000,34,0,1000000,34,'2026-11-12')",
                (lot_id,),
            )


def test_T_RET_018_reissued_return_is_returned_again_as_child_layer(shop: Shop):
    shop.purchase(3_000_000, 333_333)  # 3m / 100 piastres
    first_sale = _make_sale(shop, qty=2_000_000, price=1_000_000)
    second_sale = _make_sale(shop, qty=1_000_000, price=1_000_000)
    first_line = _sale_line_id(shop, first_sale)
    assert _sale_line_id(shop, second_sale) > first_line

    first_return = shop.sales.create_return(
        first_sale,
        [{"sales_line_id": first_line, "quantity_micros": 1_000_000, "condition": "SELLABLE"}],
        "2026-11-12", "مرتجع أول", actor_id=shop.manager,
    )
    first_result = shop.sales.post_return(first_return, actor_id=shop.manager)
    assert first_result["inventory_cost"] == 34

    reissue = shop.sale(1_000_000, 1_000_000, actor=shop.manager)
    reissue_result = shop.sales.post_invoice(reissue, actor_id=shop.manager)
    assert reissue_result["cost"] == 34
    reissue_line = _sale_line_id(shop, reissue)

    second_return = shop.sales.create_return(
        reissue,
        [{"sales_line_id": reissue_line, "quantity_micros": 1_000_000, "condition": "SELLABLE"}],
        "2026-11-13", "مرتجع ثانٍ", actor_id=shop.manager,
    )
    second_result = shop.sales.post_return(second_return, actor_id=shop.manager)
    assert second_result["inventory_cost"] == 34

    with shop.db.connect() as conn:
        child = conn.execute(
            "SELECT parent_issue_allocation_id,layer_type,remaining_qty_micros,remaining_cost_piastres "
            "FROM inventory_cost_layers WHERE layer_type='CUSTOMER_RETURN' AND status='OPEN'"
        ).fetchone()
        reissue_alloc = conn.execute(
            "SELECT id FROM lot_allocations WHERE allocation_role='SALE_ISSUE' AND sales_line_id=?",
            (reissue_line,),
        ).fetchone()
    assert child["parent_issue_allocation_id"] == reissue_alloc["id"]
    assert child["remaining_qty_micros"] == 1_000_000
    assert child["remaining_cost_piastres"] == 34
    # Q0=3m and C0=100 piastres are conserved after the reissue/return cycle.
    with shop.db.connect() as conn:
        remaining = conn.execute(
            "SELECT COALESCE(SUM(remaining_qty_micros),0),COALESCE(SUM(remaining_cost_piastres),0) "
            "FROM inventory_cost_layers"
        ).fetchone()
        issued = conn.execute(
            "SELECT COALESCE(SUM(quantity_micros),0),COALESCE(SUM(cost_piastres),0) "
            "FROM lot_allocations WHERE allocation_role='SALE_ISSUE'"
        ).fetchone()
        returned = conn.execute(
            "SELECT COALESCE(SUM(quantity_micros),0),COALESCE(SUM(cost_piastres),0) "
            "FROM lot_allocations WHERE allocation_role='SALE_RETURN'"
        ).fetchone()
    assert remaining[0] == 1_000_000
    assert remaining[1] == 34
    assert issued[0] - returned[0] + remaining[0] == 3_000_000
    assert issued[1] - returned[1] + remaining[1] == 100


# ---------------------------------------------------------------------------
# Mixed damaged/sellable and rollback
# ---------------------------------------------------------------------------

def test_T_RET_015_multiple_allocations_and_micro_reissue(shop: Shop):
    shop.purchase(3_000_000, 333_333)  # 3m / 100 piastres
    invoice = shop.sale(0, None, lines=[
        {"product_id": shop.lot_product, "unit_id": shop.meter,
         "quantity_micros": 1_000_000, "unit_price_micros": 1_000_000},
        {"product_id": shop.lot_product, "unit_id": shop.meter,
         "quantity_micros": 1_000_000, "unit_price_micros": 1_000_000},
        {"product_id": shop.lot_product, "unit_id": shop.meter,
         "quantity_micros": 1_000_000, "unit_price_micros": 1_000_000},
    ], actor=shop.manager)
    shop.sales.post_invoice(invoice, actor_id=shop.manager)
    with shop.db.connect() as conn:
        sales_lines = conn.execute(
            "SELECT id,cost_piastres FROM sales_lines WHERE sales_id=? ORDER BY line_no",
            (invoice,),
        ).fetchall()
        allocs = conn.execute(
            "SELECT id,sales_line_id FROM lot_allocations WHERE allocation_role='SALE_ISSUE' "
            "AND sales_line_id IN (SELECT id FROM sales_lines WHERE sales_id=?) ORDER BY id",
            (invoice,),
        ).fetchall()
    assert [row["cost_piastres"] for row in sales_lines] == [33, 34, 33]
    line1, line3 = sales_lines[0]["id"], sales_lines[2]["id"]
    return_id = shop.sales.create_return(
        invoice,
        [
            {"sales_line_id": line1, "quantity_micros": 1_000_000, "condition": "SELLABLE"},
            {"sales_line_id": line3, "quantity_micros": 1_000_000, "condition": "SELLABLE"},
        ],
        "2026-11-12", "إرجاع الأول والثالث", actor_id=shop.manager,
    )
    result = shop.sales.post_return(return_id, actor_id=shop.manager)
    _assert_journal_balanced(shop, result["journal_id"])
    assert result["inventory_cost"] == 66
    with shop.db.connect() as conn:
        return_lines = conn.execute(
            "SELECT cost_piastres FROM sales_return_lines WHERE sales_return_id=? ORDER BY line_no",
            (return_id,),
        ).fetchall()
        layers = conn.execute(
            "SELECT id,parent_issue_allocation_id,remaining_qty_micros,remaining_cost_piastres "
            "FROM inventory_cost_layers WHERE layer_type='CUSTOMER_RETURN' ORDER BY id"
        ).fetchall()
    assert [row["cost_piastres"] for row in return_lines] == [33, 33]
    assert [row["parent_issue_allocation_id"] for row in layers] == [
        allocs[0]["id"], allocs[2]["id"],
    ]

    # Reissue 0.001m from the first return layer: the LCP outflow can be zero,
    # but it must never be negative, and the remaining layer must stay correct.
    reissue = shop.sale(1_000, 10_000_000_000, actor=shop.manager)
    reissue_result = shop.sales.post_invoice(reissue, actor_id=shop.manager)
    assert reissue_result["cost"] == 0
    with shop.db.connect() as conn:
        layers = conn.execute(
            "SELECT remaining_qty_micros,remaining_cost_piastres FROM inventory_cost_layers "
            "WHERE layer_type='CUSTOMER_RETURN' ORDER BY id"
        ).fetchall()
    assert [(row["remaining_qty_micros"], row["remaining_cost_piastres"]) for row in layers] == [
        (999_000, 33), (1_000_000, 33),
    ]


def test_T_RET_017_mixed_sellable_and_damaged(shop: Shop):
    shop.purchase(100_000_000, 22_000_000)
    invoice = _make_sale(shop, qty=40_000_000)
    line_id = _sale_line_id(shop, invoice)
    return_id = shop.sales.create_return(
        invoice,
        [
            {"sales_line_id": line_id, "quantity_micros": 20_000_000, "condition": "SELLABLE"},
            {"sales_line_id": line_id, "quantity_micros": 20_000_000, "condition": "DAMAGED"},
        ],
        "2026-11-12", "مختلط", actor_id=shop.manager,
    )
    result = shop.sales.post_return(return_id, actor_id=shop.manager)
    _assert_journal_balanced(shop, result["journal_id"])
    assert result["commercial_total"] == 120_000
    assert result["inventory_cost"] == 88_000
    assert _return_journal_accounts(shop, return_id) == [
        "4020", "1100", "1200", "5010", "5020", "5010",
    ]
    assert shop.accounting.account_balance("1200") == 176_000
    assert shop.accounting.account_balance("5010") == 0
    assert shop.accounting.account_balance("5020") == 44_000
    with shop.db.connect() as conn:
        damaged = conn.execute(
            "SELECT * FROM inventory_cost_layers WHERE layer_type='CUSTOMER_RETURN' AND status='DAMAGED'"
        ).fetchone()
        sellable = conn.execute(
            "SELECT * FROM inventory_cost_layers WHERE layer_type='CUSTOMER_RETURN' AND status='OPEN'"
        ).fetchone()
    assert damaged["remaining_qty_micros"] == 0
    assert damaged["original_cost_piastres"] == 44_000
    assert sellable["remaining_qty_micros"] == 20_000_000
    assert sellable["remaining_cost_piastres"] == 44_000


def test_T_RB_002_damage_failure_rolls_back_all_effects(shop: Shop, monkeypatch):
    shop.purchase(100_000_000, 22_000_000)
    invoice = _make_sale(shop)
    line_id = _sale_line_id(shop, invoice)
    receipt = shop.sales.create_receipt(
        shop.retail, shop.cash, 60_000, "2026-11-11",
        allocations=[{"sales_id": invoice, "amount_piastres": 60_000}],
        actor_id=shop.manager,
    )
    shop.sales.post_receipt(receipt, actor_id=shop.manager)
    return_id = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 20_000_000, "condition": "DAMAGED"}],
        "2026-11-12", "تالف", settlement_method="MIXED",
        refund_piastres=20_000, refund_reference="RF-9",
        financial_account_id=shop.cash, actor_id=shop.manager,
    )

    def boom(*args, **kwargs):
        raise RuntimeError("forced DAMAGE failure")

    monkeypatch.setattr(shop.sales, "_damage_return_layer", boom)
    with pytest.raises(RuntimeError):
        shop.sales.post_return(return_id, actor_id=shop.manager)

    assert shop.db.scalar("SELECT status FROM sales_returns WHERE id=?", (return_id,)) == "DRAFT"
    assert shop.db.scalar(
        "SELECT COUNT(*) FROM stock_movements WHERE movement_type IN ('SALE_RETURN_IN','DAMAGE') "
        "AND source_type='SALES_RETURN' AND source_id=?", (return_id,)
    ) == 0
    assert shop.db.scalar("SELECT COUNT(*) FROM inventory_cost_layers WHERE layer_type='CUSTOMER_RETURN'") == 0
    assert shop.db.scalar("SELECT COUNT(*) FROM lot_allocations WHERE sales_return_line_id IS NOT NULL") == 0
    assert shop.db.scalar(
        "SELECT COUNT(*) FROM journal_entries WHERE source_type='SALES_RETURN' AND source_id=?", (return_id,)
    ) == 0
    assert shop.db.scalar(
        "SELECT COUNT(*) FROM customer_credits WHERE source_type='SALES_RETURN' AND source_id=?", (return_id,)
    ) == 0
    with shop.db.connect() as conn:
        row = conn.execute(
            "SELECT commercial_total_piastres,inventory_cost_piastres,ar_reduction_piastres,"
            "customer_credit_piastres,refund_piastres,journal_entry_id FROM sales_returns WHERE id=?",
            (return_id,),
        ).fetchone()
    assert tuple(row) == (0, 0, 0, 0, 20_000, None)  # refund stays a draft request
    assert shop.accounting.account_balance("1010") == 60_000
    assert shop.accounting.account_balance("2020") == 0
    assert shop.accounting.account_balance("4020") == 0
    assert shop.accounting.account_balance("1100") == 0
    assert len(shop.sales.returnable_allocations(line_id)) == 1
    assert shop.sales.returnable_allocations(line_id)[0]["remaining_qty_micros"] == 20_000_000

    # A failure while posting the damaged-cost journal must roll back the same way.
    monkeypatch.undo()

    def boom_journal(*args, **kwargs):
        raise RuntimeError("forced journal failure")

    monkeypatch.setattr(AccountingService, "post_journal", boom_journal)
    with pytest.raises(RuntimeError):
        shop.sales.post_return(return_id, actor_id=shop.manager)
    assert shop.db.scalar("SELECT status FROM sales_returns WHERE id=?", (return_id,)) == "DRAFT"
    assert shop.db.scalar(
        "SELECT COUNT(*) FROM stock_movements WHERE movement_type IN ('SALE_RETURN_IN','DAMAGE') "
        "AND source_type='SALES_RETURN' AND source_id=?", (return_id,)
    ) == 0
    assert shop.db.scalar("SELECT COUNT(*) FROM inventory_cost_layers WHERE layer_type='CUSTOMER_RETURN'") == 0
    assert shop.db.scalar(
        "SELECT COUNT(*) FROM journal_entries WHERE source_type='SALES_RETURN' AND source_id=?", (return_id,)
    ) == 0


# ---------------------------------------------------------------------------
# OQ-INV-012 returned reel identity
# ---------------------------------------------------------------------------

def test_T_INV_026_reel_return_creates_new_damaged_reel(shop: Shop):
    shop.purchase(100_000_000, 30_000_000, product=shop.reel_product,
                  reel_lengths=[100_000_000])
    invoice = shop.sale(10_000_000, 40_000_000, product=shop.reel_product, actor=shop.manager)
    shop.sales.post_invoice(invoice, actor_id=shop.manager)
    line_id = _sale_line_id(shop, invoice)
    with shop.db.connect() as conn:
        original_alloc = conn.execute(
            "SELECT * FROM reel_allocations WHERE sales_line_id=? AND allocation_role='SALE_ISSUE'",
            (line_id,),
        ).fetchone()
    original_reel_id = original_alloc["reel_id"]
    assert shop.purchasing.reel_remaining(original_reel_id) == (90_000_000, 270_000)

    return_id = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 10_000_000, "condition": "DAMAGED"}],
        "2026-11-12", "مرتجع بكرة تالف", actor_id=shop.manager,
    )
    result = shop.sales.post_return(return_id, actor_id=shop.manager)
    _assert_journal_balanced(shop, result["journal_id"])
    assert result["inventory_cost"] == 30_000
    assert _return_journal_accounts(shop, return_id) == ["4020", "1100", "5020", "5010"]
    assert shop.accounting.account_balance("5010") == 0
    assert shop.accounting.account_balance("5020") == 30_000

    with shop.db.connect() as conn:
        new_reel = conn.execute(
            "SELECT * FROM reels WHERE source_reel_id=? AND source_issue_allocation_id=?",
            (original_reel_id, original_alloc["id"]),
        ).fetchone()
        new_lot = conn.execute(
            "SELECT * FROM purchase_lots WHERE id=?", (new_reel["lot_id"],)
        ).fetchone()
        layer = conn.execute(
            "SELECT * FROM inventory_cost_layers WHERE reel_id=?", (new_reel["id"],)
        ).fetchone()
        damage = conn.execute(
            "SELECT * FROM stock_movements WHERE movement_type='DAMAGE' "
            "AND source_type='SALES_RETURN' AND source_id=?", (return_id,),
        ).fetchone()
        damage_alloc = conn.execute(
            "SELECT * FROM reel_allocations WHERE reel_id=? AND allocation_role='DAMAGE'",
            (new_reel["id"],),
        ).fetchone()
    assert new_reel["id"] != original_reel_id
    assert new_reel["original_length_micros"] == 10_000_000
    assert new_reel["original_cost_piastres"] == 30_000
    assert new_reel["status"] == "DAMAGED"
    assert new_lot["source_type"] == "RETURN"
    assert new_lot["original_qty_micros"] == 10_000_000
    assert new_lot["original_landed_cost_piastres"] == 30_000
    assert layer["layer_type"] == "CUSTOMER_RETURN"
    assert layer["parent_issue_allocation_id"] == original_alloc["id"]
    assert layer["status"] == "DAMAGED"
    assert layer["remaining_qty_micros"] == 0
    assert damage["reel_id"] == new_reel["id"]
    assert damage["cost_piastres"] == 30_000
    assert damage_alloc["source_issue_allocation_id"] == original_alloc["id"]
    assert shop.purchasing.reel_remaining(original_reel_id) == (90_000_000, 270_000)
    assert shop.purchasing.reel_remaining(new_reel["id"]) == (0, 0)


def test_T_RET_013_reel_return_creates_linked_new_reel(shop: Shop):
    shop.purchase(100_000_000, 30_000_000, product=shop.reel_product,
                  reel_lengths=[100_000_000])
    invoice = shop.sale(37_500_000, 40_000_000, product=shop.reel_product, actor=shop.manager)
    shop.sales.post_invoice(invoice, actor_id=shop.manager)
    line_id = _sale_line_id(shop, invoice)
    with shop.db.connect() as conn:
        original_alloc = conn.execute(
            "SELECT * FROM reel_allocations WHERE sales_line_id=? AND allocation_role='SALE_ISSUE'",
            (line_id,),
        ).fetchone()
    original_reel_id = original_alloc["reel_id"]
    assert shop.purchasing.reel_remaining(original_reel_id) == (62_500_000, 187_500)

    return_id = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 10_000_000, "condition": "SELLABLE"}],
        "2026-11-12", "مرتجع بكرة", actor_id=shop.manager,
    )
    result = shop.sales.post_return(return_id, actor_id=shop.manager)
    _assert_journal_balanced(shop, result["journal_id"])
    assert result["commercial_total"] == 40_000
    assert result["inventory_cost"] == 30_000
    assert result["ar_reduction"] == 40_000

    with shop.db.connect() as conn:
        new_reel = conn.execute(
            "SELECT * FROM reels WHERE source_reel_id=? AND source_issue_allocation_id=?",
            (original_reel_id, original_alloc["id"]),
        ).fetchone()
        new_lot = conn.execute(
            "SELECT * FROM purchase_lots WHERE id=?", (new_reel["lot_id"],)
        ).fetchone()
        layer = conn.execute(
            "SELECT * FROM inventory_cost_layers WHERE reel_id=?", (new_reel["id"],)
        ).fetchone()
        alloc = conn.execute(
            "SELECT * FROM reel_allocations WHERE reel_id=? AND allocation_role='SALE_RETURN'",
            (new_reel["id"],),
        ).fetchone()
        return_line = conn.execute(
            "SELECT * FROM sales_return_lines WHERE sales_return_id=?", (return_id,)
        ).fetchone()
    assert new_reel["id"] != original_reel_id
    assert new_reel["original_length_micros"] == 10_000_000
    assert new_reel["original_cost_piastres"] == 30_000
    assert new_reel["supplier_cost_piastres"] == 0
    assert new_reel["allocated_expense_piastres"] == 30_000
    assert new_reel["status"] == "OPENED"
    assert new_lot["source_type"] == "RETURN"
    assert new_lot["original_qty_micros"] == 10_000_000
    assert new_lot["original_landed_cost_piastres"] == 30_000
    assert layer["layer_type"] == "CUSTOMER_RETURN"
    assert layer["parent_issue_allocation_id"] == original_alloc["id"]
    assert layer["lot_id"] == new_lot["id"]
    assert layer["reel_id"] == new_reel["id"]
    assert alloc["source_issue_allocation_id"] == original_alloc["id"]
    assert alloc["created_layer_id"] == layer["id"]
    assert return_line["reel_id"] == new_reel["id"]
    assert return_line["lot_id"] == new_lot["id"]
    assert shop.purchasing.reel_remaining(original_reel_id) == (62_500_000, 187_500)
    assert shop.purchasing.reel_remaining(new_reel["id"]) == (10_000_000, 30_000)

    reports = ReportingService(shop.db)
    received_reel, returned_reel = reports.reel_balances(product_id=shop.reel_product)
    assert received_reel["remaining_length_micros"] == 62_500_000
    assert returned_reel["remaining_length_micros"] == 10_000_000
    assert returned_reel["source_reel_id"] == received_reel["reel_id"]
    assert returned_reel["source_reel_no"] == received_reel["reel_no"]
    assert returned_reel["source_issue_allocation_id"] == original_alloc["id"]
    assert returned_reel["source_document_no"] == shop.db.scalar(
        "SELECT document_no FROM sales_returns WHERE id=?", (return_id,),
    )
    received_lot, returned_lot = reports.fifo_lots(product_id=shop.reel_product)
    assert received_lot["remaining_cost_piastres"] == 187_500
    assert returned_lot["remaining_cost_piastres"] == 30_000
    assert returned_lot["source_document_no"] == returned_reel["source_document_no"]

    returnable = shop.sales.returnable_allocations(line_id)
    assert len(returnable) == 1
    assert returnable[0]["kind"] == "REEL"
    assert returnable[0]["remaining_qty_micros"] == 27_500_000
    with pytest.raises(ValueError):
        shop.sales.create_return(
            invoice,
            [{"sales_line_id": line_id, "quantity_micros": 27_500_001, "condition": "SELLABLE"}],
            "2026-11-13", "زيادة", actor_id=shop.manager,
        )
    assert shop.db.scalar("SELECT COUNT(*) FROM reels WHERE source_reel_id=?", (original_reel_id,)) == 1

    second = shop.sales.create_return(
        invoice,
        [{"sales_line_id": line_id, "quantity_micros": 27_500_000, "condition": "SELLABLE"}],
        "2026-11-13", "بقية المرتجع", actor_id=shop.manager,
    )
    shop.sales.post_return(second, actor_id=shop.manager)
    assert shop.sales.returnable_allocations(line_id) == []
    with shop.db.connect() as conn:
        returned_reels = conn.execute(
            "SELECT * FROM reels WHERE source_reel_id=? ORDER BY id", (original_reel_id,)
        ).fetchall()
    assert len(returned_reels) == 2
    assert sum(row["original_length_micros"] for row in returned_reels) == 37_500_000
    assert sum(row["original_cost_piastres"] for row in returned_reels) == 112_500
    assert all(row["source_issue_allocation_id"] == original_alloc["id"] for row in returned_reels)
    assert shop.purchasing.reel_remaining(original_reel_id) == (62_500_000, 187_500)
    with pytest.raises(ValueError):
        shop.sales.create_return(
            invoice,
            [{"sales_line_id": line_id, "quantity_micros": 1_000_000, "condition": "SELLABLE"}],
            "2026-11-14", "تكرار", actor_id=shop.manager,
        )
