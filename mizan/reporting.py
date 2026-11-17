from __future__ import annotations

from contextlib import closing
from datetime import date

from .accounting import MAPPINGS
from .db import Database


_OPEN_RECEIVABLES = """
WITH invoices AS (
    SELECT s.id AS sales_id, s.document_no, s.customer_id, s.invoice_date,
           COALESCE(NULLIF(TRIM(s.due_date), ''), s.invoice_date) AS aging_date, s.due_date,
           s.total_piastres, c.code AS customer_code, c.name_ar AS customer_name
    FROM sales s JOIN customers c ON c.id=s.customer_id
    JOIN journal_entries j ON j.id=s.journal_entry_id
    LEFT JOIN journal_entries reversal ON reversal.id=s.reversal_journal_entry_id
    WHERE s.status='POSTED' AND s.payment_type='CREDIT' AND j.entry_date<=:as_of
      AND (reversal.id IS NULL OR reversal.entry_date>:as_of)
      AND (:customer_id IS NULL OR s.customer_id=:customer_id)
), settlements AS (
    SELECT a.sales_id, a.amount_piastres AS amount
    FROM invoices i JOIN customer_receipt_allocations a ON a.sales_id=i.sales_id
    JOIN customer_receipts r ON r.id=a.receipt_id
    JOIN journal_entries j ON j.id=r.journal_entry_id
    WHERE r.status='POSTED' AND j.entry_date<=:as_of
    UNION ALL
    SELECT r.sales_id, r.ar_reduction_piastres
    FROM invoices i JOIN sales_returns r ON r.sales_id=i.sales_id
    JOIN journal_entries j ON j.id=r.journal_entry_id
    WHERE r.status='POSTED' AND j.entry_date<=:as_of
    UNION ALL
    SELECT a.sales_id, a.amount_piastres
    FROM invoices i JOIN customer_credit_applications a ON a.sales_id=i.sales_id
    JOIN journal_entries j ON j.source_type='CUSTOMER_CREDIT_APPLICATION'
                          AND j.source_id=a.id AND j.entry_kind='POSTING'
    WHERE j.entry_date<=:as_of
), applied AS (
    SELECT sales_id, SUM(amount) AS total FROM settlements GROUP BY sales_id
)
SELECT i.*, i.total_piastres-COALESCE(a.total,0) AS outstanding_piastres
FROM invoices i LEFT JOIN applied a ON a.sales_id=i.sales_id
WHERE i.total_piastres<>COALESCE(a.total,0)
ORDER BY i.customer_code, i.aging_date, i.sales_id
"""

_OPEN_PAYABLES = """
WITH invoices AS (
    SELECT p.id AS purchase_invoice_id, p.document_no, p.supplier_id, p.invoice_date,
           COALESCE(NULLIF(TRIM(p.due_date), ''), p.invoice_date) AS aging_date, p.due_date,
           p.subtotal_piastres AS supplier_invoice_piastres, s.code AS supplier_code,
           s.name_ar AS supplier_name
    FROM purchase_invoices p JOIN suppliers s ON s.id=p.supplier_id
    JOIN journal_entries j ON j.id=p.journal_entry_id
    LEFT JOIN journal_entries reversal ON reversal.id=j.reversed_by_id
    WHERE p.status='POSTED' AND p.payment_type='CREDIT' AND j.entry_date<=:as_of
      AND (reversal.id IS NULL OR reversal.entry_date>:as_of)
      AND (:supplier_id IS NULL OR p.supplier_id=:supplier_id)
), settlements AS (
    SELECT r.purchase_invoice_id, r.total_piastres AS amount
    FROM invoices i JOIN purchase_returns r ON r.purchase_invoice_id=i.purchase_invoice_id
    JOIN journal_entries j ON j.id=r.journal_entry_id
    LEFT JOIN journal_entries reversal ON reversal.id=j.reversed_by_id
    WHERE r.status='POSTED' AND j.entry_date<=:as_of
      AND (reversal.id IS NULL OR reversal.entry_date>:as_of)
    UNION ALL
    SELECT a.purchase_invoice_id, a.amount_piastres
    FROM invoices i JOIN supplier_payment_allocations a ON a.purchase_invoice_id=i.purchase_invoice_id
    JOIN supplier_payments p ON p.id=a.payment_id
    JOIN journal_entries j ON j.id=p.journal_entry_id
    LEFT JOIN journal_entries reversal ON reversal.id=j.reversed_by_id
    WHERE j.entry_date<=:as_of AND (reversal.id IS NULL OR reversal.entry_date>:as_of)
), applied AS (
    SELECT purchase_invoice_id, SUM(amount) AS total FROM settlements GROUP BY purchase_invoice_id
)
SELECT i.*, i.supplier_invoice_piastres-COALESCE(a.total,0) AS outstanding_piastres
FROM invoices i LEFT JOIN applied a ON a.purchase_invoice_id=i.purchase_invoice_id
WHERE i.supplier_invoice_piastres<>COALESCE(a.total,0)
ORDER BY i.supplier_code, i.aging_date, i.purchase_invoice_id
"""

