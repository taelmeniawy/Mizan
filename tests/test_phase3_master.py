import pytest

from mizan.master import MasterDataService


@pytest.fixture()
def master(db):
    service = MasterDataService(db)
    service.create_unit("PIECE", "قطعة", False)
    service.create_unit("METER", "متر", True)
    service.create_unit("CARTON", "كرتونة", True)
    return service


def test_unit_conversion_carton_to_pieces(master):
    product = master.create_product("WIRE-1", "سلك", "PIECE", "LOT")
    master.add_conversion(product, "CARTON", 12, 1)
    assert master.convert_to_base(product, "CARTON", 2_500_000) == 30_000_000
    assert master.convert_to_base(product, "PIECE", 3_000_000) == 3_000_000


def test_fractional_piece_is_rejected(master):
    product = master.create_product("SCREW", "مسمار", "PIECE", "LOT")
    with pytest.raises(ValueError):
        master.convert_to_base(product, "PIECE", 1_500_000)


def test_price_priority_customer_then_list_then_default(master):
    product = master.create_product("CABLE", "كابل", "METER", "REEL")
    customer = master.create_customer("C1", "عميل", "CONTRACTOR")
    price_list = master.create_price_list("WHOLESALE", "جملة")
    unit_id = master.db.scalar("SELECT id FROM units WHERE code='METER'")
    master.set_price("product_default_prices", product_id=product, unit_id=unit_id, price_micros=30_000_000)
    master.set_price("price_list_items", price_list_id=price_list, product_id=product, unit_id=unit_id, price_micros=28_000_000)
    master.set_price("customer_prices", customer_id=customer, product_id=product, unit_id=unit_id, price_micros=25_000_000)
    assert master.resolve_price(customer, product, unit_id, price_list) == 25_000_000
    assert master.resolve_price(None, product, unit_id, price_list) == 28_000_000
    assert master.resolve_price(None, product, unit_id) == 30_000_000


def test_project_requires_contractor(master):
    retail = master.create_customer("R1", "تجزئة", "RETAIL")
    with pytest.raises(ValueError):
        master.create_project(retail, "P1", "مشروع")
    contractor = master.create_customer("C1", "مقاول", "CONTRACTOR")
    assert master.create_project(contractor, "P1", "مشروع") > 0
