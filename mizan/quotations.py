"""Quotation lifecycle, revisions, conversion, and document snapshots.

The lifecycle is renderer-independent. Issuance requires an official PDF
registered by the approved A4 export workflow.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

from .auth import PermissionDenied, PermissionService
from .db import Database
from .documents import DocumentError, DocumentService
from .inventory import available_stock
from .lcp import allocate_largest_remainder, line_total
from .numbering import next_number
from .pricing import base_quantity, resolve_price


_UNSET = object()

CONVERSION_CREATED_EVENT = "CONVERSION_DRAFT_CREATED"
CONVERSION_CANCELLED_EVENT = "CONVERSION_DRAFT_CANCELLED"
SALES_POSTED_EVENT = "SALES_POSTED"
REVISION_SUPERSEDED_EVENT = "SUPERSEDED_BY_REVISION"


class QuotationError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _as_date(value: str, field: str = "date") -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise QuotationError(f"{field} must be an ISO date") from exc


def _today(as_of: str | None = None) -> date:
    return date.today() if as_of is None else _as_date(as_of, "as_of")


def _is_expired_row(row, as_of: str | None = None) -> bool:
    if not row["valid_until"]:
        return False
    return _today(as_of) > _as_date(row["valid_until"], "valid_until")


def _load_metadata(value: str | None) -> dict:
    if not value:
        return {}
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _insert_history(conn, quotation_id: int, from_status: str | None, to_status: str,
                    reason: str | None, actor_id: int | None,
                    metadata: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO quotation_status_history(quotation_id,from_status,to_status,reason,"
        "actor_user_id,occurred_at,metadata_json) VALUES(?,?,?,?,?,?,?)",
        (quotation_id, from_status, to_status, reason, actor_id, _now(),
         json.dumps(metadata, ensure_ascii=False, sort_keys=True) if metadata else None),
    )


def _audit(conn, event_type: str, entity_id: int, actor_id: int | None,
           reason: str | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_events(event_type,entity_type,entity_id,actor_user_id,occurred_at,reason) "
        "VALUES(?,?,?,?,?,?)",
        (event_type, "quotation", entity_id, actor_id, _now(), reason),
    )


def _quotation_row(conn, quotation_id: int):
    row = conn.execute("SELECT * FROM quotations WHERE id=?", (quotation_id,)).fetchone()
    if row is None:
        raise QuotationError("quotation not found")
    return row


def _active_sale(conn, quotation_id: int):
    return conn.execute(
        "SELECT id FROM sales WHERE source_quotation_id=? AND status <> 'CANCELLED'",
        (quotation_id,),
    ).fetchone()


def mark_quotation_converted(conn, quotation_id: int, sales_id: int,
                             actor_id: int | None) -> None:
    """Set a quotation to CONVERTED when its sale is posted, atomically."""
    row = _quotation_row(conn, quotation_id)
    if row["status"] == "CONVERTED":
        raise QuotationError("quotation is already converted")
    if row["status"] not in {"ISSUED", "ACCEPTED"}:
        raise QuotationError(
            f"quotation in status {row['status']} cannot be marked CONVERTED"
        )
    conn.execute(
        "UPDATE quotations SET status='CONVERTED',converted_at=? WHERE id=?",
        (_now(), quotation_id),
    )
    _insert_history(
        conn, quotation_id, row["status"], "CONVERTED", None, actor_id,
        {"event": SALES_POSTED_EVENT, "sales_id": sales_id},
    )
    _audit(conn, "QUOTATION_CONVERTED", quotation_id, actor_id, f"sales {sales_id}")


def restore_quotation_after_draft_cancel(conn, quotation_id: int, sales_id: int,
                                         actor_id: int | None, reason: str) -> None:
    """Restore the status saved before a conversion draft was created.

    The quote status is not changed when the draft is created (05 section 3.2),
    so this is normally a no-op.  If the status changed while the draft was
    active, OQ-QUO-001 says to restore the saved status; an expired quote is
    never made valid by cancelling the draft.
    """
    row = conn.execute("SELECT * FROM quotations WHERE id=?", (quotation_id,)).fetchone()
    if row is None:
        return
    status_before = row["status"]
    history = conn.execute(
        "SELECT * FROM quotation_status_history WHERE quotation_id=? ORDER BY id DESC",
        (quotation_id,),
    ).fetchall()
    for item in history:
        metadata = _load_metadata(item["metadata_json"])
        if metadata.get("event") == CONVERSION_CREATED_EVENT:
            status_before = metadata.get("status_before_conversion") or status_before
            break

    current = row["status"]
    expired = _is_expired_row(row)
    restored = current
    updated = False
    if (current in {"ISSUED", "ACCEPTED"} and not expired
            and status_before in {"DRAFT", "ISSUED", "ACCEPTED"}
            and status_before != current):
        conn.execute("UPDATE quotations SET status=? WHERE id=?", (status_before, quotation_id))
        restored = status_before
        updated = True
    _insert_history(
        conn, quotation_id, current, restored, reason, actor_id,
        {
            "event": CONVERSION_CANCELLED_EVENT,
            "sales_id": sales_id,
            "status_before_conversion": status_before,
            "expired": expired,
            "restored": updated,
        },
    )
    _audit(conn, "QUOTATION_CONVERSION_DRAFT_CANCELLED", quotation_id, actor_id, reason)


class QuotationService:
    def __init__(self, db: Database):
        self.db = db
        self.documents = DocumentService(db)

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _require(self, actor_id: int | None, permission: str) -> None:
        if actor_id is not None:
            PermissionService(self.db).require(actor_id, permission)

    @staticmethod
    def _customer(conn, customer_id: int):
        row = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        if row is None:
            raise QuotationError("customer not found")
        if not row["is_active"]:
            raise QuotationError("customer is inactive")
        return row

    @staticmethod
    def _product(conn, product_id: int):
        row = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if row is None:
            raise QuotationError("product not found")
        if not row["is_active"]:
            raise QuotationError("product is inactive")
        return row

    @staticmethod
    def _project(conn, project_id: int, customer_id: int):
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise QuotationError("project not found")
        if row["customer_id"] != customer_id:
            raise QuotationError("project does not belong to the customer")
        return row

    @staticmethod
    def _price_list(conn, price_list_id: int):
        row = conn.execute("SELECT * FROM price_lists WHERE id=?", (price_list_id,)).fetchone()
        if row is None:
            raise QuotationError("price list not found")
        if not row["is_active"]:
            raise QuotationError("price list is inactive")
        return row

    @staticmethod
    def _financial_account(conn, financial_account_id: int):
        row = conn.execute(
            "SELECT * FROM financial_accounts WHERE id=?", (financial_account_id,)
        ).fetchone()
        if row is None:
            raise QuotationError("financial account not found")
        if not row["is_active"]:
            raise QuotationError("financial account is inactive")
        return row

    # ------------------------------------------------------------------
    # Draft construction
    # ------------------------------------------------------------------

    def _prepare_lines(self, conn, quotation_id: int | None, customer_id: int,
                       lines: list[dict], price_list_id: int | None,
                       project_id: int | None, invoice_discount_piastres: int,
                       actor_id: int | None) -> list[dict]:
        if not lines:
            raise QuotationError("quotation needs lines")
        if invoice_discount_piastres < 0:
            raise QuotationError("invoice discount cannot be negative")
        computed: list[dict] = []
        for index, line in enumerate(lines, start=1):
            try:
                product_id = int(line["product_id"])
                unit_id = int(line["unit_id"])
                quantity = int(line["quantity_micros"])
            except (KeyError, TypeError, ValueError) as exc:
                raise QuotationError("invalid quotation line") from exc
            product = self._product(conn, product_id)
            try:
                base_qty, conversion_num, conversion_den = base_quantity(
                    conn, product, unit_id, quantity
                )
            except ValueError as exc:
                raise QuotationError(str(exc)) from exc
            unit = conn.execute("SELECT * FROM units WHERE id=?", (unit_id,)).fetchone()
            if unit is None:
                raise QuotationError("unit not found")
            if not unit["is_active"]:
                raise QuotationError("unit is inactive")

            resolved_price, resolved_source = resolve_price(
                conn, customer_id, product["id"], unit_id, price_list_id,
            )
            manual_price = line.get("unit_price_micros")
            reason = line.get("price_adjustment_reason")
            if manual_price is not None:
                try:
                    manual_price = int(manual_price)
                except (TypeError, ValueError) as exc:
                    raise QuotationError("invalid unit price") from exc
                if manual_price < 0:
                    raise QuotationError("price cannot be negative")
                if resolved_price is None:
                    original_price, final_price = manual_price, manual_price
                    price_source = "MANUAL"
                elif manual_price == resolved_price:
                    original_price = final_price = manual_price
                    price_source = resolved_source
                else:
                    if actor_id is not None:
                        PermissionService(self.db).require(actor_id, "edit_sale_price")
                    if not reason or not str(reason).strip():
                        raise QuotationError("manual price requires a reason")
                    original_price, final_price = resolved_price, manual_price
                    price_source = "MANUAL"
                    _audit(conn, "QUOTATION_PRICE_OVERRIDDEN", quotation_id or 0, actor_id,
                           str(reason))
            else:
                if resolved_price is None:
                    raise QuotationError("no price found for product/unit")
                original_price = final_price = resolved_price
                price_source = resolved_source

            gross = line_total(final_price, quantity)
            item_discount = int(line.get("item_discount_piastres", 0) or 0)
            if item_discount < 0 or item_discount > gross:
                raise QuotationError("item discount exceeds the line gross value")
            if item_discount and actor_id is not None:
                PermissionService(self.db).require(actor_id, "apply_item_discount")
            line_project_id = line.get("project_id", project_id)
            if line_project_id is not None:
                self._project(conn, int(line_project_id), customer_id)
            computed.append({
                "line_no": index,
                "product": product,
                "unit": unit,
                "unit_id": unit_id,
                "quantity": quantity,
                "base_qty": base_qty,
                "conversion_num": conversion_num,
                "conversion_den": conversion_den,
                "original_price": original_price,
                "final_price": final_price,
                "gross": gross,
                "item_discount": item_discount,
                "price_source": price_source,
                "price_adjustment_reason": reason if price_source == "MANUAL" else None,
                "project_id": int(line_project_id) if line_project_id is not None else None,
                "description": (line.get("description") or product["name_ar"]),
            })
        net_before = [item["gross"] - item["item_discount"] for item in computed]
        if invoice_discount_piastres > sum(net_before):
            raise QuotationError("invoice discount exceeds the eligible net amount")
        if invoice_discount_piastres and actor_id is not None:
            PermissionService(self.db).require(actor_id, "apply_invoice_discount")
        allocations = allocate_largest_remainder(invoice_discount_piastres, net_before)
        for item, allocation in zip(computed, allocations):
            item["invoice_discount_alloc"] = allocation
            item["net"] = item["gross"] - item["item_discount"] - allocation
            if item["net"] < 0:
                raise QuotationError("invoice discount produced a negative line")
        return computed

    def _replace_lines(self, conn, quotation_id: int, customer_id: int, lines: list[dict],
                       price_list_id: int | None, project_id: int | None,
                       invoice_discount_piastres: int,
                       actor_id: int | None) -> dict:
        computed = self._prepare_lines(
            conn, quotation_id, customer_id, lines, price_list_id, project_id,
            invoice_discount_piastres, actor_id,
        )
        conn.execute("DELETE FROM quotation_lines WHERE quotation_id=?", (quotation_id,))
        for item in computed:
            conn.execute(
                "INSERT INTO quotation_lines(quotation_id,line_no,product_id,unit_id,"
                "quantity_micros,base_qty_micros,original_unit_price_micros,"
                "final_unit_price_micros,price_source,price_adjustment_reason,"
                "conversion_num,conversion_den,item_discount_piastres,"
                "invoice_discount_alloc_piastres,net_piastres,description_snapshot,"
                "product_code_snapshot,product_name_snapshot,unit_name_snapshot,project_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (quotation_id, item["line_no"], item["product"]["id"], item["unit_id"],
                 item["quantity"], item["base_qty"], item["original_price"],
                 item["final_price"], item["price_source"],
                 item["price_adjustment_reason"], item["conversion_num"],
                 item["conversion_den"], item["item_discount"],
                 item["invoice_discount_alloc"], item["net"], item["description"],
                 item["product"]["code"], item["product"]["name_ar"],
                 item["unit"]["name_ar"], item["project_id"]),
            )
        subtotal = sum(item["gross"] for item in computed)
        item_discount = sum(item["item_discount"] for item in computed)
        total = sum(item["net"] for item in computed)
        conn.execute(
            "UPDATE quotations SET subtotal_piastres=?,item_discount_piastres=?,"
            "invoice_discount_piastres=?,total_piastres=? WHERE id=?",
            (subtotal, item_discount, invoice_discount_piastres, total, quotation_id),
        )
        return {
            "subtotal_piastres": subtotal,
            "item_discount_piastres": item_discount,
            "invoice_discount_piastres": invoice_discount_piastres,
            "total_piastres": total,
        }

    def create_quotation(self, customer_id: int, issue_date: str, lines: list[dict], *,
                         valid_until: str | None = None, project_id: int | None = None,
                         price_list_id: int | None = None,
                         invoice_discount_piastres: int = 0,
                         notes: str | None = None, terms: str | None = None,
                         actor_id: int | None = None) -> int:
        if not lines:
            raise QuotationError("quotation needs lines")
        issue = _as_date(issue_date, "issue_date")
        if valid_until is not None and _as_date(valid_until, "valid_until") < issue:
            raise QuotationError("valid_until cannot be before issue_date")
        if invoice_discount_piastres < 0:
            raise QuotationError("invoice discount cannot be negative")
        self._require(actor_id, "create_quotation")
        with self.db.transaction() as conn:
            self._customer(conn, customer_id)
            if project_id is not None:
                self._project(conn, project_id, customer_id)
            if price_list_id is not None:
                self._price_list(conn, price_list_id)
            base_no = next_number(conn, "QUOTATION_SERIES", issue.year, "Q-")
            cur = conn.execute(
                "INSERT INTO quotation_series(base_no) VALUES(?)", (base_no,)
            )
            series_id = int(cur.lastrowid)
            cur = conn.execute(
                "INSERT INTO quotations(document_no,series_id,version_no,customer_id,"
                "project_id,price_list_id,status,issue_date,valid_until,notes,terms,created_by) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (base_no, series_id, 1, customer_id, project_id, price_list_id, "DRAFT",
                 issue_date, valid_until, notes, terms, actor_id),
            )
            quotation_id = int(cur.lastrowid)
            conn.execute(
                "UPDATE quotations SET root_quotation_id=? WHERE id=?",
                (quotation_id, quotation_id),
            )
            self._replace_lines(
                conn, quotation_id, customer_id, lines, price_list_id, project_id,
                invoice_discount_piastres, actor_id,
            )
            _insert_history(conn, quotation_id, None, "DRAFT", None, actor_id)
            _audit(conn, "QUOTATION_CREATED", quotation_id, actor_id)
            return quotation_id

    def update_draft(self, quotation_id: int, lines: list[dict] | None, *,
                     customer_id: int | None = None,
                     project_id=_UNSET,
                     price_list_id=_UNSET,
                     invoice_discount_piastres: int | None = None,
                     issue_date: str | None = None,
                     valid_until=_UNSET,
                     notes=_UNSET,
                     terms=_UNSET,
                     actor_id: int | None = None) -> None:
        """Update a draft. Pass no replacement lines to edit dates and text only."""
        if lines is not None and not lines:
            raise QuotationError("quotation needs lines")
        self._require(actor_id, "create_quotation")
        with self.db.transaction() as conn:
            row = _quotation_row(conn, quotation_id)
            if row["status"] != "DRAFT":
                raise QuotationError("only a draft quotation can be edited")
            new_customer_id = row["customer_id"] if customer_id is None else customer_id
            self._customer(conn, new_customer_id)
            if project_id is _UNSET:
                new_project_id = row["project_id"]
                if (new_customer_id != row["customer_id"] and new_project_id is not None):
                    self._project(conn, new_project_id, new_customer_id)
            else:
                new_project_id = project_id
                if new_project_id is not None:
                    self._project(conn, new_project_id, new_customer_id)
            new_price_list_id = row["price_list_id"] if price_list_id is _UNSET else price_list_id
            if new_price_list_id is not None:
                self._price_list(conn, new_price_list_id)
            new_issue_date = issue_date or row["issue_date"]
            issue = _as_date(new_issue_date, "issue_date")
            new_valid_until = row["valid_until"] if valid_until is _UNSET else valid_until
            if new_valid_until is not None and _as_date(new_valid_until, "valid_until") < issue:
                raise QuotationError("valid_until cannot be before issue_date")
            new_discount = (
                row["invoice_discount_piastres"]
                if invoice_discount_piastres is None
                else invoice_discount_piastres
            )
            new_notes = row["notes"] if notes is _UNSET else notes
            new_terms = row["terms"] if terms is _UNSET else terms
            if lines is None and (
                new_customer_id != row["customer_id"] or new_project_id != row["project_id"]
                or new_price_list_id != row["price_list_id"] or new_discount != row["invoice_discount_piastres"]
            ):
                raise QuotationError("commercial changes require replacement lines")
            conn.execute(
                "UPDATE quotations SET customer_id=?,project_id=?,price_list_id=?,"
                "issue_date=?,valid_until=?,notes=?,terms=? WHERE id=?",
                (new_customer_id, new_project_id, new_price_list_id, new_issue_date,
                 new_valid_until, new_notes, new_terms, quotation_id),
            )
            if lines is not None:
                self._replace_lines(
                    conn, quotation_id, new_customer_id, lines, new_price_list_id,
                    new_project_id, new_discount, actor_id,
                )
            _audit(conn, "QUOTATION_DRAFT_UPDATED", quotation_id, actor_id)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _official_export(self, conn, quotation_id: int, official_export_id: int | None):
        if official_export_id is None:
            raise QuotationError(
                "official PDF export is required to issue a quotation; "
                "ADR-0004 renderer is not approved"
            )
        row = conn.execute(
            "SELECT * FROM document_exports WHERE id=?", (official_export_id,)
        ).fetchone()
        if (row is None or row["document_type"] != "QUOTATION"
                or row["document_id"] != quotation_id
                or row["export_type"] != "PDF" or not row["is_official"]):
            raise QuotationError(
                "official PDF export is required to issue a quotation; "
                "ADR-0004 renderer is not approved"
            )
        return row

    def issue_quotation(self, quotation_id: int, *, official_export_id: int | None = None,
                        official_export: dict | None = None,
                        expected_snapshot: dict | None = None,
                        logo_asset: dict | None = None,
                        actor_id: int | None = None) -> int:
        if official_export_id is not None and official_export is not None:
            raise QuotationError("provide official_export_id or official_export, not both")
        self._require(actor_id, "issue_quotation")
        with self.db.transaction() as conn:
            row = _quotation_row(conn, quotation_id)
            if row["status"] != "DRAFT":
                raise QuotationError("only a draft quotation can be issued")
            lines = conn.execute(
                "SELECT * FROM quotation_lines WHERE quotation_id=? ORDER BY line_no",
                (quotation_id,),
            ).fetchall()
            if not lines:
                raise QuotationError("quotation has no lines")
            self._customer(conn, row["customer_id"])
            if row["project_id"] is not None:
                self._project(conn, row["project_id"], row["customer_id"])
            if row["valid_until"] is not None:
                if _as_date(row["valid_until"], "valid_until") < _as_date(
                    row["issue_date"], "issue_date"
                ):
                    raise QuotationError("valid_until cannot be before issue_date")
            if official_export is not None:
                try:
                    export_id = self.documents.record_export_in_conn(
                        conn,
                        document_type="QUOTATION",
                        document_id=quotation_id,
                        export_type="PDF",
                        relative_path=official_export["relative_path"],
                        sha256=official_export["sha256"],
                        file_size=int(official_export["file_size"]),
                        template_version=official_export["template_version"],
                        renderer_version=official_export["renderer_version"],
                        generated_by=official_export.get("generated_by", actor_id),
                        generated_at=official_export.get("generated_at"),
                        is_official=True,
                    )
                except (DocumentError, KeyError) as exc:
                    raise QuotationError(f"cannot register official export: {exc}") from exc
                export = conn.execute(
                    "SELECT * FROM document_exports WHERE id=?", (export_id,)
                ).fetchone()
            else:
                export = self._official_export(conn, quotation_id, official_export_id)
                export_id = int(export["id"])

            prior = conn.execute(
                "SELECT * FROM quotations WHERE series_id=? AND version_no<? "
                "AND status IN ('ISSUED','ACCEPTED') ORDER BY version_no",
                (row["series_id"], row["version_no"]),
            ).fetchall()
            for previous in prior:
                conn.execute(
                    "UPDATE quotations SET status='SUPERSEDED' WHERE id=?", (previous["id"],)
                )
                _insert_history(
                    conn, previous["id"], previous["status"], "SUPERSEDED", None, actor_id,
                    {"event": REVISION_SUPERSEDED_EVENT, "superseded_by": quotation_id},
                )
                _audit(conn, "QUOTATION_SUPERSEDED", previous["id"], actor_id)

            snapshot = self._build_snapshot(conn, row, export, logo_asset=logo_asset)
            if expected_snapshot is not None:
                rendered_content = {
                    key: value for key, value in snapshot.items()
                    if key != "official_export"
                }
                if json.dumps(rendered_content, ensure_ascii=False, sort_keys=True) != json.dumps(
                    expected_snapshot, ensure_ascii=False, sort_keys=True
                ):
                    raise QuotationError(
                        "quotation changed while the official PDF was being rendered; "
                        "retry the issue from the current draft"
                    )
            self.documents.create_snapshot(
                document_type="QUOTATION", document_id=quotation_id,
                snapshot=snapshot, conn=conn,
            )
            conn.execute(
                "UPDATE quotations SET status='ISSUED',issued_at=?,official_export_id=? WHERE id=?",
                (_now(), export_id, quotation_id),
            )
            _insert_history(
                conn, quotation_id, "DRAFT", "ISSUED", None, actor_id,
                {"event": "ISSUED", "official_export_id": export_id,
                 "sha256": export["sha256"]},
            )
            _audit(conn, "QUOTATION_ISSUED", quotation_id, actor_id)
            return export_id

    def accept_quotation(self, quotation_id: int, *, note: str | None = None,
                         actor_id: int | None = None) -> None:
        self._require(actor_id, "accept_quotation")
        with self.db.transaction() as conn:
            row = _quotation_row(conn, quotation_id)
            if row["status"] != "ISSUED":
                raise QuotationError("only an issued quotation can be accepted")
            conn.execute("UPDATE quotations SET status='ACCEPTED' WHERE id=?", (quotation_id,))
            _insert_history(conn, quotation_id, "ISSUED", "ACCEPTED", note, actor_id,
                            {"event": "ACCEPTED"})
            _audit(conn, "QUOTATION_ACCEPTED", quotation_id, actor_id, note)

    def reject_quotation(self, quotation_id: int, *, reason: str,
                         actor_id: int | None = None) -> None:
        if not reason or not reason.strip():
            raise QuotationError("rejection reason is required")
        self._require(actor_id, "reject_quotation")
        with self.db.transaction() as conn:
            row = _quotation_row(conn, quotation_id)
            if row["status"] != "ISSUED":
                raise QuotationError("only an issued quotation can be rejected")
            conn.execute("UPDATE quotations SET status='REJECTED' WHERE id=?", (quotation_id,))
            _insert_history(conn, quotation_id, "ISSUED", "REJECTED", reason.strip(), actor_id,
                            {"event": "REJECTED"})
            _audit(conn, "QUOTATION_REJECTED", quotation_id, actor_id, reason.strip())

    def cancel_quotation(self, quotation_id: int, *, reason: str,
                         actor_id: int | None = None) -> None:
        if not reason or not reason.strip():
            raise QuotationError("cancellation reason is required")
        self._require(actor_id, "cancel_quotation")
        with self.db.transaction() as conn:
            row = _quotation_row(conn, quotation_id)
            if row["status"] == "ACCEPTED":
                raise QuotationError("an accepted quotation cannot be cancelled (OQ-QUO-005)")
            if row["status"] not in {"DRAFT", "ISSUED"}:
                raise QuotationError(f"quotation in status {row['status']} cannot be cancelled")
            conn.execute(
                "UPDATE quotations SET status='CANCELLED',cancellation_reason=? WHERE id=?",
                (reason.strip(), quotation_id),
            )
            _insert_history(conn, quotation_id, row["status"], "CANCELLED", reason.strip(),
                            actor_id, {"event": "CANCELLED"})
            _audit(conn, "QUOTATION_CANCELLED", quotation_id, actor_id, reason.strip())

    def create_revision(self, quotation_id: int, *, actor_id: int | None = None) -> int:
        self._require(actor_id, "create_quotation_revision")
        with self.db.transaction() as conn:
            source = _quotation_row(conn, quotation_id)
            if source["status"] not in {"ISSUED", "ACCEPTED"}:
                raise QuotationError("only an issued or accepted quotation can be revised")
            latest = conn.execute(
                "SELECT MAX(version_no) FROM quotations WHERE series_id=?",
                (source["series_id"],),
            ).fetchone()[0]
            if source["version_no"] != latest:
                raise QuotationError("only the latest quotation version can be revised")
            active = conn.execute(
                "SELECT s.id FROM sales s JOIN quotations q ON q.id=s.source_quotation_id "
                "WHERE q.series_id=? AND s.status <> 'CANCELLED'",
                (source["series_id"],),
            ).fetchone()
            if active is not None:
                raise QuotationError(
                    "cannot create a revision while an active sale draft exists"
                )
            series = conn.execute(
                "SELECT * FROM quotation_series WHERE id=?", (source["series_id"],)
            ).fetchone()
            version_no = int(latest) + 1
            document_no = f"{series['base_no']}-R{version_no}"
            root_id = source["root_quotation_id"] or source["id"]
            cur = conn.execute(
                "INSERT INTO quotations(document_no,series_id,root_quotation_id,version_no,"
                "customer_id,project_id,price_list_id,status,issue_date,valid_until,"
                "subtotal_piastres,item_discount_piastres,invoice_discount_piastres,"
                "total_piastres,notes,terms,created_by) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (document_no, source["series_id"], root_id, version_no,
                 source["customer_id"], source["project_id"], source["price_list_id"],
                 "DRAFT", source["issue_date"], source["valid_until"],
                 source["subtotal_piastres"], source["item_discount_piastres"],
                 source["invoice_discount_piastres"], source["total_piastres"],
                 source["notes"], source["terms"], actor_id),
            )
            revision_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO quotation_lines(quotation_id,line_no,product_id,unit_id,"
                "quantity_micros,base_qty_micros,original_unit_price_micros,"
                "final_unit_price_micros,price_source,price_adjustment_reason,"
                "conversion_num,conversion_den,item_discount_piastres,"
                "invoice_discount_alloc_piastres,net_piastres,description_snapshot,"
                "product_code_snapshot,product_name_snapshot,unit_name_snapshot,project_id) "
                "SELECT ?,line_no,product_id,unit_id,quantity_micros,base_qty_micros,"
                "original_unit_price_micros,final_unit_price_micros,price_source,"
                "price_adjustment_reason,conversion_num,conversion_den,"
                "item_discount_piastres,invoice_discount_alloc_piastres,net_piastres,"
                "description_snapshot,product_code_snapshot,product_name_snapshot,"
                "unit_name_snapshot,project_id FROM quotation_lines WHERE quotation_id=?",
                (revision_id, quotation_id),
            )
            _insert_history(
                conn, revision_id, None, "DRAFT", f"revision of {source['document_no']}",
                actor_id, {"event": "REVISION_CREATED", "source_quotation_id": quotation_id},
            )
            _audit(conn, "QUOTATION_REVISION_CREATED", revision_id, actor_id,
                   source["document_no"])
            return revision_id

    # ------------------------------------------------------------------
    # Conversion to a sales DRAFT
    # ------------------------------------------------------------------

    def _advisory_shortages(self, conn, lines) -> list[dict]:
        demand: dict[int, dict] = {}
        for line in lines:
            product = self._product(conn, line["product_id"])
            entry = demand.get(product["id"])
            if entry is None:
                entry = {
                    "product_id": product["id"],
                    "requested": 0,
                    "available": available_stock(conn, product),
                }
                demand[product["id"]] = entry
            entry["requested"] += line["base_qty_micros"]
        shortages = []
        for entry in demand.values():
            if entry["requested"] > entry["available"]:
                shortages.append({
                    "product_id": entry["product_id"],
                    "requested": entry["requested"],
                    "available": entry["available"],
                    "shortage": entry["requested"] - entry["available"],
                })
        return shortages

    def convert_to_sale(self, quotation_id: int, *, payment_type: str = "CREDIT",
                        invoice_date: str | None = None, due_date: str | None = None,
                        financial_account_id: int | None = None,
                        override_expired: bool = False,
                        override_reason: str | None = None,
                        as_of: str | None = None,
                        actor_id: int | None = None) -> dict:
        self._require(actor_id, "convert_quotation_to_sale")
        if payment_type not in {"CASH", "CREDIT"}:
            raise QuotationError("payment type MIXED is deferred under OQ-SAL-003")
        invoice_date = invoice_date or date.today().isoformat()
        _as_date(invoice_date, "invoice_date")
        try:
            with self.db.transaction() as conn:
                row = _quotation_row(conn, quotation_id)
                if row["status"] not in {"ISSUED", "ACCEPTED"}:
                    raise QuotationError(
                        f"quotation in status {row['status']} cannot be converted"
                    )
                lines = conn.execute(
                    "SELECT * FROM quotation_lines WHERE quotation_id=? ORDER BY line_no",
                    (quotation_id,),
                ).fetchall()
                if not lines:
                    raise QuotationError("quotation has no lines")
                self._customer(conn, row["customer_id"])
                if row["project_id"] is not None:
                    self._project(conn, row["project_id"], row["customer_id"])
                expired = _is_expired_row(row, as_of)
                if expired:
                    reason = (override_reason or "").strip()
                    if not override_expired:
                        raise QuotationError(
                            "quotation is expired; override_expired_quotation and a reason "
                            "are required"
                        )
                    if actor_id is None:
                        raise PermissionDenied(
                            "expired quotation override requires an authorized user"
                        )
                    PermissionService(self.db).require(
                        actor_id, "override_expired_quotation"
                    )
                    if not reason:
                        raise QuotationError("expired quotation override requires a reason")
                if _active_sale(conn, quotation_id) is not None:
                    raise QuotationError("quotation already has an active sale draft")
                if payment_type == "CASH":
                    if financial_account_id is None:
                        raise QuotationError("cash sale requires a financial account")
                    self._financial_account(conn, financial_account_id)
                customer = self._customer(conn, row["customer_id"])
                document_no = next_number(
                    conn, "SALES_INVOICE", int(invoice_date[:4]), "SI-"
                )
                cur = conn.execute(
                    "INSERT INTO sales(document_no,customer_id,project_id,price_list_id,"
                    "sale_type,payment_type,status,invoice_date,due_date,financial_account_id,"
                    "subtotal_piastres,item_discount_piastres,invoice_discount_piastres,"
                    "total_piastres,source_quotation_id,created_by) "
                    "VALUES(?,?,?,?,?,?,'DRAFT',?,?,?,?,?,?,?,?,?)",
                    (document_no, row["customer_id"], row["project_id"],
                     row["price_list_id"], customer["customer_type"], payment_type,
                     invoice_date, due_date, financial_account_id,
                     row["subtotal_piastres"], row["item_discount_piastres"],
                     row["invoice_discount_piastres"], row["total_piastres"],
                     quotation_id, actor_id),
                )
                sales_id = int(cur.lastrowid)
                for line in lines:
                    product = self._product(conn, line["product_id"])
                    gross = line_total(line["final_unit_price_micros"], line["quantity_micros"])
                    expected_net = (gross - line["item_discount_piastres"]
                                    - line["invoice_discount_alloc_piastres"])
                    if expected_net != line["net_piastres"]:
                        raise QuotationError(
                            f"quotation line {line['line_no']} totals do not match"
                        )
                    conn.execute(
                        "INSERT INTO sales_lines(sales_id,line_no,product_id,unit_id,"
                        "quantity_micros,base_qty_micros,unit_price_micros,gross_piastres,"
                        "item_discount_piastres,invoice_discount_alloc_piastres,"
                        "net_piastres,cost_piastres,price_source,price_adjustment_reason,"
                        "conversion_num,conversion_den,tracking_mode,project_id,"
                        "quotation_line_id) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?)",
                        (sales_id, line["line_no"], line["product_id"], line["unit_id"],
                         line["quantity_micros"], line["base_qty_micros"],
                         line["final_unit_price_micros"], gross,
                         line["item_discount_piastres"],
                         line["invoice_discount_alloc_piastres"], line["net_piastres"],
                         line["price_source"], line["price_adjustment_reason"],
                         line["conversion_num"], line["conversion_den"],
                         product["tracking_mode"], line["project_id"], line["id"]),
                    )
                conn.execute(
                    "INSERT INTO sales_status_history(sales_id,from_status,to_status,"
                    "actor_user_id,occurred_at) VALUES(?,?,?,?,?)",
                    (sales_id, None, "DRAFT", actor_id, _now()),
                )
                metadata = {
                    "event": CONVERSION_CREATED_EVENT,
                    "sales_id": sales_id,
                    "status_before_conversion": row["status"],
                }
                if expired:
                    metadata.update({
                        "event": CONVERSION_CREATED_EVENT,
                        "expired_override": True,
                        "override_reason": (override_reason or "").strip(),
                    })
                _insert_history(
                    conn, quotation_id, row["status"], row["status"], None, actor_id,
                    metadata,
                )
                _audit(conn, "QUOTATION_CONVERTED_TO_DRAFT", quotation_id, actor_id,
                       f"sales {sales_id}")
                return {
                    "sales_id": sales_id,
                    "document_no": document_no,
                    "quotation_id": quotation_id,
                    "quotation_status": row["status"],
                    "expired_override": bool(expired),
                    "shortages": self._advisory_shortages(conn, lines),
                }
        except sqlite3.IntegrityError as exc:
            if "ux_sales_source_quotation_active" in str(exc) or "source_quotation_id" in str(exc):
                raise QuotationError("quotation already has an active sale draft") from exc
            raise

    # ------------------------------------------------------------------
    # Snapshots and reads
    # ------------------------------------------------------------------

    def _build_snapshot(self, conn, row, export=None, *,
                        template_version: str | None = None,
                        renderer_version: str | None = None,
                        logo_asset: dict | None = None) -> dict:
        store = conn.execute("SELECT * FROM store_profile WHERE id=1").fetchone()
        customer = conn.execute(
            "SELECT * FROM customers WHERE id=?", (row["customer_id"],)
        ).fetchone()
        project = None
        if row["project_id"] is not None:
            project = conn.execute(
                "SELECT * FROM projects WHERE id=?", (row["project_id"],)
            ).fetchone()
        lines = conn.execute(
            "SELECT * FROM quotation_lines WHERE quotation_id=? ORDER BY line_no",
            (row["id"],),
        ).fetchall()
        snapshot_lines = []
        for line in lines:
            snapshot_lines.append({
                "line_no": line["line_no"],
                "product_id": line["product_id"],
                "unit_id": line["unit_id"],
                "product_code": line["product_code_snapshot"],
                "product_name": line["product_name_snapshot"],
                "unit_name": line["unit_name_snapshot"],
                "description": line["description_snapshot"],
                "quantity_micros": line["quantity_micros"],
                "base_qty_micros": line["base_qty_micros"],
                "conversion_num": line["conversion_num"],
                "conversion_den": line["conversion_den"],
                "original_unit_price_micros": line["original_unit_price_micros"],
                "final_unit_price_micros": line["final_unit_price_micros"],
                "price_source": line["price_source"],
                "price_adjustment_reason": line["price_adjustment_reason"],
                "item_discount_piastres": line["item_discount_piastres"],
                "invoice_discount_alloc_piastres": line["invoice_discount_alloc_piastres"],
                "net_piastres": line["net_piastres"],
                "project_id": line["project_id"],
            })
        template = (export["template_version"] if export is not None
                    else template_version)
        renderer = (export["renderer_version"] if export is not None
                    else renderer_version)
        if not template or not renderer:
            raise QuotationError("template_version and renderer_version are required")
        snapshot = {
            "document_type": "QUOTATION",
            "document_id": row["id"],
            "document_no": row["document_no"],
            "version_no": row["version_no"],
            "root_quotation_id": row["root_quotation_id"],
            "status": "ISSUED",
            "issue_date": row["issue_date"],
            "valid_until": row["valid_until"],
            "currency": "EGP",
            "store": None if store is None else {
                "name_ar": store["name_ar"],
                "address": store["address"],
                "phone": store["phone"],
                "logo_path": store["logo_path"],
                "logo_asset": logo_asset,
            },
            "customer": {
                "id": customer["id"],
                "code": customer["code"],
                "name_ar": customer["name_ar"],
                "phone": customer["phone"],
                "address": customer["address"],
            },
            "project": None if project is None else {
                "id": project["id"],
                "code": project["code"],
                "name_ar": project["name_ar"],
            },
            "lines": snapshot_lines,
            "totals": {
                "subtotal_piastres": row["subtotal_piastres"],
                "item_discount_piastres": row["item_discount_piastres"],
                "invoice_discount_piastres": row["invoice_discount_piastres"],
                "total_piastres": row["total_piastres"],
            },
            "notes": row["notes"],
            "terms": row["terms"],
            "template_version": template,
            "renderer_version": renderer,
        }
        if export is not None:
            snapshot["official_export"] = {
                "id": export["id"],
                "export_type": export["export_type"],
                "relative_path": export["relative_path"],
                "sha256": export["sha256"],
                "file_size": export["file_size"],
            }
        return snapshot

    def build_snapshot_content(self, quotation_id: int, *,
                               template_version: str,
                               renderer_version: str,
                               logo_asset: dict | None = None) -> dict:
        """Build the renderable snapshot content without an export row.

        The renderer uses this content; `issue_quotation` later persists the
        final snapshot with the official export metadata.
        """
        with self.db.connect() as conn:
            row = _quotation_row(conn, quotation_id)
            return self._build_snapshot(
                conn, row, template_version=template_version,
                renderer_version=renderer_version, logo_asset=logo_asset,
            )

    def get_quotation(self, quotation_id: int) -> dict:
        with self.db.connect() as conn:
            row = _quotation_row(conn, quotation_id)
            lines = conn.execute(
                "SELECT * FROM quotation_lines WHERE quotation_id=? ORDER BY line_no",
                (quotation_id,),
            ).fetchall()
            history = conn.execute(
                "SELECT * FROM quotation_status_history WHERE quotation_id=? ORDER BY id",
                (quotation_id,),
            ).fetchall()
        result = dict(row)
        result["lines"] = [dict(line) for line in lines]
        result["history"] = [
            dict(item) | {"metadata": _load_metadata(item["metadata_json"])}
            for item in history
        ]
        result["expired"] = _is_expired_row(row)
        result["effective_status"] = (
            "EXPIRED" if result["expired"] and row["status"] in {"DRAFT", "ISSUED", "ACCEPTED"}
            else row["status"]
        )
        return result

    def status_history(self, quotation_id: int) -> list[dict]:
        return self.get_quotation(quotation_id)["history"]

    def is_expired(self, quotation_id: int, as_of: str | None = None) -> bool:
        with self.db.connect() as conn:
            row = _quotation_row(conn, quotation_id)
        return _is_expired_row(row, as_of)

    def effective_status(self, quotation_id: int, as_of: str | None = None) -> str:
        with self.db.connect() as conn:
            row = _quotation_row(conn, quotation_id)
        if (row["status"] in {"DRAFT", "ISSUED", "ACCEPTED"}
                and _is_expired_row(row, as_of)):
            return "EXPIRED"
        return row["status"]
