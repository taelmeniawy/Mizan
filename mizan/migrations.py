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
    Migration(
        3,
        "accounting",
        """
        CREATE TABLE accounts (
            id INTEGER PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            name_ar TEXT NOT NULL,
            account_type TEXT NOT NULL CHECK (account_type IN ('ASSET','LIABILITY','EQUITY','REVENUE','EXPENSE')),
            parent_id INTEGER REFERENCES accounts(id) ON DELETE RESTRICT,
            is_control INTEGER NOT NULL DEFAULT 0 CHECK (is_control IN (0,1)),
            is_postable INTEGER NOT NULL DEFAULT 1 CHECK (is_postable IN (0,1)),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
            normal_balance TEXT NOT NULL CHECK (normal_balance IN ('DEBIT','CREDIT'))
        );

        CREATE TABLE account_mappings (
            id INTEGER PRIMARY KEY,
            mapping_key TEXT NOT NULL UNIQUE,
            account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT
        );

        CREATE TABLE journal_entries (
            id INTEGER PRIMARY KEY,
            entry_no TEXT NOT NULL UNIQUE,
            entry_date TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            entry_kind TEXT NOT NULL CHECK (entry_kind IN ('POSTING','REVERSAL','ADJUSTMENT')),
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'POSTED' CHECK (status IN ('POSTED','REVERSED')),
            posted_at TEXT NOT NULL,
            posted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            reversal_of_id INTEGER REFERENCES journal_entries(id) ON DELETE RESTRICT,
            reversed_by_id INTEGER REFERENCES journal_entries(id) ON DELETE RESTRICT,
            idempotency_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (source_type, source_id, entry_kind)
        );

        CREATE TABLE journal_lines (
            id INTEGER PRIMARY KEY,
            journal_entry_id INTEGER NOT NULL REFERENCES journal_entries(id) ON DELETE RESTRICT,
            line_no INTEGER NOT NULL,
            account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
            debit_piastres INTEGER NOT NULL DEFAULT 0 CHECK (debit_piastres >= 0),
            credit_piastres INTEGER NOT NULL DEFAULT 0 CHECK (credit_piastres >= 0),
            party_type TEXT,
            party_id INTEGER,
            project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
            description TEXT,
            source_line_id INTEGER,
            CHECK ((debit_piastres > 0) <> (credit_piastres > 0)),
            UNIQUE (journal_entry_id, line_no)
        );
        CREATE INDEX ix_journal_lines_account ON journal_lines(account_id, journal_entry_id);

        CREATE TABLE financial_accounts (
            id INTEGER PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            name_ar TEXT NOT NULL,
            account_type TEXT NOT NULL CHECK (account_type IN ('CASH','BANK','WALLET','OTHER')),
            gl_account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
            opening_date TEXT
        );

        CREATE TRIGGER journal_entries_no_delete
        BEFORE DELETE ON journal_entries
        WHEN OLD.status = 'POSTED'
        BEGIN
            SELECT RAISE(ABORT, 'posted journal entry is immutable');
        END;

        CREATE TRIGGER journal_entries_no_edit
        BEFORE UPDATE ON journal_entries
        WHEN OLD.status = 'POSTED' AND NEW.status = 'POSTED'
        BEGIN
            SELECT RAISE(ABORT, 'posted journal entry is immutable');
        END;

        CREATE TRIGGER journal_lines_no_update
        BEFORE UPDATE ON journal_lines
        WHEN (SELECT status FROM journal_entries WHERE id = OLD.journal_entry_id) = 'POSTED'
        BEGIN
            SELECT RAISE(ABORT, 'posted journal line is immutable');
        END;

        CREATE TRIGGER journal_lines_no_delete
        BEFORE DELETE ON journal_lines
        WHEN (SELECT status FROM journal_entries WHERE id = OLD.journal_entry_id) = 'POSTED'
        BEGIN
            SELECT RAISE(ABORT, 'posted journal line is immutable');
        END;
        """,
    ),
    Migration(
        4,
        "purchasing_inventory",
        """
        CREATE TABLE purchase_invoices (
            id INTEGER PRIMARY KEY,
            document_no TEXT NOT NULL UNIQUE,
            supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
            supplier_invoice_no TEXT,
            invoice_date TEXT NOT NULL,
            due_date TEXT,
            payment_type TEXT NOT NULL CHECK (payment_type IN ('CASH','CREDIT')),
            financial_account_id INTEGER REFERENCES financial_accounts(id) ON DELETE RESTRICT,
            status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','POSTED','CANCELLED')),
            subtotal_piastres INTEGER NOT NULL DEFAULT 0 CHECK (subtotal_piastres >= 0),
            expense_piastres INTEGER NOT NULL DEFAULT 0 CHECK (expense_piastres >= 0),
            total_piastres INTEGER NOT NULL DEFAULT 0 CHECK (total_piastres >= 0),
            posted_at TEXT,
            journal_entry_id INTEGER UNIQUE REFERENCES journal_entries(id) ON DELETE RESTRICT,
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE purchase_invoice_lines (
            id INTEGER PRIMARY KEY,
            purchase_invoice_id INTEGER NOT NULL REFERENCES purchase_invoices(id) ON DELETE RESTRICT,
            line_no INTEGER NOT NULL,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            unit_id INTEGER NOT NULL REFERENCES units(id) ON DELETE RESTRICT,
            quantity_micros INTEGER NOT NULL CHECK (quantity_micros > 0),
            base_qty_micros INTEGER NOT NULL CHECK (base_qty_micros > 0),
            unit_price_micros INTEGER NOT NULL CHECK (unit_price_micros >= 0),
            supplier_line_total_piastres INTEGER NOT NULL CHECK (supplier_line_total_piastres >= 0),
            net_piastres INTEGER NOT NULL CHECK (net_piastres >= 0),
            tracking_mode TEXT NOT NULL CHECK (tracking_mode IN ('LOT','REEL')),
            UNIQUE (purchase_invoice_id, line_no)
        );

        CREATE TABLE purchase_reel_inputs (
            id INTEGER PRIMARY KEY,
            purchase_invoice_line_id INTEGER NOT NULL REFERENCES purchase_invoice_lines(id) ON DELETE RESTRICT,
            seq INTEGER NOT NULL,
            length_micros INTEGER NOT NULL CHECK (length_micros > 0),
            UNIQUE (purchase_invoice_line_id, seq)
        );

        CREATE TABLE purchase_lots (
            id INTEGER PRIMARY KEY,
            lot_no TEXT NOT NULL UNIQUE,
            purchase_invoice_line_id INTEGER REFERENCES purchase_invoice_lines(id) ON DELETE RESTRICT,
            source_type TEXT NOT NULL DEFAULT 'PURCHASE' CHECK (source_type IN ('PURCHASE','OPENING','RETURN','ADJUSTMENT')),
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            warehouse_id INTEGER NOT NULL DEFAULT 1,
            supplier_id INTEGER REFERENCES suppliers(id) ON DELETE RESTRICT,
            tracking_mode TEXT NOT NULL CHECK (tracking_mode IN ('LOT','REEL')),
            received_at TEXT NOT NULL,
            original_qty_micros INTEGER NOT NULL CHECK (original_qty_micros > 0),
            original_supplier_cost_piastres INTEGER NOT NULL CHECK (original_supplier_cost_piastres >= 0),
            original_landed_cost_piastres INTEGER NOT NULL CHECK (original_landed_cost_piastres >= 0),
            status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','EXHAUSTED','RETURNED_TO_SUPPLIER')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE reels (
            id INTEGER PRIMARY KEY,
            reel_no TEXT NOT NULL UNIQUE,
            lot_id INTEGER NOT NULL REFERENCES purchase_lots(id) ON DELETE RESTRICT,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            original_length_micros INTEGER NOT NULL CHECK (original_length_micros > 0),
            supplier_cost_piastres INTEGER NOT NULL CHECK (supplier_cost_piastres >= 0),
            allocated_expense_piastres INTEGER NOT NULL CHECK (allocated_expense_piastres >= 0),
            original_cost_piastres INTEGER NOT NULL CHECK (original_cost_piastres >= 0),
            status TEXT NOT NULL DEFAULT 'SEALED' CHECK (status IN ('SEALED','OPENED','EMPTY','RETURNED','DAMAGED')),
            received_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE inventory_cost_layers (
            id INTEGER PRIMARY KEY,
            lot_id INTEGER NOT NULL REFERENCES purchase_lots(id) ON DELETE RESTRICT,
            reel_id INTEGER REFERENCES reels(id) ON DELETE RESTRICT,
            layer_type TEXT NOT NULL CHECK (layer_type IN ('BASE','CUSTOMER_RETURN','ADJUSTMENT_IN')),
            parent_issue_allocation_id INTEGER,
            original_qty_micros INTEGER NOT NULL CHECK (original_qty_micros > 0),
            original_cost_piastres INTEGER NOT NULL CHECK (original_cost_piastres >= 0),
            position_micros INTEGER NOT NULL DEFAULT 0 CHECK (position_micros >= 0),
            remaining_qty_micros INTEGER NOT NULL,
            remaining_cost_piastres INTEGER NOT NULL CHECK (remaining_cost_piastres >= 0),
            fifo_anchor_at TEXT NOT NULL,
            fifo_suborder INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','EXHAUSTED','DAMAGED')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (position_micros <= original_qty_micros),
            CHECK (remaining_qty_micros = original_qty_micros - position_micros),
            CHECK (remaining_cost_piastres <= original_cost_piastres)
        );
        CREATE INDEX ix_layers_fifo ON inventory_cost_layers(lot_id, status, fifo_anchor_at, fifo_suborder);

        CREATE TABLE stock_movements (
            id INTEGER PRIMARY KEY,
            movement_no TEXT NOT NULL UNIQUE,
            movement_type TEXT NOT NULL CHECK (movement_type IN ('RECEIPT','SALE_ISSUE','SALE_RETURN_IN','PURCHASE_RETURN','DAMAGE','ADJUSTMENT')),
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            warehouse_id INTEGER NOT NULL DEFAULT 1,
            lot_id INTEGER REFERENCES purchase_lots(id) ON DELETE RESTRICT,
            reel_id INTEGER REFERENCES reels(id) ON DELETE RESTRICT,
            quantity_micros INTEGER NOT NULL CHECK (quantity_micros > 0),
            cost_piastres INTEGER NOT NULL CHECK (cost_piastres >= 0),
            direction TEXT NOT NULL CHECK (direction IN ('IN','OUT')),
            source_type TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            source_line_id INTEGER,
            occurred_at TEXT NOT NULL,
            posted_at TEXT NOT NULL,
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE lot_allocations (
            id INTEGER PRIMARY KEY,
            stock_movement_id INTEGER NOT NULL REFERENCES stock_movements(id) ON DELETE RESTRICT,
            cost_layer_id INTEGER NOT NULL REFERENCES inventory_cost_layers(id) ON DELETE RESTRICT,
            lot_id INTEGER NOT NULL REFERENCES purchase_lots(id) ON DELETE RESTRICT,
            sales_line_id INTEGER,
            sales_return_line_id INTEGER,
            purchase_return_line_id INTEGER,
            allocation_role TEXT NOT NULL CHECK (allocation_role IN ('RECEIPT','SALE_ISSUE','SALE_RETURN','DAMAGE','PURCHASE_RETURN','ADJUSTMENT_IN','ADJUSTMENT_OUT')),
            quantity_micros INTEGER NOT NULL CHECK (quantity_micros > 0),
            cost_piastres INTEGER NOT NULL CHECK (cost_piastres >= 0),
            direction TEXT NOT NULL CHECK (direction IN ('IN','OUT')),
            layer_position_start_micros INTEGER,
            layer_position_end_micros INTEGER,
            source_issue_allocation_id INTEGER,
            created_layer_id INTEGER REFERENCES inventory_cost_layers(id) ON DELETE RESTRICT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (stock_movement_id, cost_layer_id, allocation_role)
        );

        CREATE TABLE reel_allocations (
            id INTEGER PRIMARY KEY,
            stock_movement_id INTEGER NOT NULL REFERENCES stock_movements(id) ON DELETE RESTRICT,
            cost_layer_id INTEGER NOT NULL REFERENCES inventory_cost_layers(id) ON DELETE RESTRICT,
            reel_id INTEGER NOT NULL REFERENCES reels(id) ON DELETE RESTRICT,
            sales_line_id INTEGER,
            sales_return_line_id INTEGER,
            purchase_return_line_id INTEGER,
            allocation_role TEXT NOT NULL CHECK (allocation_role IN ('RECEIPT','SALE_ISSUE','SALE_RETURN','DAMAGE','PURCHASE_RETURN','ADJUSTMENT_IN','ADJUSTMENT_OUT')),
            quantity_micros INTEGER NOT NULL CHECK (quantity_micros > 0),
            cost_piastres INTEGER NOT NULL CHECK (cost_piastres >= 0),
            direction TEXT NOT NULL CHECK (direction IN ('IN','OUT')),
            layer_position_start_micros INTEGER,
            layer_position_end_micros INTEGER,
            source_issue_allocation_id INTEGER,
            created_layer_id INTEGER REFERENCES inventory_cost_layers(id) ON DELETE RESTRICT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (stock_movement_id, cost_layer_id, allocation_role)
        );

        CREATE TABLE purchase_returns (
            id INTEGER PRIMARY KEY,
            document_no TEXT NOT NULL UNIQUE,
            purchase_invoice_id INTEGER NOT NULL REFERENCES purchase_invoices(id) ON DELETE RESTRICT,
            supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
            return_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','POSTED','CANCELLED')),
            total_piastres INTEGER NOT NULL DEFAULT 0 CHECK (total_piastres >= 0),
            journal_entry_id INTEGER UNIQUE REFERENCES journal_entries(id) ON DELETE RESTRICT,
            reason TEXT,
            posted_at TEXT
        );

        CREATE TABLE purchase_return_lines (
            id INTEGER PRIMARY KEY,
            purchase_return_id INTEGER NOT NULL REFERENCES purchase_returns(id) ON DELETE RESTRICT,
            purchase_invoice_line_id INTEGER NOT NULL REFERENCES purchase_invoice_lines(id) ON DELETE RESTRICT,
            lot_id INTEGER NOT NULL REFERENCES purchase_lots(id) ON DELETE RESTRICT,
            reel_id INTEGER REFERENCES reels(id) ON DELETE RESTRICT,
            quantity_micros INTEGER NOT NULL CHECK (quantity_micros > 0),
            cost_piastres INTEGER NOT NULL DEFAULT 0 CHECK (cost_piastres >= 0)
        );

        CREATE TRIGGER purchase_invoices_no_edit
        BEFORE UPDATE ON purchase_invoices
        WHEN OLD.status = 'POSTED' AND NEW.status = 'POSTED'
        BEGIN SELECT RAISE(ABORT, 'posted purchase invoice is immutable'); END;

        CREATE TRIGGER purchase_invoices_no_delete
        BEFORE DELETE ON purchase_invoices
        WHEN OLD.status = 'POSTED'
        BEGIN SELECT RAISE(ABORT, 'posted purchase invoice is immutable'); END;
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
