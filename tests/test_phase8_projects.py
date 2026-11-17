from mizan.reporting import ReportingService
from phase6_support import build_shop


def test_project_balances_keep_mixed_invoice_settlements_unassigned(db):
    shop = build_shop(db)
    shop.purchase(50_000_000, 22_000_000)
    second = shop.master.create_project(shop.contractor, "PRJ-2", "مشروع ثانٍ")
    sale = shop.sale(10_000_000, 30_000_000, customer=shop.contractor,
                     project_id=shop.project, actor=shop.manager)
    cash = shop.sale(1_000_000, 30_000_000, customer=shop.contractor,
                     project_id=shop.project, payment_type="CASH", actor=shop.manager)
    line = {"product_id": shop.lot_product, "unit_id": shop.meter,
            "quantity_micros": 2_000_000, "unit_price_micros": 30_000_000}
    mixed = shop.sale(0, 0, customer=shop.contractor, date="2026-11-12",
                      lines=[line | {"project_id": shop.project}, line | {"project_id": second}],
                      actor=shop.manager)
    for invoice in (sale, cash, mixed):
        shop.sales.post_invoice(invoice, actor_id=shop.manager)

    receipt = shop.sales.create_receipt(
        shop.contractor, shop.cash, 6_000, "2026-11-11",
        allocations=[{"sales_id": sale, "amount_piastres": 5_000}], actor_id=shop.manager,
    )
    shop.sales.post_receipt(receipt, actor_id=shop.manager)
    credit = db.scalar("SELECT id FROM customer_credits WHERE source_id=?", (receipt,))
    shop.sales.apply_customer_credit(credit, sale, 1_000, actor_id=shop.manager, application_date="2026-11-12")
    sale_line = db.scalar("SELECT id FROM sales_lines WHERE sales_id=?", (sale,))
    returned = shop.sales.create_return(
        sale, [{"sales_line_id": sale_line, "quantity_micros": 1_000_000, "condition": "SELLABLE"}],
        "2026-11-13", "مرتجع", actor_id=shop.manager,
    )
    shop.sales.post_return(returned, actor_id=shop.manager)
    mixed_receipt = shop.sales.create_receipt(
        shop.contractor, shop.cash, 4_000, "2026-11-14",
        allocations=[{"sales_id": mixed, "amount_piastres": 4_000}], actor_id=shop.manager,
    )
    shop.sales.post_receipt(mixed_receipt, actor_id=shop.manager)

    reports = ReportingService(db)
    early = reports.project_balances("2026-11-11", customer_id=shop.contractor)
    initial, = early["projects"]
    assert initial["outstanding_piastres"] == 25_000
    assert initial["cash_sales_piastres"] == 3_000
    assert early["unassigned"] == []

    report = reports.project_balances("2026-11-14")
    first_project, second_project = report["projects"]
    assert first_project["project_id"] == shop.project
    assert first_project["net_sales_piastres"] == 36_000
    assert first_project["receipts_piastres"] == 5_000
    assert first_project["outstanding_piastres"] == 27_000
    assert second_project["project_id"] == second
    assert second_project["outstanding_piastres"] == 6_000
    residual, = report["unassigned"]
    assert residual["receipts_piastres"] == 4_000
    assert residual["outstanding_piastres"] == -4_000
    assert sum(row["outstanding_piastres"] for row in report["projects"] + report["unassigned"]) == (
        shop.sales.customer_balance(shop.contractor)
    )
    assert reports.project_balances("2026-11-14", customer_id=shop.retail)["projects"] == []
