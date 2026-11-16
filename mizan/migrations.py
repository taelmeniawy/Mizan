from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


MIGRATIONS: list[Migration] = [
    Migration(
        1,
        "foundation",
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sequences (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            year INTEGER NOT NULL,
            next_value INTEGER NOT NULL DEFAULT 1 CHECK (next_value > 0),
            prefix TEXT,
            UNIQUE (name, year)
        );

        CREATE TABLE IF NOT EXISTS store_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            name_ar TEXT NOT NULL,
            address TEXT,
            phone TEXT,
            logo_path TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_login_at TEXT
        );

        CREATE TABLE IF NOT EXISTS roles (
            id INTEGER PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            name_ar TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS permissions (
            id INTEGER PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            name_ar TEXT NOT NULL,
            category TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS role_permissions (
            role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
            permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
            PRIMARY KEY (role_id, permission_id)
        );

        CREATE TABLE IF NOT EXISTS user_roles (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
            PRIMARY KEY (user_id, role_id)
        );

        CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY,
            event_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id INTEGER,
            actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            before_json TEXT,
            after_json TEXT,
            reason TEXT,
            correlation_id TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_audit_entity
            ON audit_events(entity_type, entity_id, occurred_at);
        """,
    ),
    Migration(
        2,
        "master_data",
        """
        CREATE TABLE units (
            id INTEGER PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            name_ar TEXT NOT NULL,
            allows_fraction INTEGER NOT NULL DEFAULT 0 CHECK (allows_fraction IN (0,1)),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1))
        );

        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            name_ar TEXT NOT NULL,
            base_unit_id INTEGER NOT NULL REFERENCES units(id),
            tracking_mode TEXT NOT NULL CHECK (tracking_mode IN ('LOT','REEL')),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
            reorder_level_micros INTEGER NOT NULL DEFAULT 0 CHECK (reorder_level_micros >= 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE product_unit_conversions (
            id INTEGER PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            unit_id INTEGER NOT NULL REFERENCES units(id) ON DELETE RESTRICT,
            factor_num INTEGER NOT NULL CHECK (factor_num > 0),
            factor_den INTEGER NOT NULL CHECK (factor_den > 0),
            UNIQUE (product_id, unit_id)
        );

        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            name_ar TEXT NOT NULL,
            customer_type TEXT NOT NULL DEFAULT 'RETAIL' CHECK (customer_type IN ('RETAIL','WHOLESALE','CONTRACTOR')),
            phone TEXT,
            address TEXT,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1))
        );

        CREATE TABLE suppliers (
            id INTEGER PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            name_ar TEXT NOT NULL,
            phone TEXT,
            address TEXT,
            tax_id TEXT,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1))
        );

        CREATE TABLE projects (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
            code TEXT NOT NULL,
            name_ar TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            start_date TEXT,
            UNIQUE (customer_id, code)
        );

        CREATE TABLE price_lists (
            id INTEGER PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            name_ar TEXT NOT NULL,
            list_type TEXT NOT NULL DEFAULT 'GENERAL',
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1))
        );

        CREATE TABLE price_list_items (
            id INTEGER PRIMARY KEY,
            price_list_id INTEGER NOT NULL REFERENCES price_lists(id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            unit_id INTEGER NOT NULL REFERENCES units(id) ON DELETE RESTRICT,
            price_micros INTEGER NOT NULL CHECK (price_micros >= 0),
            UNIQUE (price_list_id, product_id, unit_id)
        );

        CREATE TABLE customer_prices (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            unit_id INTEGER NOT NULL REFERENCES units(id) ON DELETE RESTRICT,
            price_micros INTEGER NOT NULL CHECK (price_micros >= 0),
            UNIQUE (customer_id, product_id, unit_id)
        );

        CREATE TABLE product_default_prices (
            id INTEGER PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            unit_id INTEGER NOT NULL REFERENCES units(id) ON DELETE RESTRICT,
            price_micros INTEGER NOT NULL CHECK (price_micros >= 0),
            UNIQUE (product_id, unit_id)
        );
        """,
    ),
]


def apply_migrations(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    for migration in MIGRATIONS:
        if migration.version in applied:
            continue
        conn.executescript(migration.sql)
        conn.execute(
            "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
            (migration.version, migration.name),
        )
