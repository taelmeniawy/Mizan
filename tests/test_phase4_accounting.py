import sqlite3

import pytest

from mizan.accounting import AccountingService


@pytest.fixture()
def acct(db):
    service = AccountingService(db)
    service.seed_coa()
    return service


def test_T_ACC_001_balanced_sale_journal(acct):
    entry = acct.post_journal(
        "2026-11-01",
        "SALE",
        1,
        [
            {"account_code": "1100", "debit": 348000, "party_type": "CUSTOMER", "party_id": 1},
            {"account_code": "4010", "credit": 348000},
            {"account_code": "5010", "debit": 244000},
            {"account_code": "1200", "credit": 244000},
        ],
    )
    assert entry > 0
    assert acct.account_balance("1100") == 348000
    assert acct.account_balance("4010") == -348000
    assert acct.account_balance("5010") == 244000


def test_T_ACC_003_unbalanced_rejected(acct):
    with pytest.raises(ValueError):
        acct.post_journal("2026-11-01", "TEST", 1, [
            {"account_code": "1010", "debit": 100},
            {"account_code": "4010", "credit": 99},
        ])


def test_T_ACC_004_posted_journal_immutable(acct):
    entry = acct.post_journal("2026-11-01", "TEST", 2, [
        {"account_code": "1010", "debit": 100},
        {"account_code": "3030", "credit": 100},
    ])
    with pytest.raises(sqlite3.IntegrityError):
        with acct.db.transaction() as conn:
            conn.execute("UPDATE journal_entries SET description='edited' WHERE id=?", (entry,))
    with pytest.raises(sqlite3.IntegrityError):
        with acct.db.transaction() as conn:
            conn.execute("DELETE FROM journal_entries WHERE id=?", (entry,))


def test_T_ACC_005_reversal_keeps_original(acct):
    entry = acct.post_journal("2026-11-01", "TEST", 3, [
        {"account_code": "1010", "debit": 500},
        {"account_code": "3030", "credit": 500},
    ])
    reversal = acct.reverse(entry)
    assert reversal != entry
    assert acct.account_balance("1010") == 0
    with acct.db.connect() as conn:
        original = conn.execute("SELECT status,reversed_by_id FROM journal_entries WHERE id=?", (entry,)).fetchone()
        assert original["status"] == "REVERSED"
        assert original["reversed_by_id"] == reversal


def test_T_BAL_003_party_balance(acct):
    acct.post_journal("2026-11-01", "SALE", 4, [
        {"account_code": "1100", "debit": 1000, "party_type": "CUSTOMER", "party_id": 7},
        {"account_code": "4010", "credit": 1000},
    ])
    assert acct.party_balance("1100", "CUSTOMER", 7) == 1000
    assert acct.party_balance("1100", "CUSTOMER", 8) == 0


def test_opening_and_trial_balance(acct):
    acct.post_opening("2026-01-01", "1010", 10000)
    trial = {r["code"]: r for r in acct.trial_balance()}
    assert trial["1010"]["debit"] == 10000
    assert trial["3030"]["credit"] == 10000