_SALES_DETAIL = """
WITH sale_events AS (
    SELECT s.id AS invoice_id, s.document_no AS invoice_no, s.customer_id,
           j.id AS journal_id, j.entry_date, j.entry_no, j.posted_by,
           s.document_no, 'SALE' AS kind, 1 AS sign
    FROM sales s JOIN journal_entries j ON j.id=s.journal_entry_id WHERE s.status='POSTED'
    UNION ALL
    SELECT s.id, s.document_no, s.customer_id, j.id, j.entry_date, j.entry_no, j.posted_by,
           s.document_no, 'REVERSAL', -1
    FROM sales s JOIN journal_entries j ON j.id=s.reversal_journal_entry_id WHERE s.status='POSTED'
), lines AS (
    SELECT e.invoice_id,e.invoice_no,e.customer_id,e.journal_id,e.entry_date,e.entry_no,e.posted_by,
           e.document_no,e.kind,l.id AS line_id,l.product_id,
           l.base_qty_micros*e.sign AS quantity_micros,l.net_piastres*e.sign AS net_piastres,
           l.cost_piastres*e.sign AS cost_of_sales_piastres
    FROM sale_events e JOIN sales_lines l ON l.sales_id=e.invoice_id
    UNION ALL
    SELECT s.id,s.document_no,s.customer_id,j.id,j.entry_date,j.entry_no,j.posted_by,
           r.document_no,'RETURN',rl.id,l.product_id,
           -rl.base_qty_micros,-rl.commercial_value_piastres,-rl.cost_piastres
    FROM sales_returns r JOIN journal_entries j ON j.id=r.journal_entry_id
    JOIN sales s ON s.id=r.sales_id JOIN sales_return_lines rl ON rl.sales_return_id=r.id
    JOIN sales_lines l ON l.id=rl.sales_line_id WHERE r.status='POSTED'
)
SELECT l.*,p.code AS product_code,u.code AS base_unit_code,c.code AS customer_code
FROM lines l JOIN products p ON p.id=l.product_id JOIN units u ON u.id=p.base_unit_id
JOIN customers c ON c.id=l.customer_id
WHERE l.entry_date BETWEEN :from_date AND :through_date
  AND (:customer_id IS NULL OR l.customer_id=:customer_id)
  AND (:product_id IS NULL OR l.product_id=:product_id)
  AND (:posted_by IS NULL OR l.posted_by=:posted_by)
ORDER BY l.entry_date,l.journal_id,l.line_id
"""

_PURCHASE_DETAIL = """
WITH lines AS (
    SELECT i.id AS invoice_id,i.document_no AS invoice_no,i.supplier_id,j.id AS journal_id,
           j.entry_date,j.entry_no,i.document_no,'PURCHASE' AS kind,l.id AS line_id,l.product_id,
           l.base_qty_micros AS quantity_micros,l.unit_price_micros AS supplier_unit_price_micros,
           l.unit_id AS invoice_unit_id,l.net_piastres AS supplier_piastres,
           SUM(p.original_landed_cost_piastres) AS inventory_piastres
    FROM purchase_invoices i JOIN journal_entries j ON j.id=i.journal_entry_id
    JOIN purchase_invoice_lines l ON l.purchase_invoice_id=i.id
    JOIN purchase_lots p ON p.purchase_invoice_line_id=l.id AND p.source_type='PURCHASE'
    WHERE i.status='POSTED' AND j.entry_date BETWEEN :from_date AND :through_date
      AND (:supplier_id IS NULL OR i.supplier_id=:supplier_id)
      AND (:product_id IS NULL OR l.product_id=:product_id)
    GROUP BY l.id
    UNION ALL
    SELECT i.id,i.document_no,i.supplier_id,j.id,j.entry_date,j.entry_no,r.document_no,'RETURN',rl.id,
           l.product_id,-rl.quantity_micros,NULL,NULL,NULL,-rl.cost_piastres
    FROM purchase_returns r JOIN purchase_invoices i ON i.id=r.purchase_invoice_id
    JOIN journal_entries j ON j.id=r.journal_entry_id
    JOIN purchase_return_lines rl ON rl.purchase_return_id=r.id
    JOIN purchase_invoice_lines l ON l.id=rl.purchase_invoice_line_id
    WHERE r.status='POSTED' AND j.entry_date BETWEEN :from_date AND :through_date
      AND (:supplier_id IS NULL OR i.supplier_id=:supplier_id)
      AND (:product_id IS NULL OR l.product_id=:product_id)
)
SELECT l.*,p.code AS product_code,u.code AS base_unit_code,s.code AS supplier_code
FROM lines l JOIN products p ON p.id=l.product_id JOIN units u ON u.id=p.base_unit_id
JOIN suppliers s ON s.id=l.supplier_id
ORDER BY l.entry_date,l.journal_id,l.line_id
"""

_LOT_ORIGINS = """
WITH first_inflow AS (
    SELECT lot_id, MIN(id) AS movement_id
    FROM stock_movements WHERE direction='IN' AND lot_id IS NOT NULL GROUP BY lot_id
), lots AS (
    SELECT l.id AS lot_id,l.lot_no,l.product_id,p.code AS product_code,p.name_ar AS product_name,
           u.code AS base_unit_code,l.warehouse_id,l.tracking_mode,l.status AS lot_status,l.received_at,
           l.original_qty_micros,l.original_supplier_cost_piastres,l.original_landed_cost_piastres,
           l.source_type AS lot_source_type,l.supplier_id,s.code AS supplier_code,
           m.source_type,m.source_id,m.movement_no AS source_movement_no,
           COALESCE(i.document_no,r.document_no,si.document_no,m.movement_no) AS source_document_no
    FROM purchase_lots l JOIN products p ON p.id=l.product_id JOIN units u ON u.id=p.base_unit_id
    LEFT JOIN suppliers s ON s.id=l.supplier_id
    LEFT JOIN purchase_invoice_lines pl ON pl.id=l.purchase_invoice_line_id
    LEFT JOIN purchase_invoices i ON i.id=pl.purchase_invoice_id
    LEFT JOIN first_inflow f ON f.lot_id=l.id LEFT JOIN stock_movements m ON m.id=f.movement_id
    LEFT JOIN sales_returns r ON m.source_type='SALES_RETURN' AND r.id=m.source_id
    LEFT JOIN sales si ON m.source_type='SALES_REVERSAL' AND si.id=m.source_id
    WHERE (:product_id IS NULL OR l.product_id=:product_id)
      AND (:warehouse_id IS NULL OR l.warehouse_id=:warehouse_id)
)
"""

