from __future__ import annotations

from datetime import datetime, timezone

from .accounting import AccountingService
from .auth import PermissionDenied, PermissionService
from .db import Database
from .inventory import (
    available_stock,
    consume_layer,
    create_layer,
    create_movement,
    refresh_lot_status,
    refresh_reel_status,
)
from .lcp import allocate_largest_remainder, line_total, return_cost
from .numbering import next_number
from .pricing import base_quantity, resolve_price, unit_conversion
from .quotations import mark_quotation_converted, restore_quotation_after_draft_cancel


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


_UNSET = object()


class SalesError(ValueError):
    pass


class InsufficientStock(SalesError):
    def __init__(self, shortages: list[dict]):
        self.shortages = shortages
        details = "; ".join(
            f"product {item['product_id']}: requested {item['requested']}, "
            f"available {item['available']}, shortage {item['shortage']}"
            for item in shortages
        )
        super().__init__(f"insufficient stock: {details}")


class SalesService:
    def __init__(self, db: Database):
        self.db = db
        self.accounting = AccountingService(db)

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _require(self, actor_id: int | None, permission: str) -> None:
        if actor_id is not None:
            PermissionService(self.db).require(actor_id, permission)

    @staticmethod
    def _product(conn, product_id: int):
        row = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if row is None:
            raise SalesError("product not found")
        if not row["is_active"]:
            raise SalesError("product is inactive")
        return row

    @staticmethod
    def _conversion(conn, product, unit_id: int) -> tuple[int, int]:
        try:
            return unit_conversion(conn, product, unit_id)
        except ValueError as exc:
            raise SalesError(str(exc)) from exc

    def _base_qty(self, conn, product, unit_id: int, quantity_micros: int) -> tuple[int, int, int]:
        try:
            return base_quantity(conn, product, unit_id, quantity_micros)
        except ValueError as exc:
            raise SalesError(str(exc)) from exc

    @staticmethod
    def _customer(conn, customer_id: int):
        row = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        if row is None:
            raise SalesError("customer not found")
        if not row["is_active"]:
            raise SalesError("customer is inactive")
        return row

    @staticmethod
    def _project(conn, project_id: int, customer_id: int):
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise SalesError("project not found")
        if row["customer_id"] != customer_id:
            raise SalesError("project does not belong to the customer")
        return row

    @staticmethod
    def _financial_account(conn, financial_account_id: int):
        row = conn.execute(
            "SELECT f.*,a.code AS gl_code FROM financial_accounts f "
            "JOIN accounts a ON a.id=f.gl_account_id WHERE f.id=?",
            (financial_account_id,),
        ).fetchone()
        if row is None:
            raise SalesError("financial account not found")
        if not row["is_active"]:
            raise SalesError("financial account is inactive")
        return row

    @staticmethod
    def _price_list(conn, price_list_id: int):
        row = conn.execute("SELECT * FROM price_lists WHERE id=?", (price_list_id,)).fetchone()
        if row is None:
            raise SalesError("price list not found")
        if not row["is_active"]:
            raise SalesError("price list is inactive")
        return row

    def _resolve_price(self, conn, customer_id: int, product_id: int, unit_id: int,
                       price_list_id: int | None) -> tuple[int | None, str | None]:
        return resolve_price(conn, customer_id, product_id, unit_id, price_list_id)

    @staticmethod
    def _stock_available(conn, product) -> int:
        return available_stock(conn, product)

    # ------------------------------------------------------------------
    # Draft invoices
    # ------------------------------------------------------------------

    def create_invoice(self, customer_id: int, invoice_date: str, lines: list[dict],
                       sale_type: str | None = None, payment_type: str = "CREDIT",
                       price_list_id: int | None = None, project_id: int | None = None,
                       invoice_discount_piastres: int = 0, due_date: str | None = None,
                       financial_account_id: int | None = None,
                       actor_id: int | None = None) -> int:
        if not lines:
            raise SalesError("invoice needs lines")
        if payment_type not in {"CASH", "CREDIT"}:
            raise SalesError("payment type MIXED is deferred under OQ-SAL-003")
        if invoice_discount_piastres < 0:
            raise SalesError("invoice discount cannot be negative")
        self._require(actor_id, "create_sales_draft")
        with self.db.transaction() as conn:
            customer = self._customer(conn, customer_id)
            sale_type = sale_type or customer["customer_type"]
            if sale_type != customer["customer_type"]:
                raise SalesError("sale type must match the customer type")
            if project_id is not None:
                self._project(conn, project_id, customer_id)
            if price_list_id is not None:
                self._price_list(conn, price_list_id)
            if payment_type == "CASH":
                if financial_account_id is None:
                    raise SalesError("cash sale requires a financial account")
                self._financial_account(conn, financial_account_id)
            document_no = next_number(conn, "SALES_INVOICE", int(invoice_date[:4]), "SI-")
            cur = conn.execute(
                "INSERT INTO sales(document_no,customer_id,project_id,price_list_id,sale_type,"
                "payment_type,invoice_date,due_date,financial_account_id,created_by) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (document_no, customer_id, project_id, price_list_id, sale_type,
                 payment_type, invoice_date, due_date, financial_account_id, actor_id),
            )
            invoice_id = int(cur.lastrowid)
            self._replace_lines(conn, invoice_id, customer_id, lines, price_list_id,
                                project_id, invoice_discount_piastres, actor_id)
            conn.execute(
                "INSERT INTO sales_status_history(sales_id,from_status,to_status,actor_user_id,occurred_at) "
                "VALUES(?,?,?,?,?)",
                (invoice_id, None, "DRAFT", actor_id, _now()),
            )
            return invoice_id

    def update_draft(self, invoice_id: int, lines: list[dict], *,
                     sale_type: str | None = None, payment_type: str | None = None,
                     price_list_id: int | None = None, project_id=_UNSET,
                     invoice_discount_piastres: int | None = None,
                     due_date=_UNSET, financial_account_id=_UNSET,
                     actor_id: int | None = None) -> None:
        if not lines:
            raise SalesError("invoice needs lines")
        self._require(actor_id, "create_sales_draft")
        with self.db.transaction() as conn:
            invoice = conn.execute("SELECT * FROM sales WHERE id=?", (invoice_id,)).fetchone()
            if invoice is None or invoice["status"] != "DRAFT":
                raise SalesError("draft sales invoice not found")
            if invoice["source_quotation_id"] is not None:
                raise SalesError(
                    "commercial fields of a quotation-generated draft are locked; "
                    "use update_draft_header for payment terms and payment data"
                )
            customer = self._customer(conn, invoice["customer_id"])
            new_sale_type = sale_type or invoice["sale_type"]
            if new_sale_type != customer["customer_type"]:
                raise SalesError("sale type must match the customer type")
            new_payment_type = payment_type or invoice["payment_type"]
            if new_payment_type not in {"CASH", "CREDIT"}:
                raise SalesError("payment type MIXED is deferred under OQ-SAL-003")
            new_price_list_id = price_list_id if price_list_id is not None else invoice["price_list_id"]
            if new_price_list_id is not None:
                self._price_list(conn, new_price_list_id)
            new_project_id = invoice["project_id"] if project_id is _UNSET else project_id
            if new_project_id is not None:
                self._project(conn, new_project_id, invoice["customer_id"])
            new_financial_account_id = (
                invoice["financial_account_id"] if financial_account_id is _UNSET else financial_account_id
            )
            if new_payment_type == "CASH":
                if new_financial_account_id is None:
                    raise SalesError("cash sale requires a financial account")
                self._financial_account(conn, new_financial_account_id)
            new_due_date = invoice["due_date"] if due_date is _UNSET else due_date
            new_invoice_discount = (
                invoice["invoice_discount_piastres"]
                if invoice_discount_piastres is None
                else invoice_discount_piastres
            )
            if new_invoice_discount < 0:
                raise SalesError("invoice discount cannot be negative")
            conn.execute(
                "UPDATE sales SET sale_type=?,payment_type=?,price_list_id=?,project_id=?,"
                "financial_account_id=?,due_date=? WHERE id=?",
                (new_sale_type, new_payment_type, new_price_list_id, new_project_id,
                 new_financial_account_id, new_due_date, invoice_id),
            )
            self._replace_lines(conn, invoice_id, customer["id"], lines, new_price_list_id,
                                new_project_id, new_invoice_discount, actor_id)

    def update_draft_header(self, invoice_id: int, *,
                            payment_type: str | None = None,
                            due_date=_UNSET,
                            financial_account_id=_UNSET,
                            invoice_date: str | None = None,
                            actor_id: int | None = None) -> None:
        """Update the non-commercial header fields of a sales draft.

        A quotation-generated draft locks customer/project/lines/prices, but
        payment terms, payment account, and the invoice date remain editable
        where the sales rules allow it (BR-QUO-048).
        """
        self._require(actor_id, "create_sales_draft")
        with self.db.transaction() as conn:
            invoice = conn.execute("SELECT * FROM sales WHERE id=?", (invoice_id,)).fetchone()
            if invoice is None or invoice["status"] != "DRAFT":
                raise SalesError("draft sales invoice not found")
            new_payment_type = payment_type or invoice["payment_type"]
            if new_payment_type not in {"CASH", "CREDIT"}:
                raise SalesError("payment type MIXED is deferred under OQ-SAL-003")
            if financial_account_id is _UNSET:
                new_financial_account_id = (
                    invoice["financial_account_id"] if new_payment_type == "CASH" else None
                )
            else:
                new_financial_account_id = financial_account_id
            if new_payment_type == "CASH":
                if new_financial_account_id is None:
                    raise SalesError("cash sale requires a financial account")
                self._financial_account(conn, new_financial_account_id)
            new_due_date = invoice["due_date"] if due_date is _UNSET else due_date
            new_invoice_date = invoice_date or invoice["invoice_date"]
            conn.execute(
                "UPDATE sales SET payment_type=?,financial_account_id=?,due_date=?,invoice_date=? "
                "WHERE id=?",
                (new_payment_type, new_financial_account_id, new_due_date,
                 new_invoice_date, invoice_id),
            )
            self._audit(conn, "SALES_DRAFT_HEADER_UPDATED", "sales", invoice_id, actor_id)

    def _replace_lines(self, conn, invoice_id: int, customer_id: int, lines: list[dict],
                       price_list_id: int | None, project_id: int | None,
                       invoice_discount_piastres: int, actor_id: int | None) -> None:
        if not lines:
            raise SalesError("invoice needs lines")
        conn.execute("DELETE FROM sales_lines WHERE sales_id=?", (invoice_id,))
        computed = []
        for index, line in enumerate(lines, start=1):
            product = self._product(conn, line["product_id"])
            unit_id = int(line["unit_id"])
            quantity = int(line["quantity_micros"])
            base_qty, conversion_num, conversion_den = self._base_qty(conn, product, unit_id, quantity)
            manual_price = line.get("unit_price_micros")
            reason = line.get("price_adjustment_reason")
            resolved_price, resolved_source = self._resolve_price(
                conn, customer_id, product["id"], unit_id, price_list_id,
            )
            if manual_price is not None:
                manual_price = int(manual_price)
                if manual_price < 0:
                    raise SalesError("price cannot be negative")
                if resolved_price is None:
                    # No reference price exists, so the entered price is the
                    # only usable price.  There is nothing to override.
                    unit_price, price_source = manual_price, "MANUAL"
                elif manual_price == resolved_price:
                    unit_price, price_source = manual_price, resolved_source
                else:
                    if actor_id is not None:
                        PermissionService(self.db).require(actor_id, "edit_sale_price")
                    if not reason or not str(reason).strip():
                        raise SalesError("manual price requires a reason")
                    unit_price, price_source = manual_price, "MANUAL"
                    self._audit(conn, "SALE_PRICE_OVERRIDDEN", "sales_line", None, actor_id, reason)
            else:
                if resolved_price is None:
                    raise SalesError("no price found for product/unit")
                unit_price, price_source = resolved_price, resolved_source
            gross = line_total(unit_price, quantity)
            item_discount = int(line.get("item_discount_piastres", 0) or 0)
            if item_discount < 0 or item_discount > gross:
                raise SalesError("item discount exceeds the line gross value")
            if item_discount and actor_id is not None:
                PermissionService(self.db).require(actor_id, "apply_item_discount")
            line_project_id = line.get("project_id", project_id)
            if line_project_id is not None:
                self._project(conn, line_project_id, customer_id)
            computed.append({
                "line_no": index,
                "product": product,
                "unit_id": unit_id,
                "quantity": quantity,
                "base_qty": base_qty,
                "conversion_num": conversion_num,
                "conversion_den": conversion_den,
                "unit_price": unit_price,
                "gross": gross,
                "item_discount": item_discount,
                "price_source": price_source,
                "price_adjustment_reason": reason if price_source == "MANUAL" else None,
                "project_id": line_project_id,
            })
        net_before = [item["gross"] - item["item_discount"] for item in computed]
        if invoice_discount_piastres < 0:
            raise SalesError("invoice discount cannot be negative")
        if invoice_discount_piastres > sum(net_before):
            raise SalesError("invoice discount exceeds the eligible net amount")
        if invoice_discount_piastres and actor_id is not None:
            PermissionService(self.db).require(actor_id, "apply_invoice_discount")
        allocations = allocate_largest_remainder(invoice_discount_piastres, net_before)
        for item, alloc in zip(computed, allocations):
            net = item["gross"] - item["item_discount"] - alloc
            if net < 0:
                raise SalesError("invoice discount produced a negative line")
            conn.execute(
                "INSERT INTO sales_lines(sales_id,line_no,product_id,unit_id,quantity_micros,"
                "base_qty_micros,unit_price_micros,gross_piastres,item_discount_piastres,"
                "invoice_discount_alloc_piastres,net_piastres,price_source,price_adjustment_reason,"
                "conversion_num,conversion_den,tracking_mode,project_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (invoice_id, item["line_no"], item["product"]["id"], item["unit_id"],
                 item["quantity"], item["base_qty"], item["unit_price"], item["gross"],
                 item["item_discount"], alloc, net, item["price_source"],
                 item["price_adjustment_reason"], item["conversion_num"],
                 item["conversion_den"], item["product"]["tracking_mode"], item["project_id"]),
            )
        subtotal = sum(item["gross"] for item in computed)
        item_discount = sum(item["item_discount"] for item in computed)
        total = sum(item["gross"] - item["item_discount"] - alloc
                    for item, alloc in zip(computed, allocations))
        conn.execute(
            "UPDATE sales SET subtotal_piastres=?,item_discount_piastres=?,"
            "invoice_discount_piastres=?,total_piastres=? WHERE id=?",
            (subtotal, item_discount, invoice_discount_piastres, total, invoice_id),
        )

    def cancel_draft(self, invoice_id: int, reason: str, actor_id: int | None = None) -> None:
        if not reason or not reason.strip():
            raise SalesError("cancellation reason is required")
        self._require(actor_id, "cancel_sales_draft")
        with self.db.transaction() as conn:
            invoice = conn.execute("SELECT * FROM sales WHERE id=?", (invoice_id,)).fetchone()
            if invoice is None or invoice["status"] != "DRAFT":
                raise SalesError("draft sales invoice not found")
            conn.execute(
                "UPDATE sales SET status='CANCELLED',cancellation_reason=? WHERE id=?",
                (reason.strip(), invoice_id),
            )
            conn.execute(
                "INSERT INTO sales_status_history(sales_id,from_status,to_status,reason,actor_user_id,occurred_at) "
                "VALUES(?,?,?,?,?,?)",
                (invoice_id, "DRAFT", "CANCELLED", reason.strip(), actor_id, _now()),
            )
            if invoice["source_quotation_id"] is not None:
                restore_quotation_after_draft_cancel(
                    conn, invoice["source_quotation_id"], invoice_id, actor_id, reason.strip(),
                )
            self._audit(conn, "SALES_DRAFT_CANCELLED", "sales", invoice_id, actor_id, reason.strip())

    # ------------------------------------------------------------------
    # Posting
    # ------------------------------------------------------------------

    def post_invoice(self, invoice_id: int, actor_id: int | None = None,
                     below_cost_reason: str | None = None) -> dict:
        self._require(actor_id, "post_sales")
        with self.db.transaction() as conn:
            invoice = conn.execute("SELECT * FROM sales WHERE id=?", (invoice_id,)).fetchone()
            if invoice is None or invoice["status"] != "DRAFT":
                raise SalesError("draft sales invoice not found")
            if invoice["payment_type"] not in {"CASH", "CREDIT"}:
                raise SalesError("payment type MIXED is deferred under OQ-SAL-003")
            customer = self._customer(conn, invoice["customer_id"])
            if invoice["project_id"] is not None:
                self._project(conn, invoice["project_id"], customer["id"])
            if invoice["payment_type"] == "CASH":
                if invoice["financial_account_id"] is None:
                    raise SalesError("cash sale requires a financial account")
                self._financial_account(conn, invoice["financial_account_id"])
            lines = conn.execute(
                "SELECT * FROM sales_lines WHERE sales_id=? ORDER BY line_no", (invoice_id,)
            ).fetchall()
            if not lines:
                raise SalesError("invoice has no lines")
            self._check_header_totals(invoice, lines)

            shortages = []
            demand: dict[int, dict] = {}
            for line in lines:
                product = self._product(conn, line["product_id"])
                entry = demand.get(product["id"])
                if entry is None:
                    entry = {
                        "product_id": product["id"],
                        "requested": 0,
                        "available": self._stock_available(conn, product),
                    }
                    demand[product["id"]] = entry
                entry["requested"] += line["base_qty_micros"]
            for entry in demand.values():
                if entry["requested"] > entry["available"]:
                    shortages.append({
                        "product_id": entry["product_id"],
                        "requested": entry["requested"],
                        "available": entry["available"],
                        "shortage": entry["requested"] - entry["available"],
                    })
            if shortages:
                raise InsufficientStock(shortages)

            costs: dict[int, int] = {}
            for line in lines:
                cost = self._issue_sales_line(conn, invoice, line, actor_id)
                costs[line["id"]] = cost
                conn.execute(
                    "UPDATE sales_lines SET cost_piastres=? WHERE id=?", (cost, line["id"])
                )

            below = [line for line in lines if line["net_piastres"] < costs[line["id"]]]
            if below:
                reason = (below_cost_reason or "").strip()
                if actor_id is None or not PermissionService(self.db).has(actor_id, "approve_below_cost"):
                    raise PermissionDenied("missing permission: approve_below_cost")
                if not reason:
                    raise SalesError("below-cost sale requires a recorded reason")
            else:
                reason = None

            total_cost = sum(costs.values())
            total = invoice["total_piastres"]
            if total == 0 and total_cost == 0:
                raise SalesError("invoice has no commercial or cost value")
            journal_id = self._post_sales_journal(conn, invoice, total, total_cost, actor_id)
            conn.execute(
                "UPDATE sales SET status='POSTED',posted_at=?,journal_entry_id=?,"
                "below_cost_reason=?,below_cost_approved_by=? WHERE id=?",
                (_now(), journal_id, reason, actor_id if reason else None, invoice_id),
            )
            conn.execute(
                "INSERT INTO sales_status_history(sales_id,from_status,to_status,reason,actor_user_id,occurred_at) "
                "VALUES(?,?,?,?,?,?)",
                (invoice_id, "DRAFT", "POSTED", reason, actor_id, _now()),
            )
            if invoice["source_quotation_id"] is not None:
                mark_quotation_converted(
                    conn, invoice["source_quotation_id"], invoice_id, actor_id,
                )
            return {
                "invoice_id": invoice_id,
                "journal_id": journal_id,
                "total": total,
                "cost": total_cost,
                "below_cost": bool(below),
            }

    @staticmethod
    def _check_header_totals(invoice, lines) -> None:
        subtotal = sum(line["gross_piastres"] for line in lines)
        item_discount = sum(line["item_discount_piastres"] for line in lines)
        invoice_discount_alloc = sum(line["invoice_discount_alloc_piastres"] for line in lines)
        total = sum(line["net_piastres"] for line in lines)
        if (subtotal != invoice["subtotal_piastres"]
                or item_discount != invoice["item_discount_piastres"]
                or invoice_discount_alloc != invoice["invoice_discount_piastres"]
                or total != invoice["total_piastres"]):
            raise SalesError("invoice header totals do not match its lines")

    def _issue_sales_line(self, conn, invoice, line, actor_id: int | None) -> int:
        product = self._product(conn, line["product_id"])
        if product["tracking_mode"] == "LOT":
            return self._issue_lot_line(conn, invoice, line, product, actor_id)
        return self._issue_reel_line(conn, invoice, line, product, actor_id)

    def _issue_lot_line(self, conn, invoice, line, product, actor_id: int | None) -> int:
        layers = conn.execute(
            "SELECT l.* FROM inventory_cost_layers l JOIN purchase_lots p ON p.id=l.lot_id "
            "WHERE p.product_id=? AND l.reel_id IS NULL AND l.status='OPEN' "
            "AND l.remaining_qty_micros>0 "
            "ORDER BY l.fifo_anchor_at,l.fifo_suborder,l.id",
            (product["id"],),
        ).fetchall()
        remaining = line["base_qty_micros"]
        by_lot: dict[int, list[dict]] = {}
        for layer in layers:
            if remaining <= 0:
                break
            take = min(remaining, layer["remaining_qty_micros"])
            part = consume_layer(conn, layer, take)
            by_lot.setdefault(layer["lot_id"], []).append(part)
            remaining -= take
        if remaining:
            raise InsufficientStock([{
                "product_id": product["id"], "requested": line["base_qty_micros"],
                "available": line["base_qty_micros"] - remaining, "shortage": remaining,
            }])
        total_cost = 0
        for lot_id, parts in by_lot.items():
            quantity = sum(part["quantity"] for part in parts)
            cost = sum(part["cost"] for part in parts)
            movement_id = create_movement(
                conn, "SALE_ISSUE", product["id"], lot_id, None, quantity, cost, "OUT",
                "SALES_INVOICE", invoice["id"], invoice["invoice_date"], actor_id, line["id"],
            )
            for part in parts:
                conn.execute(
                    "INSERT INTO lot_allocations(stock_movement_id,cost_layer_id,lot_id,sales_line_id,"
                    "allocation_role,quantity_micros,cost_piastres,direction,"
                    "layer_position_start_micros,layer_position_end_micros) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (movement_id, part["layer"]["id"], lot_id, line["id"], "SALE_ISSUE",
                     part["quantity"], part["cost"], "OUT", part["start"], part["end"]),
                )
            refresh_lot_status(conn, lot_id)
            total_cost += cost
        return total_cost

    def _issue_reel_line(self, conn, invoice, line, product, actor_id: int | None) -> int:
        layers = conn.execute(
            "SELECT l.* FROM inventory_cost_layers l JOIN purchase_lots p ON p.id=l.lot_id "
            "JOIN reels r ON r.id=l.reel_id "
            "WHERE p.product_id=? AND l.reel_id IS NOT NULL AND l.status='OPEN' "
            "AND l.remaining_qty_micros>0 AND r.status NOT IN ('DAMAGED','RETURNED') "
            "ORDER BY l.fifo_anchor_at,l.fifo_suborder,l.id",
            (product["id"],),
        ).fetchall()
        remaining = line["base_qty_micros"]
        by_reel: dict[int, list[dict]] = {}
        for layer in layers:
            if remaining <= 0:
                break
            take = min(remaining, layer["remaining_qty_micros"])
            part = consume_layer(conn, layer, take)
            by_reel.setdefault(layer["reel_id"], []).append(part)
            remaining -= take
        if remaining:
            raise InsufficientStock([{
                "product_id": product["id"], "requested": line["base_qty_micros"],
                "available": line["base_qty_micros"] - remaining, "shortage": remaining,
            }])
        total_cost = 0
        for reel_id, parts in by_reel.items():
            reel = conn.execute("SELECT * FROM reels WHERE id=?", (reel_id,)).fetchone()
            quantity = sum(part["quantity"] for part in parts)
            cost = sum(part["cost"] for part in parts)
            movement_id = create_movement(
                conn, "SALE_ISSUE", product["id"], reel["lot_id"], reel_id, quantity, cost, "OUT",
                "SALES_INVOICE", invoice["id"], invoice["invoice_date"], actor_id, line["id"],
            )
            for part in parts:
                conn.execute(
                    "INSERT INTO reel_allocations(stock_movement_id,cost_layer_id,reel_id,sales_line_id,"
                    "allocation_role,quantity_micros,cost_piastres,direction,"
                    "layer_position_start_micros,layer_position_end_micros) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (movement_id, part["layer"]["id"], reel_id, line["id"], "SALE_ISSUE",
                     part["quantity"], part["cost"], "OUT", part["start"], part["end"]),
                )
            refresh_reel_status(conn, reel_id)
            refresh_lot_status(conn, reel["lot_id"])
            total_cost += cost
        return total_cost

    def _post_sales_journal(self, conn, invoice, total: int, total_cost: int,
                            actor_id: int | None) -> int:
        lines: list[dict] = []
        project_id = invoice["project_id"]
        if total > 0:
            if invoice["payment_type"] == "CASH":
                account = self._financial_account(conn, invoice["financial_account_id"])
                lines.append({"account_code": account["gl_code"], "debit": total,
                              "project_id": project_id})
            else:
                lines.append({"account_code": "1100", "debit": total,
                              "party_type": "CUSTOMER", "party_id": invoice["customer_id"],
                              "project_id": project_id})
            lines.append({"account_code": "4010", "credit": total, "project_id": project_id})
        if total_cost > 0:
            lines.append({"account_code": "5010", "debit": total_cost, "project_id": project_id})
            lines.append({"account_code": "1200", "credit": total_cost, "project_id": project_id})
        if not lines:
            raise SalesError("invoice has no journal value")
        return self.accounting.post_journal(
            invoice["invoice_date"], "SALES_INVOICE", invoice["id"], lines,
            description=f"فاتورة بيع {invoice['document_no']}",
            posted_by=actor_id, conn=conn,
        )

    # ------------------------------------------------------------------
    # Reversal
    # ------------------------------------------------------------------

    def reverse_invoice(self, invoice_id: int, reason: str, *,
                        reversal_date: str | None = None,
                        actor_id: int | None = None) -> dict:
        if not reason or not reason.strip():
            raise SalesError("reversal reason is required")
        self._require(actor_id, "post_sales")
        reversal_date = reversal_date or _now()[:10]
        with self.db.transaction() as conn:
            invoice = conn.execute("SELECT * FROM sales WHERE id=?", (invoice_id,)).fetchone()
            if invoice is None or invoice["status"] != "POSTED":
                raise SalesError("posted sales invoice not found")
            if invoice["reversal_journal_entry_id"] is not None:
                raise SalesError("sales invoice is already reversed")
            if invoice["payment_type"] == "CASH":
                if actor_id is None:
                    raise PermissionDenied("cash reversal requires an authorized user")
                PermissionService(self.db).require(actor_id, "issue_customer_refund")
            if conn.execute(
                "SELECT 1 FROM sales_returns WHERE sales_id=? AND status='POSTED'", (invoice_id,)
            ).fetchone():
                raise SalesError("reverse is not allowed after a posted sales return")
            if conn.execute(
                "SELECT 1 FROM customer_receipt_allocations a "
                "JOIN customer_receipts r ON r.id=a.receipt_id "
                "WHERE a.sales_id=? AND r.status='POSTED'", (invoice_id,)
            ).fetchone():
                raise SalesError("reverse is not allowed after a posted receipt")
            if conn.execute(
                "SELECT 1 FROM customer_credit_applications WHERE sales_id=?", (invoice_id,)
            ).fetchone():
                raise SalesError("reverse is not allowed after a credit application")
            original = conn.execute(
                "SELECT * FROM journal_entries WHERE id=?", (invoice["journal_entry_id"],)
            ).fetchone()
            if original is None or original["status"] != "POSTED":
                raise SalesError("original sales journal is not reversible")

            lot_allocations = conn.execute(
                "SELECT la.*,sl.product_id,sl.line_no,sl.quantity_micros AS line_qty "
                "FROM lot_allocations la JOIN sales_lines sl ON sl.id=la.sales_line_id "
                "WHERE la.allocation_role='SALE_ISSUE' AND sl.sales_id=? ORDER BY la.id",
                (invoice_id,),
            ).fetchall()
            reel_allocations = conn.execute(
                "SELECT ra.*,r.lot_id,sl.product_id,sl.line_no FROM reel_allocations ra "
                "JOIN reels r ON r.id=ra.reel_id "
                "JOIN sales_lines sl ON sl.id=ra.sales_line_id "
                "WHERE ra.allocation_role='SALE_ISSUE' AND sl.sales_id=? ORDER BY ra.id",
                (invoice_id,),
            ).fetchall()
            for seq, allocation in enumerate(lot_allocations, start=1):
                layer_id = create_layer(
                    conn, allocation["lot_id"], None, "CUSTOMER_RETURN",
                    allocation["quantity_micros"], allocation["cost_piastres"],
                    reversal_date, seq, parent_issue_allocation_id=allocation["id"],
                )
                movement_id = create_movement(
                    conn, "SALE_RETURN_IN", allocation["product_id"], allocation["lot_id"], None,
                    allocation["quantity_micros"], allocation["cost_piastres"], "IN",
                    "SALES_REVERSAL", invoice_id, reversal_date, actor_id, allocation["sales_line_id"],
                )
                conn.execute(
                    "INSERT INTO lot_allocations(stock_movement_id,cost_layer_id,lot_id,sales_line_id,"
                    "allocation_role,quantity_micros,cost_piastres,direction,"
                    "source_issue_allocation_id,created_layer_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (movement_id, layer_id, allocation["lot_id"], allocation["sales_line_id"],
                     "SALE_RETURN", allocation["quantity_micros"], allocation["cost_piastres"],
                     "IN", allocation["id"], layer_id),
                )
                refresh_lot_status(conn, allocation["lot_id"])

            for seq, allocation in enumerate(reel_allocations, start=len(lot_allocations) + 1):
                selection = {
                    "allocation_id": allocation["id"],
                    "quantity_micros": allocation["quantity_micros"],
                    "cost": allocation["cost_piastres"],
                    "lot_id": allocation["lot_id"],
                    "reel_id": allocation["reel_id"],
                    "original_qty_micros": allocation["quantity_micros"],
                    "original_cost_piastres": allocation["cost_piastres"],
                }
                self._create_returned_reel(
                    conn, return_id=invoice_id, return_line_id=None,
                    sales_line_id=allocation["sales_line_id"],
                    product_id=allocation["product_id"], selection=selection,
                    return_date=reversal_date, actor_id=actor_id, seq=seq,
                    source_type="SALES_REVERSAL",
                )

            original_lines = conn.execute(
                "SELECT a.code,l.debit_piastres,l.credit_piastres,l.party_type,l.party_id,"
                "l.project_id,l.description,l.source_line_id FROM journal_lines l "
                "JOIN accounts a ON a.id=l.account_id WHERE l.journal_entry_id=? ORDER BY l.line_no",
                (original["id"],),
            ).fetchall()
            reversal_lines = [
                {
                    "account_code": row["code"],
                    "debit": row["credit_piastres"],
                    "credit": row["debit_piastres"],
                    "party_type": row["party_type"],
                    "party_id": row["party_id"],
                    "project_id": row["project_id"],
                    "description": f"reversal of {original['entry_no']}",
                    "source_line_id": row["source_line_id"],
                }
                for row in original_lines
            ]
            reversal_id = self.accounting.post_journal(
                reversal_date, "SALES_INVOICE", invoice_id, reversal_lines,
                description=f"عكس فاتورة بيع {invoice['document_no']}",
                posted_by=actor_id, entry_kind="REVERSAL",
                reversal_of_id=original["id"], conn=conn,
            )
            conn.execute(
                "UPDATE journal_entries SET status='REVERSED',reversed_by_id=? WHERE id=?",
                (reversal_id, original["id"]),
            )
            conn.execute(
                "UPDATE sales SET reversal_journal_entry_id=?,reversal_reason=?,reversed_at=?,reversed_by=? "
                "WHERE id=?",
                (reversal_id, reason.strip(), _now(), actor_id, invoice_id),
            )
            self._audit(conn, "SALES_INVOICE_REVERSED", "sales", invoice_id, actor_id, reason.strip())
            return {"invoice_id": invoice_id, "reversal_journal_id": reversal_id}

    # ------------------------------------------------------------------
    # Customer balances and receipts
    # ------------------------------------------------------------------

    def _invoice_balance(self, conn, invoice) -> int:
        if (invoice["status"] != "POSTED"
                or invoice["reversal_journal_entry_id"] is not None
                or invoice["payment_type"] == "CASH"):
            return 0
        receipt = int(conn.execute(
            "SELECT COALESCE(SUM(a.amount_piastres),0) FROM customer_receipt_allocations a "
            "JOIN customer_receipts r ON r.id=a.receipt_id "
            "WHERE a.sales_id=? AND r.status='POSTED'", (invoice["id"],)
        ).fetchone()[0])
        returned = int(conn.execute(
            "SELECT COALESCE(SUM(ar_reduction_piastres),0) FROM sales_returns "
            "WHERE sales_id=? AND status='POSTED'", (invoice["id"],)
        ).fetchone()[0])
        credits = int(conn.execute(
            "SELECT COALESCE(SUM(amount_piastres),0) FROM customer_credit_applications "
            "WHERE sales_id=?", (invoice["id"],)
        ).fetchone()[0])
        balance = invoice["total_piastres"] - receipt - returned - credits
        if balance < 0:
            raise SalesError("invoice receivable would be negative")
        return balance

    def invoice_balance(self, invoice_id: int) -> int:
        with self.db.connect() as conn:
            invoice = conn.execute("SELECT * FROM sales WHERE id=?", (invoice_id,)).fetchone()
            if invoice is None:
                raise SalesError("sales invoice not found")
            return self._invoice_balance(conn, invoice)

    def customer_balance(self, customer_id: int) -> int:
        return self.accounting.party_balance("1100", "CUSTOMER", customer_id)

    def customer_credit_balance(self, customer_id: int) -> int:
        """Positive customer-credit liability balance for account 2020."""
        return -self.accounting.party_balance("2020", "CUSTOMER", customer_id)

    def customer_account_summary(self, customer_id: int) -> dict:
        return {
            "ar_balance": self.customer_balance(customer_id),
            "credit_balance": self.customer_credit_balance(customer_id),
        }

    @staticmethod
    def _statement_document_no(conn, source_type: str, source_id: int) -> str | None:
        if source_type == "SALES_INVOICE":
            row = conn.execute(
                "SELECT document_no FROM sales WHERE id=?", (source_id,)
            ).fetchone()
        elif source_type == "CUSTOMER_RECEIPT":
            row = conn.execute(
                "SELECT document_no FROM customer_receipts WHERE id=?", (source_id,)
            ).fetchone()
        elif source_type == "SALES_RETURN":
            row = conn.execute(
                "SELECT document_no FROM sales_returns WHERE id=?", (source_id,)
            ).fetchone()
        else:
            return None
        return row["document_no"] if row else None

    def customer_statement(self, customer_id: int) -> list[dict]:
        """Customer AR and customer-credit statement.

        Account 1100 lines affect `ar_balance`; account 2020 lines affect
        `credit_balance` and never change `ar_balance`.  `balance` is kept as
        the AR running balance for callers that only need the receivable.
        Cash-only documents that never touch 1100/2020 are not AR statement
        lines; customer credits are shown distinctly instead of as negative AR.
        """
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT e.entry_no,e.entry_date,e.entry_kind,e.source_type,e.source_id,"
                "e.description,a.code AS account_code,l.debit_piastres,l.credit_piastres "
                "FROM journal_lines l "
                "JOIN journal_entries e ON e.id=l.journal_entry_id "
                "JOIN accounts a ON a.id=l.account_id "
                "WHERE a.code IN ('1100','2020') "
                "AND l.party_type='CUSTOMER' AND l.party_id=? "
                "ORDER BY e.entry_date,e.id,l.line_no",
                (customer_id,),
            ).fetchall()
            result = []
            ar_balance = 0
            credit_balance = 0
            for row in rows:
                debit = int(row["debit_piastres"])
                credit = int(row["credit_piastres"])
                is_ar = row["account_code"] == "1100"
                if is_ar:
                    ar_balance += debit - credit
                    kind = "AR"
                else:
                    credit_balance += credit - debit
                    kind = "CUSTOMER_CREDIT"
                result.append({
                    "entry_no": row["entry_no"],
                    "entry_date": row["entry_date"],
                    "entry_kind": row["entry_kind"],
                    "source_type": row["source_type"],
                    "source_id": row["source_id"],
                    "description": row["description"],
                    "document_no": self._statement_document_no(
                        conn, row["source_type"], row["source_id"]
                    ),
                    "account_code": row["account_code"],
                    "kind": kind,
                    "debit": debit,
                    "credit": credit,
                    "affects_ar": is_ar,
                    "affects_credit": not is_ar,
                    "ar_balance": ar_balance,
                    "credit_balance": credit_balance,
                    "balance": ar_balance,
                })
        return result

    def create_receipt(self, customer_id: int, financial_account_id: int, amount_piastres: int,
                       receipt_date: str, allocations: list[dict] | None = None,
                       notes: str | None = None, actor_id: int | None = None) -> int:
        if amount_piastres <= 0:
            raise SalesError("receipt amount must be positive")
        self._require(actor_id, "manage_customer_receipts")
        allocations = allocations or []
        with self.db.transaction() as conn:
            self._customer(conn, customer_id)
            self._financial_account(conn, financial_account_id)
            total = 0
            seen: set[int] = set()
            for allocation in allocations:
                sales_id = int(allocation["sales_id"])
                amount = int(allocation["amount_piastres"])
                if amount <= 0:
                    raise SalesError("receipt allocation must be positive")
                if sales_id in seen:
                    raise SalesError("duplicate invoice allocation in receipt")
                seen.add(sales_id)
                invoice = conn.execute("SELECT * FROM sales WHERE id=?", (sales_id,)).fetchone()
                if invoice is None or invoice["status"] != "POSTED":
                    raise SalesError("posted sales invoice not found")
                if invoice["customer_id"] != customer_id:
                    raise SalesError("invoice does not belong to the receipt customer")
                if invoice["reversal_journal_entry_id"] is not None:
                    raise SalesError("cannot allocate a receipt to a reversed invoice")
                outstanding = self._invoice_balance(conn, invoice)
                if amount > outstanding:
                    raise SalesError("receipt allocation exceeds the invoice outstanding balance")
                total += amount
            if total > amount_piastres:
                raise SalesError("receipt allocations exceed the receipt amount")
            document_no = next_number(conn, "CUSTOMER_RECEIPT", int(receipt_date[:4]), "CR-")
            cur = conn.execute(
                "INSERT INTO customer_receipts(document_no,customer_id,financial_account_id,"
                "amount_piastres,receipt_date,notes,created_by) VALUES(?,?,?,?,?,?,?)",
                (document_no, customer_id, financial_account_id, amount_piastres,
                 receipt_date, notes, actor_id),
            )
            receipt_id = int(cur.lastrowid)
            for allocation in allocations:
                conn.execute(
                    "INSERT INTO customer_receipt_allocations(receipt_id,sales_id,amount_piastres) "
                    "VALUES(?,?,?)",
                    (receipt_id, int(allocation["sales_id"]), int(allocation["amount_piastres"])),
                )
            return receipt_id

    def post_receipt(self, receipt_id: int, actor_id: int | None = None) -> dict:
        self._require(actor_id, "manage_customer_receipts")
        with self.db.transaction() as conn:
            receipt = conn.execute(
                "SELECT * FROM customer_receipts WHERE id=?", (receipt_id,)
            ).fetchone()
            if receipt is None or receipt["status"] != "DRAFT":
                raise SalesError("draft customer receipt not found")
            self._customer(conn, receipt["customer_id"])
            allocations = conn.execute(
                "SELECT * FROM customer_receipt_allocations WHERE receipt_id=?", (receipt_id,)
            ).fetchall()
            allocated = 0
            for allocation in allocations:
                invoice = conn.execute(
                    "SELECT * FROM sales WHERE id=?", (allocation["sales_id"],)
                ).fetchone()
                if invoice is None or invoice["status"] != "POSTED":
                    raise SalesError("posted sales invoice not found")
                if invoice["customer_id"] != receipt["customer_id"]:
                    raise SalesError("invoice does not belong to the receipt customer")
                outstanding = self._invoice_balance(conn, invoice)
                if allocation["amount_piastres"] > outstanding:
                    raise SalesError("receipt allocation exceeds the invoice outstanding balance")
                allocated += allocation["amount_piastres"]
            if allocated > receipt["amount_piastres"]:
                raise SalesError("receipt allocations exceed the receipt amount")
            excess = receipt["amount_piastres"] - allocated
            account = self._financial_account(conn, receipt["financial_account_id"])
            lines = [{"account_code": account["gl_code"], "debit": receipt["amount_piastres"]}]
            if allocated:
                lines.append({"account_code": "1100", "credit": allocated,
                              "party_type": "CUSTOMER", "party_id": receipt["customer_id"]})
            if excess:
                lines.append({"account_code": "2020", "credit": excess,
                              "party_type": "CUSTOMER", "party_id": receipt["customer_id"]})
            journal_id = self.accounting.post_journal(
                receipt["receipt_date"], "CUSTOMER_RECEIPT", receipt_id, lines,
                description=f"تحصيل عميل {receipt['document_no']}",
                posted_by=actor_id, conn=conn,
            )
            if excess:
                conn.execute(
                    "INSERT INTO customer_credits(customer_id,source_type,source_id,amount_piastres,"
                    "remaining_piastres,status) VALUES(?,?,?,?,?,?)",
                    (receipt["customer_id"], "OVERPAYMENT", receipt_id, excess, excess, "OPEN"),
                )
            conn.execute(
                "UPDATE customer_receipts SET status='POSTED',posted_at=?,journal_entry_id=? WHERE id=?",
                (_now(), journal_id, receipt_id),
            )
            return {
                "receipt_id": receipt_id,
                "journal_id": journal_id,
                "allocated": allocated,
                "customer_credit": excess,
            }

    def apply_customer_credit(self, credit_id: int, sales_id: int, amount_piastres: int,
                              actor_id: int | None = None,
                              application_date: str | None = None) -> int:
        if amount_piastres <= 0:
            raise SalesError("credit application must be positive")
        self._require(actor_id, "manage_customer_receipts")
        journal_date = application_date or _now()[:10]
        with self.db.transaction() as conn:
            credit = conn.execute("SELECT * FROM customer_credits WHERE id=?", (credit_id,)).fetchone()
            if credit is None:
                raise SalesError("customer credit not found")
            if credit["status"] == "CLOSED":
                raise SalesError("customer credit is closed")
            if amount_piastres > credit["remaining_piastres"]:
                raise SalesError("credit application exceeds the remaining credit")
            invoice = conn.execute("SELECT * FROM sales WHERE id=?", (sales_id,)).fetchone()
            if invoice is None or invoice["status"] != "POSTED":
                raise SalesError("posted sales invoice not found")
            if invoice["customer_id"] != credit["customer_id"]:
                raise SalesError("invoice does not belong to the credit customer")
            if amount_piastres > self._invoice_balance(conn, invoice):
                raise SalesError("credit application exceeds the invoice outstanding balance")
            cur = conn.execute(
                "INSERT INTO customer_credit_applications(credit_id,sales_id,amount_piastres,applied_at) "
                "VALUES(?,?,?,?)",
                (credit_id, sales_id, amount_piastres, _now()),
            )
            application_id = int(cur.lastrowid)
            self.accounting.post_journal(
                journal_date, "CUSTOMER_CREDIT_APPLICATION", application_id,
                [
                    {"account_code": "2020", "debit": amount_piastres,
                     "party_type": "CUSTOMER", "party_id": credit["customer_id"]},
                    {"account_code": "1100", "credit": amount_piastres,
                     "party_type": "CUSTOMER", "party_id": credit["customer_id"]},
                ],
                description="تطبيق رصيد دائن للعميل", posted_by=actor_id, conn=conn,
            )
            remaining = credit["remaining_piastres"] - amount_piastres
            conn.execute(
                "UPDATE customer_credits SET remaining_piastres=?,status=? WHERE id=?",
                (remaining, "APPLIED" if remaining == 0 else "OPEN", credit_id),
            )
            return application_id

    # ------------------------------------------------------------------
    # Sales returns
    # ------------------------------------------------------------------

    def _returned_lot_qty(self, conn, allocation_id: int) -> int:
        return int(conn.execute(
            "SELECT COALESCE(SUM(la.quantity_micros),0) FROM lot_allocations la "
            "JOIN sales_return_lines srl ON srl.id=la.sales_return_line_id "
            "JOIN sales_returns sr ON sr.id=srl.sales_return_id "
            "WHERE la.allocation_role='SALE_RETURN' AND la.source_issue_allocation_id=? "
            "AND sr.status='POSTED'", (allocation_id,)
        ).fetchone()[0])

    def _returned_reel_qty(self, conn, allocation_id: int) -> int:
        return int(conn.execute(
            "SELECT COALESCE(SUM(ra.quantity_micros),0) FROM reel_allocations ra "
            "JOIN sales_return_lines srl ON srl.id=ra.sales_return_line_id "
            "JOIN sales_returns sr ON sr.id=srl.sales_return_id "
            "WHERE ra.allocation_role='SALE_RETURN' AND ra.source_issue_allocation_id=? "
            "AND sr.status='POSTED'", (allocation_id,)
        ).fetchone()[0])

    def _returnable_for_line(self, conn, sales_line, include_zero: bool = False) -> list[dict]:
        product = self._product(conn, sales_line["product_id"])
        if product["tracking_mode"] == "LOT":
            rows = conn.execute(
                "SELECT la.id,la.lot_id,la.quantity_micros,la.cost_piastres "
                "FROM lot_allocations la WHERE la.allocation_role='SALE_ISSUE' "
                "AND la.sales_line_id=? ORDER BY la.id DESC", (sales_line["id"],)
            ).fetchall()
            kind = "LOT"
        else:
            rows = conn.execute(
                "SELECT ra.id,ra.reel_id,ra.quantity_micros,ra.cost_piastres,"
                "r.lot_id FROM reel_allocations ra JOIN reels r ON r.id=ra.reel_id "
                "WHERE ra.allocation_role='SALE_ISSUE' AND ra.sales_line_id=? ORDER BY ra.id DESC",
                (sales_line["id"],),
            ).fetchall()
            kind = "REEL"
        result = []
        for row in rows:
            returned = (
                self._returned_lot_qty(conn, row["id"])
                if kind == "LOT" else self._returned_reel_qty(conn, row["id"])
            )
            remaining_qty = row["quantity_micros"] - returned
            if remaining_qty <= 0 and not include_zero:
                continue
            remaining_cost = row["cost_piastres"] - return_cost(
                row["cost_piastres"], row["quantity_micros"], 0, returned
            )
            item = {
                "kind": kind,
                "allocation_id": row["id"],
                "lot_id": row["lot_id"],
                "reel_id": row["reel_id"] if kind == "REEL" else None,
                "original_qty_micros": row["quantity_micros"],
                "original_cost_piastres": row["cost_piastres"],
                "returned_qty_micros": returned,
                "remaining_qty_micros": remaining_qty,
                "remaining_cost_piastres": remaining_cost,
            }
            result.append(item)
        return result

    def returnable_allocations(self, sales_line_id: int) -> list[dict]:
        with self.db.connect() as conn:
            sales_line = conn.execute(
                "SELECT * FROM sales_lines WHERE id=?", (sales_line_id,)
            ).fetchone()
            if sales_line is None:
                raise SalesError("sales line not found")
            return self._returnable_for_line(conn, sales_line)

    @staticmethod
    def _posted_returned_sold_qty(conn, sales_line_id: int) -> int:
        return int(conn.execute(
            "SELECT COALESCE(SUM(srl.quantity_micros),0) FROM sales_return_lines srl "
            "JOIN sales_returns sr ON sr.id=srl.sales_return_id "
            "WHERE srl.sales_line_id=? AND sr.status='POSTED'", (sales_line_id,)
        ).fetchone()[0])

    def _validate_refund_request(self, conn, return_header) -> None:
        method = return_header["settlement_method"]
        refund = return_header["refund_piastres"]
        if method not in {"REDUCE_RECEIVABLE", "CUSTOMER_CREDIT", "CASH_REFUND", "BANK_REFUND", "MIXED"}:
            raise SalesError("invalid settlement method")
        if refund < 0:
            raise SalesError("refund cannot be negative")
        if refund > 0:
            if method not in {"CASH_REFUND", "BANK_REFUND", "MIXED"}:
                raise SalesError("refund requires a cash/bank/mixed settlement method")
            if return_header["financial_account_id"] is None:
                raise SalesError("refund requires a financial account")
            if not return_header["refund_reference"] or not return_header["refund_reference"].strip():
                raise SalesError("refund requires a document/reference")
            account = self._financial_account(conn, return_header["financial_account_id"])
            if method == "CASH_REFUND" and account["account_type"] != "CASH":
                raise SalesError("cash refund requires a cash financial account")
            if method == "BANK_REFUND" and account["account_type"] != "BANK":
                raise SalesError("bank refund requires a bank financial account")
        else:
            if method in {"CASH_REFUND", "BANK_REFUND", "MIXED"}:
                raise SalesError("cash/bank/mixed settlement requires a refund amount")

    def create_return(self, invoice_id: int, lines: list[dict], return_date: str, reason: str,
                      settlement_method: str = "REDUCE_RECEIVABLE",
                      financial_account_id: int | None = None,
                      refund_piastres: int = 0, refund_reference: str | None = None,
                      actor_id: int | None = None) -> int:
        if not lines:
            raise SalesError("sales return needs lines")
        if not reason or not reason.strip():
            raise SalesError("sales return reason is required")
        self._require(actor_id, "post_sales_return")
        method = settlement_method
        if method not in {"REDUCE_RECEIVABLE", "CUSTOMER_CREDIT", "CASH_REFUND", "BANK_REFUND", "MIXED"}:
            raise SalesError("invalid settlement method")
        if refund_piastres < 0:
            raise SalesError("refund cannot be negative")
        if refund_piastres > 0:
            if method not in {"CASH_REFUND", "BANK_REFUND", "MIXED"}:
                raise SalesError("refund requires a cash/bank/mixed settlement method")
            if financial_account_id is None:
                raise SalesError("refund requires a financial account")
            if not refund_reference or not refund_reference.strip():
                raise SalesError("refund requires a document/reference")
            if actor_id is None:
                raise PermissionDenied("refund requires an authorized user")
            PermissionService(self.db).require(actor_id, "issue_customer_refund")
        elif method in {"CASH_REFUND", "BANK_REFUND", "MIXED"}:
            raise SalesError("cash/bank/mixed settlement requires a refund amount")
        with self.db.transaction() as conn:
            invoice = conn.execute("SELECT * FROM sales WHERE id=?", (invoice_id,)).fetchone()
            if invoice is None or invoice["status"] != "POSTED":
                raise SalesError("posted sales invoice not found")
            if invoice["reversal_journal_entry_id"] is not None:
                raise SalesError("cannot return a reversed sales invoice")
            self._customer(conn, invoice["customer_id"])
            if financial_account_id is not None:
                self._financial_account(conn, financial_account_id)
            any_damaged = any(str(line.get("condition", "SELLABLE")).upper() == "DAMAGED" for line in lines)
            document_no = next_number(conn, "SALES_RETURN", int(return_date[:4]), "SR-")
            cur = conn.execute(
                "INSERT INTO sales_returns(document_no,sales_id,customer_id,return_date,"
                "settlement_method,financial_account_id,refund_piastres,refund_reference,"
                "damaged_cost_accounting,reason,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (document_no, invoice_id, invoice["customer_id"], return_date, method,
                 financial_account_id, refund_piastres, refund_reference,
                 "PENDING" if any_damaged else "NOT_APPLICABLE",
                 reason.strip(), actor_id),
            )
            return_id = int(cur.lastrowid)
            pending_sold: dict[int, int] = {}
            pending_alloc: dict[tuple[str, int], int] = {}
            for index, line in enumerate(lines, start=1):
                sales_line = conn.execute(
                    "SELECT * FROM sales_lines WHERE id=?", (line["sales_line_id"],)
                ).fetchone()
                if sales_line is None or sales_line["sales_id"] != invoice_id:
                    raise SalesError("sales line does not belong to the invoice")
                product = self._product(conn, sales_line["product_id"])
                quantity = int(line["quantity_micros"])
                base_qty, _, _ = self._base_qty(
                    conn, product, sales_line["unit_id"], quantity
                )
                already = self._posted_returned_sold_qty(conn, sales_line["id"])
                pending = pending_sold.get(sales_line["id"], 0)
                if already + pending + quantity > sales_line["quantity_micros"]:
                    raise SalesError("return quantity exceeds the remaining sold quantity")
                returnable = self._returnable_for_line(conn, sales_line)
                selections = line.get("allocation_selections")
                if selections is not None:
                    if not selections:
                        raise SalesError("allocation selection cannot be empty")
                    selection_rows = self._resolve_selections(
                        selections, returnable, base_qty, pending_alloc,
                    )
                else:
                    selection_rows = self._default_selections(returnable, base_qty, pending_alloc)
                if sum(row["quantity_micros"] for row in selection_rows) != base_qty:
                    raise SalesError("allocation selections must cover the returned base quantity")
                for row in selection_rows:
                    key = (row["kind"], row["allocation_id"])
                    pending_alloc[key] = pending_alloc.get(key, 0) + row["quantity_micros"]
                pending_sold[sales_line["id"]] = pending + quantity
                lots = {row["lot_id"] for row in selection_rows}
                reels = {row["reel_id"] for row in selection_rows if row["kind"] == "REEL"}
                condition = str(line.get("condition", "SELLABLE")).upper()
                if condition not in {"SELLABLE", "DAMAGED"}:
                    raise SalesError("return condition must be SELLABLE or DAMAGED")
                cur = conn.execute(
                    "INSERT INTO sales_return_lines(sales_return_id,line_no,sales_line_id,"
                    "quantity_micros,base_qty_micros,condition,lot_id,reel_id) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (return_id, index, sales_line["id"], quantity, base_qty, condition,
                     next(iter(lots)) if len(lots) == 1 else None,
                     next(iter(reels)) if len(reels) == 1 else None),
                )
                return_line_id = int(cur.lastrowid)
                for row in selection_rows:
                    conn.execute(
                        "INSERT INTO sales_return_line_allocations(sales_return_line_id,allocation_kind,"
                        "original_allocation_id,quantity_micros) VALUES(?,?,?,?)",
                        (return_line_id, row["kind"], row["allocation_id"], row["quantity_micros"]),
                    )
            return return_id

    def _resolve_selections(self, selections: list[dict], returnable: list[dict],
                            base_qty: int, pending: dict[tuple[str, int], int]) -> list[dict]:
        by_key = {(row["kind"], row["allocation_id"]): row for row in returnable}
        result = []
        seen: set[tuple[str, int]] = set()
        for selection in selections:
            kind = str(selection.get("kind", "")).upper()
            allocation_id = int(selection["allocation_id"])
            quantity = int(selection["quantity_micros"])
            if quantity <= 0:
                raise SalesError("allocation selection quantity must be positive")
            key = (kind, allocation_id)
            if key in seen:
                raise SalesError("duplicate allocation selection")
            seen.add(key)
            row = by_key.get(key)
            if row is None:
                raise SalesError("allocation is not returnable")
            available = row["remaining_qty_micros"] - pending.get(key, 0)
            if quantity > available:
                raise SalesError("allocation selection exceeds the remaining returnable quantity")
            result.append({
                "kind": kind,
                "allocation_id": allocation_id,
                "quantity_micros": quantity,
                "lot_id": row["lot_id"],
                "reel_id": row["reel_id"],
                "original_qty_micros": row["original_qty_micros"],
                "original_cost_piastres": row["original_cost_piastres"],
            })
        if sum(row["quantity_micros"] for row in result) != base_qty:
            raise SalesError("allocation selections must cover the returned base quantity")
        return result

    def _default_selections(self, returnable: list[dict], base_qty: int,
                            pending: dict[tuple[str, int], int]) -> list[dict]:
        result = []
        needed = base_qty
        for row in returnable:
            if needed <= 0:
                break
            key = (row["kind"], row["allocation_id"])
            available = row["remaining_qty_micros"] - pending.get(key, 0)
            if available <= 0:
                continue
            take = min(needed, available)
            result.append({
                "kind": row["kind"],
                "allocation_id": row["allocation_id"],
                "quantity_micros": take,
                "lot_id": row["lot_id"],
                "reel_id": row["reel_id"],
                "original_qty_micros": row["original_qty_micros"],
                "original_cost_piastres": row["original_cost_piastres"],
            })
            needed -= take
        if needed != 0:
            raise SalesError("insufficient remaining returnable quantity")
        return result

    def post_return(self, return_id: int, actor_id: int | None = None) -> dict:
        self._require(actor_id, "post_sales_return")
        with self.db.transaction() as conn:
            sales_return = conn.execute(
                "SELECT * FROM sales_returns WHERE id=?", (return_id,)
            ).fetchone()
            if sales_return is None or sales_return["status"] != "DRAFT":
                raise SalesError("draft sales return not found")
            invoice = conn.execute(
                "SELECT * FROM sales WHERE id=?", (sales_return["sales_id"],)
            ).fetchone()
            if invoice is None or invoice["status"] != "POSTED":
                raise SalesError("posted sales invoice not found")
            if invoice["reversal_journal_entry_id"] is not None:
                raise SalesError("cannot return a reversed sales invoice")
            self._validate_refund_request(conn, sales_return)
            if sales_return["refund_piastres"] > 0:
                if actor_id is None:
                    raise PermissionDenied("refund requires an authorized user")
                PermissionService(self.db).require(actor_id, "issue_customer_refund")
            lines = conn.execute(
                "SELECT * FROM sales_return_lines WHERE sales_return_id=? ORDER BY line_no",
                (return_id,),
            ).fetchall()
            if not lines:
                raise SalesError("sales return has no lines")

            pending_sold: dict[int, int] = {}
            pending_alloc: dict[tuple[str, int], int] = {}
            planned: list[dict] = []
            sellable_cost = 0
            damaged_cost = 0
            commercial_total = 0
            for line in lines:
                sales_line = conn.execute(
                    "SELECT * FROM sales_lines WHERE id=?", (line["sales_line_id"],)
                ).fetchone()
                if sales_line is None or sales_line["sales_id"] != invoice["id"]:
                    raise SalesError("sales line does not belong to the invoice")
                product = self._product(conn, sales_line["product_id"])
                selections = conn.execute(
                    "SELECT * FROM sales_return_line_allocations "
                    "WHERE sales_return_line_id=? ORDER BY id", (line["id"],)
                ).fetchall()
                if not selections:
                    raise SalesError("sales return line has no allocation selection")
                if sum(row["quantity_micros"] for row in selections) != line["base_qty_micros"]:
                    raise SalesError("allocation selections must cover the returned base quantity")
                returnable = self._returnable_for_line(conn, sales_line)
                by_key = {(row["kind"], row["allocation_id"]): row for row in returnable}
                line_selections = []
                for selection in selections:
                    key = (selection["allocation_kind"], selection["original_allocation_id"])
                    row = by_key.get(key)
                    if row is None:
                        raise SalesError("allocation is no longer returnable")
                    available = row["remaining_qty_micros"] - pending_alloc.get(key, 0)
                    if selection["quantity_micros"] > available:
                        raise SalesError("allocation selection exceeds the remaining returnable quantity")
                    returned_qty = (
                        self._returned_reel_qty(conn, row["allocation_id"])
                        if key[0] == "REEL"
                        else self._returned_lot_qty(conn, row["allocation_id"])
                    )
                    old = returned_qty + pending_alloc.get(key, 0)
                    new = old + selection["quantity_micros"]
                    cost = return_cost(row["original_cost_piastres"], row["original_qty_micros"], old, new)
                    pending_alloc[key] = pending_alloc.get(key, 0) + selection["quantity_micros"]
                    line_selections.append({
                        "kind": key[0],
                        "allocation_id": key[1],
                        "quantity_micros": selection["quantity_micros"],
                        "cost": cost,
                        "lot_id": row["lot_id"],
                        "reel_id": row["reel_id"],
                        "original_qty_micros": row["original_qty_micros"],
                        "original_cost_piastres": row["original_cost_piastres"],
                    })
                old_sold = self._posted_returned_sold_qty(conn, sales_line["id"]) + pending_sold.get(sales_line["id"], 0)
                new_sold = old_sold + line["quantity_micros"]
                commercial = return_cost(
                    sales_line["net_piastres"], sales_line["quantity_micros"], old_sold, new_sold
                )
                pending_sold[sales_line["id"]] = new_sold
                line_cost = sum(row["cost"] for row in line_selections)
                commercial_total += commercial
                if line["condition"] == "SELLABLE":
                    sellable_cost += line_cost
                else:
                    damaged_cost += line_cost
                planned.append({
                    "line": line,
                    "sales_line": sales_line,
                    "product": product,
                    "selections": line_selections,
                    "commercial": commercial,
                    "cost": line_cost,
                })

            outstanding = self._invoice_balance(conn, invoice)
            ar_reduction = min(commercial_total, outstanding)
            excess = commercial_total - ar_reduction
            method = sales_return["settlement_method"]
            if method == "CUSTOMER_CREDIT" and outstanding > 0:
                raise SalesError("reduce outstanding AR first; customer-credit-only is not allowed")
            if method in {"CASH_REFUND", "BANK_REFUND"}:
                if sales_return["refund_piastres"] != excess:
                    raise SalesError("cash/bank refund must equal the excess after reducing AR")
                refund = sales_return["refund_piastres"]
                credit = 0
            elif method == "MIXED":
                refund = sales_return["refund_piastres"]
                if refund <= 0 or refund > excess:
                    raise SalesError("mixed refund must be positive and no more than the excess")
                credit = excess - refund
            else:
                refund = 0
                credit = excess
            if commercial_total == 0 and sellable_cost == 0 and damaged_cost == 0:
                raise SalesError("sales return has no commercial or inventory cost value")

            written: list[dict] = []
            for seq, item in enumerate(planned, start=1):
                layer_rows = []
                result_lots: set[int] = set()
                result_reels: set[int] = set()
                for selection in item["selections"]:
                    if selection["kind"] == "REEL":
                        layer_id, lot_id, reel_id = self._create_returned_reel(
                            conn, return_id=return_id, return_line_id=item["line"]["id"],
                            sales_line_id=item["sales_line"]["id"],
                            product_id=item["product"]["id"], selection=selection,
                            return_date=sales_return["return_date"], actor_id=actor_id, seq=seq,
                        )
                        result_lots.add(lot_id)
                        result_reels.add(reel_id)
                    else:
                        layer_id = create_layer(
                            conn, selection["lot_id"], None, "CUSTOMER_RETURN",
                            selection["quantity_micros"], selection["cost"],
                            sales_return["return_date"], seq,
                            parent_issue_allocation_id=selection["allocation_id"],
                        )
                        movement_id = create_movement(
                            conn, "SALE_RETURN_IN", item["product"]["id"], selection["lot_id"], None,
                            selection["quantity_micros"], selection["cost"], "IN",
                            "SALES_RETURN", return_id, sales_return["return_date"],
                            actor_id, item["line"]["id"],
                        )
                        conn.execute(
                            "INSERT INTO lot_allocations(stock_movement_id,cost_layer_id,lot_id,sales_line_id,"
                            "sales_return_line_id,allocation_role,quantity_micros,cost_piastres,direction,"
                            "source_issue_allocation_id,created_layer_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (movement_id, layer_id, selection["lot_id"], item["sales_line"]["id"],
                             item["line"]["id"], "SALE_RETURN", selection["quantity_micros"],
                             selection["cost"], "IN", selection["allocation_id"], layer_id),
                        )
                        refresh_lot_status(conn, selection["lot_id"])
                        result_lots.add(selection["lot_id"])
                    layer_rows.append((layer_id, selection))
                conn.execute(
                    "UPDATE sales_return_lines SET commercial_value_piastres=?,cost_piastres=?,"
                    "lot_id=?,reel_id=? WHERE id=?",
                    (item["commercial"], item["cost"],
                     next(iter(result_lots)) if len(result_lots) == 1 else None,
                     next(iter(result_reels)) if len(result_reels) == 1 else None,
                     item["line"]["id"]),
                )
                written.append({**item, "layer_rows": layer_rows})

            journal_lines: list[dict] = []
            if commercial_total > 0:
                journal_lines.append({"account_code": "4020", "debit": commercial_total,
                                      "party_type": "CUSTOMER", "party_id": invoice["customer_id"]})
                if ar_reduction:
                    journal_lines.append({"account_code": "1100", "credit": ar_reduction,
                                          "party_type": "CUSTOMER", "party_id": invoice["customer_id"]})
                if credit:
                    journal_lines.append({"account_code": "2020", "credit": credit,
                                          "party_type": "CUSTOMER", "party_id": invoice["customer_id"]})
                if refund:
                    account = self._financial_account(conn, sales_return["financial_account_id"])
                    journal_lines.append({"account_code": account["gl_code"], "credit": refund,
                                          "party_type": "CUSTOMER", "party_id": invoice["customer_id"]})
            if sellable_cost > 0:
                journal_lines.append({"account_code": "1200", "debit": sellable_cost})
                journal_lines.append({"account_code": "5010", "credit": sellable_cost})
            if damaged_cost > 0:
                journal_lines.append({"account_code": "5020", "debit": damaged_cost})
                journal_lines.append({"account_code": "5010", "credit": damaged_cost})
            journal_id = self.accounting.post_journal(
                sales_return["return_date"], "SALES_RETURN", return_id, journal_lines,
                description=f"مرتجع بيع {sales_return['document_no']}",
                posted_by=actor_id, conn=conn,
            )
            if credit:
                conn.execute(
                    "INSERT INTO customer_credits(customer_id,source_type,source_id,amount_piastres,"
                    "remaining_piastres,status) VALUES(?,?,?,?,?,?)",
                    (invoice["customer_id"], "SALES_RETURN", return_id, credit, credit, "OPEN"),
                )
            for item in written:
                if item["line"]["condition"] != "DAMAGED":
                    continue
                for layer_id, selection in item["layer_rows"]:
                    self._damage_return_layer(
                        conn, return_id=return_id, return_line_id=item["line"]["id"],
                        layer_id=layer_id, product_id=item["product"]["id"],
                        actor_id=actor_id, occurred_at=sales_return["return_date"],
                        original_allocation_id=selection["allocation_id"],
                    )
            any_damaged = any(line["condition"] == "DAMAGED" for line in lines)
            damaged_cost_accounting = (
                "POSTED_5020_5010" if any_damaged else "NOT_APPLICABLE"
            )
            conn.execute(
                "UPDATE sales_returns SET status='POSTED',commercial_total_piastres=?,"
                "inventory_cost_piastres=?,ar_reduction_piastres=?,customer_credit_piastres=?,"
                "refund_piastres=?,damaged_cost_accounting=?,journal_entry_id=?,posted_at=? WHERE id=?",
                (commercial_total, sellable_cost + damaged_cost, ar_reduction, credit,
                 refund, damaged_cost_accounting, journal_id, _now(), return_id),
            )
            self._audit(conn, "SALES_RETURN_POSTED", "sales_return", return_id, actor_id,
                        sales_return["reason"])
            return {
                "return_id": return_id,
                "journal_id": journal_id,
                "commercial_total": commercial_total,
                "inventory_cost": sellable_cost + damaged_cost,
                "ar_reduction": ar_reduction,
                "customer_credit": credit,
                "refund": refund,
                "damaged_cost_accounting": damaged_cost_accounting,
            }

    def _create_returned_reel(self, conn, *, return_id: int, return_line_id: int | None,
                              sales_line_id: int, product_id: int, selection: dict,
                              return_date: str, actor_id: int | None, seq: int,
                              source_type: str = "SALES_RETURN") -> tuple[int, int, int]:
        quantity = selection["quantity_micros"]
        cost = selection["cost"]
        original_reel_id = selection["reel_id"]
        if original_reel_id is None:
            raise SalesError("reel return is missing its original reel")
        original = conn.execute("SELECT * FROM reels WHERE id=?", (original_reel_id,)).fetchone()
        if original is None or original["product_id"] != product_id:
            raise SalesError("original reel not found")
        lot_no = next_number(conn, "LOT", int(return_date[:4]), "LOT-")
        cur = conn.execute(
            "INSERT INTO purchase_lots(lot_no,purchase_invoice_line_id,source_type,product_id,"
            "warehouse_id,supplier_id,tracking_mode,received_at,original_qty_micros,"
            "original_supplier_cost_piastres,original_landed_cost_piastres) "
            "VALUES(?,NULL,'RETURN',?,1,NULL,'REEL',?,?,0,?)",
            (lot_no, product_id, return_date, quantity, cost),
        )
        lot_id = int(cur.lastrowid)
        reel_no = next_number(conn, "REEL", int(return_date[:4]), "RL-")
        cur = conn.execute(
            "INSERT INTO reels(reel_no,lot_id,product_id,original_length_micros,"
            "supplier_cost_piastres,allocated_expense_piastres,original_cost_piastres,"
            "received_at,source_reel_id,source_issue_allocation_id) "
            "VALUES(?,?,?,?,0,?,?,?,?,?)",
            (reel_no, lot_id, product_id, quantity, cost, cost, return_date,
             original_reel_id, selection["allocation_id"]),
        )
        reel_id = int(cur.lastrowid)
        layer_id = create_layer(
            conn, lot_id, reel_id, "CUSTOMER_RETURN", quantity, cost, return_date, seq,
            parent_issue_allocation_id=selection["allocation_id"],
        )
        movement_id = create_movement(
            conn, "SALE_RETURN_IN", product_id, lot_id, reel_id, quantity, cost, "IN",
            source_type, return_id, return_date, actor_id, return_line_id,
        )
        conn.execute(
            "INSERT INTO reel_allocations(stock_movement_id,cost_layer_id,reel_id,sales_line_id,"
            "sales_return_line_id,allocation_role,quantity_micros,cost_piastres,direction,"
            "source_issue_allocation_id,created_layer_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (movement_id, layer_id, reel_id, sales_line_id, return_line_id, "SALE_RETURN",
             quantity, cost, "IN", selection["allocation_id"], layer_id),
        )
        refresh_reel_status(conn, reel_id)
        refresh_lot_status(conn, lot_id)
        return layer_id, lot_id, reel_id

    def _damage_return_layer(self, conn, *, return_id: int, return_line_id: int, layer_id: int,
                             product_id: int, actor_id: int | None, occurred_at: str,
                             original_allocation_id: int) -> int:
        layer = conn.execute(
            "SELECT * FROM inventory_cost_layers WHERE id=?", (layer_id,)
        ).fetchone()
        if layer is None or layer["layer_type"] != "CUSTOMER_RETURN":
            raise SalesError("return layer not found")
        quantity = layer["remaining_qty_micros"]
        if quantity <= 0:
            raise SalesError("return layer has no remaining quantity")
        part = consume_layer(conn, layer, quantity)
        conn.execute(
            "UPDATE inventory_cost_layers SET status='DAMAGED' WHERE id=?", (layer_id,)
        )
        lot_id = layer["lot_id"]
        reel_id = layer["reel_id"]
        movement_id = create_movement(
            conn, "DAMAGE", product_id, lot_id, reel_id, quantity, part["cost"], "OUT",
            "SALES_RETURN", return_id, occurred_at, actor_id, return_line_id,
        )
        if reel_id is None:
            conn.execute(
                "INSERT INTO lot_allocations(stock_movement_id,cost_layer_id,lot_id,sales_line_id,"
                "sales_return_line_id,allocation_role,quantity_micros,cost_piastres,direction,"
                "layer_position_start_micros,layer_position_end_micros,source_issue_allocation_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (movement_id, layer_id, lot_id, None, return_line_id, "DAMAGE", quantity,
                 part["cost"], "OUT", part["start"], part["end"], original_allocation_id),
            )
            refresh_lot_status(conn, lot_id)
        else:
            conn.execute(
                "INSERT INTO reel_allocations(stock_movement_id,cost_layer_id,reel_id,sales_line_id,"
                "sales_return_line_id,allocation_role,quantity_micros,cost_piastres,direction,"
                "layer_position_start_micros,layer_position_end_micros,source_issue_allocation_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (movement_id, layer_id, reel_id, None, return_line_id, "DAMAGE", quantity,
                 part["cost"], "OUT", part["start"], part["end"], original_allocation_id),
            )
            refresh_reel_status(conn, reel_id, damaged=True)
            refresh_lot_status(conn, lot_id)
        return part["cost"]

    @staticmethod
    def _audit(conn, event_type: str, entity_type: str, entity_id: int | None,
               actor_id: int | None, reason: str | None = None) -> None:
        conn.execute(
            "INSERT INTO audit_events(event_type,entity_type,entity_id,actor_user_id,occurred_at,reason) "
            "VALUES(?,?,?,?,?,?)",
            (event_type, entity_type, entity_id, actor_id, _now(), reason),
        )
