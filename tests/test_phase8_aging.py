from datetime import date, timedelta

from mizan.reporting import ReportingService
from phase6_support import build_shop


def test_aging_uses_due_date_and_inclusive_bucket_boundaries(db):
    shop = build_shop(db)
    shop.purchase(20_000_000, 20_000_000, date="2026-07-01")
    line = {"product_id": shop.lot_product, "unit_id": shop.meter,
            "quantity_micros": 1_000_000, "unit_price_micros": 30_000_000}
    cutoff = date(2026, 11, 30)
    periods = ((-1, "not_due"), (0, "0_30"), (30, "0_30"), (31, "31_60"),
               (60, "31_60"), (61, "61_90"), (90, "61_90"), (91, "over_90"))
    expected = {}
    for days, bucket in periods:
        invoice = shop.sales.create_invoice(
            shop.retail, "2026-08-01", [line],
            due_date=(cutoff - timedelta(days=days)).isoformat(), actor_id=shop.manager,
        )
        shop.sales.post_invoice(invoice, actor_id=shop.manager)
        expected[invoice] = (days, bucket)

    shop.sale(1_000_000, 30_000_000, actor=shop.manager)  # draft
    cash = shop.sale(1_000_000, 30_000_000, payment_type="CASH", actor=shop.manager)
    shop.sales.post_invoice(cash, actor_id=shop.manager)
    other = shop.sales.create_invoice(shop.wholesale, "2026-11-10", [line],
                                      due_date="", actor_id=shop.manager)
    shop.sales.post_invoice(other, actor_id=shop.manager)

    service = ReportingService(db)
    report = service.customer_receivables_aging(cutoff.isoformat(), customer_id=shop.retail)
    assert {row["sales_id"]: (row["days_past_due"], row["bucket"])
            for row in report["invoices"]} == expected
    assert report["buckets_piastres"] == {
        "not_due": 3_000, "0_30": 6_000, "31_60": 6_000, "61_90": 6_000, "over_90": 3_000,
    }
    assert report["total_piastres"] == report["ledger_piastres"] == 24_000
    combined = service.customer_receivables_aging(cutoff.isoformat())
    assert combined["total_piastres"] == combined["ledger_piastres"] == 27_000
    assert next(row for row in combined["invoices"] if row["sales_id"] == other)["days_past_due"] == 20


def test_aging_respects_historical_settlements_and_reversal_dates(db):
    shop = build_shop(db)
    shop.purchase(100_000_000, 22_000_000)
    invoice = shop.sale(20_000_000, 30_000_000, actor=shop.manager)
    reversed_sale = shop.sale(1_000_000, 30_000_000, actor=shop.manager)
    shop.sales.post_invoice(invoice, actor_id=shop.manager)
    shop.sales.post_invoice(reversed_sale, actor_id=shop.manager)
    shop.sales.reverse_invoice(reversed_sale, "عكس", reversal_date="2026-11-15", actor_id=shop.manager)
    receipt = shop.sales.create_receipt(
        shop.retail, shop.cash, 15_000, "2026-11-11",
        allocations=[{"sales_id": invoice, "amount_piastres": 10_000}], actor_id=shop.manager,
    )
    shop.sales.post_receipt(receipt, actor_id=shop.manager)
    line = db.scalar("SELECT id FROM sales_lines WHERE sales_id=?", (invoice,))
    returned = shop.sales.create_return(
        invoice, [{"sales_line_id": line, "quantity_micros": 5_000_000, "condition": "SELLABLE"}],
        "2026-11-12", "مرتجع", actor_id=shop.manager,
    )
    shop.sales.post_return(returned, actor_id=shop.manager)
    credit = db.scalar("SELECT id FROM customer_credits WHERE source_type='OVERPAYMENT'")
    shop.sales.apply_customer_credit(credit, invoice, 5_000, actor_id=shop.manager,
                                     application_date="2026-11-13")

    service = ReportingService(db)
    before = service.customer_receivables_aging("2026-11-09")
    assert before["invoices"] == []
    for day, expected in ((10, 63_000), (11, 53_000), (12, 38_000), (13, 33_000), (15, 30_000)):
        report = service.customer_receivables_aging(f"2026-11-{day}")
        assert report["total_piastres"] == report["ledger_piastres"] == expected
        assert report["difference_piastres"] == 0
    outstanding, = report["invoices"]
    assert outstanding["sales_id"] == invoice
    assert outstanding["days_past_due"] == 5
    assert outstanding["document_no"]
    assert outstanding["outstanding_piastres"] == shop.sales.invoice_balance(invoice)

    shop.accounting.post_opening("2026-11-16", "1100", 2_000)
    assert service.customer_receivables_aging("2026-11-16")["difference_piastres"] == 2_000
