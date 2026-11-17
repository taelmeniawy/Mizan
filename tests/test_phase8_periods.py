import sqlite3

import pytest

from mizan.auth import PermissionDenied
from mizan.periods import ClosedPeriodError, PeriodService
from phase6_support import build_shop


def test_month_lock_blocks_backdated_posting_and_allows_later_reversal(db):
    shop = build_shop(db)
    shop.purchase(10_000_000, 22_000_000)
    original = shop.sale(1_000_000, 30_000_000, actor=shop.manager)
    shop.sales.post_invoice(original, actor_id=shop.manager)
    draft = shop.sale(1_000_000, 30_000_000, actor=shop.manager)
    lot = db.scalar("SELECT id FROM purchase_lots")
    stock_before = shop.purchasing.lot_remaining(lot)
    service = PeriodService(db)

    with pytest.raises(PermissionDenied):
        service.close_month("2026-11", actor_id=shop.manager, reason="اعتماد")
    service.close_month("2026-11", actor_id=shop.owner, reason="اعتماد نوفمبر")
    service.close_month("2026-11", actor_id=shop.owner, reason="إعادة الطلب")
    assert service.is_closed("2026-11")
    with pytest.raises(ClosedPeriodError, match="2026-11"):
        shop.sales.post_invoice(draft, actor_id=shop.manager)
    with pytest.raises(ClosedPeriodError):
        shop.accounting.post_opening("2026-11-30T23:59:00+02:00", "1010", 100)
    assert shop.purchasing.lot_remaining(lot) == stock_before
    assert db.scalar("SELECT status FROM sales WHERE id=?", (draft,)) == "DRAFT"

    with pytest.raises(sqlite3.IntegrityError, match="accounting period is closed"):
        with db.transaction() as conn:
            conn.execute("UPDATE stock_movements SET occurred_at='2026-12-01'")

    november_assets = shop.accounting.balance_sheet(as_of="2026-11-30")
    shop.sales.reverse_invoice(original, "تصحيح", reversal_date="2026-12-01", actor_id=shop.manager)
    assert shop.accounting.balance_sheet(as_of="2026-11-30") == november_assets
    assert shop.sales.customer_balance(shop.retail) == 0

    service.reopen_month("2026-11", actor_id=shop.owner, reason="تصحيح فاتورة")
    assert not service.is_closed("2026-11")
    shop.sales.post_invoice(draft, actor_id=shop.manager)
    with db.connect() as conn:
        events = conn.execute(
            "SELECT event_type,reason FROM audit_events WHERE entity_type='ACCOUNTING_PERIOD' ORDER BY id"
        ).fetchall()
    assert [tuple(row) for row in events] == [
        ("PERIOD_CLOSED", "اعتماد نوفمبر"), ("PERIOD_REOPENED", "تصحيح فاتورة"),
    ]


def test_close_requires_inventory_and_party_reconciliation(db):
    shop = build_shop(db)
    service = PeriodService(db)
    shop.accounting.post_opening("2026-11-01", "1100", 1_000)
    with pytest.raises(ValueError, match="account 1100"):
        service.close_month("2026-11", actor_id=shop.owner, reason="مطابقة")

    shop.accounting.post_journal("2026-10-01", "TEST_INVENTORY", 1, [
        {"account_code": "1200", "debit": 50}, {"account_code": "3030", "credit": 50},
    ])
    with pytest.raises(ValueError, match="account 1200"):
        service.close_month("2026-10", actor_id=shop.owner, reason="مطابقة")
    assert not service.is_closed("2026-11")
    assert not service.is_closed("2026-10")


@pytest.mark.parametrize("account_code", ["1100", "2010"])
def test_close_rejects_offsetting_unassigned_party_lines(db, account_code):
    shop = build_shop(db)
    shop.accounting.post_journal("2026-11-01", "UNASSIGNED", 1, [
        {"account_code": account_code, "debit": 1_000},
        {"account_code": "3030", "credit": 1_000},
    ])
    shop.accounting.post_journal("2026-11-02", "UNASSIGNED", 2, [
        {"account_code": "3030", "debit": 1_000},
        {"account_code": account_code, "credit": 1_000},
    ])
    report = shop.accounting.reconcile_parties(as_of="2026-11-30")[account_code]
    assert report["difference_piastres"] == 0
    assert report["unassigned_lines"] == 2
    assert not report["matched"]

    service = PeriodService(db)
    with pytest.raises(ValueError, match=f"account {account_code}"):
        service.close_month("2026-11", actor_id=shop.owner, reason="مطابقة")
    assert not service.is_closed("2026-11")
    assert db.scalar("SELECT COUNT(*) FROM audit_events WHERE event_type='PERIOD_CLOSED'") == 0
