import sqlite3

import pytest

from mizan.accounting import AccountingService
from mizan.lcp import outflow_cost, return_cost, t_layer, validate_layer
from mizan.master import MasterDataService
from mizan.purchasing import InventoryError, PurchasingService


@pytest.fixture()
def shop(db):
    master = MasterDataService(db)
    master.create_unit("METER", "متر", True)
    master.create_unit("PIECE", "قطعة", False)
    lot_product = master.create_product("CABLE-LOT", "كابل LOT", "METER", "LOT")
    reel_product = master.create_product("CABLE-REEL", "كابل بكرة", "METER", "REEL")
    supplier = master.create_supplier("SUP-1", "مورد")
    accounting = AccountingService(db)
    accounting.seed_coa()
    purchasing = PurchasingService(db)
    return {
        "db": db, "master": master, "accounting": accounting, "purchasing": purchasing,
        "lot_product": lot_product, "reel_product": reel_product, "supplier": supplier,
        "meter": db.scalar("SELECT id FROM units WHERE code='METER'"),
    }


def _invoice(shop, product_id, qty, price, expense=0, reel_lengths=None):
    return shop["purchasing"].create_invoice(
        shop["supplier"], "2026-11-10",
        [{"product_id": product_id, "unit_id": shop["meter"], "quantity_micros": qty,
          "unit_price_micros": price, "reel_lengths_micros": reel_lengths}],
        expense_piastres=expense,
    )


def test_T_ACC_007_purchase_with_landed_cost(shop):
    invoice = _invoice(shop, shop["lot_product"], 100_000_000, 20_000_000, expense=50)
    shop["purchasing"].post_invoice(invoice)
    assert shop["accounting"].account_balance("1200") == 200050
    assert shop["accounting"].account_balance("2010") == -200000
    assert shop["accounting"].account_balance("2030") == -50
    lot = shop["db"].scalar("SELECT id FROM purchase_lots WHERE purchase_invoice_line_id=1")
    assert shop["purchasing"].lot_remaining(lot) == (100_000_000, 200050)


def test_T_INV_001_fifo_two_lots(shop):
    first = _invoice(shop, shop["lot_product"], 100_000_000, 20_000_000)
    second = _invoice(shop, shop["lot_product"], 100_000_000, 22_000_000)
    shop["purchasing"].post_invoice(first)
    shop["purchasing"].post_invoice(second)
    result = shop["purchasing"].issue_fifo(shop["lot_product"], 120_000_000)
    assert result["cost"] == 244000


def test_T_INV_003_lcp_full_layer(shop):
    invoice = _invoice(shop, shop["lot_product"], 90_000_000, 11_666_667)
    shop["purchasing"].post_invoice(invoice)
    lot = shop["db"].scalar("SELECT id FROM purchase_lots WHERE purchase_invoice_line_id=1")
    assert shop["purchasing"].issue_lot(lot, 2_000_000)["cost"] == 2333
    # Split issuance is invariant: T(1m)-T(0) + T(2m)-T(1m) = T(2m).
    assert outflow_cost(90_000_000, 105_000, 0, 1_000_000) == 1167
    assert outflow_cost(90_000_000, 105_000, 1_000_000, 1_000_000) == 1166


def test_T_INV_007_full_layer_exhaustion(shop):
    invoice = _invoice(shop, shop["lot_product"], 90_000_000, 11_666_667)
    shop["purchasing"].post_invoice(invoice)
    lot = shop["db"].scalar("SELECT id FROM purchase_lots WHERE purchase_invoice_line_id=1")
    result = shop["purchasing"].issue_lot(lot, 90_000_000)
    assert result["cost"] == 105_000
    assert shop["purchasing"].lot_remaining(lot) == (0, 0)


def test_T_INV_016_reel_layers_have_reel_identity(shop):
    invoice = _invoice(shop, shop["reel_product"], 100_000_000, 30_000_000, reel_lengths=[60_000_000, 40_000_000])
    shop["purchasing"].post_invoice(invoice)
    with shop["db"].connect() as conn:
        rows = conn.execute("SELECT reel_id,original_qty_micros FROM inventory_cost_layers").fetchall()
    assert len(rows) == 2
    assert all(row["reel_id"] is not None for row in rows)
    assert sum(row["original_qty_micros"] for row in rows) == 100_000_000


def test_T_INV_009_reel_cut(shop):
    invoice = _invoice(shop, shop["reel_product"], 100_000_000, 30_000_000, reel_lengths=[100_000_000])
    shop["purchasing"].post_invoice(invoice)
    reel = shop["db"].scalar("SELECT id FROM reels LIMIT 1")
    result = shop["purchasing"].cut_reel(reel, 37_500_000)
    assert result["cost"] == 112_500
    assert shop["purchasing"].reel_remaining(reel) == (62_500_000, 187_500)


