import pytest

from mizan.reporting import ReportingService
from phase6_support import build_shop


def test_profitability_uses_journals_and_separates_operating_and_other_result(db):
    shop = build_shop(db)
    shop.purchase(100_000_000, 22_000_000)
    invoice = shop.sale(20_000_000, 30_000_000, actor=shop.manager)
    shop.sales.post_invoice(invoice, actor_id=shop.manager)
    shop.sale(5_000_000, 30_000_000, actor=shop.manager)  # draft
    line = db.scalar("SELECT id FROM sales_lines WHERE sales_id=?", (invoice,))
    returned = shop.sales.create_return(
        invoice, [{"sales_line_id": line, "quantity_micros": 5_000_000, "condition": "DAMAGED"}],
        "2026-11-12", "مرتجع تالف", actor_id=shop.manager,
    )
    shop.sales.post_return(returned, actor_id=shop.manager)
    shop.accounting.post_journal("2026-11-12", "RENT", 1, [
        {"account_code": "5110", "debit": 3_000}, {"account_code": "1010", "credit": 3_000},
    ])
    shop.accounting.post_journal("2026-11-12", "OTHER_INCOME", 1, [
        {"account_code": "1010", "debit": 1_000}, {"account_code": "4030", "credit": 1_000},
    ])
    shop.accounting.post_journal("2026-11-13", "RENT", 2, [
        {"account_code": "5110", "debit": 8_000}, {"account_code": "1010", "credit": 8_000},
    ])

    reports = ReportingService(db)
    report = reports.profitability("2026-11-10", "2026-11-12")
    assert report["sales_revenue_piastres"] == 60_000
    assert report["sales_returns_piastres"] == 15_000
    assert report["net_sales_piastres"] == 45_000
    assert report["cost_of_sales_piastres"] == 33_000
    assert report["gross_profit_piastres"] == 12_000
    assert report["operating_expenses_piastres"] == 3_000
    assert report["operating_profit_piastres"] == 9_000
    assert report["other_income_piastres"] == 1_000
    assert report["other_expenses_piastres"] == 11_000
    assert report["net_result_piastres"] == -1_000
    assert report["net_result_piastres"] == sum(
        row["credit_piastres"] - row["debit_piastres"] for row in report["accounts"]
    )

    detail = reports.income_expense_detail("2026-11-10", "2026-11-12")
    assert detail["income_piastres"] == 46_000
    assert detail["expenses_piastres"] == 47_000
    assert detail["net_result_piastres"] == report["net_result_piastres"]
    returns = reports.income_expense_detail("2026-11-12", "2026-11-12", account_type="REVENUE",
                                            account_code="4020")
    returned_amount, = returns["lines"]
    assert returned_amount["amount_piastres"] == -15_000
    assert returned_amount["source_id"] == returned
    assert returned_amount["document_no"] == db.scalar("SELECT document_no FROM sales_returns WHERE id=?", (returned,))
    assert returns["accounts"][0]["amount_piastres"] == -15_000


def test_profitability_reports_reversal_in_its_own_period(db):
    shop = build_shop(db)
    shop.purchase(10_000_000, 22_000_000)
    invoice = shop.sale(1_000_000, 30_000_000, actor=shop.manager)
    shop.sales.post_invoice(invoice, actor_id=shop.manager)
    shop.sales.reverse_invoice(invoice, "عكس", reversal_date="2026-11-15", actor_id=shop.manager)

    service = ReportingService(db)
    assert service.profitability("2026-11-10", "2026-11-10")["gross_profit_piastres"] == 800
    assert service.profitability("2026-11-15", "2026-11-15")["gross_profit_piastres"] == -800
    assert service.profitability("2026-11-10", "2026-11-15")["net_result_piastres"] == 0
    assert service.profitability("2026-11-16", "2026-11-16")["accounts"] == []
    with pytest.raises(ValueError, match="on or before"):
        service.profitability("2026-11-16", "2026-11-15")

    reversal = service.income_expense_detail("2026-11-15", "2026-11-15", account_type="REVENUE")
    line, = reversal["lines"]
    assert line["entry_kind"] == "REVERSAL"
    assert line["document_no"] == db.scalar("SELECT document_no FROM sales WHERE id=?", (invoice,))
    assert reversal["income_piastres"] == -3_000
    with pytest.raises(ValueError, match="account_type"):
        service.income_expense_detail("2026-11-15", "2026-11-15", account_type="ASSET")
