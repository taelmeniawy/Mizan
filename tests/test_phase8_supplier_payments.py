import sqlite3

import pytest

from mizan.auth import PermissionDenied
from mizan.reporting import ReportingService
from phase6_support import build_shop


def test_supplier_payment_posts_allocations_and_updates_aged_balances(db):
    shop = build_shop(db)
    first = shop.purchase(10_000_000, 20_000_000)
    second = shop.purchase(10_000_000, 30_000_000)
    allocations = [
        {"purchase_invoice_id": first, "amount_piastres": 20_000},
        {"purchase_invoice_id": second, "amount_piastres": 10_000},
    ]
    payment = shop.purchasing.post_supplier_payment(
        shop.supplier, shop.bank, "2026-11-11", allocations, reference="BANK-901", actor_id=shop.manager,
    )
    assert payment["amount_piastres"] == 30_000
    assert shop.accounting.account_balance("1020") == -30_000

    reports = ReportingService(db)
    assert reports.supplier_payables_aging("2026-11-10")["total_piastres"] == 50_000
    aged = reports.supplier_payables_aging("2026-11-11")
    assert aged["total_piastres"] == aged["ledger_piastres"] == 20_000
    remaining, = aged["invoices"]
    assert remaining["purchase_invoice_id"] == second
    assert remaining["outstanding_piastres"] == 20_000
    last = reports.supplier_statement(shop.supplier, as_of="2026-11-11")[-1]
    assert last["document_no"] == payment["document_no"]
    assert last["debit_piastres"] == 30_000
    assert last["balance_piastres"] == 20_000
    period = reports.supplier_period_statement(shop.supplier, "2026-11-11", "2026-11-11")
    assert period["accounts"]["2010"] == {
        "opening_piastres": 50_000, "closing_piastres": 20_000, "debit_piastres": 30_000, "credit_piastres": 0,
    }
    movement, = period["movements"]
    assert movement["document_no"] == payment["document_no"]

    with pytest.raises(ValueError, match="reference already exists"):
        shop.purchasing.post_supplier_payment(
            shop.supplier, shop.bank, "2026-11-11", allocations, reference=" BANK-901 ", actor_id=shop.manager,
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with db.transaction() as conn:
            conn.execute("UPDATE supplier_payment_allocations SET amount_piastres=1 WHERE payment_id=?",
                         (payment["payment_id"],))

    shop.accounting.reverse(payment["journal_id"], posted_by=shop.manager)
    restored = reports.supplier_payables_aging("2026-11-11")
    assert restored["total_piastres"] == restored["ledger_piastres"] == 50_000
    period = reports.supplier_period_statement(shop.supplier, "2026-11-11", "2026-11-11")
    paid, reversal = period["movements"]
    assert paid["balance_piastres"] == 20_000
    assert reversal["balance_piastres"] == period["accounts"]["2010"]["closing_piastres"] == 50_000
    assert reversal["entry_kind"] == "REVERSAL"
    assert reversal["document_no"] == payment["document_no"]


def test_rejected_supplier_payments_leave_no_documents_or_journals(db, monkeypatch):
    shop = build_shop(db)
    invoice = shop.purchase(10_000_000, 20_000_000)
    allocation = {"purchase_invoice_id": invoice, "amount_piastres": 20_000}

    def post(allocations, *, actor=shop.manager, day="2026-11-11"):
        return shop.purchasing.post_supplier_payment(
            shop.supplier, shop.bank, day, allocations, reference="BANK-902", actor_id=actor,
        )

    with pytest.raises(PermissionDenied):
        post([allocation], actor=shop.cashier)
    with pytest.raises(ValueError, match="by the payment date"):
        post([allocation], day="2026-10-31")
    with pytest.raises(ValueError, match="exceeds invoice outstanding"):
        post([allocation | {"amount_piastres": 20_001}])
    shop.accounting.post_journal("2026-11-10", "LEGACY_PAYMENT", 1, [
        {"account_code": "2010", "debit": 2_000, "party_type": "SUPPLIER", "party_id": shop.supplier},
        {"account_code": "1020", "credit": 2_000},
    ])
    with pytest.raises(ValueError, match="exceeds supplier outstanding"):
        post([allocation])

    def fail_journal(*args, **kwargs):
        raise RuntimeError("posting failure")

    monkeypatch.setattr(shop.purchasing.accounting, "post_journal", fail_journal)
    with pytest.raises(RuntimeError, match="posting failure"):
        post([allocation | {"amount_piastres": 18_000}])
    assert db.scalar("SELECT COUNT(*) FROM supplier_payments") == 0
    assert db.scalar("SELECT COUNT(*) FROM supplier_payment_allocations") == 0
    assert db.scalar("SELECT COUNT(*) FROM journal_entries WHERE source_type='SUPPLIER_PAYMENT'") == 0
