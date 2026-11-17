"""Shared Phase 6 fixtures/helpers.  This module is intentionally not collected."""

from __future__ import annotations

from dataclasses import dataclass

from mizan.accounting import AccountingService
from mizan.auth import AuthService
from mizan.master import MasterDataService
from mizan.purchasing import PurchasingService
from mizan.sales import SalesService


@dataclass
class Shop:
    db: object
    master: MasterDataService
    accounting: AccountingService
    auth: AuthService
    purchasing: PurchasingService
    sales: SalesService
    meter: int
    piece: int
    carton: int
    lot_product: int
    reel_product: int
    supplier: int
    retail: int
    wholesale: int
    contractor: int
    project: int
    price_list: int
    cash: int
    bank: int
    owner: int
    manager: int
    cashier: int

    def purchase(self, qty: int, price: int, *, product: int | None = None,
                 date: str = "2026-11-01", reel_lengths: list[int] | None = None) -> int:
        invoice = self.purchasing.create_invoice(
            self.supplier,
            date,
            [{
                "product_id": product or self.lot_product,
                "unit_id": self.meter,
                "quantity_micros": qty,
                "unit_price_micros": price,
                "reel_lengths_micros": reel_lengths,
            }],
        )
        self.purchasing.post_invoice(invoice)
        return invoice

    def sale(self, qty: int, price: int, *, customer: int | None = None,
             payment_type: str = "CREDIT", date: str = "2026-11-10",
             lines: list[dict] | None = None, invoice_discount: int = 0,
             price_list_id: int | None = None, project_id: int | None = None,
             financial_account_id: int | None = None, actor: int | None = None,
             product: int | None = None) -> int:
        customer = customer or self.retail
        if lines is None:
            line = {
                "product_id": product or self.lot_product,
                "unit_id": self.meter,
                "quantity_micros": qty,
            }
            if price is not None:
                line["unit_price_micros"] = price
            lines = [line]
        return self.sales.create_invoice(
            customer,
            date,
            lines,
            payment_type=payment_type,
            invoice_discount_piastres=invoice_discount,
            price_list_id=price_list_id,
            project_id=project_id,
            financial_account_id=financial_account_id or (self.cash if payment_type == "CASH" else None),
            actor_id=actor,
        )


def build_shop(db) -> Shop:
    master = MasterDataService(db)
    master.create_unit("METER", "متر", True)
    master.create_unit("PIECE", "قطعة", False)
    master.create_unit("CARTON", "كرتونة", True)

    lot_product = master.create_product("WIRE-LOT", "سلك LOT", "METER", "LOT")
    reel_product = master.create_product("CABLE-REEL", "كابل بكرة", "METER", "REEL")
    master.add_conversion(lot_product, "CARTON", 12, 1)
    supplier = master.create_supplier("SUP-1", "مورد")

    retail = master.create_customer("C-RETAIL", "تجزئة", "RETAIL")
    wholesale = master.create_customer("C-WHOLESALE", "جملة", "WHOLESALE")
    contractor = master.create_customer("C-CONTRACTOR", "مقاول", "CONTRACTOR")
    project = master.create_project(contractor, "PRJ-1", "مشروع")

    price_list = master.create_price_list("WHOLESALE", "جملة", "WHOLESALE")
    meter = db.scalar("SELECT id FROM units WHERE code='METER'")
    piece = db.scalar("SELECT id FROM units WHERE code='PIECE'")
    carton = db.scalar("SELECT id FROM units WHERE code='CARTON'")

    accounting = AccountingService(db)
    accounting.seed_coa()
    cash = accounting.create_financial_account("CASH-1", "نقدية", "CASH", "1010")
    bank = accounting.create_financial_account("BANK-1", "بنك", "BANK", "1020")

    auth = AuthService(db)
    auth.seed_defaults()
    owner = auth.create_user("owner", "Owner", "owner-pass", ["OWNER"])
    manager = auth.create_user("manager", "Manager", "manager-pass", ["MANAGER"])
    cashier = auth.create_user("cashier", "Cashier", "cashier-pass", ["CASHIER"])

    # A return-only role without refund permission keeps the OQ-SAL-007
    # permission check testable.
    with db.transaction() as conn:
        conn.execute("INSERT INTO roles(code,name_ar) VALUES('RETURNS_ONLY','مرتجع فقط')")
        role_id = conn.execute("SELECT id FROM roles WHERE code='RETURNS_ONLY'").fetchone()["id"]
        for code in ("create_sales_draft", "post_sales", "post_sales_return",
                     "view_customer_balance", "manage_customer_receipts"):
            conn.execute(
                "INSERT INTO role_permissions(role_id,permission_id) "
                "SELECT ?,id FROM permissions WHERE code=?",
                (role_id, code),
            )
    auth.create_user("returns", "Returns", "returns-pass", ["RETURNS_ONLY"])

    purchasing = PurchasingService(db)
    sales = SalesService(db)
    return Shop(
        db=db, master=master, accounting=accounting, auth=auth,
        purchasing=purchasing, sales=sales,
        meter=meter, piece=piece, carton=carton,
        lot_product=lot_product, reel_product=reel_product, supplier=supplier,
        retail=retail, wholesale=wholesale, contractor=contractor, project=project,
        price_list=price_list, cash=cash, bank=bank,
        owner=owner, manager=manager, cashier=cashier,
    )
