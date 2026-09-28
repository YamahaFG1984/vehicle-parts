"""Sorting the SKU table on product pages (price / MOQ / quote date / stock, currency filter)."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.catalog.selectors import sort_members


def sku(code, part, price=None, currency="USD", moq=None, day=None, stock=None):
    offer = None if price is None and moq is None and day is None else SimpleNamespace(
        price=None if price is None else Decimal(price), currency=currency if price else "",
        moq=moq, quote_date=day)
    return SimpleNamespace(supplier=SimpleNamespace(code=code), supplier_part_no=part,
                           current_offer=offer, stock_total=stock)


MEMBERS = [
    sku("C", "C-1", "39.80", "USD", moq=3, day=date(2026, 9, 15), stock=5),
    sku("A", "A-1", "42.67", "USD", moq=2, day=date(2026, 8, 3)),
    sku("B", "B-1", "46.58", "USD", moq=5, day=date(2026, 9, 3), stock=0),
    sku("D", "D-1", "35.00", "EUR", moq=10, day=date(2026, 9, 1), stock=12),
    sku("E", "E-1"),  # no offer at all
]


def parts(rows):
    return [r.supplier_part_no for r in rows]


def test_default_order_is_by_supplier():
    rows, applied = sort_members(MEMBERS)
    assert parts(rows) == ["A-1", "B-1", "C-1", "D-1", "E-1"] and applied == ""


def test_price_ascending_and_descending_grouped_by_currency():
    # Never compare EUR with USD: EUR group first (alphabetical), then USD; missing last.
    assert parts(sort_members(MEMBERS, "price")[0]) == ["D-1", "C-1", "A-1", "B-1", "E-1"]
    assert parts(sort_members(MEMBERS, "-price")[0]) == ["D-1", "B-1", "A-1", "C-1", "E-1"]


def test_currency_filter_makes_prices_comparable():
    rows, _ = sort_members(MEMBERS, "price", currency="USD")
    assert parts(rows) == ["C-1", "A-1", "B-1"]


@pytest.mark.parametrize(("sort", "expected"), [
    ("moq", ["A-1", "C-1", "B-1", "D-1", "E-1"]),
    ("-date", ["C-1", "B-1", "D-1", "A-1", "E-1"]),
    ("-stock", ["D-1", "C-1", "B-1", "A-1", "E-1"]),   # stock 0 is a value, missing is last
    ("stock", ["B-1", "C-1", "D-1", "A-1", "E-1"]),
])
def test_other_columns(sort, expected):
    assert parts(sort_members(MEMBERS, sort)[0]) == expected


def test_unknown_sort_is_ignored():
    assert sort_members(MEMBERS, "name; drop table")[1] == ""


@pytest.fixture
def staff(client, django_user_model):
    user = django_user_model.objects.create_user("staff", password="pw-12345-x", is_staff=True)
    client.force_login(user)
    return user


def _rows(resp):
    return [str(m) for m in resp.context["rows"]]


@pytest.mark.django_db
def test_product_page_price_sort(client, staff, imported, item):
    url = f"/products/{item('A-001').product.code}/"
    assert _rows(client.get(url, {"sort": "price"})) == ["A:A-01-00", "B:B01X"]    # 42.67 < 46.58
    resp = client.get(url, {"sort": "-price"})
    assert _rows(resp) == ["B:B01X", "A:A-01-00"]
    html = resp.content.decode()
    assert 'aria-sort="descending"' in html and "?sort=price#offers" in html  # click again → asc


@pytest.mark.django_db
def test_product_page_currency_filter(client, staff, imported, item):
    url = f"/products/{item('A-003').product.code}/"   # A-003 quotes EUR, B-003 USD
    resp = client.get(url, {"sort": "price"})
    assert resp.context["mixed_currency_sort"]
    assert [c for c, _, _ in resp.context["currency_links"]] == ["全部", "EUR", "USD"]
    only_eur = client.get(url, {"sort": "price", "currency": "EUR"})
    assert _rows(only_eur) == ["A:A-03-L"] and not only_eur.context["mixed_currency_sort"]
    assert _rows(client.get(url, {"currency": "XXX"})) == ["A:A-03-L", "B:B03L"]  # ignored