def test_report_cutoff_includes_reversals_and_uses_control_account_signs(acct):
    acct.post_journal("2026-11-01", "OPENING", 10, [
        {"account_code": "1100", "debit": 600, "party_type": "CUSTOMER", "party_id": 1},
        {"account_code": "1100", "debit": 400, "party_type": "CUSTOMER", "party_id": 2},
        {"account_code": "3030", "credit": 1000},
    ])
    acct.post_journal("2026-11-05", "PURCHASE", 10, [
        {"account_code": "1200", "debit": 700},
        {"account_code": "2010", "credit": 700, "party_type": "SUPPLIER", "party_id": 1},
    ])
    acct.post_journal("2026-11-06", "RECEIPT", 10, [
        {"account_code": "1010", "debit": 250},
        {"account_code": "1100", "credit": 250, "party_type": "CUSTOMER", "party_id": 1},
    ])
    reversed_entry = acct.post_journal("2026-11-05", "PAYMENT", 10, [
        {"account_code": "2010", "debit": 300, "party_type": "SUPPLIER", "party_id": 1},
        {"account_code": "1010", "credit": 300},
    ])
    acct.reverse(reversed_entry)

    trial = {row["code"]: row for row in acct.trial_balance(as_of="2026-11-05")}
    assert trial["1100"]["debit"] == 1000
    assert trial["1100"]["credit"] == 0
    assert (trial["2010"]["debit"], trial["2010"]["credit"]) == (300, 1000)
    assert trial["1030"]["debit"] == trial["1030"]["credit"] == 0
    assert sum(row["debit"] for row in trial.values()) == sum(row["credit"] for row in trial.values())

    report = acct.reconcile_parties(as_of="2026-11-05")
    assert report["1100"]["party_balances_piastres"] == {1: 600, 2: 400}
    assert report["2010"]["party_balances_piastres"] == {1: 700}
    assert report["1100"]["matched"] and report["2010"]["matched"]
    assert acct.reconcile_parties()["1100"]["ledger_piastres"] == 750


def test_party_reconciliation_flags_even_offsetting_unassigned_lines(acct):
    acct.post_journal("2026-11-01", "OPENING", 11, [
        {"account_code": "1100", "debit": 500},
        {"account_code": "1100", "credit": 500, "party_type": "SUPPLIER", "party_id": 1},
    ])
    report = acct.reconcile_parties()["1100"]
    assert report["difference_piastres"] == 0
    assert report["unassigned_lines"] == 2
    assert not report["matched"]


def test_balance_sheet_includes_unclosed_result_and_avoids_double_counting_after_close(acct):
    acct.post_opening("2026-01-01", "1010", 100_000)
    acct.post_journal("2026-11-01", "PURCHASE", 12, [
        {"account_code": "1200", "debit": 10_000},
        {"account_code": "2010", "credit": 10_000, "party_type": "SUPPLIER", "party_id": 1},
    ])
    acct.post_journal("2026-11-02", "SALE", 12, [
        {"account_code": "1100", "debit": 15_000, "party_type": "CUSTOMER", "party_id": 1},
        {"account_code": "4010", "credit": 15_000},
        {"account_code": "5010", "debit": 6_000},
        {"account_code": "1200", "credit": 6_000},
    ])
    acct.post_journal("2026-11-03T23:45:00+02:00", "RENT", 12, [
        {"account_code": "5110", "debit": 1_000},
        {"account_code": "1010", "credit": 1_000},
    ])
    acct.post_journal("2026-11-04", "CLOSING", 12, [
        {"account_code": "4010", "debit": 15_000},
        {"account_code": "5010", "credit": 6_000},
        {"account_code": "5110", "credit": 1_000},
        {"account_code": "3020", "credit": 8_000},
    ], entry_kind="ADJUSTMENT")

    report = acct.balance_sheet(as_of="2026-11-03")
    assets = {row["code"]: row["balance_piastres"] for row in report["assets"]}
    assert assets["1010"] == 99_000
    assert assets["1100"] == 15_000
    assert assets["1200"] == 4_000
    assert report["assets_piastres"] == report["liabilities_and_equity_piastres"] == 118_000
    assert report["liabilities_piastres"] == 10_000
    assert report["posted_equity_piastres"] == 100_000
    assert report["unclosed_result_piastres"] == 8_000
    assert report["equity_piastres"] == 108_000
    assert report["balanced"] and report["difference_piastres"] == 0

    closed = acct.balance_sheet(as_of="2026-11-04")
    assert closed["posted_equity_piastres"] == 108_000
    assert closed["unclosed_result_piastres"] == 0
    assert closed["equity_piastres"] == report["equity_piastres"]
    assert closed["balanced"]
    trial = {row["code"]: row for row in acct.trial_balance(as_of="2026-11-03")}
    assert trial["5110"]["debit"] == 1_000
