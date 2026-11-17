from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone

from .db import Database


class PermissionDenied(PermissionError):
    pass


ROLE_DEFS = {
    "OWNER": "المالك",
    "MANAGER": "المدير",
    "CASHIER": "الكاشير",
}

PERMISSION_DEFS = {
    "view_dashboard": ("عرض لوحة التشغيل", "general"),
    "manage_users": ("إدارة المستخدمين", "admin"),
    "manage_roles": ("إدارة الأدوار", "admin"),
    "manage_settings": ("إدارة الإعدادات", "admin"),
    "manage_products": ("إدارة المنتجات", "master"),
    "manage_customers": ("إدارة العملاء", "master"),
    "manage_suppliers": ("إدارة الموردين", "master"),
    "manage_projects": ("إدارة المشاريع", "master"),
    "manage_price_lists": ("إدارة قوائم الأسعار", "master"),
    "post_purchases": ("ترحيل فواتير الشراء", "purchases"),
    "post_purchase_return": ("ترحيل مرتجع الشراء", "purchases"),
    "manage_supplier_payments": ("دفعات الموردين", "purchases"),
    "manage_inventory": ("إدارة المخزون والبكرات", "inventory"),
    "view_purchase_cost": ("رؤية تكلفة الشراء", "inventory"),
    "view_inventory_cost": ("رؤية قيمة المخزون", "inventory"),
    "post_sales": ("ترحيل المبيعات", "sales"),
    "create_sales_draft": ("إنشاء مسودة بيع", "sales"),
    "cancel_sales_draft": ("إلغاء مسودة بيع", "sales"),
    "post_sales_return": ("ترحيل مرتجع البيع", "sales"),
    "apply_item_discount": ("خصم بند", "sales"),
    "apply_invoice_discount": ("خصم فاتورة", "sales"),
    "edit_sale_price": ("تعديل سعر البيع", "sales"),
    "issue_customer_refund": ("رد نقدي/بنكي لعميل", "sales"),
    "manage_customer_receipts": ("تحصيلات العملاء", "finance"),
    "view_customer_balance": ("رصيد العميل", "sales"),
    "approve_below_cost": ("اعتماد البيع تحت التكلفة", "pricing"),
    "view_sensitive_accounting_reports": ("تقارير محاسبية حساسة", "finance"),
    "view_profit_reports": ("تقارير الربح", "reports"),
    "backup_database": ("نسخ احتياطي", "system"),
    "restore_database": ("استعادة نسخة", "system"),
}

