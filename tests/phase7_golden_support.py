"""Deterministic synthetic snapshot and golden manifest for the T-DOC-007 test.

The snapshot mirrors the owner-approved 2026-11-17 review layout: 45 synthetic
lines, 3 A4 pages (20/20/5), a fixed header/customer block, fixed notes/terms,
and reconciled totals.  Nothing here reads a database, clock, filesystem, or
random source, so the manifest can only change when the template changes.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from mizan.rendering import RENDERER_VERSION, TEMPLATE_VERSION
from pdf_probe import normalize_arabic, read_pdf

REVIEW_UNITS = ("12.50", "14.75", "17.00", "19.25", "21.50", "23.75", "26.00")
REVIEW_QUANTITIES = ("1", "1.5", "2")
REVIEW_PRODUCTS = (
    "كابل نحاس مرن اصطناعي",
    "مفتاح جدار أبيض اصطناعي",
    "قاطع كهربائي اصطناعي",
    "علبة توصيل اصطناعية",
    "شريط عزل اصطناعي",
)


def _piastres(value: Decimal) -> int:
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def build_review_snapshot() -> dict:
    """Build the synthetic 45-line quotation used for the approved template."""
    lines = []
    gross_total = 0
    discount_total = 0
    net_total = 0
    for index in range(45):
        quantity = Decimal(REVIEW_QUANTITIES[index % 3])
        unit_price = Decimal(REVIEW_UNITS[index % 7])
        discount = Decimal(index % 5)
        gross_piastres = _piastres(quantity * unit_price)
        discount_piastres = _piastres(discount)
        net_piastres = _piastres(quantity * unit_price - discount)
        gross_total += gross_piastres
        discount_total += discount_piastres
        net_total += net_piastres
        lines.append({
            "line_no": index + 1,
            "description": f"{REVIEW_PRODUCTS[index % 5]} — بند مراجعة {index + 1:02d}",
            "unit_name": "متر",
            "quantity_micros": int(quantity * 1_000_000),
            "final_unit_price_micros": int(unit_price * 1_000_000),
            "item_discount_piastres": discount_piastres,
            "net_piastres": net_piastres,
        })

    snapshot = {
        "document_no": "REVIEW-Q-0001",
        "issue_date": "2026-11-17",
        "valid_until": "2026-12-17",
        "store": {
            "name_ar": "متجر المراجعة — ميزان",
            "address": "عنوان اصطناعي للعرض فقط، القاهرة",
            "phone": "01000000000",
        },
        "customer": {
            "name_ar": "عميل المراجعة الاصطناعي",
            "code": "C-RETAIL",
            "phone": "01100000000",
            "address": "عنوان عميل اصطناعي — الجيزة",
        },
        "lines": lines,
        "totals": {
            "subtotal_piastres": net_total + discount_total,
            "item_discount_piastres": discount_total,
            "invoice_discount_piastres": 0,
            "total_piastres": net_total,
        },
        "notes": (
            "ملاحظات مراجعة اصطناعية: هذه البيانات للعرض فقط ولا تمثل عميلاً "
            "أو متجرًا حقيقيًا."
        ),
        "terms": (
            "شروط مراجعة اصطناعية: صالحة لمراجعة التخطيط فقط، وليست عرض سعر "
            "رسميًا."
        ),
    }
    assert gross_total - discount_total == net_total
    assert gross_total == 128141 and discount_total == 9000 and net_total == 119141
    return snapshot


def canonical_page_text(text: str) -> str:
    """Normalize Arabic forms and collapse layout whitespace.

    Collapsing whitespace keeps the golden independent of line-break details
    while still locking the text content and run order.
    """
    return " ".join(normalize_arabic(text).split())


def build_golden_manifest(renderer, snapshot: dict, pdf_path) -> dict:
    report = read_pdf(pdf_path)
    plans = renderer.paginate(snapshot)
    if len(plans) != report.page_count:
        raise AssertionError(
            f"page plans ({len(plans)}) and rendered pages ({report.page_count}) differ"
        )
    return {
        "schema": 1,
        "template_version": TEMPLATE_VERSION,
        "renderer_version": RENDERER_VERSION,
        "page_width_pt": round(report.media_boxes[0][0], 3),
        "page_height_pt": round(report.media_boxes[0][1], 3),
        "page_count": report.page_count,
        "pages": [
            {
                "line_numbers": [row["line_no"] for row in plan.rows],
                "has_summary": plan.include_summary,
                "text": canonical_page_text(report.page_texts[index]),
            }
            for index, plan in enumerate(plans)
        ],
    }