_PROJECT_MOVEMENTS = """
WITH invoices AS (
    SELECT s.*,c.code AS customer_code,c.name_ar AS customer_name
    FROM sales s JOIN customers c ON c.id=s.customer_id
    JOIN journal_entries j ON j.id=s.journal_entry_id
    WHERE s.status='POSTED' AND c.customer_type='CONTRACTOR' AND j.entry_date<=:as_of
      AND (:customer_id IS NULL OR s.customer_id=:customer_id)
), invoice_projects AS (
    SELECT l.sales_id,
           CASE WHEN MIN(COALESCE(l.project_id,0))=MAX(COALESCE(l.project_id,0))
                THEN MIN(l.project_id) END AS project_id
    FROM invoices i JOIN sales_lines l ON l.sales_id=i.id
    WHERE l.net_piastres>0 GROUP BY l.sales_id
), movements AS (
    SELECT i.id AS sales_id,l.project_id,i.journal_entry_id AS journal_id,i.document_no,
           'SALE' AS kind,l.id AS detail_id,l.net_piastres AS net_sales_piastres,
           CASE WHEN i.payment_type='CREDIT' THEN l.net_piastres ELSE 0 END AS ar_delta_piastres,
           CASE WHEN i.payment_type='CASH' THEN l.net_piastres ELSE 0 END AS cash_sale_piastres,
           0 AS receipts_piastres
    FROM invoices i JOIN sales_lines l ON l.sales_id=i.id
    UNION ALL
    SELECT i.id,l.project_id,i.reversal_journal_entry_id,i.document_no,'REVERSAL',l.id,-l.net_piastres,
           CASE WHEN i.payment_type='CREDIT' THEN -l.net_piastres ELSE 0 END,
           CASE WHEN i.payment_type='CASH' THEN -l.net_piastres ELSE 0 END,0
    FROM invoices i JOIN sales_lines l ON l.sales_id=i.id
    WHERE i.reversal_journal_entry_id IS NOT NULL
    UNION ALL
    SELECT i.id,l.project_id,r.journal_entry_id,r.document_no,'RETURN',rl.id,
           -rl.commercial_value_piastres,0,0,0
    FROM invoices i JOIN sales_returns r ON r.sales_id=i.id AND r.status='POSTED'
    JOIN sales_return_lines rl ON rl.sales_return_id=r.id JOIN sales_lines l ON l.id=rl.sales_line_id
    UNION ALL
    SELECT i.id,p.project_id,r.journal_entry_id,r.document_no,'RETURN_AR',r.id,0,-r.ar_reduction_piastres,0,0
    FROM invoices i JOIN sales_returns r ON r.sales_id=i.id AND r.status='POSTED'
    LEFT JOIN invoice_projects p ON p.sales_id=i.id WHERE r.ar_reduction_piastres>0
    UNION ALL
    SELECT i.id,p.project_id,r.journal_entry_id,r.document_no,'RECEIPT',a.id,0,-a.amount_piastres,0,a.amount_piastres
    FROM invoices i JOIN customer_receipt_allocations a ON a.sales_id=i.id
    JOIN customer_receipts r ON r.id=a.receipt_id AND r.status='POSTED'
    LEFT JOIN invoice_projects p ON p.sales_id=i.id
    UNION ALL
    SELECT i.id,p.project_id,j.id,j.entry_no,'CREDIT_APPLICATION',a.id,0,-a.amount_piastres,0,0
    FROM invoices i JOIN customer_credit_applications a ON a.sales_id=i.id
    JOIN journal_entries j ON j.source_type='CUSTOMER_CREDIT_APPLICATION'
                          AND j.source_id=a.id AND j.entry_kind='POSTING'
    LEFT JOIN invoice_projects p ON p.sales_id=i.id
)
SELECT m.*,i.customer_id,i.customer_code,i.customer_name,i.document_no AS invoice_no,
       j.entry_date,j.entry_no,p.code AS project_code,p.name_ar AS project_name
FROM movements m JOIN invoices i ON i.id=m.sales_id JOIN journal_entries j ON j.id=m.journal_id
LEFT JOIN projects p ON p.id=m.project_id WHERE j.entry_date<=:as_of
ORDER BY i.customer_code,p.code,j.entry_date,j.id,m.kind,m.detail_id
"""


def _report_date(value: str, field: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field} must be a YYYY-MM-DD date") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{field} must be a YYYY-MM-DD date")
    return parsed