ROLE_PERMISSIONS = {
    "OWNER": set(PERMISSION_DEFS),
    "MANAGER": set(PERMISSION_DEFS) - {"manage_users", "manage_roles", "manage_settings", "backup_database", "restore_database"},
    "CASHIER": {"view_dashboard", "create_sales_draft", "post_sales", "apply_item_discount",
                "view_customer_balance", "manage_customer_receipts"},
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class AuthService:
    def __init__(self, db: Database):
        self.db = db

    def seed_defaults(self) -> None:
        with self.db.transaction() as conn:
            for code, name in ROLE_DEFS.items():
                conn.execute("INSERT OR IGNORE INTO roles(code,name_ar) VALUES(?,?)", (code, name))
            for code, (name, category) in PERMISSION_DEFS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO permissions(code,name_ar,category) VALUES(?,?,?)",
                    (code, name, category),
                )
            for role_code, permission_codes in ROLE_PERMISSIONS.items():
                role_id = conn.execute("SELECT id FROM roles WHERE code=?", (role_code,)).fetchone()["id"]
                for code in permission_codes:
                    permission_id = conn.execute("SELECT id FROM permissions WHERE code=?", (code,)).fetchone()["id"]
                    conn.execute(
                        "INSERT OR IGNORE INTO role_permissions(role_id,permission_id) VALUES(?,?)",
                        (role_id, permission_id),
                    )

    @staticmethod
    def _hash_password(password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
        return f"pbkdf2_sha256$120000${salt.hex()}${digest.hex()}"

    @staticmethod
    def _verify_password(password: str, stored: str) -> bool:
        try:
            scheme, iterations, salt_hex, digest_hex = stored.split("$", 3)
            if scheme != "pbkdf2_sha256":
                return False
            digest = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
            )
            return hmac.compare_digest(digest.hex(), digest_hex)
        except (ValueError, TypeError):
            return False

    def create_user(self, username: str, display_name: str, password: str, role_codes: list[str]) -> int:
        if not username.strip() or not password:
            raise ValueError("username and password are required")
        with self.db.transaction() as conn:
            role_ids = []
            for role_code in role_codes:
                row = conn.execute("SELECT id FROM roles WHERE code=?", (role_code,)).fetchone()
                if row is None:
                    raise ValueError(f"unknown role: {role_code}")
                role_ids.append(row["id"])
            try:
                cur = conn.execute(
                    "INSERT INTO users(username,display_name,password_hash) VALUES(?,?,?)",
                    (username.strip(), display_name.strip(), self._hash_password(password)),
                )
            except Exception as exc:
                raise ValueError(f"cannot create user: {exc}") from exc
            user_id = int(cur.lastrowid)
            for role_id in role_ids:
                conn.execute("INSERT INTO user_roles(user_id,role_id) VALUES(?,?)", (user_id, role_id))
            return user_id

    def login(self, username: str, password: str) -> dict:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if row is None or not row["is_active"] or not self._verify_password(password, row["password_hash"]):
            raise ValueError("invalid username or password")
        with self.db.transaction() as conn:
            conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (_now(), row["id"]))
            conn.execute(
                "INSERT INTO audit_events(event_type,entity_type,entity_id,actor_user_id,occurred_at) VALUES(?,?,?,?,?)",
                ("LOGIN", "user", row["id"], row["id"], _now()),
            )
        return dict(row) | {"last_login_at": _now()}

    def disable_user(self, user_id: int, actor_id: int | None) -> None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT id,is_active FROM users WHERE id=?", (user_id,)).fetchone()
            if row is None:
                raise ValueError("user not found")
            conn.execute("UPDATE users SET is_active=0 WHERE id=?", (user_id,))
            self._audit(conn, "USER_DISABLED", "user", user_id, actor_id, before={"is_active": 1}, after={"is_active": 0})

    def assign_role(self, user_id: int, role_code: str, actor_id: int | None) -> None:
        with self.db.transaction() as conn:
            role = conn.execute("SELECT id FROM roles WHERE code=?", (role_code,)).fetchone()
            if role is None:
                raise ValueError(f"unknown role: {role_code}")
            if conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() is None:
                raise ValueError("user not found")
            conn.execute("DELETE FROM user_roles WHERE user_id=?", (user_id,))
            conn.execute("INSERT INTO user_roles(user_id,role_id) VALUES(?,?)", (user_id, role["id"]))
            self._audit(conn, "USER_ROLE_CHANGED", "user", user_id, actor_id, after={"role": role_code})

    @staticmethod
    def _audit(conn, event_type, entity_type, entity_id, actor_id, before=None, after=None):
        conn.execute(
            "INSERT INTO audit_events(event_type,entity_type,entity_id,actor_user_id,occurred_at,before_json,after_json) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                event_type,
                entity_type,
                entity_id,
                actor_id,
                _now(),
                json.dumps(before, ensure_ascii=False) if before is not None else None,
                json.dumps(after, ensure_ascii=False) if after is not None else None,
            ),
        )


class PermissionService:
    def __init__(self, db: Database):
        self.db = db

    def permissions(self, user_id: int) -> set[str]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT p.code FROM permissions p "
                "JOIN role_permissions rp ON rp.permission_id=p.id "
                "JOIN user_roles ur ON ur.role_id=rp.role_id "
                "JOIN users u ON u.id=ur.user_id "
                "WHERE ur.user_id=? AND u.is_active=1",
                (user_id,),
            ).fetchall()
        return {r["code"] for r in rows}

    def has(self, user_id: int, permission: str) -> bool:
        return permission in self.permissions(user_id)

    def require(self, user_id: int, permission: str) -> None:
        if not self.has(user_id, permission):
            raise PermissionDenied(f"missing permission: {permission}")
