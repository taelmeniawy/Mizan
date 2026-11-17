from __future__ import annotations

from calendar import monthrange
from datetime import date

from .auth import PermissionService
from .db import Database


class ClosedPeriodError(ValueError):
    pass


def require_open_period(conn, recorded_date: str) -> None:
    calendar_date = recorded_date[:10]
    if date.fromisoformat(calendar_date).isoformat() != calendar_date:
        raise ValueError("recorded date must start with YYYY-MM-DD")
    month = calendar_date[:7]
    if conn.execute("SELECT 1 FROM closed_periods WHERE month=?", (month,)).fetchone():
        raise ClosedPeriodError(f"accounting period {month} is closed")


class PeriodService:
    def __init__(self, db: Database):
        self.db = db

    def is_closed(self, month: str) -> bool:
        month = self._month(month)
        return bool(self.db.scalar("SELECT 1 FROM closed_periods WHERE month=?", (month,)))

    def close_month(self, month: str, *, actor_id: int, reason: str) -> None:
        self._set_closed(month, actor_id, reason, closed=True)

    def reopen_month(self, month: str, *, actor_id: int, reason: str) -> None:
        self._set_closed(month, actor_id, reason, closed=False)

    @staticmethod
    def _month(month: str) -> str:
        if date.fromisoformat(f"{month}-01").isoformat() != f"{month}-01":
            raise ValueError("period must be a YYYY-MM month")
        return month

    def _set_closed(self, month: str, actor_id: int, reason: str, *, closed: bool) -> None:
        PermissionService(self.db).require(actor_id, "manage_settings")
        month = self._month(month)
        reason = reason.strip()
        if not reason:
            raise ValueError("a period change requires a reason")
        with self.db.transaction() as conn:
            if closed:
                if conn.execute("SELECT 1 FROM closed_periods WHERE month=?", (month,)).fetchone():
                    return
                self._check_balances(month)
                changed = conn.execute("INSERT INTO closed_periods(month) VALUES(?)", (month,))
            else:
                changed = conn.execute("DELETE FROM closed_periods WHERE month=?", (month,))
            if changed.rowcount:
                conn.execute(
                    "INSERT INTO audit_events(event_type,entity_type,entity_id,actor_user_id,reason)"
                    " VALUES(?,'ACCOUNTING_PERIOD',?,?,?)",
                    ("PERIOD_CLOSED" if closed else "PERIOD_REOPENED", int(month.replace("-", "")), actor_id, reason),
                )

    def _check_balances(self, month: str) -> None:
        # The caller holds the SQLite write transaction while these read-only reports run.
        from .accounting import AccountingService
        from .reporting import ReportingService

        year, number = map(int, month.split("-"))
        as_of = date(year, number, monthrange(year, number)[1]).isoformat()
        accounting = AccountingService(self.db)
        if not accounting.balance_sheet(as_of=as_of)["balanced"]:
            raise ValueError("cannot close period: the ledger is not balanced")
        parties = accounting.reconcile_parties(as_of=as_of)
        for code, party in parties.items():
            if not party["matched"]:
                raise ValueError(f"cannot close period: account {code} does not reconcile")
        if ReportingService(self.db).inventory_valuation(as_of)["difference_piastres"]:
            raise ValueError("cannot close period: account 1200 does not reconcile")
