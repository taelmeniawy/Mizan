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
    Migration(
        5,
        "sales",
        """
        CREATE TABLE sales (
            id INTEGER PRIMARY KEY,
            document_no TEXT NOT NULL UNIQUE,
            customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
            project_id INTEGER REFERENCES projects(id) ON DELETE RESTRICT,
            price_list_id INTEGER REFERENCES price_lists(id) ON DELETE RESTRICT,
            sale_type TEXT NOT NULL CHECK (sale_type IN ('RETAIL','WHOLESALE','CONTRACTOR')),
            payment_type TEXT NOT NULL CHECK (payment_type IN ('CASH','CREDIT','MIXED')),
            status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','POSTED','CANCELLED')),
            invoice_date TEXT NOT NULL,
            due_date TEXT,
            financial_account_id INTEGER REFERENCES financial_accounts(id) ON DELETE RESTRICT,
            subtotal_piastres INTEGER NOT NULL DEFAULT 0 CHECK (subtotal_piastres >= 0),
            item_discount_piastres INTEGER NOT NULL DEFAULT 0 CHECK (item_discount_piastres >= 0),
            invoice_discount_piastres INTEGER NOT NULL DEFAULT 0 CHECK (invoice_discount_piastres >= 0),
            total_piastres INTEGER NOT NULL DEFAULT 0 CHECK (total_piastres >= 0),
            below_cost_reason TEXT,
            below_cost_approved_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            posted_at TEXT,
            journal_entry_id INTEGER UNIQUE REFERENCES journal_entries(id) ON DELETE RESTRICT,
            reversal_journal_entry_id INTEGER UNIQUE REFERENCES journal_entries(id) ON DELETE RESTRICT,
            reversal_reason TEXT,
            reversed_at TEXT,
            reversed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            cancellation_reason TEXT,
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (total_piastres = subtotal_piastres - item_discount_piastres - invoice_discount_piastres),
            CHECK (item_discount_piastres <= subtotal_piastres),
            CHECK (invoice_discount_piastres <= subtotal_piastres - item_discount_piastres)
        );
        CREATE INDEX ix_sales_customer_date ON sales(customer_id, invoice_date);
        CREATE INDEX ix_sales_status ON sales(status);

        CREATE TABLE sales_lines (
            id INTEGER PRIMARY KEY,
            sales_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE RESTRICT,
            line_no INTEGER NOT NULL,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            unit_id INTEGER NOT NULL REFERENCES units(id) ON DELETE RESTRICT,
            quantity_micros INTEGER NOT NULL CHECK (quantity_micros > 0),
            base_qty_micros INTEGER NOT NULL CHECK (base_qty_micros > 0),
            unit_price_micros INTEGER NOT NULL CHECK (unit_price_micros >= 0),
            gross_piastres INTEGER NOT NULL CHECK (gross_piastres >= 0),
            item_discount_piastres INTEGER NOT NULL DEFAULT 0 CHECK (item_discount_piastres >= 0),
            invoice_discount_alloc_piastres INTEGER NOT NULL DEFAULT 0 CHECK (invoice_discount_alloc_piastres >= 0),
            net_piastres INTEGER NOT NULL CHECK (net_piastres >= 0),
            cost_piastres INTEGER NOT NULL DEFAULT 0 CHECK (cost_piastres >= 0),
            price_source TEXT NOT NULL CHECK (price_source IN ('CUSTOMER','PRICE_LIST','DEFAULT','MANUAL')),
            price_adjustment_reason TEXT,
            conversion_num INTEGER NOT NULL CHECK (conversion_num > 0),
            conversion_den INTEGER NOT NULL CHECK (conversion_den > 0),
            tracking_mode TEXT NOT NULL CHECK (tracking_mode IN ('LOT','REEL')),
            project_id INTEGER REFERENCES projects(id) ON DELETE RESTRICT,
            quotation_line_id INTEGER,
            UNIQUE (sales_id, line_no),
            CHECK (item_discount_piastres <= gross_piastres),
            CHECK (invoice_discount_alloc_piastres <= gross_piastres - item_discount_piastres),
            CHECK (net_piastres = gross_piastres - item_discount_piastres - invoice_discount_alloc_piastres)
        );
        CREATE INDEX ix_sales_lines_sales ON sales_lines(sales_id);
        CREATE INDEX ix_sales_lines_product ON sales_lines(product_id);
        CREATE INDEX ix_lot_alloc_sales_line ON lot_allocations(sales_line_id);
        CREATE INDEX ix_reel_alloc_sales_line ON reel_allocations(sales_line_id);

        CREATE TABLE sales_status_history (
            id INTEGER PRIMARY KEY,
            sales_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE RESTRICT,
            from_status TEXT,
            to_status TEXT NOT NULL,
            reason TEXT,
            actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX ix_sales_status_history ON sales_status_history(sales_id, occurred_at);

        CREATE TABLE sales_returns (
            id INTEGER PRIMARY KEY,
            document_no TEXT NOT NULL UNIQUE,
            sales_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE RESTRICT,
            customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
            return_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','POSTED','CANCELLED')),
            settlement_method TEXT NOT NULL DEFAULT 'REDUCE_RECEIVABLE'
                CHECK (settlement_method IN ('REDUCE_RECEIVABLE','CUSTOMER_CREDIT','CASH_REFUND','BANK_REFUND','MIXED')),
            commercial_total_piastres INTEGER NOT NULL DEFAULT 0 CHECK (commercial_total_piastres >= 0),
            inventory_cost_piastres INTEGER NOT NULL DEFAULT 0 CHECK (inventory_cost_piastres >= 0),
            ar_reduction_piastres INTEGER NOT NULL DEFAULT 0 CHECK (ar_reduction_piastres >= 0),
            customer_credit_piastres INTEGER NOT NULL DEFAULT 0 CHECK (customer_credit_piastres >= 0),
            refund_piastres INTEGER NOT NULL DEFAULT 0 CHECK (refund_piastres >= 0),
            financial_account_id INTEGER REFERENCES financial_accounts(id) ON DELETE RESTRICT,
            refund_reference TEXT,
            damaged_cost_accounting TEXT NOT NULL DEFAULT 'NOT_APPLICABLE'
                CHECK (damaged_cost_accounting IN (
                    'NOT_APPLICABLE','PENDING','DEFERRED_OQ_INV_004','POSTED_5020_5010'
                )),
            reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
            journal_entry_id INTEGER UNIQUE REFERENCES journal_entries(id) ON DELETE RESTRICT,
            posted_at TEXT,
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (status <> 'POSTED'
                   OR commercial_total_piastres = ar_reduction_piastres + customer_credit_piastres + refund_piastres),
            CHECK (refund_piastres = 0 OR financial_account_id IS NOT NULL),
            CHECK (refund_piastres = 0 OR (refund_reference IS NOT NULL AND length(trim(refund_reference)) > 0))
        );
        CREATE INDEX ix_sales_returns_sales ON sales_returns(sales_id);

        CREATE TABLE sales_return_lines (
            id INTEGER PRIMARY KEY,
            sales_return_id INTEGER NOT NULL REFERENCES sales_returns(id) ON DELETE RESTRICT,
            line_no INTEGER NOT NULL,
            sales_line_id INTEGER NOT NULL REFERENCES sales_lines(id) ON DELETE RESTRICT,
            quantity_micros INTEGER NOT NULL CHECK (quantity_micros > 0),
            base_qty_micros INTEGER NOT NULL CHECK (base_qty_micros > 0),
            commercial_value_piastres INTEGER NOT NULL DEFAULT 0 CHECK (commercial_value_piastres >= 0),
            cost_piastres INTEGER NOT NULL DEFAULT 0 CHECK (cost_piastres >= 0),
            condition TEXT NOT NULL CHECK (condition IN ('SELLABLE','DAMAGED')),
            lot_id INTEGER REFERENCES purchase_lots(id) ON DELETE RESTRICT,
            reel_id INTEGER REFERENCES reels(id) ON DELETE RESTRICT,
            UNIQUE (sales_return_id, line_no)
        );
        CREATE INDEX ix_sales_return_lines_return ON sales_return_lines(sales_return_id);
        CREATE INDEX ix_sales_return_lines_sale_line ON sales_return_lines(sales_line_id);

        CREATE TABLE sales_return_line_allocations (
            id INTEGER PRIMARY KEY,
            sales_return_line_id INTEGER NOT NULL REFERENCES sales_return_lines(id) ON DELETE RESTRICT,
            allocation_kind TEXT NOT NULL CHECK (allocation_kind IN ('LOT','REEL')),
            original_allocation_id INTEGER NOT NULL,
            quantity_micros INTEGER NOT NULL CHECK (quantity_micros > 0),
            UNIQUE (sales_return_line_id, allocation_kind, original_allocation_id)
        );
        CREATE INDEX ix_srla_line ON sales_return_line_allocations(sales_return_line_id);
        CREATE INDEX ix_srla_original ON sales_return_line_allocations(allocation_kind, original_allocation_id);

        CREATE TABLE customer_receipts (
            id INTEGER PRIMARY KEY,
            document_no TEXT NOT NULL UNIQUE,
            customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
            financial_account_id INTEGER NOT NULL REFERENCES financial_accounts(id) ON DELETE RESTRICT,
            amount_piastres INTEGER NOT NULL CHECK (amount_piastres > 0),
            receipt_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','POSTED','CANCELLED')),
            notes TEXT,
            journal_entry_id INTEGER UNIQUE REFERENCES journal_entries(id) ON DELETE RESTRICT,
            posted_at TEXT,
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE customer_receipt_allocations (
            id INTEGER PRIMARY KEY,
            receipt_id INTEGER NOT NULL REFERENCES customer_receipts(id) ON DELETE RESTRICT,
            sales_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE RESTRICT,
            amount_piastres INTEGER NOT NULL CHECK (amount_piastres > 0),
            UNIQUE (receipt_id, sales_id)
        );
        CREATE INDEX ix_receipt_alloc_sales ON customer_receipt_allocations(sales_id);

        CREATE TABLE customer_credits (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
            source_type TEXT NOT NULL CHECK (source_type IN ('SALES_RETURN','OVERPAYMENT','MANUAL_APPROVED')),
            source_id INTEGER NOT NULL,
            amount_piastres INTEGER NOT NULL CHECK (amount_piastres > 0),
            remaining_piastres INTEGER NOT NULL
                CHECK (remaining_piastres >= 0 AND remaining_piastres <= amount_piastres),
            status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','APPLIED','CLOSED')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (source_type, source_id)
        );
        CREATE INDEX ix_customer_credits_customer ON customer_credits(customer_id, status);

        CREATE TABLE customer_credit_applications (
            id INTEGER PRIMARY KEY,
            credit_id INTEGER NOT NULL REFERENCES customer_credits(id) ON DELETE RESTRICT,
            sales_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE RESTRICT,
            amount_piastres INTEGER NOT NULL CHECK (amount_piastres > 0),
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX ix_credit_app_sales ON customer_credit_applications(sales_id);

        CREATE TRIGGER customer_return_layer_parent_required
        BEFORE INSERT ON inventory_cost_layers
        WHEN NEW.layer_type = 'CUSTOMER_RETURN' AND NEW.parent_issue_allocation_id IS NULL
        BEGIN SELECT RAISE(ABORT, 'customer return layer requires a parent issue allocation'); END;

        CREATE TRIGGER base_layer_no_parent
        BEFORE INSERT ON inventory_cost_layers
        WHEN NEW.layer_type IN ('BASE','ADJUSTMENT_IN') AND NEW.parent_issue_allocation_id IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'base/adjustment layer cannot have a parent allocation'); END;

        CREATE TRIGGER sales_posted_immutable
        BEFORE UPDATE ON sales
        WHEN OLD.status = 'POSTED' AND (
            NEW.document_no IS NOT OLD.document_no OR
            NEW.customer_id IS NOT OLD.customer_id OR
            NEW.project_id IS NOT OLD.project_id OR
            NEW.price_list_id IS NOT OLD.price_list_id OR
            NEW.sale_type IS NOT OLD.sale_type OR
            NEW.payment_type IS NOT OLD.payment_type OR
            NEW.invoice_date IS NOT OLD.invoice_date OR
            NEW.due_date IS NOT OLD.due_date OR
            NEW.financial_account_id IS NOT OLD.financial_account_id OR
            NEW.subtotal_piastres IS NOT OLD.subtotal_piastres OR
            NEW.item_discount_piastres IS NOT OLD.item_discount_piastres OR
            NEW.invoice_discount_piastres IS NOT OLD.invoice_discount_piastres OR
            NEW.total_piastres IS NOT OLD.total_piastres OR
            NEW.below_cost_reason IS NOT OLD.below_cost_reason OR
            NEW.below_cost_approved_by IS NOT OLD.below_cost_approved_by OR
            NEW.cancellation_reason IS NOT OLD.cancellation_reason OR
            NEW.posted_at IS NOT OLD.posted_at OR
            NEW.journal_entry_id IS NOT OLD.journal_entry_id OR
            NEW.created_by IS NOT OLD.created_by OR
            NEW.created_at IS NOT OLD.created_at
        )
        BEGIN SELECT RAISE(ABORT, 'posted sales invoice is immutable'); END;

        CREATE TRIGGER sales_posted_no_cancel
        BEFORE UPDATE ON sales
        WHEN OLD.status = 'POSTED' AND NEW.status = 'CANCELLED'
        BEGIN SELECT RAISE(ABORT, 'posted sales invoice cannot be cancelled'); END;

        CREATE TRIGGER sales_posted_no_reopen
        BEFORE UPDATE ON sales
        WHEN OLD.status = 'POSTED' AND NEW.status = 'DRAFT'
        BEGIN SELECT RAISE(ABORT, 'posted sales invoice cannot be reopened'); END;

        CREATE TRIGGER sales_no_delete_posted
        BEFORE DELETE ON sales
        WHEN OLD.status = 'POSTED'
        BEGIN SELECT RAISE(ABORT, 'posted sales invoice is immutable'); END;

        CREATE TRIGGER sales_reversal_immutable
        BEFORE UPDATE ON sales
        WHEN OLD.reversal_journal_entry_id IS NOT NULL AND (
            NEW.reversal_journal_entry_id IS NOT OLD.reversal_journal_entry_id OR
            NEW.reversal_reason IS NOT OLD.reversal_reason OR
            NEW.reversed_at IS NOT OLD.reversed_at OR
            NEW.reversed_by IS NOT OLD.reversed_by
        )
        BEGIN SELECT RAISE(ABORT, 'sales reversal is immutable'); END;

        CREATE TRIGGER sales_lines_no_edit_posted
        BEFORE UPDATE ON sales_lines
        WHEN (SELECT status FROM sales WHERE id = OLD.sales_id) = 'POSTED'
        BEGIN SELECT RAISE(ABORT, 'posted sales line is immutable'); END;

        CREATE TRIGGER sales_lines_no_delete_posted
        BEFORE DELETE ON sales_lines
        WHEN (SELECT status FROM sales WHERE id = OLD.sales_id) = 'POSTED'
        BEGIN SELECT RAISE(ABORT, 'posted sales line is immutable'); END;

        CREATE TRIGGER sales_returns_no_edit_posted
        BEFORE UPDATE ON sales_returns
        WHEN OLD.status = 'POSTED' AND (
            NEW.document_no IS NOT OLD.document_no OR
            NEW.sales_id IS NOT OLD.sales_id OR
            NEW.customer_id IS NOT OLD.customer_id OR
            NEW.return_date IS NOT OLD.return_date OR
            NEW.settlement_method IS NOT OLD.settlement_method OR
            NEW.commercial_total_piastres IS NOT OLD.commercial_total_piastres OR
            NEW.inventory_cost_piastres IS NOT OLD.inventory_cost_piastres OR
            NEW.ar_reduction_piastres IS NOT OLD.ar_reduction_piastres OR
            NEW.customer_credit_piastres IS NOT OLD.customer_credit_piastres OR
            NEW.refund_piastres IS NOT OLD.refund_piastres OR
            NEW.financial_account_id IS NOT OLD.financial_account_id OR
            NEW.refund_reference IS NOT OLD.refund_reference OR
            NEW.damaged_cost_accounting IS NOT OLD.damaged_cost_accounting OR
            NEW.reason IS NOT OLD.reason OR
            NEW.journal_entry_id IS NOT OLD.journal_entry_id OR
            NEW.posted_at IS NOT OLD.posted_at OR
            NEW.created_by IS NOT OLD.created_by OR
            NEW.created_at IS NOT OLD.created_at
        )
        BEGIN SELECT RAISE(ABORT, 'posted sales return is immutable'); END;

        CREATE TRIGGER sales_returns_posted_no_cancel
        BEFORE UPDATE ON sales_returns
        WHEN OLD.status = 'POSTED' AND NEW.status = 'CANCELLED'
        BEGIN SELECT RAISE(ABORT, 'posted sales return cannot be cancelled'); END;

        CREATE TRIGGER sales_returns_no_delete_posted
        BEFORE DELETE ON sales_returns
        WHEN OLD.status = 'POSTED'
        BEGIN SELECT RAISE(ABORT, 'posted sales return is immutable'); END;

        CREATE TRIGGER sales_return_lines_no_edit_posted
        BEFORE UPDATE ON sales_return_lines
        WHEN (SELECT status FROM sales_returns WHERE id = OLD.sales_return_id) = 'POSTED'
        BEGIN SELECT RAISE(ABORT, 'posted sales return line is immutable'); END;

        CREATE TRIGGER sales_return_lines_no_delete_posted
        BEFORE DELETE ON sales_return_lines
        WHEN (SELECT status FROM sales_returns WHERE id = OLD.sales_return_id) = 'POSTED'
        BEGIN SELECT RAISE(ABORT, 'posted sales return line is immutable'); END;

        CREATE TRIGGER sales_return_line_allocations_no_edit
        BEFORE UPDATE ON sales_return_line_allocations
        WHEN (SELECT sr.status FROM sales_returns sr
              JOIN sales_return_lines srl ON srl.sales_return_id = sr.id
              WHERE srl.id = OLD.sales_return_line_id) = 'POSTED'
        BEGIN SELECT RAISE(ABORT, 'posted sales return allocation is immutable'); END;

        CREATE TRIGGER sales_return_line_allocations_no_delete
        BEFORE DELETE ON sales_return_line_allocations
        WHEN (SELECT sr.status FROM sales_returns sr
              JOIN sales_return_lines srl ON srl.sales_return_id = sr.id
              WHERE srl.id = OLD.sales_return_line_id) = 'POSTED'
        BEGIN SELECT RAISE(ABORT, 'posted sales return allocation is immutable'); END;

        CREATE TRIGGER customer_receipts_no_edit_posted
        BEFORE UPDATE ON customer_receipts
        WHEN OLD.status = 'POSTED' AND (
            NEW.document_no IS NOT OLD.document_no OR
            NEW.customer_id IS NOT OLD.customer_id OR
            NEW.financial_account_id IS NOT OLD.financial_account_id OR
            NEW.amount_piastres IS NOT OLD.amount_piastres OR
            NEW.receipt_date IS NOT OLD.receipt_date OR
            NEW.journal_entry_id IS NOT OLD.journal_entry_id OR
            NEW.posted_at IS NOT OLD.posted_at
        )
        BEGIN SELECT RAISE(ABORT, 'posted customer receipt is immutable'); END;

        CREATE TRIGGER customer_receipts_no_delete_posted
        BEFORE DELETE ON customer_receipts
        WHEN OLD.status = 'POSTED'
        BEGIN SELECT RAISE(ABORT, 'posted customer receipt is immutable'); END;

        CREATE TRIGGER customer_receipt_allocations_no_edit
        BEFORE UPDATE ON customer_receipt_allocations
        WHEN (SELECT status FROM customer_receipts WHERE id = OLD.receipt_id) = 'POSTED'
        BEGIN SELECT RAISE(ABORT, 'posted customer receipt allocation is immutable'); END;

        CREATE TRIGGER customer_receipt_allocations_no_delete
        BEFORE DELETE ON customer_receipt_allocations
        WHEN (SELECT status FROM customer_receipts WHERE id = OLD.receipt_id) = 'POSTED'
        BEGIN SELECT RAISE(ABORT, 'posted customer receipt allocation is immutable'); END;
        """,
    ),
    Migration(
        6,
        "returned_reel_identity",
        """
        ALTER TABLE reels ADD COLUMN source_reel_id INTEGER REFERENCES reels(id) ON DELETE RESTRICT;
        ALTER TABLE reels ADD COLUMN source_issue_allocation_id INTEGER REFERENCES reel_allocations(id) ON DELETE RESTRICT;
        CREATE INDEX ix_reels_source_reel ON reels(source_reel_id);
        CREATE INDEX ix_reels_source_issue_alloc ON reels(source_issue_allocation_id);
        """,
    ),
    Migration(
        7,
        "quotations_and_document_model",
        """
        CREATE TABLE document_exports (
            id INTEGER PRIMARY KEY,
            document_type TEXT NOT NULL CHECK (document_type IN (
                'QUOTATION','SALES_INVOICE','PURCHASE_DOC','STATEMENT','REPORT'
            )),
            document_id INTEGER NOT NULL CHECK (document_id > 0),
            export_type TEXT NOT NULL CHECK (export_type IN ('PDF','PRINT')),
            relative_path TEXT NOT NULL CHECK (length(trim(relative_path)) > 0),
            sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
            file_size INTEGER NOT NULL CHECK (file_size >= 0),
            template_version TEXT NOT NULL CHECK (length(trim(template_version)) > 0),
            renderer_version TEXT NOT NULL CHECK (length(trim(renderer_version)) > 0),
            generated_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            generated_at TEXT NOT NULL,
            is_official INTEGER NOT NULL DEFAULT 0 CHECK (is_official IN (0,1))
        );
        CREATE INDEX ix_document_exports_document
            ON document_exports(document_type, document_id);
        CREATE UNIQUE INDEX ux_document_exports_official
            ON document_exports(document_type, document_id)
            WHERE is_official = 1;

        CREATE TABLE quotation_series (
            id INTEGER PRIMARY KEY,
            base_no TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE quotations (
            id INTEGER PRIMARY KEY,
            document_no TEXT NOT NULL UNIQUE,
            series_id INTEGER NOT NULL REFERENCES quotation_series(id) ON DELETE RESTRICT,
            root_quotation_id INTEGER REFERENCES quotations(id) ON DELETE RESTRICT,
            version_no INTEGER NOT NULL CHECK (version_no >= 1),
            customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
            project_id INTEGER REFERENCES projects(id) ON DELETE RESTRICT,
            price_list_id INTEGER REFERENCES price_lists(id) ON DELETE RESTRICT,
            status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN (
                'DRAFT','ISSUED','ACCEPTED','REJECTED','CANCELLED','SUPERSEDED','CONVERTED'
            )),
            issue_date TEXT NOT NULL,
            valid_until TEXT,
            subtotal_piastres INTEGER NOT NULL DEFAULT 0 CHECK (subtotal_piastres >= 0),
            item_discount_piastres INTEGER NOT NULL DEFAULT 0 CHECK (item_discount_piastres >= 0),
            invoice_discount_piastres INTEGER NOT NULL DEFAULT 0 CHECK (invoice_discount_piastres >= 0),
            total_piastres INTEGER NOT NULL DEFAULT 0 CHECK (total_piastres >= 0),
            notes TEXT,
            terms TEXT,
            official_export_id INTEGER REFERENCES document_exports(id) ON DELETE RESTRICT,
            cancellation_reason TEXT,
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            issued_at TEXT,
            converted_at TEXT,
            UNIQUE (series_id, version_no),
            CHECK (total_piastres = subtotal_piastres - item_discount_piastres - invoice_discount_piastres),
            CHECK (item_discount_piastres <= subtotal_piastres),
            CHECK (invoice_discount_piastres <= subtotal_piastres - item_discount_piastres)
        );
        CREATE INDEX ix_quotations_customer_status ON quotations(customer_id, status);
        CREATE INDEX ix_quotations_root ON quotations(root_quotation_id);

        CREATE TABLE quotation_lines (
            id INTEGER PRIMARY KEY,
            quotation_id INTEGER NOT NULL REFERENCES quotations(id) ON DELETE RESTRICT,
            line_no INTEGER NOT NULL,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            unit_id INTEGER NOT NULL REFERENCES units(id) ON DELETE RESTRICT,
            quantity_micros INTEGER NOT NULL CHECK (quantity_micros > 0),
            base_qty_micros INTEGER NOT NULL CHECK (base_qty_micros > 0),
            original_unit_price_micros INTEGER NOT NULL CHECK (original_unit_price_micros >= 0),
            final_unit_price_micros INTEGER NOT NULL CHECK (final_unit_price_micros >= 0),
            price_source TEXT NOT NULL CHECK (price_source IN ('CUSTOMER','PRICE_LIST','DEFAULT','MANUAL')),
            price_adjustment_reason TEXT,
            conversion_num INTEGER NOT NULL CHECK (conversion_num > 0),
            conversion_den INTEGER NOT NULL CHECK (conversion_den > 0),
            item_discount_piastres INTEGER NOT NULL DEFAULT 0 CHECK (item_discount_piastres >= 0),
            invoice_discount_alloc_piastres INTEGER NOT NULL DEFAULT 0 CHECK (invoice_discount_alloc_piastres >= 0),
            net_piastres INTEGER NOT NULL CHECK (net_piastres >= 0),
            description_snapshot TEXT NOT NULL,
            product_code_snapshot TEXT NOT NULL,
            product_name_snapshot TEXT NOT NULL,
            unit_name_snapshot TEXT NOT NULL,
            project_id INTEGER REFERENCES projects(id) ON DELETE RESTRICT,
            UNIQUE (quotation_id, line_no)
        );
        CREATE INDEX ix_quotation_lines_quotation ON quotation_lines(quotation_id);
        CREATE INDEX ix_quotation_lines_product ON quotation_lines(product_id);

        CREATE TABLE quotation_status_history (
            id INTEGER PRIMARY KEY,
            quotation_id INTEGER NOT NULL REFERENCES quotations(id) ON DELETE RESTRICT,
            from_status TEXT,
            to_status TEXT NOT NULL,
            reason TEXT,
            actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            occurred_at TEXT NOT NULL,
            metadata_json TEXT
        );
        CREATE INDEX ix_quotation_status_history
            ON quotation_status_history(quotation_id, occurred_at);

        CREATE TABLE document_snapshots (
            id INTEGER PRIMARY KEY,
            document_type TEXT NOT NULL CHECK (document_type IN (
                'QUOTATION','SALES_INVOICE','PURCHASE_DOC','STATEMENT','REPORT'
            )),
            document_id INTEGER NOT NULL CHECK (document_id > 0),
            snapshot_json TEXT NOT NULL CHECK (length(trim(snapshot_json)) > 0),
            created_at TEXT NOT NULL,
            UNIQUE (document_type, document_id)
        );

        ALTER TABLE sales ADD COLUMN source_quotation_id INTEGER
            REFERENCES quotations(id) ON DELETE RESTRICT;
        CREATE INDEX ix_sales_source_quotation ON sales(source_quotation_id);
        CREATE UNIQUE INDEX ux_sales_source_quotation_active
            ON sales(source_quotation_id)
            WHERE source_quotation_id IS NOT NULL
              AND status <> 'CANCELLED';

        CREATE TRIGGER quotations_no_delete
        BEFORE DELETE ON quotations
        BEGIN SELECT RAISE(ABORT, 'quotations cannot be deleted'); END;

        CREATE TRIGGER quotation_series_no_delete
        BEFORE DELETE ON quotation_series
        BEGIN SELECT RAISE(ABORT, 'quotation series cannot be deleted'); END;

        CREATE TRIGGER quotations_issued_business_fields_immutable
        BEFORE UPDATE ON quotations
        WHEN OLD.status <> 'DRAFT' AND (
            NEW.document_no IS NOT OLD.document_no OR
            NEW.series_id IS NOT OLD.series_id OR
            NEW.root_quotation_id IS NOT OLD.root_quotation_id OR
            NEW.version_no IS NOT OLD.version_no OR
            NEW.customer_id IS NOT OLD.customer_id OR
            NEW.project_id IS NOT OLD.project_id OR
            NEW.price_list_id IS NOT OLD.price_list_id OR
            NEW.issue_date IS NOT OLD.issue_date OR
            NEW.valid_until IS NOT OLD.valid_until OR
            NEW.subtotal_piastres IS NOT OLD.subtotal_piastres OR
            NEW.item_discount_piastres IS NOT OLD.item_discount_piastres OR
            NEW.invoice_discount_piastres IS NOT OLD.invoice_discount_piastres OR
            NEW.total_piastres IS NOT OLD.total_piastres OR
            NEW.notes IS NOT OLD.notes OR
            NEW.terms IS NOT OLD.terms OR
            NEW.official_export_id IS NOT OLD.official_export_id OR
            NEW.issued_at IS NOT OLD.issued_at OR
            NEW.created_by IS NOT OLD.created_by OR
            NEW.created_at IS NOT OLD.created_at
        )
        BEGIN SELECT RAISE(ABORT, 'issued quotation business fields are immutable'); END;

        CREATE TRIGGER quotation_issue_requires_root
        BEFORE UPDATE OF status ON quotations
        WHEN NEW.status IN ('ISSUED','ACCEPTED','CONVERTED')
             AND NEW.root_quotation_id IS NULL
        BEGIN SELECT RAISE(ABORT, 'quotation root is required before issue'); END;

        CREATE TRIGGER quotation_insert_issue_requires_root
        BEFORE INSERT ON quotations
        WHEN NEW.status IN ('ISSUED','ACCEPTED','CONVERTED')
             AND NEW.root_quotation_id IS NULL
        BEGIN SELECT RAISE(ABORT, 'quotation root is required before issue'); END;

        CREATE TRIGGER quotation_lines_no_edit_after_issue
        BEFORE UPDATE ON quotation_lines
        WHEN (SELECT status FROM quotations WHERE id = OLD.quotation_id) <> 'DRAFT'
        BEGIN SELECT RAISE(ABORT, 'quotation line is immutable after issue'); END;

        CREATE TRIGGER quotation_lines_no_delete_after_issue
        BEFORE DELETE ON quotation_lines
        WHEN (SELECT status FROM quotations WHERE id = OLD.quotation_id) <> 'DRAFT'
        BEGIN SELECT RAISE(ABORT, 'quotation line is immutable after issue'); END;

        CREATE TRIGGER quotation_status_history_no_update
        BEFORE UPDATE ON quotation_status_history
        BEGIN SELECT RAISE(ABORT, 'quotation status history is immutable'); END;

        CREATE TRIGGER quotation_status_history_no_delete
        BEFORE DELETE ON quotation_status_history
        BEGIN SELECT RAISE(ABORT, 'quotation status history is immutable'); END;

        CREATE TRIGGER document_snapshots_no_update
        BEFORE UPDATE ON document_snapshots
        BEGIN SELECT RAISE(ABORT, 'document snapshot is immutable'); END;

        CREATE TRIGGER document_snapshots_no_delete
        BEFORE DELETE ON document_snapshots
        BEGIN SELECT RAISE(ABORT, 'document snapshot is immutable'); END;

        CREATE TRIGGER sales_source_quotation_immutable
        BEFORE UPDATE OF source_quotation_id ON sales
        WHEN OLD.source_quotation_id IS NOT NEW.source_quotation_id
        BEGIN SELECT RAISE(ABORT, 'sales source quotation is immutable'); END;
        """,
    ),
    Migration(
        8,
        "supplier_payments",
        """
        BEGIN IMMEDIATE;

        CREATE TABLE supplier_payments (
            id INTEGER PRIMARY KEY,
            document_no TEXT NOT NULL UNIQUE,
            supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
            financial_account_id INTEGER NOT NULL REFERENCES financial_accounts(id) ON DELETE RESTRICT,
            payment_date TEXT NOT NULL,
            reference TEXT NOT NULL CHECK (length(trim(reference)) > 0),
            amount_piastres INTEGER NOT NULL CHECK (amount_piastres > 0),
            journal_entry_id INTEGER UNIQUE REFERENCES journal_entries(id) ON DELETE RESTRICT,
            created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (supplier_id, reference)
        );

        CREATE TABLE supplier_payment_allocations (
            payment_id INTEGER NOT NULL REFERENCES supplier_payments(id) ON DELETE RESTRICT,
            purchase_invoice_id INTEGER NOT NULL REFERENCES purchase_invoices(id) ON DELETE RESTRICT,
            amount_piastres INTEGER NOT NULL CHECK (amount_piastres > 0),
            PRIMARY KEY (payment_id, purchase_invoice_id)
        );
        CREATE INDEX ix_supplier_payment_alloc_invoice
            ON supplier_payment_allocations(purchase_invoice_id);

        CREATE TRIGGER supplier_payment_no_update_after_post
        BEFORE UPDATE ON supplier_payments WHEN OLD.journal_entry_id IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'posted supplier payment is immutable'); END;

        CREATE TRIGGER supplier_payment_no_delete_after_post
        BEFORE DELETE ON supplier_payments WHEN OLD.journal_entry_id IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'posted supplier payment is immutable'); END;

        CREATE TRIGGER supplier_payment_allocation_no_insert_after_post
        BEFORE INSERT ON supplier_payment_allocations
        WHEN (SELECT journal_entry_id FROM supplier_payments WHERE id=NEW.payment_id) IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'posted supplier payment allocation is immutable'); END;

        CREATE TRIGGER supplier_payment_allocation_no_update
        BEFORE UPDATE ON supplier_payment_allocations
        BEGIN SELECT RAISE(ABORT, 'supplier payment allocation is immutable'); END;

        CREATE TRIGGER supplier_payment_allocation_no_delete
        BEFORE DELETE ON supplier_payment_allocations
        BEGIN SELECT RAISE(ABORT, 'supplier payment allocation is immutable'); END;
        """,
    ),
    Migration(
        9,
        "accounting_period_locks",
        """
        BEGIN IMMEDIATE;

        CREATE TABLE closed_periods (
            month TEXT PRIMARY KEY CHECK (month GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]'
                                          AND substr(month,6,2) BETWEEN '01' AND '12')
        );

        CREATE TRIGGER closed_period_journal_no_insert
        BEFORE INSERT ON journal_entries
        WHEN substr(NEW.entry_date,1,7) IN (SELECT month FROM closed_periods)
        BEGIN SELECT RAISE(ABORT, 'accounting period is closed'); END;

        CREATE TRIGGER closed_period_journal_no_edit
        BEFORE UPDATE OF id, entry_no, entry_date, source_type, source_id, entry_kind, description,
                         posted_at, posted_by, reversal_of_id, idempotency_key, created_at ON journal_entries
        WHEN substr(OLD.entry_date,1,7) IN (SELECT month FROM closed_periods)
          OR substr(NEW.entry_date,1,7) IN (SELECT month FROM closed_periods)
        BEGIN SELECT RAISE(ABORT, 'accounting period is closed'); END;

        CREATE TRIGGER closed_period_journal_reversal_link_only
        BEFORE UPDATE OF status, reversed_by_id ON journal_entries
        WHEN substr(OLD.entry_date,1,7) IN (SELECT month FROM closed_periods)
         AND NOT (OLD.status='POSTED' AND NEW.status='REVERSED' AND EXISTS (
             SELECT 1 FROM journal_entries reversal
             WHERE reversal.id=NEW.reversed_by_id AND reversal.reversal_of_id=OLD.id
               AND reversal.entry_kind='REVERSAL' AND reversal.status='POSTED'
               AND substr(reversal.entry_date,1,10)>=substr(OLD.entry_date,1,10)
               AND substr(reversal.entry_date,1,7) NOT IN (SELECT month FROM closed_periods)
         ))
        BEGIN SELECT RAISE(ABORT, 'accounting period is closed'); END;

        CREATE TRIGGER closed_period_journal_no_delete
        BEFORE DELETE ON journal_entries
        WHEN substr(OLD.entry_date,1,7) IN (SELECT month FROM closed_periods)
        BEGIN SELECT RAISE(ABORT, 'accounting period is closed'); END;

        CREATE TRIGGER closed_period_journal_line_no_insert
        BEFORE INSERT ON journal_lines
        WHEN EXISTS (SELECT 1 FROM journal_entries e JOIN closed_periods p ON p.month=substr(e.entry_date,1,7)
                     WHERE e.id=NEW.journal_entry_id)
        BEGIN SELECT RAISE(ABORT, 'accounting period is closed'); END;

        CREATE TRIGGER closed_period_journal_line_no_edit
        BEFORE UPDATE ON journal_lines
        WHEN EXISTS (SELECT 1 FROM journal_entries e JOIN closed_periods p ON p.month=substr(e.entry_date,1,7)
                     WHERE e.id IN (OLD.journal_entry_id,NEW.journal_entry_id))
        BEGIN SELECT RAISE(ABORT, 'accounting period is closed'); END;

        CREATE TRIGGER closed_period_journal_line_no_delete
        BEFORE DELETE ON journal_lines
        WHEN EXISTS (SELECT 1 FROM journal_entries e JOIN closed_periods p ON p.month=substr(e.entry_date,1,7)
                     WHERE e.id=OLD.journal_entry_id)
        BEGIN SELECT RAISE(ABORT, 'accounting period is closed'); END;

        CREATE TRIGGER closed_period_stock_no_insert
        BEFORE INSERT ON stock_movements
        WHEN substr(NEW.occurred_at,1,7) IN (SELECT month FROM closed_periods)
        BEGIN SELECT RAISE(ABORT, 'accounting period is closed'); END;

        CREATE TRIGGER closed_period_stock_no_edit
        BEFORE UPDATE ON stock_movements
        WHEN substr(OLD.occurred_at,1,7) IN (SELECT month FROM closed_periods)
          OR substr(NEW.occurred_at,1,7) IN (SELECT month FROM closed_periods)
        BEGIN SELECT RAISE(ABORT, 'accounting period is closed'); END;

        CREATE TRIGGER closed_period_stock_no_delete
        BEFORE DELETE ON stock_movements
        WHEN substr(OLD.occurred_at,1,7) IN (SELECT month FROM closed_periods)
        BEGIN SELECT RAISE(ABORT, 'accounting period is closed'); END;
        """,
    ),
    Migration(
        10,
        "audit_log_protection",
        """
        CREATE TRIGGER audit_events_no_update
        BEFORE UPDATE ON audit_events
        BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;

        CREATE TRIGGER audit_events_no_delete
        BEFORE DELETE ON audit_events
        BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;

        CREATE INDEX IF NOT EXISTS ix_audit_event_type
            ON audit_events(event_type, occurred_at);
        CREATE INDEX IF NOT EXISTS ix_audit_actor
            ON audit_events(actor_user_id, occurred_at);
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