def test_T_SUP_001_purchase_return(shop):
    invoice = _invoice(shop, shop["lot_product"], 100_000_000, 20_000_000)
    shop["purchasing"].post_invoice(invoice)
    lot = shop["db"].scalar("SELECT id FROM purchase_lots WHERE purchase_invoice_line_id=1")
    line = shop["db"].scalar("SELECT id FROM purchase_invoice_lines WHERE purchase_invoice_id=?", (invoice,))
    ret = shop["purchasing"].create_purchase_return(invoice, [
        {"purchase_invoice_line_id": line, "lot_id": lot, "quantity_micros": 10_000_000}
    ], "2026-11-11")
    result = shop["purchasing"].post_purchase_return(ret)
    assert result["total"] == 20000
    assert shop["purchasing"].lot_remaining(lot) == (90_000_000, 180000)
    assert shop["accounting"].account_balance("2010") == -180000


def test_T_DMG_001_damage(shop):
    invoice = _invoice(shop, shop["lot_product"], 100_000_000, 20_000_000)
    shop["purchasing"].post_invoice(invoice)
    lot = shop["db"].scalar("SELECT id FROM purchase_lots WHERE purchase_invoice_line_id=1")
    result = shop["purchasing"].damage_lot(lot, 10_000_000)
    assert result["cost"] == 20000
    assert shop["purchasing"].lot_remaining(lot) == (90_000_000, 180000)
    assert shop["accounting"].account_balance("5020") == 20000


def test_T_COST_001_returns_and_micro_issue():
    assert t_layer(3, 100, 1) == 33
    assert t_layer(3, 100, 2) == 67
    assert t_layer(3, 100, 3) == 100
    assert return_cost(33, 1_000_000, 0, 1_000_000) == 33
    assert t_layer(1_000_000, 33, 0) == 0
    # 0.001 from a 1-unit/33-piastre return layer is zero-cost and non-negative.
    assert outflow_cost(1_000_000, 33, 0, 1_000) == 0


def test_T_COST_002_partial_returns():
    assert return_cost(2333, 2_000_000, 0, 500_000) == 583
    assert return_cost(2333, 2_000_000, 500_000, 1_000_000) == 584
    assert return_cost(2333, 2_000_000, 0, 1_000_000) == 1167


def test_T_DB_008_and_009_layer_invariants(shop):
    invoice = _invoice(shop, shop["lot_product"], 100_000_000, 20_000_000)
    shop["purchasing"].post_invoice(invoice)
    lot = shop["db"].scalar("SELECT id FROM purchase_lots WHERE purchase_invoice_line_id=1")
    with pytest.raises(ValueError):
        validate_layer(3, 100, 1, 2, 50)
    with pytest.raises(sqlite3.IntegrityError):
        with shop["db"].transaction() as conn:
            conn.execute(
                "INSERT INTO inventory_cost_layers(lot_id,layer_type,original_qty_micros,original_cost_piastres,"
                "position_micros,remaining_qty_micros,remaining_cost_piastres,fifo_anchor_at) "
                "VALUES(?,'BASE',3,100,1,1,67,'2026-01-01')",
                (lot,),
            )


def test_T_DB_003_reel_cannot_use_lot_allocation(shop):
    invoice = _invoice(shop, shop["reel_product"], 10_000_000, 30_000_000, reel_lengths=[10_000_000])
    shop["purchasing"].post_invoice(invoice)
    lot = shop["db"].scalar("SELECT id FROM purchase_lots LIMIT 1")
    with pytest.raises(InventoryError):
        shop["purchasing"].issue_lot(lot, 1_000_000)


def test_purchase_posting_rolls_back_all_effects(shop):
    # Cash purchase without a financial account fails after lots/layers were built.
    cash_invoice = shop["purchasing"].create_invoice(
        shop["supplier"], "2026-11-12",
        [{"product_id": shop["lot_product"], "unit_id": shop["meter"],
          "quantity_micros": 10_000_000, "unit_price_micros": 20_000_000}],
        payment_type="CASH",
    )
    with pytest.raises(ValueError):
        shop["purchasing"].post_invoice(cash_invoice)
    with shop["db"].connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM purchase_lots WHERE purchase_invoice_line_id IN "
                            "(SELECT id FROM purchase_invoice_lines WHERE purchase_invoice_id=?)",
                            (cash_invoice,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM stock_movements WHERE source_id=?", (cash_invoice,)).fetchone()[0] == 0
        assert conn.execute("SELECT status FROM purchase_invoices WHERE id=?", (cash_invoice,)).fetchone()[0] == "DRAFT"


def test_no_negative_stock(shop):
    invoice = _invoice(shop, shop["lot_product"], 10_000_000, 20_000_000)
    shop["purchasing"].post_invoice(invoice)
    with pytest.raises(InventoryError):
        shop["purchasing"].issue_fifo(shop["lot_product"], 11_000_000)
    assert shop["purchasing"].lot_remaining(1)[0] == 10_000_000
