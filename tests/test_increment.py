"""Incremental import of the demo files (samples/demo) on top of the originals."""

import pytest

from apps.catalog.models import SupplierItem, SupplierOffer
from apps.ingestion.models import SourceRecord
from apps.ingestion.services import import_source
from apps.matching.engine import run_matching
from apps.matching.models import Issue, MatchCandidate

from .conftest import DEMO_DIR

pytestmark = pytest.mark.django_db
MC = MatchCandidate


@pytest.fixture
def incremented(imported):
    result = import_source(DEMO_DIR / "供应商A报价表_v2.xlsx", "A")
    run_matching()
    return result


def test_diff_counts(incremented):
    stats = incremented.batch.stats
    assert (stats["updated"], stats["conflict"], stats["new"], stats["not_in_latest"],
            stats["unchanged"]) == (3, 1, 3, 1, 22)


def test_price_update_keeps_history(incremented, item):
    offers = SupplierOffer.objects.filter(item=item("A-001")).order_by("created")
    assert [(str(o.price), o.is_current) for o in offers] == [("42.6700", False), ("44.1000", True)]


def test_key_change_reopens_auto_merge(incremented, pair, item):
    cand = pair("A-006", "B-006")
    assert cand.status == MC.Status.PENDING
    assert "SHARED_OE_CONFLICT" in cand.reasons
    assert item("A-006").product_id != item("B-006").product_id
    issue = Issue.objects.get(item=item("A-006"), code="KEY_ATTR_CHANGED")
    assert issue.details["changes"]["dims"]["new"] == [151.0, 91.0, 20.0]


def test_missing_row_is_flagged_not_deleted(incremented, item):
    a027 = item("A-027")
    assert not a027.present_in_latest
    assert Issue.objects.filter(item=a027, code="NOT_IN_LATEST", status="open").exists()


def test_same_supplier_not_merged_transitively(incremented, item):
    # A-028 shares OE-VNL-1005 with A-005 (same supplier) and B-005: it must not ride B-005 in.
    assert item("A-028").product_id != item("A-005").product_id
    assert item("A-005").product_id == item("B-005").product_id
    assert Issue.objects.filter(code="CLUSTER_CONFLICT", status="open").exists()


def test_new_unknown_category_needs_data(incremented, item):
    codes = set(item("A-030").issues.values_list("code", flat=True))
    assert "UNKNOWN_CATEGORY" in codes


def test_pdf_import(imported):
    result = import_source(DEMO_DIR / "供应商C目录.pdf", "C")
    run_matching()
    assert result.batch.stats["new"] == 8
    assert result.batch.stats["duplicate"] == 1
    rec = SourceRecord.objects.get(batch=result.batch, supplier_part_no="C-2004")
    assert rec.locator == {"page": 2, "table": 1, "row": 1}  # continued table, inherited header
    c1001 = SupplierItem.objects.get(supplier__code="C", supplier_part_no="C-1001")
    assert c1001.dims_cm == [122.0, 75.0, 7.0]  # mm converted
    assert c1001.product.items.count() == 3  # joins A-001 / B-001
    c1002 = SupplierItem.objects.get(supplier_part_no="C-1002")
    assert c1002.offers.get(is_current=True).currency == "USD"
    assert c1002.issues.filter(code="CURRENCY_INFERRED").exists()
    assert Issue.objects.filter(code="DUPLICATE_KEY_IN_FILE").count() == 1
