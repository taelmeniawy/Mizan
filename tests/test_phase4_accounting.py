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
