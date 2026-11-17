"""Read-only access to the append-only audit trail.

Audit rows are written by the services that own each operation.  The database
rejects UPDATE/DELETE on ``audit_events`` (migration 10), and this module only
reads them.  Viewing the trail requires the ``view_audit_log`` permission,
which the Owner and Manager roles hold and the Cashier role does not
(docs/08_SECURITY_AND_PERMISSIONS.md §4).
"""

from __future__ import annotations

import json
import re
from contextlib import closing
from datetime import date

from .auth import PermissionService

MAX_LIMIT = 500
DEFAULT_LIMIT = 100

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class AuditQueryError(ValueError):
    pass


def _decode(payload):
    if payload is None:
        return None
    try:
        return json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return payload


def _as_date(value, field: str):
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and _DATE.match(value):
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise AuditQueryError(f"{field} is not a valid date") from exc
        return value
    raise AuditQueryError(f"{field} must be an ISO date (YYYY-MM-DD)")


class AuditService:
    def __init__(self, db):
        self.db = db

    def query(self, actor_id: int, *, event_type: str | None = None,
              entity_type: str | None = None, entity_id: int | None = None,
              actor_user_id: int | None = None, from_date=None,
              through_date=None, limit: int = DEFAULT_LIMIT,
              offset: int = 0) -> dict:
        """Return a page of audit events, newest first.

        Date filters use the recorded calendar date (the first ten characters of
        the stored timestamp), inclusive at both ends, so the result does not
        depend on the caller's time zone.
        """
        PermissionService(self.db).require(actor_id, "view_audit_log")
        from_date = _as_date(from_date, "from_date")
        through_date = _as_date(through_date, "through_date")
        if from_date and through_date and from_date > through_date:
            raise AuditQueryError("from_date is after through_date")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
            raise AuditQueryError(f"limit must be between 1 and {MAX_LIMIT}")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise AuditQueryError("offset must not be negative")

        clauses, params = [], []
        for column, value in (
            ("a.event_type", event_type),
            ("a.entity_type", entity_type),
            ("a.entity_id", entity_id),
            ("a.actor_user_id", actor_user_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if from_date:
            clauses.append("substr(a.occurred_at,1,10) >= ?")
            params.append(from_date)
        if through_date:
            clauses.append("substr(a.occurred_at,1,10) <= ?")
            params.append(through_date)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with closing(self.db.connect()) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM audit_events a {where}", params
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT a.id, a.event_type, a.entity_type, a.entity_id, a.actor_user_id, "
                "a.occurred_at, a.before_json, a.after_json, a.reason, a.correlation_id, "
                "u.username AS actor_username "
                "FROM audit_events a LEFT JOIN users u ON u.id = a.actor_user_id "
                f"{where} ORDER BY a.occurred_at DESC, a.id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()

        events = []
        for row in rows:
            event = dict(row)
            event["before"] = _decode(event.pop("before_json"))
            event["after"] = _decode(event.pop("after_json"))
            events.append(event)
        return {"events": events, "total": total, "limit": limit, "offset": offset}
