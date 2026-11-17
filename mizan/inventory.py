from __future__ import annotations

from datetime import datetime, timezone

from .lcp import outflow_cost, remaining_cost
from .numbering import next_number
from .periods import require_open_period


class InventoryError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def create_layer(conn, lot_id: int, reel_id: int | None, layer_type: str, qty: int,
                 cost: int, received_at: str, suborder: int = 0,
                 parent_issue_allocation_id: int | None = None) -> int:
    if qty <= 0 or cost < 0:
        raise ValueError("invalid layer quantity/cost")
    cur = conn.execute(
        "INSERT INTO inventory_cost_layers(lot_id,reel_id,layer_type,parent_issue_allocation_id,"
        "original_qty_micros,original_cost_piastres,position_micros,remaining_qty_micros,"
        "remaining_cost_piastres,fifo_anchor_at,fifo_suborder) "
        "VALUES(?,?,?,?,?,?,0,?,?,?,?)",
        (lot_id, reel_id, layer_type, parent_issue_allocation_id, qty, cost,
         qty, cost, received_at, suborder),
    )
    return int(cur.lastrowid)


def create_movement(conn, movement_type: str, product_id: int, lot_id: int | None, reel_id: int | None,
                    quantity: int, cost: int, direction: str, source_type: str, source_id: int,
                    occurred_at: str, actor_id: int | None, source_line_id: int | None = None) -> int:
    require_open_period(conn, occurred_at)
    movement_no = next_number(conn, "STOCK_MOVEMENT", int(occurred_at[:4]), "SM-")
    cur = conn.execute(
        "INSERT INTO stock_movements(movement_no,movement_type,product_id,lot_id,reel_id,quantity_micros,"
        "cost_piastres,direction,source_type,source_id,source_line_id,occurred_at,posted_at,created_by) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (movement_no, movement_type, product_id, lot_id, reel_id, quantity, cost, direction,
         source_type, source_id, source_line_id, occurred_at, _now(), actor_id),
    )
    return int(cur.lastrowid)


def consume_layer(conn, layer, quantity: int) -> dict:
    if quantity <= 0 or quantity > layer["remaining_qty_micros"]:
        raise InventoryError("invalid layer consumption")
    original_qty = layer["original_qty_micros"]
    original_cost = layer["original_cost_piastres"]
    cost = outflow_cost(original_qty, original_cost, layer["position_micros"], quantity)
    if cost < 0:
        raise InventoryError("layer consumption produced a negative cost")
    start = layer["position_micros"]
    end = start + quantity
    new_remaining_qty = original_qty - end
    new_remaining_cost = remaining_cost(original_qty, original_cost, end)
    conn.execute(
        "UPDATE inventory_cost_layers SET position_micros=?,remaining_qty_micros=?,remaining_cost_piastres=?,"
        "status=? WHERE id=?",
        (end, new_remaining_qty, new_remaining_cost,
         "EXHAUSTED" if new_remaining_qty == 0 else "OPEN", layer["id"]),
    )
    return {
        "layer": layer,
        "quantity": quantity,
        "cost": cost,
        "start": start,
        "end": end,
        "remaining_qty": new_remaining_qty,
        "remaining_cost": new_remaining_cost,
    }


def consume_lot_layers(conn, lot_id: int, quantity: int, reel_id: int | None = None) -> list[dict]:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    layers = conn.execute(
        "SELECT * FROM inventory_cost_layers WHERE lot_id=? AND (? IS NULL OR reel_id=?) "
        "AND remaining_qty_micros > 0 AND status='OPEN' "
        "ORDER BY fifo_anchor_at, fifo_suborder, id",
        (lot_id, reel_id, reel_id),
    ).fetchall()
    available = sum(layer["remaining_qty_micros"] for layer in layers)
    if available < quantity:
        raise InventoryError(f"insufficient stock: requested {quantity}, available {available}")
    result = []
    left = quantity
    for layer in layers:
        if left <= 0:
            break
        take = min(left, layer["remaining_qty_micros"])
        result.append(consume_layer(conn, layer, take))
        left -= take
    return result


def available_stock(conn, product) -> int:
    """Return sellable base-quantity on hand for a LOT/REEL product."""
    if product["tracking_mode"] == "LOT":
        return int(conn.execute(
            "SELECT COALESCE(SUM(l.remaining_qty_micros),0) FROM inventory_cost_layers l "
            "JOIN purchase_lots p ON p.id=l.lot_id "
            "WHERE p.product_id=? AND l.reel_id IS NULL AND l.status='OPEN' "
            "AND l.remaining_qty_micros>0",
            (product["id"],),
        ).fetchone()[0])
    return int(conn.execute(
        "SELECT COALESCE(SUM(l.remaining_qty_micros),0) FROM inventory_cost_layers l "
        "JOIN purchase_lots p ON p.id=l.lot_id "
        "JOIN reels r ON r.id=l.reel_id "
        "WHERE p.product_id=? AND l.reel_id IS NOT NULL AND l.status='OPEN' "
        "AND l.remaining_qty_micros>0 AND r.status NOT IN ('DAMAGED','RETURNED')",
        (product["id"],),
    ).fetchone()[0])


def refresh_lot_status(conn, lot_id: int) -> None:
    remaining = conn.execute(
        "SELECT COALESCE(SUM(remaining_qty_micros),0) FROM inventory_cost_layers WHERE lot_id=?",
        (lot_id,),
    ).fetchone()[0]
    conn.execute(
        "UPDATE purchase_lots SET status=? WHERE id=?",
        ("EXHAUSTED" if remaining == 0 else "OPEN", lot_id),
    )


def refresh_reel_status(conn, reel_id: int, damaged: bool = False) -> str:
    remaining = conn.execute(
        "SELECT COALESCE(SUM(remaining_qty_micros),0) FROM inventory_cost_layers WHERE reel_id=?",
        (reel_id,),
    ).fetchone()[0]
    if remaining == 0:
        status = "DAMAGED" if damaged else "EMPTY"
    else:
        status = "OPENED"
    conn.execute(
        "UPDATE reels SET status=?,version=version+1 WHERE id=?",
        (status, reel_id),
    )
    return status
