"""Shared Phase 7 fixtures/helpers.  This module is intentionally not collected."""

from __future__ import annotations

from dataclasses import dataclass

from mizan.documents import DocumentService
from mizan.quotations import QuotationService
from phase6_support import Shop, build_shop


TEST_SHA256 = "a" * 64


@dataclass
class QuoteShop:
    shop: Shop
    quotes: QuotationService
    documents: DocumentService

    @property
    def db(self):
        return self.shop.db

    def line(self, *, product_id: int | None = None, unit_id: int | None = None,
             quantity_micros: int = 1_000_000, unit_price_micros: int | None = None,
             item_discount_piastres: int = 0, price_adjustment_reason: str | None = None,
             description: str | None = None, project_id: int | None = None) -> dict:
        line = {
            "product_id": product_id or self.shop.lot_product,
            "unit_id": unit_id or self.shop.meter,
            "quantity_micros": quantity_micros,
            "item_discount_piastres": item_discount_piastres,
        }
        if unit_price_micros is not None:
            line["unit_price_micros"] = unit_price_micros
        if price_adjustment_reason is not None:
            line["price_adjustment_reason"] = price_adjustment_reason
        if description is not None:
            line["description"] = description
        if project_id is not None:
            line["project_id"] = project_id
        return line

    def create_quote(self, *, customer_id: int | None = None,
                     issue_date: str = "2026-11-10",
                     valid_until: str | None = "2026-11-30",
                     lines: list[dict] | None = None,
                     project_id: int | None = None,
                     price_list_id: int | None = None,
                     invoice_discount_piastres: int = 0,
                     notes: str | None = None,
                     terms: str | None = None,
                     actor_id: int | None = None,
                     product_id: int | None = None,
                     quantity_micros: int = 1_000_000,
                     unit_price_micros: int | None = None) -> int:
        if lines is None:
            lines = [self.line(
                product_id=product_id, quantity_micros=quantity_micros,
                unit_price_micros=unit_price_micros,
            )]
        return self.quotes.create_quotation(
            customer_id or self.shop.retail,
            issue_date,
            lines,
            valid_until=valid_until,
            project_id=project_id,
            price_list_id=price_list_id,
            invoice_discount_piastres=invoice_discount_piastres,
            notes=notes,
            terms=terms,
            actor_id=actor_id if actor_id is not None else self.shop.manager,
        )

    def export_for(self, quotation_id: int, *, is_official: bool = True,
                   generated_by: int | None = None, sha256: str = TEST_SHA256,
                   template_version: str = "TEST_DOUBLE_TEMPLATE",
                   renderer_version: str = "TEST_DOUBLE_NO_RENDERER") -> int:
        """Register metadata standing in for the future PDF renderer.

        No PDF file is produced, opened, or validated here.  This test double
        only lets the quotation state machine reach ISSUED so downstream
        renderer-independent behaviour can be exercised.  It is not evidence
        for the ADR-0004 PDF/A4/printer gate.
        """
        return self.documents.record_export(
            document_type="QUOTATION",
            document_id=quotation_id,
            export_type="PDF",
            relative_path=f"quotations/{quotation_id}.pdf",
            sha256=sha256,
            file_size=1234,
            template_version=template_version,
            renderer_version=renderer_version,
            generated_by=generated_by or self.shop.manager,
            is_official=is_official,
        )

    def issue(self, quotation_id: int, *, actor_id: int | None = None,
              export_id: int | None = None) -> int:
        if export_id is None:
            export_id = self.export_for(quotation_id)
        self.quotes.issue_quotation(
            quotation_id,
            official_export_id=export_id,
            actor_id=actor_id if actor_id is not None else self.shop.manager,
        )
        return export_id

    def convert(self, quotation_id: int, *, payment_type: str = "CREDIT",
                invoice_date: str = "2026-11-17", due_date: str | None = None,
                financial_account_id: int | None = None,
                override_expired: bool = False,
                override_reason: str | None = None,
                as_of: str | None = "2026-11-17",
                actor_id: int | None = None) -> dict:
        return self.quotes.convert_to_sale(
            quotation_id,
            payment_type=payment_type,
            invoice_date=invoice_date,
            due_date=due_date,
            financial_account_id=financial_account_id,
            override_expired=override_expired,
            override_reason=override_reason,
            as_of=as_of,
            actor_id=actor_id if actor_id is not None else self.shop.manager,
        )


def build_quote_shop(db) -> QuoteShop:
    shop = build_shop(db)
    with db.transaction() as conn:
        for product_id, unit_id, price in (
            (shop.lot_product, shop.meter, 30_000_000),
            (shop.lot_product, shop.carton, 360_000_000),
            (shop.reel_product, shop.meter, 40_000_000),
        ):
            conn.execute(
                "INSERT INTO product_default_prices(product_id,unit_id,price_micros) "
                "VALUES(?,?,?)",
                (product_id, unit_id, price),
            )
        conn.execute(
            "INSERT INTO store_profile(id,name_ar,address,phone,logo_path) "
            "VALUES(1,'متجر ميزان','القاهرة','01000000000','/assets/logo.png')"
        )
        conn.execute(
            "UPDATE customers SET phone='01111111111',address='عنوان تجزئة' WHERE id=?",
            (shop.retail,),
        )
    return QuoteShop(shop=shop, quotes=QuotationService(db), documents=DocumentService(db))