class ReportingService:
    def __init__(self, db: Database):
        self.db = db

    def project_balances(self, as_of: str, *, customer_id: int | None = None) -> dict:
        """Summarize contractor sales and receivables by project at a journal cutoff."""
        _report_date(as_of, "as_of")
        params = {"as_of": as_of, "customer_id": customer_id}
        with closing(self.db.connect()) as conn:
            rows = conn.execute(_PROJECT_MOVEMENTS, params).fetchall()

        groups = {}
        for row in rows:
            key = (row["customer_id"], row["project_id"])
            if key not in groups:
                groups[key] = {
                    field: row[field] for field in (
                        "customer_id", "customer_code", "customer_name", "project_id", "project_code", "project_name",
                    )
                } | {"net_sales_piastres": 0, "cash_sales_piastres": 0, "receipts_piastres": 0,
                     "outstanding_piastres": 0, "movements": []}
            group = groups[key]
            group["net_sales_piastres"] += row["net_sales_piastres"]
            group["cash_sales_piastres"] += row["cash_sale_piastres"]
            group["receipts_piastres"] += row["receipts_piastres"]
            group["outstanding_piastres"] += row["ar_delta_piastres"]
            group["movements"].append(dict(row))

        return {
            **params,
            "projects": [group for group in groups.values() if group["project_id"] is not None],
            "unassigned": [group for group in groups.values() if group["project_id"] is None],
        }

    def fifo_lots(self, *, product_id: int | None = None, warehouse_id: int | None = None) -> list[dict]:
        """List original receipt values and current FIFO-layer balances for each lot."""
        with closing(self.db.connect()) as conn:
            rows = conn.execute(
                _LOT_ORIGINS + """
                SELECT l.*,
                       COALESCE(SUM(c.remaining_qty_micros),0) AS remaining_qty_micros,
                       COALESCE(SUM(c.remaining_cost_piastres),0) AS remaining_cost_piastres
                FROM lots l LEFT JOIN inventory_cost_layers c ON c.lot_id=l.lot_id
                GROUP BY l.lot_id ORDER BY l.product_code,l.received_at,l.lot_id
                """,
                {"product_id": product_id, "warehouse_id": warehouse_id},
            ).fetchall()
        return [dict(row) for row in rows]

    def reel_balances(self, *, product_id: int | None = None, warehouse_id: int | None = None) -> list[dict]:
        """List current reel balances with original reel and return references."""
        with closing(self.db.connect()) as conn:
            rows = conn.execute(
                _LOT_ORIGINS + """
                , first_reel_inflow AS (
                    SELECT reel_id,MIN(id) AS movement_id
                    FROM stock_movements WHERE direction='IN' AND reel_id IS NOT NULL GROUP BY reel_id
                )
                SELECT r.id AS reel_id,r.reel_no,r.status,r.received_at,r.original_length_micros,
                       r.original_cost_piastres,r.source_reel_id,origin.reel_no AS source_reel_no,
                       r.source_issue_allocation_id,l.product_id,l.product_code,l.base_unit_code,
                       l.warehouse_id,l.lot_id,l.lot_no,l.lot_source_type,
                       m.source_type,m.source_id,l.source_document_no,m.movement_no AS source_movement_no,
                       COALESCE(SUM(c.remaining_qty_micros),0) AS remaining_length_micros,
                       COALESCE(SUM(c.remaining_cost_piastres),0) AS remaining_cost_piastres
                FROM lots l JOIN reels r ON r.lot_id=l.lot_id
                LEFT JOIN reels origin ON origin.id=r.source_reel_id
                LEFT JOIN first_reel_inflow f ON f.reel_id=r.id
                LEFT JOIN stock_movements m ON m.id=f.movement_id
                LEFT JOIN inventory_cost_layers c ON c.reel_id=r.id
                GROUP BY r.id ORDER BY l.product_code,r.received_at,r.id
                """,
                {"product_id": product_id, "warehouse_id": warehouse_id},
            ).fetchall()
        return [dict(row) for row in rows]

    def financial_activity(self, from_date: str, through_date: str, *,
                           financial_account_id: int | None = None) -> dict:
        """Return journal-based cash movements and balances grouped by linked GL account."""
        if _report_date(from_date, "from_date") > _report_date(through_date, "through_date"):
            raise ValueError("from_date must be on or before through_date")
        params = {"from_date": from_date, "through_date": through_date,
                  "financial_account_id": financial_account_id}
        with closing(self.db.connect()) as conn:
            conn.execute("BEGIN")
            mappings = conn.execute(
                "SELECT f.id, f.code, f.name_ar, f.account_type, f.is_active,"
                " a.id AS gl_id, a.code AS gl_code, a.name_ar AS gl_name"
                " FROM financial_accounts f JOIN accounts a ON a.id=f.gl_account_id"
                " WHERE :financial_account_id IS NULL OR a.id=("
                " SELECT gl_account_id FROM financial_accounts WHERE id=:financial_account_id)"
                " ORDER BY a.code,f.code",
                params,
            ).fetchall()
            if financial_account_id is not None and not mappings:
                raise ValueError("financial account not found")
            opening = conn.execute(
                "SELECT l.account_id, SUM(l.debit_piastres-l.credit_piastres) AS balance"
                " FROM journal_lines l JOIN journal_entries e ON e.id=l.journal_entry_id"
                " WHERE substr(e.entry_date,1,10)<:from_date"
                " AND l.account_id IN (SELECT gl_account_id FROM financial_accounts"
                " WHERE :financial_account_id IS NULL OR id=:financial_account_id) GROUP BY l.account_id",
                params,
            ).fetchall()
            movements = conn.execute(
                "SELECT l.account_id,l.id AS journal_line_id,e.id AS journal_id,e.entry_no,e.entry_date,"
                " e.source_type,e.source_id,e.entry_kind,e.description,e.posted_by,"
                " l.debit_piastres,l.credit_piastres,"
                " COALESCE(s.document_no,p.document_no,c.document_no,r.document_no,v.document_no,e.entry_no) AS document_no"
                " FROM journal_lines l JOIN journal_entries e ON e.id=l.journal_entry_id"
                " LEFT JOIN sales s ON s.journal_entry_id=COALESCE(e.reversal_of_id,e.id)"
                " LEFT JOIN purchase_invoices p ON p.journal_entry_id=COALESCE(e.reversal_of_id,e.id)"
                " LEFT JOIN customer_receipts c ON c.journal_entry_id=COALESCE(e.reversal_of_id,e.id)"
                " LEFT JOIN sales_returns r ON r.journal_entry_id=COALESCE(e.reversal_of_id,e.id)"
                " LEFT JOIN supplier_payments v ON v.journal_entry_id=COALESCE(e.reversal_of_id,e.id)"
                " WHERE substr(e.entry_date,1,10) BETWEEN :from_date AND :through_date"
                " AND l.account_id IN (SELECT gl_account_id FROM financial_accounts"
                " WHERE :financial_account_id IS NULL OR id=:financial_account_id)"
                " ORDER BY substr(e.entry_date,1,10),e.id,l.line_no",
                params,
            ).fetchall()

        opening_by_gl = {row["account_id"]: row["balance"] for row in opening}
        accounts = {}
        for row in mappings:
            gl_id = row["gl_id"]
            if gl_id not in accounts:
                balance = opening_by_gl.get(gl_id, 0)
                accounts[gl_id] = {
                    "gl_code": row["gl_code"], "gl_name": row["gl_name"], "financial_accounts": [],
                    "opening_piastres": balance, "closing_piastres": balance,
                    "debit_piastres": 0, "credit_piastres": 0, "movements": [],
                }
            accounts[gl_id]["financial_accounts"].append({
                field: row[field] for field in ("id", "code", "name_ar", "account_type", "is_active")
            })
        for row in movements:
            account = accounts[row["account_id"]]
            debit, credit = row["debit_piastres"], row["credit_piastres"]
            account["debit_piastres"] += debit
            account["credit_piastres"] += credit
            account["closing_piastres"] += debit - credit
            account["movements"].append(dict(row) | {"balance_piastres": account["closing_piastres"]})
        return {**params, "accounts": list(accounts.values())}

    def sales_detail(self, from_date: str, through_date: str, *, customer_id: int | None = None,
                     product_id: int | None = None, posted_by: int | None = None) -> dict:
        """Return posted sales, returns and reversal lines with signed amounts."""
        if _report_date(from_date, "from_date") > _report_date(through_date, "through_date"):
            raise ValueError("from_date must be on or before through_date")
        params = {"from_date": from_date, "through_date": through_date, "customer_id": customer_id,
                  "product_id": product_id, "posted_by": posted_by}
        with closing(self.db.connect()) as conn:
            lines = [dict(row) for row in conn.execute(_SALES_DETAIL, params)]
        return {
            **params,
            "lines": lines,
            "sales_piastres": sum(row["net_piastres"] for row in lines if row["kind"] == "SALE"),
            "returns_piastres": -sum(row["net_piastres"] for row in lines if row["kind"] == "RETURN"),
            "reversals_piastres": -sum(row["net_piastres"] for row in lines if row["kind"] == "REVERSAL"),
            "net_sales_piastres": sum(row["net_piastres"] for row in lines),
            "cost_of_sales_piastres": sum(row["cost_of_sales_piastres"] for row in lines),
        }

    def purchase_detail(self, from_date: str, through_date: str, *, supplier_id: int | None = None,
                        product_id: int | None = None) -> dict:
        """Return posted purchase and return lines, including frozen landed costs."""
        if _report_date(from_date, "from_date") > _report_date(through_date, "through_date"):
            raise ValueError("from_date must be on or before through_date")
        params = {"from_date": from_date, "through_date": through_date,
                  "supplier_id": supplier_id, "product_id": product_id}
        with closing(self.db.connect()) as conn:
            lines = [dict(row) for row in conn.execute(_PURCHASE_DETAIL, params)]
        purchases = [row for row in lines if row["kind"] == "PURCHASE"]
        supplier_total = sum(row["supplier_piastres"] for row in purchases)
        landed_total = sum(row["inventory_piastres"] for row in purchases)
        return {
            **params,
            "lines": lines,
            "supplier_purchases_piastres": supplier_total,
            "purchase_expenses_piastres": landed_total - supplier_total,
            "landed_purchases_piastres": landed_total,
            "return_cost_piastres": -sum(row["inventory_piastres"] for row in lines if row["kind"] == "RETURN"),
            "net_inventory_acquisition_piastres": sum(row["inventory_piastres"] for row in lines),
        }

    def inventory_valuation(self, as_of: str) -> dict:
        """Reconstruct stock at a historical cutoff and compare it to the inventory ledger."""
        _report_date(as_of, "as_of")
        with closing(self.db.connect()) as conn:
            conn.execute("BEGIN")
            rows = conn.execute(
                "WITH balances AS ("
                " SELECT product_id, warehouse_id,"
                " SUM(CASE direction WHEN 'IN' THEN quantity_micros ELSE -quantity_micros END) AS quantity_micros,"
                " SUM(CASE direction WHEN 'IN' THEN cost_piastres ELSE -cost_piastres END) AS cost_piastres"
                " FROM stock_movements WHERE substr(occurred_at,1,10)<=?"
                " GROUP BY product_id, warehouse_id"
                ") SELECT b.*, p.code AS product_code, p.name_ar AS product_name,"
                " p.tracking_mode, u.code AS base_unit_code FROM balances b"
                " JOIN products p ON p.id=b.product_id JOIN units u ON u.id=p.base_unit_id"
                " WHERE b.quantity_micros<>0 OR b.cost_piastres<>0 ORDER BY p.code,b.warehouse_id",
                (as_of,),
            ).fetchall()
            ledger = conn.execute(
                "SELECT COALESCE(SUM(l.debit_piastres-l.credit_piastres),0)"
                " FROM journal_lines l JOIN accounts a ON a.id=l.account_id"
                " JOIN journal_entries e ON e.id=l.journal_entry_id"
                " WHERE a.code=? AND substr(e.entry_date,1,10)<=?",
                (MAPPINGS["INVENTORY_CONTROL"], as_of),
            ).fetchone()[0]

        total = sum(row["cost_piastres"] for row in rows)
        return {
            "as_of": as_of,
            "products": [dict(row) for row in rows],
            "inventory_piastres": total,
            "ledger_piastres": ledger,
            "difference_piastres": ledger - total,
        }

    def inventory_movements(self, from_date: str, through_date: str, *, product_id: int | None = None) -> list[dict]:
        """List the dated stock events behind the valuation, including their source references."""
        if _report_date(from_date, "from_date") > _report_date(through_date, "through_date"):
            raise ValueError("from_date must be on or before through_date")
        with closing(self.db.connect()) as conn:
            rows = conn.execute(
                "SELECT m.id AS movement_id,m.movement_no,m.occurred_at,m.movement_type,m.direction,"
                " m.product_id,p.code AS product_code,m.warehouse_id,m.lot_id,m.reel_id,"
                " m.quantity_micros,m.cost_piastres,m.source_type,m.source_id,m.source_line_id"
                " FROM stock_movements m JOIN products p ON p.id=m.product_id"
                " WHERE substr(m.occurred_at,1,10) BETWEEN :from_date AND :through_date"
                " AND (:product_id IS NULL OR m.product_id=:product_id) ORDER BY substr(m.occurred_at,1,10),m.id",
                {"from_date": from_date, "through_date": through_date, "product_id": product_id},
            ).fetchall()
        return [dict(row) for row in rows]

    def customer_receivables_aging(self, as_of: str, *, customer_id: int | None = None) -> dict:
        """Age open credit invoices and compare their positive balance to account 1100."""
        cutoff = _report_date(as_of, "as_of")
        params = {"as_of": as_of, "customer_id": customer_id}
        with closing(self.db.connect()) as conn:
            # Pin the invoice and ledger reads to one SQLite snapshot.
            conn.execute("BEGIN")
            rows = conn.execute(_OPEN_RECEIVABLES, params).fetchall()
            ledger = conn.execute(
                "SELECT COALESCE(SUM(l.debit_piastres-l.credit_piastres),0)"
                " FROM journal_lines l JOIN accounts a ON a.id=l.account_id"
                " JOIN journal_entries j ON j.id=l.journal_entry_id"
                " WHERE a.code='1100' AND j.entry_date<=:as_of"
                " AND (:customer_id IS NULL OR (l.party_type='CUSTOMER' AND l.party_id=:customer_id))",
                params,
            ).fetchone()[0]

        return self._aging_totals(cutoff, rows, ledger) | {"as_of": as_of, "customer_id": customer_id}

    def supplier_payables_aging(self, as_of: str, *, supplier_id: int | None = None) -> dict:
        """Age identifiable invoice balances and expose unallocated ledger amounts."""
        cutoff = _report_date(as_of, "as_of")
        params = {"as_of": as_of, "supplier_id": supplier_id}
        with closing(self.db.connect()) as conn:
            conn.execute("BEGIN")
            rows = conn.execute(_OPEN_PAYABLES, params).fetchall()
            ledger = conn.execute(
                "SELECT COALESCE(SUM(l.credit_piastres-l.debit_piastres),0)"
                " FROM journal_lines l JOIN accounts a ON a.id=l.account_id"
                " JOIN journal_entries j ON j.id=l.journal_entry_id"
                " WHERE a.code='2010' AND j.entry_date<=:as_of"
                " AND (:supplier_id IS NULL OR (l.party_type='SUPPLIER' AND l.party_id=:supplier_id))",
                params,
            ).fetchone()[0]
        return self._aging_totals(cutoff, rows, ledger) | {"as_of": as_of, "supplier_id": supplier_id}

    def supplier_statement(self, supplier_id: int, *, as_of: str) -> list[dict]:
        """Return supplier ledger movements and a credit-positive running balance."""
        _report_date(as_of, "as_of")
        with closing(self.db.connect()) as conn:
            rows = conn.execute(
                "SELECT e.entry_date, e.entry_no, e.source_type, e.source_id,"
                " COALESCE(p.document_no,r.document_no,v.document_no,e.entry_no) AS document_no,"
                " l.debit_piastres, l.credit_piastres,"
                " SUM(l.credit_piastres-l.debit_piastres) OVER ("
                " ORDER BY e.entry_date,e.id,l.line_no ROWS UNBOUNDED PRECEDING) AS balance_piastres"
                " FROM journal_lines l JOIN accounts a ON a.id=l.account_id"
                " JOIN journal_entries e ON e.id=l.journal_entry_id"
                " LEFT JOIN purchase_invoices p ON p.journal_entry_id=e.id"
                " LEFT JOIN purchase_returns r ON r.journal_entry_id=e.id"
                " LEFT JOIN supplier_payments v ON v.journal_entry_id=e.id"
                " WHERE a.code='2010' AND l.party_type='SUPPLIER' AND l.party_id=? AND e.entry_date<=?"
                " ORDER BY e.entry_date,e.id,l.line_no",
                (supplier_id, as_of),
            ).fetchall()
        return [dict(row) for row in rows]

    def customer_period_statement(self, customer_id: int, from_date: str, through_date: str) -> dict:
        """Show receivables and customer credits separately for an inclusive period."""
        return self._party_period_statement("CUSTOMER", customer_id, from_date, through_date, ("1100", "2020"))

    def supplier_period_statement(self, supplier_id: int, from_date: str, through_date: str) -> dict:
        """Show opening, period movements and closing payables for one supplier."""
        return self._party_period_statement("SUPPLIER", supplier_id, from_date, through_date, ("2010", None))

    def _party_period_statement(self, party_type: str, party_id: int, from_date: str, through_date: str,
                                codes: tuple[str, str | None]) -> dict:
        if _report_date(from_date, "from_date") > _report_date(through_date, "through_date"):
            raise ValueError("from_date must be on or before through_date")
        report = {"party_type": party_type, "party_id": party_id,
                  "from_date": from_date, "through_date": through_date}
        params = report | {"first_account": codes[0], "second_account": codes[1]}
        with closing(self.db.connect()) as conn:
            conn.execute("BEGIN")
            opening = conn.execute(
                "SELECT a.code,SUM(l.debit_piastres-l.credit_piastres) AS balance"
                " FROM journal_lines l JOIN accounts a ON a.id=l.account_id"
                " JOIN journal_entries e ON e.id=l.journal_entry_id"
                " WHERE l.party_type=:party_type AND l.party_id=:party_id"
                " AND a.code IN (:first_account,:second_account) AND substr(e.entry_date,1,10)<:from_date"
                " GROUP BY a.code",
                params,
            ).fetchall()
            rows = conn.execute(
                "SELECT a.code AS account_code,l.id AS journal_line_id,e.id AS journal_id,"
                " e.entry_date,e.entry_no,e.entry_kind,e.source_type,e.source_id,"
                " COALESCE(l.description,e.description) AS description,l.debit_piastres,l.credit_piastres,"
                " COALESCE(s.document_no,c.document_no,r.document_no,p.document_no,"
                " pr.document_no,v.document_no,origin.entry_no,e.entry_no) AS document_no"
                " FROM journal_lines l JOIN accounts a ON a.id=l.account_id"
                " JOIN journal_entries e ON e.id=l.journal_entry_id"
                " LEFT JOIN journal_entries origin ON origin.id=e.reversal_of_id"
                " LEFT JOIN sales s ON s.journal_entry_id=COALESCE(origin.id,e.id)"
                " LEFT JOIN customer_receipts c ON c.journal_entry_id=COALESCE(origin.id,e.id)"
                " LEFT JOIN sales_returns r ON r.journal_entry_id=COALESCE(origin.id,e.id)"
                " LEFT JOIN purchase_invoices p ON p.journal_entry_id=COALESCE(origin.id,e.id)"
                " LEFT JOIN purchase_returns pr ON pr.journal_entry_id=COALESCE(origin.id,e.id)"
                " LEFT JOIN supplier_payments v ON v.journal_entry_id=COALESCE(origin.id,e.id)"
                " WHERE l.party_type=:party_type AND l.party_id=:party_id"
                " AND a.code IN (:first_account,:second_account)"
                " AND substr(e.entry_date,1,10) BETWEEN :from_date AND :through_date"
                " ORDER BY substr(e.entry_date,1,10),e.id,l.line_no",
                params,
            ).fetchall()

        opening_by_code = {row["code"]: row["balance"] for row in opening}
        accounts = {}
        for code in codes:
            if code is None:
                continue
            opening_balance = opening_by_code.get(code, 0) * (1 if code == "1100" else -1)
            accounts[code] = {"opening_piastres": opening_balance, "closing_piastres": opening_balance,
                              "debit_piastres": 0, "credit_piastres": 0}
        movements = []
        for row in rows:
            code = row["account_code"]
            account = accounts[code]
            debit, credit = row["debit_piastres"], row["credit_piastres"]
            account["debit_piastres"] += debit
            account["credit_piastres"] += credit
            account["closing_piastres"] += debit - credit if code == "1100" else credit - debit
            movements.append(dict(row) | {"balance_piastres": account["closing_piastres"]})
        return report | {"accounts": accounts, "movements": movements}

    @staticmethod
    def _aging_totals(cutoff, rows, ledger: int) -> dict:
        buckets = dict.fromkeys(("not_due", "0_30", "31_60", "61_90", "over_90"), 0)
        invoices = []
        for row in rows:
            try:
                aging_date = date.fromisoformat(row["aging_date"])
            except ValueError as error:
                raise ValueError(f"Invalid aging date for invoice {row['document_no']}") from error
            days = (cutoff - aging_date).days
            amount = row["outstanding_piastres"]
            bucket = None
            if amount > 0:
                bucket = "over_90"
                for limit, label in ((-1, "not_due"), (30, "0_30"), (60, "31_60"), (90, "61_90")):
                    if days <= limit:
                        bucket = label
                        break
                buckets[bucket] += amount
            invoices.append(dict(row) | {"days_past_due": days, "bucket": bucket})

        total = sum(buckets.values())
        return {
            "invoices": invoices,
            "buckets_piastres": buckets,
            "total_piastres": total,
            "ledger_piastres": ledger,
            "difference_piastres": ledger - total,
        }

    def income_expense_detail(self, from_date: str, through_date: str, *,
                              account_type: str | None = None, account_code: str | None = None) -> dict:
        """List posted income and expense journal lines and their account subtotals."""
        if _report_date(from_date, "from_date") > _report_date(through_date, "through_date"):
            raise ValueError("from_date must be on or before through_date")
        if account_type not in (None, "REVENUE", "EXPENSE"):
            raise ValueError("account_type must be REVENUE or EXPENSE")
        params = {"from_date": from_date, "through_date": through_date,
                  "account_type": account_type, "account_code": account_code}
        with closing(self.db.connect()) as conn:
            rows = conn.execute(
                "SELECT a.code,a.name_ar,a.account_type,l.id AS journal_line_id,l.line_no,"
                " l.debit_piastres,l.credit_piastres,l.project_id,e.id AS journal_id,e.entry_no,e.entry_date,"
                " e.entry_kind,e.source_type,e.source_id,e.posted_by,"
                " COALESCE(l.description,e.description) AS description,"
                " COALESCE(s.document_no,r.document_no,m.movement_no,origin.entry_no,e.entry_no) AS document_no"
                " FROM journal_lines l JOIN accounts a ON a.id=l.account_id"
                " JOIN journal_entries e ON e.id=l.journal_entry_id"
                " LEFT JOIN journal_entries origin ON origin.id=e.reversal_of_id"
                " LEFT JOIN sales s ON s.journal_entry_id=COALESCE(origin.id,e.id)"
                " LEFT JOIN sales_returns r ON r.journal_entry_id=COALESCE(origin.id,e.id)"
                " LEFT JOIN stock_movements m ON COALESCE(origin.source_type,e.source_type)='DAMAGE'"
                " AND m.id=COALESCE(origin.source_id,e.source_id)"
                " WHERE substr(e.entry_date,1,10) BETWEEN :from_date AND :through_date"
                " AND a.account_type IN ('REVENUE','EXPENSE')"
                " AND (:account_type IS NULL OR a.account_type=:account_type)"
                " AND (:account_code IS NULL OR a.code=:account_code)"
                " ORDER BY substr(e.entry_date,1,10),e.id,l.line_no",
                params,
            ).fetchall()

        accounts = {}
        totals = {"REVENUE": 0, "EXPENSE": 0}
        lines = []
        for row in rows:
            kind = row["account_type"]
            debit, credit = row["debit_piastres"], row["credit_piastres"]
            amount = credit - debit if kind == "REVENUE" else debit - credit
            account = accounts.setdefault(row["code"], {
                "code": row["code"], "name_ar": row["name_ar"], "account_type": kind,
                "debit_piastres": 0, "credit_piastres": 0, "amount_piastres": 0,
            })
            account["debit_piastres"] += debit
            account["credit_piastres"] += credit
            account["amount_piastres"] += amount
            totals[kind] += amount
            lines.append(dict(row) | {"amount_piastres": amount})

        return {
            **params,
            "accounts": [accounts[code] for code in sorted(accounts)],
            "lines": lines,
            "income_piastres": totals["REVENUE"],
            "expenses_piastres": totals["EXPENSE"],
            "net_result_piastres": totals["REVENUE"] - totals["EXPENSE"],
        }

    def profitability(self, from_date: str, through_date: str) -> dict:
        """Summarize period income and expenses in piastres from journal movements."""
        if _report_date(from_date, "from_date") > _report_date(through_date, "through_date"):
            raise ValueError("from_date must be on or before through_date")
        with closing(self.db.connect()) as conn:
            rows = conn.execute(
                "SELECT a.code, a.name_ar, a.account_type,"
                " SUM(l.debit_piastres) AS debit_piastres, SUM(l.credit_piastres) AS credit_piastres"
                " FROM journal_entries e JOIN journal_lines l ON l.journal_entry_id=e.id"
                " JOIN accounts a ON a.id=l.account_id"
                " WHERE substr(e.entry_date,1,10) BETWEEN ? AND ? AND a.account_type IN ('REVENUE','EXPENSE')"
                " GROUP BY a.id ORDER BY a.code",
                (from_date, through_date),
            ).fetchall()

        sections = {
            MAPPINGS["SALES_REVENUE"]: "sales_revenue_piastres",
            MAPPINGS["SALES_RETURNS"]: "sales_returns_piastres",
            MAPPINGS["COGS"]: "cost_of_sales_piastres",
        }
        amounts = dict.fromkeys((*sections.values(), "operating_expenses_piastres",
                                 "other_income_piastres", "other_expenses_piastres"), 0)
        accounts = []
        for row in rows:
            code = row["code"]
            debit, credit = row["debit_piastres"], row["credit_piastres"]
            if code in sections:
                section = sections[code]
                amount = credit - debit if code == MAPPINGS["SALES_REVENUE"] else debit - credit
            elif row["account_type"] == "REVENUE":
                section, amount = "other_income_piastres", credit - debit
            elif code.startswith("51"):
                section, amount = "operating_expenses_piastres", debit - credit
            else:
                section, amount = "other_expenses_piastres", debit - credit
            amounts[section] += amount
            accounts.append(dict(row) | {"section": section, "amount_piastres": amount})

        net_sales = amounts["sales_revenue_piastres"] - amounts["sales_returns_piastres"]
        gross_profit = net_sales - amounts["cost_of_sales_piastres"]
        operating_profit = gross_profit - amounts["operating_expenses_piastres"]
        return {
            "from_date": from_date,
            "through_date": through_date,
            **amounts,
            "net_sales_piastres": net_sales,
            "gross_profit_piastres": gross_profit,
            "operating_profit_piastres": operating_profit,
            "net_result_piastres": operating_profit + amounts["other_income_piastres"]
                                   - amounts["other_expenses_piastres"],
            "accounts": accounts,
        }
