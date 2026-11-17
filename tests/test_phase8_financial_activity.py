import pytest

from mizan.reporting import ReportingService
from phase6_support import build_shop


def test_financial_activity_reconciles_opening_transfers_payments_and_reversals(db):
    shop = build_shop(db)
    purchase = shop.purchase(10_000_000, 22_000_000)
    shop.accounting.post_opening("2026-11-01", "1010", 100_000)
    transfer = shop.accounting.transfer(shop.cash, "1020", 15_000, "2026-11-10")
    invoice = shop.sale(1_000_000, 30_000_000, payment_type="CASH", actor=shop.manager)
    shop.sales.post_invoice(invoice, actor_id=shop.manager)
    shop.sale(1_000_000, 30_000_000, payment_type="CASH", actor=shop.manager)  # draft
    shop.sales.reverse_invoice(invoice, "عكس", reversal_date="2026-11-13", actor_id=shop.manager)
    payment = shop.purchasing.post_supplier_payment(
        shop.supplier, shop.bank, "2026-11-11", [{"purchase_invoice_id": purchase, "amount_piastres": 8_000}],
        reference="BANK-903", actor_id=shop.manager,
    )

    reports = ReportingService(db)
    period = reports.financial_activity("2026-11-10", "2026-11-11")
    cash, bank = period["accounts"]
    assert (cash["gl_code"], bank["gl_code"]) == ("1010", "1020")
    assert cash["opening_piastres"] == 100_000
    assert cash["closing_piastres"] == 88_000
    assert bank["opening_piastres"] == 0
    assert bank["closing_piastres"] == 7_000
    assert cash["movements"][0]["journal_id"] == bank["movements"][0]["journal_id"] == transfer
    assert bank["movements"][1]["document_no"] == payment["document_no"]
    assert [row["balance_piastres"] for row in cash["movements"]] == [85_000, 88_000]

    report = reports.financial_activity("2026-11-13", "2026-11-13", financial_account_id=shop.cash)
    only_cash, = report["accounts"]
    reversal, = only_cash["movements"]
    assert only_cash["opening_piastres"] == 88_000
    assert reversal["entry_kind"] == "REVERSAL"
    assert reversal["document_no"] == cash["movements"][1]["document_no"]
    assert only_cash["closing_piastres"] == shop.accounting.account_balance("1010") == 85_000


def test_shared_financial_ledger_appears_once_and_includes_all_linked_accounts(db):
    shop = build_shop(db)
    alias = shop.accounting.create_financial_account("CASH-2", "صندوق ثانٍ", "CASH", "1010")
    shop.accounting.post_opening("2026-11-01", "1010", 10_000)
    reports = ReportingService(db)
    cash, bank = reports.financial_activity("2026-11-02", "2026-11-03")["accounts"]
    assert [row["id"] for row in cash["financial_accounts"]] == [shop.cash, alias]
    assert cash["closing_piastres"] == 10_000
    assert cash["movements"] == []
    assert bank["closing_piastres"] == 0
    assert reports.financial_activity("2026-11-02", "2026-11-03", financial_account_id=alias)["accounts"] == [cash]
    with pytest.raises(ValueError, match="financial account not found"):
        reports.financial_activity("2026-11-02", "2026-11-03", financial_account_id=999)
