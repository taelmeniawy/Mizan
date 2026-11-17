"""Shared price resolution and quantity conversion.

The quotation and sales services must resolve prices and convert units the same
way; keeping the queries here avoids a second, drifting implementation.
"""

from __future__ import annotations


def resolve_price(conn, customer_id: int, product_id: int, unit_id: int,
                  price_list_id: int | None) -> tuple[int | None, str | None]:
    """Resolve customer price, then price-list price, then product default."""
    row = conn.execute(
        "SELECT price_micros FROM customer_prices WHERE customer_id=? AND product_id=? AND unit_id=?",
        (customer_id, product_id, unit_id),
    ).fetchone()
    if row:
        return int(row["price_micros"]), "CUSTOMER"
    if price_list_id is not None:
        row = conn.execute(
            "SELECT price_micros FROM price_list_items "
            "WHERE price_list_id=? AND product_id=? AND unit_id=?",
            (price_list_id, product_id, unit_id),
        ).fetchone()
        if row:
            return int(row["price_micros"]), "PRICE_LIST"
    row = conn.execute(
        "SELECT price_micros FROM product_default_prices WHERE product_id=? AND unit_id=?",
        (product_id, unit_id),
    ).fetchone()
    if row:
        return int(row["price_micros"]), "DEFAULT"
    return None, None


def unit_conversion(conn, product, unit_id: int) -> tuple[int, int]:
    """Return the product/unit conversion factor as (numerator, denominator)."""
    if unit_id == product["base_unit_id"]:
        return 1, 1
    row = conn.execute(
        "SELECT factor_num,factor_den FROM product_unit_conversions "
        "WHERE product_id=? AND unit_id=?",
        (product["id"], unit_id),
    ).fetchone()
    if row is None:
        raise ValueError("no conversion for unit")
    return int(row["factor_num"]), int(row["factor_den"])


def base_quantity(conn, product, unit_id: int, quantity_micros: int) -> tuple[int, int, int]:
    """Convert a sale/document quantity to base micro-units."""
    if quantity_micros <= 0:
        raise ValueError("quantity must be positive")
    num, den = unit_conversion(conn, product, unit_id)
    base, remainder = divmod(quantity_micros * num, den)
    if remainder:
        raise ValueError("conversion does not produce an integer base quantity")
    unit = conn.execute(
        "SELECT allows_fraction FROM units WHERE id=?", (product["base_unit_id"],)
    ).fetchone()
    if unit is None:
        raise ValueError("base unit not found")
    if not unit["allows_fraction"] and base % 1_000_000:
        raise ValueError("base unit does not allow fractional quantities")
    return base, num, den
