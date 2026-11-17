"""Cross-report acceptance using independently calculated month-end balances."""

from mizan.periods import PeriodService
from mizan.reporting import ReportingService
from phase6_support import build_shop


def test_month_end_reports_reconcile_and_remain_stable_after_later_sale(db):
    shop = build_shop(db)
    reports = ReportingService(db)
    shop.accounting.post_opening("2026-11-01", "1010", 100_000)
    purchase = shop.purchase(20_000_000, 20_000_000)
    shop.purchase(10_000_000, 30_000_000, product=shop.reel_product,
                  reel_lengths=[6_000_000, 4_000_000])
    sale = shop.sale(5_000_000, 30_000_000, actor=shop.manager)
    shop.sales.post_invoice(sale, actor_id=shop.manager)
    shop.sale(1_000_000, 30_000_000, actor=shop.manager)  # excluded draft
    line = db.scalar("SELECT id FROM sales_lines WHERE sales_id=?", (sale,))
    returned = shop.sales.create_return(
        sale, [{"sales_line_id": line, "quantity_micros": 1_000_000, "condition": "DAMAGED"}],
        "2026-11-12", "مرتجع تالف", actor_id=shop.manager,
    )
    shop.sales.post_return(returned, actor_id=shop.manager)
    receipt = shop.sales.create_receipt(
        shop.retail, shop.cash, 5_000, "2026-11-13",
        allocations=[{"sales_id": sale, "amount_piastres": 5_000}], actor_id=shop.manager,
    )
    shop.sales.post_receipt(receipt, actor_id=shop.manager)
    shop.purchasing.post_supplier_payment(
        shop.supplier, shop.cash, "2026-11-14",
        [{"purchase_invoice_id": purchase, "amount_piastres": 10_000}],
        reference="ACCEPTANCE-001", actor_id=shop.manager,
    )

    def month_end():
        return {
            "balance_sheet": shop.accounting.balance_sheet(as_of="2026-11-30"),
            "parties": shop.accounting.reconcile_parties(as_of="2026-11-30"),
            "inventory": reports.inventory_valuation("2026-11-30"),
            "customers": reports.customer_receivables_aging("2026-11-30"),
            "suppliers": reports.supplier_payables_aging("2026-11-30"),
            "sales": reports.sales_detail("2026-11-01", "2026-11-30"),
            "profit": reports.profitability("2026-11-01", "2026-11-30"),
            "cash": reports.financial_activity("2026-11-01", "2026-11-30",
                                                financial_account_id=shop.cash),
        }

    before = month_end()
    assert before["balance_sheet"]["balanced"]
    assert all(party["matched"] for party in before["parties"].values())
    assert before["customers"]["total_piastres"] == before["customers"]["ledger_piastres"] == 7_000
    assert before["suppliers"]["total_piastres"] == before["suppliers"]["ledger_piastres"] == 60_000
    assert before["inventory"]["inventory_piastres"] == before["inventory"]["ledger_piastres"] == 60_000
    assert sum(lot["remaining_cost_piastres"] for lot in reports.fifo_lots()) == 60_000
    assert sum(reel["remaining_cost_piastres"] for reel in reports.reel_balances()) == 30_000
    assert shop.accounting.reconcile_inventory()["matched"]
    assert before["sales"]["net_sales_piastres"] == before["profit"]["net_sales_piastres"] == 12_000
    assert before["profit"]["cost_of_sales_piastres"] == 8_000
    assert before["profit"]["other_expenses_piastres"] == 2_000
    assert before["profit"]["net_result_piastres"] == 2_000
    assert before["cash"]["accounts"][0]["closing_piastres"] == 95_000
    customer = reports.customer_period_statement(shop.retail, "2026-11-01", "2026-11-30")
    supplier = reports.supplier_period_statement(shop.supplier, "2026-11-01", "2026-11-30")
    assert customer["accounts"]["1100"]["closing_piastres"] == 7_000
    assert supplier["accounts"]["2010"]["closing_piastres"] == 60_000

    PeriodService(db).close_month("2026-11", actor_id=shop.owner, reason="قبول المطابقة الشهرية")
    later = shop.sale(1_000_000, 30_000_000, date="2026-12-01", actor=shop.manager)
    shop.sales.post_invoice(later, actor_id=shop.manager)
    assert month_end() == before
    assert shop.accounting.reconcile_inventory()["matched"]
    assert reports.inventory_valuation("2026-12-01")["inventory_piastres"] == 58_000
