from __future__ import annotations

from .db import Database
from .pricing import unit_conversion


_PRICE_KEYS = {
    "product_default_prices": ("product_id", "unit_id"),
    "price_list_items": ("price_list_id", "product_id", "unit_id"),
    "customer_prices": ("customer_id", "product_id", "unit_id"),
}


class MasterDataService:
    def __init__(self, db: Database):
        self.db = db

    def create_unit(self, code: str, name_ar: str, allows_fraction: bool) -> int:
        with self.db.transaction() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO units(code,name_ar,allows_fraction) VALUES(?,?,?)",
                    (code, name_ar, int(allows_fraction)),
                )
            except Exception as exc:
                raise ValueError(f"cannot create unit: {exc}") from exc
            return int(cur.lastrowid)

    def create_product(self, code: str, name_ar: str, base_unit_code: str, tracking_mode: str) -> int:
        if tracking_mode not in {"LOT", "REEL"}:
            raise ValueError("tracking_mode must be LOT or REEL")
        with self.db.transaction() as conn:
            unit = conn.execute("SELECT id FROM units WHERE code=?", (base_unit_code,)).fetchone()
            if unit is None:
                raise ValueError("base unit not found")
            try:
                cur = conn.execute(
                    "INSERT INTO products(code,name_ar,base_unit_id,tracking_mode) VALUES(?,?,?,?)",
                    (code, name_ar, unit["id"], tracking_mode),
                )
            except Exception as exc:
                raise ValueError(f"cannot create product: {exc}") from exc
            return int(cur.lastrowid)

    def add_conversion(self, product_id: int, unit_code: str, factor_num: int, factor_den: int) -> None:
        if factor_num <= 0 or factor_den <= 0:
            raise ValueError("conversion factors must be positive")
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM products WHERE id=?", (product_id,)).fetchone() is None:
                raise ValueError("product not found")
            unit = conn.execute("SELECT id FROM units WHERE code=?", (unit_code,)).fetchone()
            if unit is None:
                raise ValueError("unit not found")
            conn.execute(
                "INSERT INTO product_unit_conversions(product_id,unit_id,factor_num,factor_den) VALUES(?,?,?,?)",
                (product_id, unit["id"], factor_num, factor_den),
            )

    def convert_to_base(self, product_id: int, unit_code: str, quantity_micros: int) -> int:
        if quantity_micros <= 0:
            raise ValueError("quantity must be positive")
        with self.db.connect() as conn:
            product = conn.execute(
                "SELECT p.id,p.base_unit_id,u.allows_fraction FROM products p "
                "JOIN units u ON u.id=p.base_unit_id WHERE p.id=?",
                (product_id,),
            ).fetchone()
            if product is None:
                raise ValueError("product not found")
            unit = conn.execute("SELECT id FROM units WHERE code=?", (unit_code,)).fetchone()
            if unit is None:
                raise ValueError("unit not found")
            if unit["id"] == product["base_unit_id"]:
                num, den = 1, 1
            else:
                conversion = conn.execute(
                    "SELECT factor_num,factor_den FROM product_unit_conversions "
                    "WHERE product_id=? AND unit_id=?",
                    (product_id, unit["id"]),
                ).fetchone()
                if conversion is None:
                    raise ValueError("no conversion for unit")
                num, den = conversion["factor_num"], conversion["factor_den"]
        base = quantity_micros * num
        base_qty, remainder = divmod(base, den)
        if remainder:
            raise ValueError("conversion does not produce an integer base quantity")
        if not product["allows_fraction"] and base_qty % 1_000_000:
            raise ValueError("base unit does not allow fractional quantities")
        return base_qty

    def create_customer(self, code: str, name_ar: str, customer_type: str = "RETAIL") -> int:
        if customer_type not in {"RETAIL", "WHOLESALE", "CONTRACTOR"}:
            raise ValueError("invalid customer type")
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO customers(code,name_ar,customer_type) VALUES(?,?,?)",
                (code, name_ar, customer_type),
            )
            return int(cur.lastrowid)

    def create_supplier(self, code: str, name_ar: str) -> int:
        with self.db.transaction() as conn:
            cur = conn.execute("INSERT INTO suppliers(code,name_ar) VALUES(?,?)", (code, name_ar))
            return int(cur.lastrowid)

    def create_project(self, customer_id: int, code: str, name_ar: str) -> int:
        with self.db.transaction() as conn:
            customer = conn.execute("SELECT customer_type FROM customers WHERE id=?", (customer_id,)).fetchone()
            if customer is None or customer["customer_type"] != "CONTRACTOR":
                raise ValueError("project customer must be a CONTRACTOR")
            cur = conn.execute(
                "INSERT INTO projects(customer_id,code,name_ar) VALUES(?,?,?)",
                (customer_id, code, name_ar),
            )
            return int(cur.lastrowid)

    def create_price_list(self, code: str, name_ar: str, list_type: str = "GENERAL") -> int:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO price_lists(code,name_ar,list_type) VALUES(?,?,?)",
                (code, name_ar, list_type),
            )
            return int(cur.lastrowid)

    def set_price(self, table: str, *, update_existing: bool = False, **values: int) -> None:
        keys = _PRICE_KEYS.get(table)
        if keys is None:
            raise ValueError("unsupported price table")
        columns = (*keys, "price_micros")
        if values.keys() != set(columns):
            raise ValueError("invalid fields for price table")
        price = values["price_micros"]
        if type(price) is not int or not 0 <= price <= 2**63 - 1:
            raise ValueError("price must be a nonnegative SQLite integer")
        placeholders = ", ".join("?" for _ in columns)
        sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
        if update_existing:
            sql += f" ON CONFLICT ({', '.join(keys)}) DO UPDATE SET price_micros=excluded.price_micros"
        with self.db.transaction() as conn:
            product = conn.execute(
                "SELECT id,base_unit_id FROM products WHERE id=?", (values["product_id"],),
            ).fetchone()
            if product is None:
                raise ValueError("product not found")
            unit_conversion(conn, product, values["unit_id"])
            conn.execute(sql, tuple(values[column] for column in columns))

    def resolve_price(self, customer_id: int | None, product_id: int, unit_id: int, price_list_id: int | None = None) -> int:
        with self.db.connect() as conn:
            if customer_id is not None:
                row = conn.execute(
                    "SELECT price_micros FROM customer_prices WHERE customer_id=? AND product_id=? AND unit_id=?",
                    (customer_id, product_id, unit_id),
                ).fetchone()
                if row:
                    return row["price_micros"]
            if price_list_id is not None:
                row = conn.execute(
                    "SELECT price_micros FROM price_list_items WHERE price_list_id=? AND product_id=? AND unit_id=?",
                    (price_list_id, product_id, unit_id),
                ).fetchone()
                if row:
                    return row["price_micros"]
            row = conn.execute(
                "SELECT price_micros FROM product_default_prices WHERE product_id=? AND unit_id=?",
                (product_id, unit_id),
            ).fetchone()
            if row:
                return row["price_micros"]
        raise ValueError("no price found")

    def deactivate_product(self, product_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE products SET is_active=0 WHERE id=?", (product_id,))
