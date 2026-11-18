"""Application bootstrap shared by the desktop UI and tests.

The core remains headless: this module only wires the paths, the database, the
permission catalogue and the Phase 9 services together, then runs the startup
integrity check before any business screen is shown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from .accounting import AccountingService
from .audit import AuditService
from .auth import AuthService, PermissionService
from .backup import BackupService
from .backup_policy import ScheduledBackups
from .config import AppPaths
from .db import Database
from .diagnostics import startup_check


class BootstrapError(ValueError):
    pass


@dataclass
class AppContext:
    paths: AppPaths
    db: Database
    accounting: AccountingService
    auth: AuthService
    permissions: PermissionService
    audit: AuditService
    backups: BackupService
    schedule: ScheduledBackups
    startup: dict

    def has_permission(self, user_id: int, permission: str) -> bool:
        return self.permissions.has(user_id, permission)

    def user_count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM users") or 0)


def start(paths: AppPaths | None = None) -> AppContext:
    """Open (creating on first run) the data root and prepare every service."""
    paths = (paths or AppPaths.default()).ensure()
    db = Database(paths.db_path)
    db.initialize()

    accounting = AccountingService(db)
    accounting.seed_coa()
    auth = AuthService(db)
    auth.seed_defaults()

    backups = BackupService(db, paths.documents_root)
    context = AppContext(
        paths=paths,
        db=db,
        accounting=accounting,
        auth=auth,
        permissions=PermissionService(db),
        audit=AuditService(db),
        backups=backups,
        schedule=ScheduledBackups(backups, paths.backups_root),
        startup=startup_check(db, paths.documents_root),
    )
    return context


def initialize_first_run(context: AppContext, *, store_name: str, display_name: str,
                         username: str, password: str, confirm_password: str | None = None,
                         address: str = "", phone: str = "", cash_account_name: str | None = None) -> int:
    """Create the store profile and the first Owner account exactly once."""
    store_name = (store_name or "").strip()
    display_name = (display_name or "").strip()
    username = (username or "").strip()
    if not store_name or not display_name or not username:
        raise BootstrapError("store name, display name and username are required")
    if not password or len(password) < 8:
        raise BootstrapError("the password must be at least 8 characters")
    if confirm_password is not None and password != confirm_password:
        raise BootstrapError("the password confirmation does not match")
    if cash_account_name is not None and not cash_account_name.strip():
        raise BootstrapError("cash account name is required")

    with context.db.transaction() as conn:
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None:
            raise BootstrapError("the application already has users")
        conn.execute(
            "INSERT INTO store_profile(id,name_ar,address,phone) VALUES(1,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name_ar=excluded.name_ar,address=excluded.address,"
            "phone=excluded.phone,updated_at=CURRENT_TIMESTAMP",
            (store_name, address.strip() or None, phone.strip() or None),
        )
        owner_id = context.auth._create_user(conn, username, display_name, password, ["OWNER"])
        cash_id = None
        if cash_account_name is not None:
            cash_id = context.accounting._create_financial_account(
                conn, "CASH", cash_account_name.strip(), "CASH", "1010",
            )
        conn.execute(
            "INSERT INTO audit_events(event_type,entity_type,entity_id,actor_user_id,"
            "occurred_at,after_json) VALUES('STORE_INITIALIZED','user',?,?,?,?)",
            (owner_id, owner_id,
             datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
             json.dumps({"store_name": store_name, "username": username, "cash_account_id": cash_id},
                        ensure_ascii=False)),
        )
    return owner_id
