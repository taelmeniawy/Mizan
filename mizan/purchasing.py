from __future__ import annotations

from datetime import datetime, timezone

from .accounting import AccountingService
from .auth import PermissionService
from .db import Database
from .inventory import (
    InventoryError,
    consume_lot_layers,
    create_layer,
    create_movement,
    refresh_lot_status,
)
from .lcp import allocate_largest_remainder, line_total
from .numbering import next_number


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class PurchasingService:
    def __init__(self, db: Database):
        self.db = db
        self.accounting = AccountingService(db)

    def _product(self, conn, product_id: int):
        row = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if row is None:
            raise ValueError("product not found")
        return row

    def _conversion(self, conn, product, unit_id: int) -> tuple[int, int]:
        if unit_id == product["base_unit_id"]:
            return 1, 1
        row = conn.execute(
            "SELECT factor_num,factor_den FROM product_unit_conversions WHERE product_id=? AND unit_id=?",
            (product["id"], unit_id),
        ).fetchone()
        if row is None:
            raise ValueError("no conversion for unit")
        return row["factor_num"], row["factor_den"]

    def _base_qty(self, conn, product, unit_id: int, quantity_micros: int) -> int:
        if quantity_micros <= 0:
            raise ValueError("quantity must be positive")
        num, den = self._conversion(conn, product, unit_id)
        base, remainder = divmod(quantity_micros * num, den)
        if remainder:
            raise ValueError("conversion does not produce an integer base quantity")
        unit = conn.execute("SELECT allows_fraction FROM units WHERE id=?", (product["base_unit_id"],)).fetchone()
        if not unit["allows_fraction"] and base % 1_000_000:
            raise ValueError("base unit does not allow fractional quantities")
        return base

    @staticmethod
    def _line_total(price_micros: int, quantity_micros: int) -> int:
        return line_total(price_micros, quantity_micros)

    def create_invoice(self, supplier_id: int, invoice_date: str, lines: list[dict], expense_piastres: int = 0,
                       payment_type: str = "CREDIT", supplier_invoice_no: str | None = None,
                       financial_account_id: int | None = None, created_by: int | None = None) -> int:
        if not lines:
            raise ValueError("invoice needs lines")
        if expense_piastres < 0:
            raise ValueError("expense cannot be negative")
        if payment_type not in {"CASH", "CREDIT"}:
            raise ValueError("invalid payment type")
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM suppliers WHERE id=?", (supplier_id,)).fetchone() is None:
                raise ValueError("supplier not found")
            document_no = next_number(conn, "PURCHASE_INVOICE", int(invoice_date[:4]), "PI-")
            cur = conn.execute(
                "INSERT INTO purchase_invoices(document_no,supplier_id,supplier_invoice_no,invoice_date,payment_type,financial_account_id,expense_piastres,created_by) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (document_no, supplier_id, supplier_invoice_no, invoice_date, payment_type, financial_account_id, expense_piastres, created_by),
            )
            invoice_id = int(cur.lastrowid)
            subtotal = 0
            for index, line in enumerate(lines, start=1):
                product = self._product(conn, line["product_id"])
                base_qty = self._base_qty(conn, product, line["unit_id"], line["quantity_micros"])
                total = self._line_total(line["unit_price_micros"], line["quantity_micros"])
                conn.execute(
                    "INSERT INTO purchase_invoice_lines(purchase_invoice_id,line_no,product_id,unit_id,quantity_micros,base_qty_micros,unit_price_micros,supplier_line_total_piastres,net_piastres,tracking_mode) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (invoice_id, index, product["id"], line["unit_id"], line["quantity_micros"], base_qty,
                     line["unit_price_micros"], total, total, product["tracking_mode"]),
                )
                line_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                if product["tracking_mode"] == "REEL":
                    lengths = line.get("reel_lengths_micros") or []
                    if not lengths or any(length <= 0 for length in lengths) or sum(lengths) != base_qty:
                        raise ValueError("reel lengths must be positive and sum to base quantity")
                    for seq, length in enumerate(lengths, start=1):
                        conn.execute(
                            "INSERT INTO purchase_reel_inputs(purchase_invoice_line_id,seq,length_micros) VALUES(?,?,?)",
                            (line_id, seq, length),
                        )
                subtotal += total
            conn.execute(
                "UPDATE purchase_invoices SET subtotal_piastres=?, total_piastres=? WHERE id=?",
                (subtotal, subtotal + expense_piastres, invoice_id),
            )
            return invoice_id

    def _create_layer(self, conn, lot_id: int, reel_id: int | None, layer_type: str, qty: int,
                      cost: int, received_at: str, suborder: int = 0) -> int:
        return create_layer(conn, lot_id, reel_id, layer_type, qty, cost, received_at, suborder)

    def _create_movement(self, conn, movement_type: str, product_id: int, lot_id: int, reel_id: int | None,
                         quantity: int, cost: int, direction: str, source_type: str, source_id: int,
                         occurred_at: str, actor_id: int | None, source_line_id: int | None = None) -> int:
        return create_movement(
            conn, movement_type, product_id, lot_id, reel_id, quantity, cost, direction,
            source_type, source_id, occurred_at, actor_id, source_line_id,
        )

    def post_invoice(self, invoice_id: int, actor_id: int | None = None) -> dict:
        if actor_id is not None:
            PermissionService(self.db).require(actor_id, "post_purchases")
        with self.db.transaction() as conn:
            invoice = conn.execute("SELECT * FROM purchase_invoices WHERE id=?", (invoice_id,)).fetchone()
            if invoice is None or invoice["status"] != "DRAFT":
                raise ValueError("draft purchase invoice not found")
            lines = conn.execute(
                "SELECT * FROM purchase_invoice_lines WHERE purchase_invoice_id=? ORDER BY line_no", (invoice_id,)
            ).fetchall()
            if not lines:
                raise ValueError("invoice has no lines")
            subtotal = sum(line["supplier_line_total_piastres"] for line in lines)
            if subtotal != invoice["subtotal_piastres"]:
                raise ValueError("invoice subtotal mismatch")
            shares = allocate_largest_remainder(invoice["expense_piastres"], [line["supplier_line_total_piastres"] for line in lines])
            received_at = invoice["invoice_date"]
            for line, expense_share in zip(lines, shares):
                product = self._product(conn, line["product_id"])
                landed = line["supplier_line_total_piastres"] + expense_share
                lot_no = next_number(conn, "LOT", int(received_at[:4]), "LOT-")
                cur = conn.execute(
                    "INSERT INTO purchase_lots(lot_no,purchase_invoice_line_id,source_type,product_id,warehouse_id,supplier_id,"
                    "tracking_mode,received_at,original_qty_micros,original_supplier_cost_piastres,original_landed_cost_piastres) "
                    "VALUES(?,?,?,?,1,?,?,?,?,?,?)",
                    (lot_no, line["id"], "PURCHASE", product["id"], invoice["supplier_id"], product["tracking_mode"],
                     received_at, line["base_qty_micros"], line["supplier_line_total_piastres"], landed),
                )
                lot_id = int(cur.lastrowid)
                if product["tracking_mode"] == "LOT":
                    layer_id = self._create_layer(conn, lot_id, None, "BASE", line["base_qty_micros"], landed, received_at)
                    movement_id = self._create_movement(
                        conn, "RECEIPT", product["id"], lot_id, None, line["base_qty_micros"], landed, "IN",
                        "PURCHASE_INVOICE", invoice_id, received_at, actor_id, line["id"],
                    )
                    conn.execute(
                        "INSERT INTO lot_allocations(stock_movement_id,cost_layer_id,lot_id,allocation_role,quantity_micros,cost_piastres,direction,created_layer_id) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (movement_id, layer_id, lot_id, "RECEIPT", line["base_qty_micros"], landed, "IN", layer_id),
                    )
                else:
                    reel_inputs = conn.execute(
                        "SELECT seq,length_micros FROM purchase_reel_inputs WHERE purchase_invoice_line_id=? ORDER BY seq",
                        (line["id"],),
                    ).fetchall()
                    lengths = [r["length_micros"] for r in reel_inputs]
                    if not lengths or sum(lengths) != line["base_qty_micros"]:
                        raise ValueError("reel inputs do not match base quantity")
                    supplier_shares = allocate_largest_remainder(line["supplier_line_total_piastres"], lengths)
                    expense_shares = allocate_largest_remainder(expense_share, lengths)
                    for seq, (length, supplier_share, reel_expense) in enumerate(zip(lengths, supplier_shares, expense_shares), start=1):
                        reel_no = next_number(conn, "REEL", int(received_at[:4]), "RL-")
                        reel_cur = conn.execute(
                            "INSERT INTO reels(reel_no,lot_id,product_id,original_length_micros,supplier_cost_piastres,allocated_expense_piastres,original_cost_piastres,received_at) "
                            "VALUES(?,?,?,?,?,?,?,?)",
                            (reel_no, lot_id, product["id"], length, supplier_share, reel_expense,
                             supplier_share + reel_expense, received_at),
                        )
                        reel_id = int(reel_cur.lastrowid)
                        layer_id = self._create_layer(conn, lot_id, reel_id, "BASE", length, supplier_share + reel_expense, received_at, seq)
                        movement_id = self._create_movement(
                            conn, "RECEIPT", product["id"], lot_id, reel_id, length, supplier_share + reel_expense, "IN",
                            "PURCHASE_INVOICE", invoice_id, received_at, actor_id, line["id"],
                        )
                        conn.execute(
                            "INSERT INTO reel_allocations(stock_movement_id,cost_layer_id,reel_id,allocation_role,quantity_micros,cost_piastres,direction,created_layer_id) "
                            "VALUES(?,?,?,?,?,?,?,?)",
                            (movement_id, layer_id, reel_id, "RECEIPT", length, supplier_share + reel_expense, "IN", layer_id),
                        )
            total = subtotal + invoice["expense_piastres"]
            journal_lines = [{"account_code": "1200", "debit": total}]
            if invoice["payment_type"] == "CASH":
                if invoice["financial_account_id"] is None:
                    raise ValueError("cash purchase requires a financial account")
                gl = conn.execute(
                    "SELECT a.code FROM financial_accounts f JOIN accounts a ON a.id=f.gl_account_id WHERE f.id=?",
                    (invoice["financial_account_id"],),
                ).fetchone()
                if gl is None:
                    raise ValueError("financial account not found")
                journal_lines.append({"account_code": gl["code"], "credit": total})
            else:
                journal_lines.append({"account_code": "2010", "credit": subtotal, "party_type": "SUPPLIER", "party_id": invoice["supplier_id"]})
                if invoice["expense_piastres"]:
                    journal_lines.append({"account_code": "2030", "credit": invoice["expense_piastres"]})
            journal_id = self.accounting.post_journal(
                invoice["invoice_date"], "PURCHASE_INVOICE", invoice_id, journal_lines,
                description=f"فاتورة شراء {invoice['document_no']}", posted_by=actor_id, conn=conn,
            )
            conn.execute(
                "UPDATE purchase_invoices SET status='POSTED', posted_at=?, journal_entry_id=? WHERE id=?",
                (_now(), journal_id, invoice_id),
            )
            return {"invoice_id": invoice_id, "journal_id": journal_id, "total": total}

    def _refresh_lot_status(self, conn, lot_id: int) -> None:
        refresh_lot_status(conn, lot_id)

    def _consume(self, conn, lot_id: int, quantity: int, reel_id: int | None = None) -> list[dict]:
        return consume_lot_layers(conn, lot_id, quantity, reel_id)

    def issue_lot(self, lot_id: int, quantity: int, source_type: str = "SALE_ISSUE", source_id: int = 1,
                  occurred_at: str | None = None, actor_id: int | None = None) -> dict:
        occurred_at = occurred_at or _now()[:10]
        with self.db.transaction() as conn:
            lot = conn.execute("SELECT * FROM purchase_lots WHERE id=?", (lot_id,)).fetchone()
            if lot is None:
                raise InventoryError("lot not found")
            if lot["tracking_mode"] == "REEL":
                raise InventoryError("REEL products must use reel cutting, not lot allocation")
            parts = self._consume(conn, lot_id, quantity)
            total_cost = sum(part["cost"] for part in parts)
            movement_id = self._create_movement(
                conn, "SALE_ISSUE", lot["product_id"], lot_id, None, quantity, total_cost, "OUT",
                source_type, source_id, occurred_at, actor_id,
            )
            for part in parts:
                conn.execute(
                    "INSERT INTO lot_allocations(stock_movement_id,cost_layer_id,lot_id,allocation_role,quantity_micros,cost_piastres,direction,layer_position_start_micros,layer_position_end_micros) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (movement_id, part["layer"]["id"], lot_id, "SALE_ISSUE", part["quantity"], part["cost"], "OUT", part["start"], part["end"]),
                )
            self._refresh_lot_status(conn, lot_id)
            return {"movement_id": movement_id, "cost": total_cost, "quantity": quantity}

    def issue_fifo(self, product_id: int, quantity: int, source_type: str = "SALE_ISSUE", source_id: int = 1,
                   occurred_at: str | None = None, actor_id: int | None = None) -> dict:
        occurred_at = occurred_at or _now()[:10]
        with self.db.transaction() as conn:
            product = self._product(conn, product_id)
            if product["tracking_mode"] == "REEL":
                raise InventoryError("REEL products must use reel cutting")
            lots = conn.execute(
                "SELECT * FROM purchase_lots WHERE product_id=? AND status='OPEN' ORDER BY received_at,id",
                (product_id,),
            ).fetchall()
            if not lots:
                raise InventoryError("no open lots")
            remaining = quantity
            selected = []
            for lot in lots:
                available = conn.execute(
                    "SELECT COALESCE(SUM(remaining_qty_micros),0) FROM inventory_cost_layers WHERE lot_id=? AND remaining_qty_micros>0",
                    (lot["id"],),
                ).fetchone()[0]
                if available <= 0:
                    continue
                take = min(available, remaining)
                parts = self._consume(conn, lot["id"], take)
                selected.append((lot, parts))
                remaining -= take
                if remaining == 0:
                    break
            if remaining:
                raise InventoryError("insufficient stock")
            total_cost = sum(part["cost"] for _, parts in selected for part in parts)
            movement_id = self._create_movement(
                conn, "SALE_ISSUE", product_id, None, None, quantity, total_cost, "OUT",
                source_type, source_id, occurred_at, actor_id,
            )
            for lot, parts in selected:
                for part in parts:
                    conn.execute(
                        "INSERT INTO lot_allocations(stock_movement_id,cost_layer_id,lot_id,allocation_role,quantity_micros,cost_piastres,direction,layer_position_start_micros,layer_position_end_micros) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (movement_id, part["layer"]["id"], lot["id"], "SALE_ISSUE", part["quantity"], part["cost"], "OUT", part["start"], part["end"]),
                    )
                if conn.execute(
                    "SELECT COALESCE(SUM(remaining_qty_micros),0) FROM inventory_cost_layers WHERE lot_id=?",
                    (lot["id"],),
                ).fetchone()[0] == 0:
                    conn.execute("UPDATE purchase_lots SET status='EXHAUSTED' WHERE id=?", (lot["id"],))
            return {"movement_id": movement_id, "cost": total_cost, "quantity": quantity}

    def cut_reel(self, reel_id: int, length: int, source_type: str = "SALE_ISSUE", source_id: int = 1,
                 occurred_at: str | None = None, actor_id: int | None = None) -> dict:
        occurred_at = occurred_at or _now()[:10]
        with self.db.transaction() as conn:
            reel = conn.execute("SELECT * FROM reels WHERE id=?", (reel_id,)).fetchone()
            if reel is None:
                raise InventoryError("reel not found")
            parts = self._consume(conn, reel["lot_id"], length, reel_id)
            total_cost = sum(part["cost"] for part in parts)
            movement_id = self._create_movement(
                conn, "SALE_ISSUE", reel["product_id"], reel["lot_id"], reel_id, length, total_cost, "OUT",
                source_type, source_id, occurred_at, actor_id,
            )
            for part in parts:
                conn.execute(
                    "INSERT INTO reel_allocations(stock_movement_id,cost_layer_id,reel_id,allocation_role,quantity_micros,cost_piastres,direction,layer_position_start_micros,layer_position_end_micros) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (movement_id, part["layer"]["id"], reel_id, "SALE_ISSUE", part["quantity"], part["cost"], "OUT", part["start"], part["end"]),
                )
            status = "EMPTY" if sum(
                r["remaining_qty_micros"] for r in conn.execute("SELECT remaining_qty_micros FROM inventory_cost_layers WHERE reel_id=?", (reel_id,))
            ) == 0 else "OPENED"
            conn.execute("UPDATE reels SET status=?,version=version+1 WHERE id=?", (status, reel_id))
            self._refresh_lot_status(conn, reel["lot_id"])
            return {"movement_id": movement_id, "cost": total_cost, "quantity": length}

    def damage_lot(self, lot_id: int, quantity: int, actor_id: int | None = None, occurred_at: str | None = None) -> dict:
        if actor_id is not None:
            PermissionService(self.db).require(actor_id, "manage_inventory")
        occurred_at = occurred_at or _now()[:10]
        with self.db.transaction() as conn:
            lot = conn.execute("SELECT * FROM purchase_lots WHERE id=?", (lot_id,)).fetchone()
            if lot is None:
                raise InventoryError("lot not found")
            parts = self._consume(conn, lot_id, quantity)
            total_cost = sum(part["cost"] for part in parts)
            movement_id = self._create_movement(
                conn, "DAMAGE", lot["product_id"], lot_id, None, quantity, total_cost, "OUT",
                "DAMAGE", 1, occurred_at, actor_id,
            )
            for part in parts:
                conn.execute(
                    "INSERT INTO lot_allocations(stock_movement_id,cost_layer_id,lot_id,allocation_role,quantity_micros,cost_piastres,direction,layer_position_start_micros,layer_position_end_micros) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (movement_id, part["layer"]["id"], lot_id, "DAMAGE", part["quantity"], part["cost"], "OUT", part["start"], part["end"]),
                )
            journal = self.accounting.post_journal(
                occurred_at, "DAMAGE", movement_id,
                [{"account_code": "5020", "debit": total_cost}, {"account_code": "1200", "credit": total_cost}],
                description="تلف مخزون", posted_by=actor_id, conn=conn,
            )
            self._refresh_lot_status(conn, lot_id)
            return {"movement_id": movement_id, "cost": total_cost, "journal_id": journal}

    def damage_reel(self, reel_id: int, length: int, actor_id: int | None = None, occurred_at: str | None = None) -> dict:
        if actor_id is not None:
            PermissionService(self.db).require(actor_id, "manage_inventory")
        occurred_at = occurred_at or _now()[:10]
        with self.db.transaction() as conn:
            reel = conn.execute("SELECT * FROM reels WHERE id=?", (reel_id,)).fetchone()
            if reel is None:
                raise InventoryError("reel not found")
            parts = self._consume(conn, reel["lot_id"], length, reel_id)
            total_cost = sum(part["cost"] for part in parts)
            movement_id = self._create_movement(
                conn, "DAMAGE", reel["product_id"], reel["lot_id"], reel_id, length, total_cost, "OUT",
                "DAMAGE", reel_id, occurred_at, actor_id,
            )
            for part in parts:
                conn.execute(
                    "INSERT INTO reel_allocations(stock_movement_id,cost_layer_id,reel_id,allocation_role,quantity_micros,cost_piastres,direction,layer_position_start_micros,layer_position_end_micros) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (movement_id, part["layer"]["id"], reel_id, "DAMAGE", part["quantity"], part["cost"], "OUT", part["start"], part["end"]),
                )
            journal = self.accounting.post_journal(
                occurred_at, "DAMAGE", movement_id,
                [{"account_code": "5020", "debit": total_cost}, {"account_code": "1200", "credit": total_cost}],
                description="تلف بكرة", posted_by=actor_id, conn=conn,
            )
            remaining = conn.execute(
                "SELECT COALESCE(SUM(remaining_qty_micros),0) FROM inventory_cost_layers WHERE reel_id=?",
                (reel_id,),
            ).fetchone()[0]
            conn.execute(
                "UPDATE reels SET status=?,version=version+1 WHERE id=?",
                ("DAMAGED" if remaining == 0 else "OPENED", reel_id),
            )
            self._refresh_lot_status(conn, reel["lot_id"])
            return {"movement_id": movement_id, "cost": total_cost, "journal_id": journal}

    def create_purchase_return(self, invoice_id: int, lines: list[dict], return_date: str, reason: str | None = None) -> int:
        with self.db.transaction() as conn:
            invoice = conn.execute("SELECT * FROM purchase_invoices WHERE id=? AND status='POSTED'", (invoice_id,)).fetchone()
            if invoice is None:
                raise ValueError("posted purchase invoice not found")
            document_no = next_number(conn, "PURCHASE_RETURN", int(return_date[:4]), "PR-")
            cur = conn.execute(
                "INSERT INTO purchase_returns(document_no,purchase_invoice_id,supplier_id,return_date,reason) VALUES(?,?,?,?,?)",
                (document_no, invoice_id, invoice["supplier_id"], return_date, reason),
            )
            return_id = int(cur.lastrowid)
            for line in lines:
                conn.execute(
                    "INSERT INTO purchase_return_lines(purchase_return_id,purchase_invoice_line_id,lot_id,reel_id,quantity_micros) "
                    "VALUES(?,?,?,?,?)",
                    (return_id, line["purchase_invoice_line_id"], line["lot_id"], line.get("reel_id"), line["quantity_micros"]),
                )
            return return_id

    def post_purchase_return(self, return_id: int, actor_id: int | None = None) -> dict:
        if actor_id is not None:
            PermissionService(self.db).require(actor_id, "post_purchase_return")
        with self.db.transaction() as conn:
            ret = conn.execute("SELECT * FROM purchase_returns WHERE id=? AND status='DRAFT'", (return_id,)).fetchone()
            if ret is None:
                raise ValueError("draft purchase return not found")
            lines = conn.execute("SELECT * FROM purchase_return_lines WHERE purchase_return_id=?", (return_id,)).fetchall()
            total = 0
            for line in lines:
                lot = conn.execute("SELECT * FROM purchase_lots WHERE id=?", (line["lot_id"],)).fetchone()
                if lot is None:
                    raise InventoryError("lot not found")
                parts = self._consume(conn, line["lot_id"], line["quantity_micros"], line["reel_id"])
                cost = sum(part["cost"] for part in parts)
                movement_id = self._create_movement(
                    conn, "PURCHASE_RETURN", lot["product_id"], line["lot_id"], line["reel_id"], line["quantity_micros"], cost, "OUT",
                    "PURCHASE_RETURN", return_id, ret["return_date"], actor_id, line["purchase_invoice_line_id"],
                )
                for part in parts:
                    table = "reel_allocations" if line["reel_id"] else "lot_allocations"
                    scope_col = "reel_id" if line["reel_id"] else "lot_id"
                    scope_val = line["reel_id"] if line["reel_id"] else line["lot_id"]
                    conn.execute(
                        f"INSERT INTO {table}(stock_movement_id,cost_layer_id,{scope_col},purchase_return_line_id,allocation_role,quantity_micros,cost_piastres,direction,layer_position_start_micros,layer_position_end_micros) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (movement_id, part["layer"]["id"], scope_val, line["id"], "PURCHASE_RETURN", part["quantity"], part["cost"], "OUT", part["start"], part["end"]),
                    )
                conn.execute("UPDATE purchase_return_lines SET cost_piastres=? WHERE id=?", (cost, line["id"]))
                self._refresh_lot_status(conn, line["lot_id"])
                total += cost
            journal = self.accounting.post_journal(
                ret["return_date"], "PURCHASE_RETURN", return_id,
                [{"account_code": "2010", "debit": total, "party_type": "SUPPLIER", "party_id": ret["supplier_id"]},
                 {"account_code": "1200", "credit": total}],
                description=f"مرتجع شراء {ret['document_no']}", posted_by=actor_id, conn=conn,
            )
            conn.execute(
                "UPDATE purchase_returns SET status='POSTED', total_piastres=?, posted_at=?, journal_entry_id=? WHERE id=?",
                (total, _now(), journal, return_id),
            )
            return {"return_id": return_id, "total": total, "journal_id": journal}

    def lot_remaining(self, lot_id: int) -> tuple[int, int]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(remaining_qty_micros),0) AS qty,COALESCE(SUM(remaining_cost_piastres),0) AS cost "
                "FROM inventory_cost_layers WHERE lot_id=?", (lot_id,)
            ).fetchone()
        return int(row["qty"]), int(row["cost"])

    def reel_remaining(self, reel_id: int) -> tuple[int, int]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(remaining_qty_micros),0) AS qty,COALESCE(SUM(remaining_cost_piastres),0) AS cost "
                "FROM inventory_cost_layers WHERE reel_id=?", (reel_id,)
            ).fetchone()
        return int(row["qty"]), int(row["cost"])
