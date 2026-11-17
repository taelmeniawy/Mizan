from __future__ import annotations


def round_half_up(num: int, den: int) -> int:
    """Half-up rounding for non-negative numerator and positive denominator."""
    if num < 0 or den <= 0:
        raise ValueError("round_half_up expects num >= 0 and den > 0")
    return (2 * num + den) // (2 * den)


def line_total(price_micros: int, quantity_micros: int) -> int:
    """Round price (micro-EGP/unit) x quantity (micro-units) to piastres, half up."""
    if price_micros < 0 or quantity_micros <= 0:
        raise ValueError("invalid price or quantity")
    return (2 * price_micros * quantity_micros + 10_000_000_000) // 20_000_000_000


def t_layer(original_qty: int, original_cost: int, position: int) -> int:
    if original_qty <= 0 or original_cost < 0 or position < 0 or position > original_qty:
        raise ValueError("invalid layer position")
    return round_half_up(original_cost * position, original_qty)


def outflow_cost(original_qty: int, original_cost: int, start: int, quantity: int) -> int:
    if quantity <= 0 or start + quantity > original_qty:
        raise ValueError("invalid layer outflow")
    return t_layer(original_qty, original_cost, start + quantity) - t_layer(original_qty, original_cost, start)


def return_cost(original_alloc_cost: int, original_alloc_qty: int, old_returned: int, new_returned: int) -> int:
    if original_alloc_qty <= 0 or original_alloc_cost < 0:
        raise ValueError("invalid allocation")
    if old_returned < 0 or new_returned < old_returned or new_returned > original_alloc_qty:
        raise ValueError("invalid cumulative return quantity")
    start = round_half_up(original_alloc_cost * old_returned, original_alloc_qty)
    end = round_half_up(original_alloc_cost * new_returned, original_alloc_qty)
    return end - start


def remaining_cost(original_qty: int, original_cost: int, position: int) -> int:
    return original_cost - t_layer(original_qty, original_cost, position)


def validate_layer(original_qty: int, original_cost: int, position: int, remaining_qty: int, remaining_cost_value: int) -> None:
    if original_qty <= 0 or original_cost < 0:
        raise ValueError("layer original values are invalid")
    if position < 0 or position > original_qty:
        raise ValueError("layer position out of range")
    if remaining_qty < 0 or remaining_qty != original_qty - position:
        raise ValueError("remaining quantity does not match original_qty - position")
    expected = remaining_cost(original_qty, original_cost, position)
    if remaining_cost_value < 0 or remaining_cost_value != expected:
        raise ValueError("remaining cost does not match original_cost - T_L(position)")


def allocate_largest_remainder(total: int, weights: list[int]) -> list[int]:
    """Allocate an integer total proportionally; ties go to the earliest index."""
    if total < 0:
        raise ValueError("total must be non-negative")
    if not weights:
        return []
    if any(w < 0 for w in weights):
        raise ValueError("weights must be non-negative")
    denominator = sum(weights)
    if denominator == 0:
        weights = [1] * len(weights)
        denominator = len(weights)
    base = [(total * w) // denominator for w in weights]
    remainder = total - sum(base)
    order = sorted(
        range(len(weights)),
        key=lambda i: ((total * weights[i]) % denominator, -i),
        reverse=True,
    )
    for index in order[:remainder]:
        base[index] += 1
    return base
