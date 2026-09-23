"""Each case mirrors a trap found in the sample files (see docs/prd.html §7)."""

from datetime import date
from decimal import Decimal

import pytest

from apps.catalog import normalizers as n


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Front Grille", "front_grille"),
        ("Side Grille, Left side", "side_grille"),
        ("Left Side Grille", "side_grille"),
        ("Side Grille", "side_grille"),
        ("Air Filter Housing", "air_filter_housing"),
        ("Air Filter Housing Cap", "air_filter_housing_cap"),  # longest phrase wins
        ("Bug Screen", "bug_screen"),
        ("FAN SHROUD", "fan_shroud"),
        ("Grille", None),  # too vague: never guessed
        ("Hood Latch", None),
    ],
)
def test_category_from_name(name, expected):
    assert n.category_from_name(name)[0] == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Left", "left"), ("Right", "right"), ("RH", "right"), ("Side Grille, Left side", "left"),
     ("Side Grille", None), ("Left/Right", None)],
)
def test_position_from_text(text, expected):
    assert n.position_from_text(text) == expected


@pytest.mark.parametrize(
    ("sku", "expected"),
    [("A-03-L", "left"), ("B03L", "left"), ("B41R", "right"), ("12345-RH", "right"),
     ("A-01-00", None), ("B01X", None), ("FILTER", None), ("AIR-R", "right")],
)
def test_position_from_sku(sku, expected):
    assert n.position_from_sku(sku) == expected


def test_position_sources_agree():
    rec = n.normalize_record({"supplier_part_no": "B41R", "name": "Side Grille", "position": "Right"})
    assert rec.position == "right"
    assert not [f for f in rec.findings if f.code in ("POSITION_INCONSISTENT", "MISSING_POSITION")]


def test_position_contradiction_becomes_unknown():
    rec = n.normalize_record({"supplier_part_no": "X-9-L", "name": "Right Side Grille"})
    assert rec.position is None
    assert "POSITION_INCONSISTENT" in {f.code for f in rec.findings}


def test_sided_category_without_position_is_flagged():
    rec = n.normalize_record({"supplier_part_no": "X-9", "name": "Side Grille"})
    assert "MISSING_POSITION" in {f.code for f in rec.findings}


def test_fitment_parsing():
    fit, _ = n.parse_fitment("Volvo VNL 2018-2023")
    assert (fit.make, fit.model, fit.year_from, fit.year_to) == ("Volvo", "VNL", 2018, 2023)
    vn, _ = n.parse_fitment("Volvo VN")
    assert vn.key != fit.key  # VN and VNL are different models
    assert vn.year_from is None
    p3, _ = n.parse_fitment("Freightliner Cascadia P3")
    assert (p3.make, p3.model) == ("Freightliner", "Cascadia P3")
    unknown, findings = n.parse_fitment("Some Truck 2010+")
    assert unknown.make == "" and unknown.year_from == 2010
    assert findings[0].code == "FITMENT_UNPARSED"


def test_dims_units_and_orientation():
    cm, _ = n.parse_dims("122 x 75 x 7 cm")
    mm, _ = n.parse_dims("1220 x 750 x 70 mm")
    assert cm == mm == (122.0, 75.0, 7.0)
    a, _ = n.parse_dims("86 x 36 x 56 cm")
    b, _ = n.parse_dims("56×36×86")
    assert n.dims_equal(a, b, 1.0)
    c, _ = n.parse_dims("65 x 55 x 36 cm")
    d, _ = n.parse_dims("65 x 30 x 30 cm")
    assert not n.dims_equal(c, d, 1.0)
    _, findings = n.parse_dims("10 x 10 x 10")
    assert findings[0].code == "UNIT_ASSUMED"
    none, findings = n.parse_dims("large")
    assert none is None and findings[0].code == "INVALID_VALUE"


def test_oe_split_and_normalize():
    assert n.split_oe("OE-VNL-1001") == [("OE-VNL-1001", "OEVNL1001")]
    assert [x for _, x in n.split_oe("oe vnl 1001; 21334455 / 8K-1")] == [
        "OEVNL1001", "21334455", "8K1"]
    assert n.normalize_number("b01x") == n.normalize_number("B-01 X")


def test_price_currency_moq_date():
    assert n.parse_price("42.67")[:2] == (Decimal("42.67"), None)
    assert n.parse_price("$1,058.20")[:2] == (Decimal("1058.20"), "USD")
    assert n.parse_price("42,67")[0] == Decimal("42.67")
    assert n.parse_price("n/a")[2][0].code == "INVALID_VALUE"
    assert n.parse_currency("usd")[0] == "USD"
    assert n.parse_currency("XYZ")[1][0].code == "INVALID_VALUE"
    assert n.parse_moq("10 pcs")[0] == 10
    assert n.parse_date("2026-08-03")[0] == date(2026, 8, 3)
    assert n.parse_date("08/13/2026")[0] == date(2026, 8, 13)
    d, findings = n.parse_date("08/03/2026")
    assert d == date(2026, 8, 3) and findings[0].code == "AMBIGUOUS_DATE"


def test_record_missing_fields_are_reported():
    rec = n.normalize_record({"supplier_part_no": "A-35-00", "name": "Fan Shroud",
                              "price": "184.45", "currency": "USD", "moq": "10"})
    codes = {f.code for f in rec.findings}
    assert {"MISSING_FITMENT", "MISSING_DIMS", "MISSING_OE"} <= codes
    assert "MISSING_PRICE" not in codes


def test_currency_inferred_from_symbol():
    rec = n.normalize_record({"supplier_part_no": "C1", "name": "Front Grille", "price": "$58.20"})
    assert rec.currency == "USD"
    assert "CURRENCY_INFERRED" in {f.code for f in rec.findings}
