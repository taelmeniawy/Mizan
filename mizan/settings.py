"""Store and financial-account settings shared by the desktop screens."""

from __future__ import annotations

from .auth import AuthService, PermissionService
from .db import Database


class SettingsService:
    def __init__(self, db: Database):
        self.db = db
        self.permissions = PermissionService(db)

    def save_store(self, name: str, address: str, phone: str, *, actor_id: int) -> None:
        name = name.strip()
        if not name:
            raise ValueError("أدخل اسم المتجر.")
        values = {"name_ar": name, "address": address.strip() or None, "phone": phone.strip() or None}
        with self.db.transaction() as conn:
            self.permissions.require(actor_id, "manage_settings")
            row = conn.execute("SELECT name_ar,address,phone FROM store_profile WHERE id=1").fetchone()
            before = dict(row) if row is not None else None
            if before == values:
                return
            conn.execute(
                "INSERT INTO store_profile(id,name_ar,address,phone) VALUES(1,:name_ar,:address,:phone) "
                "ON CONFLICT(id) DO UPDATE SET name_ar=excluded.name_ar,address=excluded.address,"
                "phone=excluded.phone,updated_at=CURRENT_TIMESTAMP",
                values,
            )
            AuthService._audit(conn, "STORE_PROFILE_UPDATED", "store_profile", 1, actor_id,
                               before=before, after=values)

    def update_financial_account(self, account_id: int, *, actor_id: int,
                                 name: str | None = None, active: bool | None = None) -> None:
        with self.db.transaction() as conn:
            self.permissions.require(actor_id, "manage_settings")
            row = conn.execute(
                "SELECT name_ar,is_active FROM financial_accounts WHERE id=?", (account_id,),
            ).fetchone()
            if row is None:
                raise ValueError("الحساب المالي غير موجود.")
            after = {
                "name_ar": row["name_ar"] if name is None else name.strip(),
                "is_active": row["is_active"] if active is None else int(active),
            }
            if not after["name_ar"]:
                raise ValueError("أدخل اسم الحساب المالي.")
            if after != dict(row):
                conn.execute(
                    "UPDATE financial_accounts SET name_ar=:name_ar,is_active=:is_active WHERE id=:id",
                    after | {"id": account_id},
                )
                AuthService._audit(conn, "FINANCIAL_ACCOUNT_UPDATED", "financial_account", account_id, actor_id,
                                   before=dict(row), after=after)
