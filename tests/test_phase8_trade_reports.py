from mizan.reporting import ReportingService
from phase6_support import build_shop


def test_sales_detail_filters_and_reconciles_posted_returns_and_reversals(db):
    shop = build_shop(db)
    shop.purchase(100_000_000, 22_000_000)
    sale = shop.sale(20_000_000, 30_000_000, actor=shop.manager)
    reversed_sale = shop.sale(5_000_000, 30_000_000, customer=shop.wholesale, actor=shop.manager)
    shop.sale(2_000_000, 30_000_000, actor=shop.manager)  # draft
    shop.sales.post_invoice(sale, actor_id=shop.manager)
    shop.sales.post_invoice(reversed_sale, actor_id=shop.manager)
    line = db.scalar("SELECT id FROM sales_lines WHERE sales_id=?", (sale,))
    returned = shop.sales.create_return(
        sale, [{"sales_line_id": line, "quantity_micros": 5_000_000, "condition": "DAMAGED"}],
        "2026-11-12", "مرتجع", actor_id=shop.manager,
    )
    shop.sales.post_return(returned, actor_id=shop.manager)
    shop.sales.reverse_invoice(reversed_sale, "عكس", reversal_date="2026-11-13", actor_id=shop.manager)

    reports = ReportingService(db)
    report = reports.sales_detail("2026-11-10", "2026-11-13")
    assert [row["kind"] for row in report["lines"]] == ["SALE", "SALE", "RETURN", "REVERSAL"]
    assert report["sales_piastres"] == 75_000
    assert report["returns_piastres"] == report["reversals_piastres"] == 15_000
    profit = reports.profitability("2026-11-10", "2026-11-13")
    assert report["net_sales_piastres"] == profit["net_sales_piastres"] == 45_000
    assert report["cost_of_sales_piastres"] == profit["cost_of_sales_piastres"] == 33_000
    filtered = reports.sales_detail("2026-11-12", "2026-11-12", customer_id=shop.retail,
                                    product_id=shop.lot_product, posted_by=shop.manager)
    detail, = filtered["lines"]
    assert detail["invoice_id"] == sale
    assert detail["document_no"] != detail["invoice_no"]
    assert detail["entry_no"]
    assert detail["quantity_micros"] == -5_000_000
    assert reports.sales_detail("2026-11-10", "2026-11-13", posted_by=shop.cashier)["lines"] == []


def test_purchase_detail_separates_supplier_cost_landed_cost_and_returns(db):
    shop = build_shop(db)
    invoice = shop.purchasing.create_invoice(shop.supplier, "2026-11-10", [
        {"product_id": shop.lot_product, "unit_id": shop.meter, "quantity_micros": 10_000_000,
         "unit_price_micros": 20_000_000},
        {"product_id": shop.reel_product, "unit_id": shop.meter, "quantity_micros": 10_000_000,
         "unit_price_micros": 30_000_000, "reel_lengths_micros": [6_000_000, 4_000_000]},
    ], expense_piastres=500)
    shop.purchasing.post_invoice(invoice)
    lot_line = db.scalar("SELECT id FROM purchase_invoice_lines WHERE product_id=?", (shop.lot_product,))
    lot = db.scalar("SELECT id FROM purchase_lots WHERE purchase_invoice_line_id=?", (lot_line,))
    returned = shop.purchasing.create_purchase_return(invoice, [
        {"purchase_invoice_line_id": lot_line, "lot_id": lot, "quantity_micros": 2_000_000},
    ], "2026-11-11")
    shop.purchasing.post_purchase_return(returned)

    reports = ReportingService(db)
    report = reports.purchase_detail("2026-11-10", "2026-11-11", supplier_id=shop.supplier)
    assert len(report["lines"]) == 3
    assert report["supplier_purchases_piastres"] == 50_000
    assert report["purchase_expenses_piastres"] == 500
    assert report["landed_purchases_piastres"] == 50_500
    assert report["return_cost_piastres"] == 4_040
    assert report["net_inventory_acquisition_piastres"] == shop.accounting.account_balance("1200") == 46_460
    filtered = reports.purchase_detail("2026-11-10", "2026-11-10", product_id=shop.reel_product)
    line, = filtered["lines"]
    assert line["supplier_unit_price_micros"] == 30_000_000
    assert line["supplier_piastres"] == 30_000
    assert line["inventory_piastres"] == 30_300
