from mizan.reporting import ReportingService
from phase6_support import build_shop


def test_inventory_valuation_reconstructs_posted_costs_at_each_cutoff(db):
    shop = build_shop(db)
    shop.purchase(20_000_000, 22_000_000, date="2026-11-01")
    shop.purchase(10_000_000, 30_000_000, product=shop.reel_product,
                  date="2026-11-02", reel_lengths=[6_000_000, 4_000_000])
    draft = shop.sale(2_000_000, 30_000_000, actor=shop.manager, date="2026-11-03")
    sale = shop.sale(4_000_000, 30_000_000, actor=shop.manager, date="2026-11-04")
    shop.sales.post_invoice(sale, actor_id=shop.manager)
    line = db.scalar("SELECT id FROM sales_lines WHERE sales_id=?", (sale,))
    returned = shop.sales.create_return(
        sale, [{"sales_line_id": line, "quantity_micros": 1_000_000, "condition": "DAMAGED"}],
        "2026-11-05", "مرتجع تالف", actor_id=shop.manager,
    )
    shop.sales.post_return(returned, actor_id=shop.manager)
    reel = db.scalar("SELECT MIN(id) FROM reels")
    shop.purchasing.damage_reel(reel, 2_000_000, occurred_at="2026-11-06T23:45:00+02:00")

    reports = ReportingService(db)
    for day, expected in ((1, 44_000), (2, 74_000), (3, 74_000), (4, 65_200),
                          (5, 65_200), (6, 59_200)):
        report = reports.inventory_valuation(f"2026-11-{day:02d}")
        assert report["inventory_piastres"] == report["ledger_piastres"] == expected
        assert report["difference_piastres"] == 0

    products = {row["product_id"]: row for row in report["products"]}
    assert products[shop.lot_product]["quantity_micros"] == 16_000_000
    assert products[shop.reel_product]["quantity_micros"] == 8_000_000
    assert products[shop.reel_product]["base_unit_code"] == "METER"
    assert shop.accounting.reconcile_inventory()["layers_piastres"] == report["inventory_piastres"]
    lot_events = reports.inventory_movements("2026-11-03", "2026-11-05", product_id=shop.lot_product)
    assert [row["movement_type"] for row in lot_events] == ["SALE_ISSUE", "SALE_RETURN_IN", "DAMAGE"]
    assert lot_events[0]["source_id"] == sale != draft
    assert lot_events[0]["source_line_id"] == line
    reel_events = reports.inventory_movements("2026-11-06", "2026-11-06", product_id=shop.reel_product)
    movement, = reel_events
    assert movement["reel_id"] == reel
    assert movement["cost_piastres"] == 6_000

    damage = reports.income_expense_detail("2026-11-06", "2026-11-06", account_code="5020")
    expense, = damage["lines"]
    assert expense["document_no"] == movement["movement_no"]
    assert damage["expenses_piastres"] == 6_000
    assert damage["net_result_piastres"] == reports.profitability("2026-11-06", "2026-11-06")["net_result_piastres"]

    lots = reports.fifo_lots(warehouse_id=1)
    assert sum(row["remaining_cost_piastres"] for row in lots) == report["inventory_piastres"]
    lot, = [row for row in lots if row["product_id"] == shop.lot_product]
    assert (lot["original_qty_micros"], lot["remaining_qty_micros"]) == (20_000_000, 16_000_000)
    assert (lot["original_landed_cost_piastres"], lot["remaining_cost_piastres"]) == (44_000, 35_200)
    assert lot["source_document_no"] == db.scalar("SELECT document_no FROM purchase_invoices WHERE id=1")
    assert reports.fifo_lots(warehouse_id=999) == []

    cut, sealed = reports.reel_balances(product_id=shop.reel_product)
    assert (cut["status"], cut["original_length_micros"], cut["remaining_length_micros"]) == (
        "OPENED", 6_000_000, 4_000_000,
    )
    assert (sealed["status"], sealed["remaining_length_micros"]) == ("SEALED", 4_000_000)
    assert cut["source_movement_no"] != sealed["source_movement_no"]
    assert cut["source_document_no"] == sealed["source_document_no"]
    assert cut["remaining_cost_piastres"] + sealed["remaining_cost_piastres"] == 24_000


def test_inventory_valuation_surfaces_an_outflow_without_a_matching_journal(db):
    shop = build_shop(db)
    shop.purchase(10_000_000, 22_000_000)
    lot = db.scalar("SELECT id FROM purchase_lots")
    shop.purchasing.issue_lot(lot, 1_000_000, occurred_at="2026-11-02")

    report = ReportingService(db).inventory_valuation("2026-11-02")
    assert report["inventory_piastres"] == 19_800
    assert report["ledger_piastres"] == 22_000
    assert report["difference_piastres"] == 2_200
