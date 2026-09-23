"""End-to-end expectations on the two original sample files (docs/design.html §5.5)."""

import hashlib

import pytest

from apps.catalog.models import FieldValue, SupplierItem, SupplierOffer
from apps.ingestion.models import ImportBatch
from apps.ingestion.services import import_source
from apps.matching.models import Issue, MatchCandidate

from .conftest import FILE_A, FILE_B

pytestmark = pytest.mark.django_db

MC = MatchCandidate


def test_all_rows_imported_with_provenance(imported):
    assert SupplierItem.objects.filter(supplier__code="A").count() == 27
    assert SupplierItem.objects.filter(supplier__code="B").count() == 26
    fv = FieldValue.objects.get(item__source_ref="A-013", field="package_dims", is_current=True)
    assert fv.raw_value == "122 x 75 x 7 cm"
    assert fv.source_column == "Package Size"
    assert fv.source_record.locator == {"sheet": "报价明细", "row": 14}
    assert fv.source_record.locator_label == "候选人材料_供应商A报价表.xlsx!报价明细!R14"


def test_originals_are_archived_unchanged(imported):
    for path in (FILE_A, FILE_B):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        batch = ImportBatch.objects.get(source_file__sha256=digest)
        stored = batch.source_file.file
        assert hashlib.sha256(stored.read()).hexdigest() == digest


def test_reimport_is_detected(imported):
    result = import_source(FILE_A, "A")
    assert result.duplicate
    assert result.batch.status == ImportBatch.Status.DUPLICATE
    assert SupplierItem.objects.count() == 53


@pytest.mark.parametrize("n", range(1, 13))
def test_clean_pairs_auto_confirmed(imported, pair, item, n):
    a, b = f"A-{n:03d}", f"B-{n:03d}"
    cand = pair(a, b)
    assert cand.classification == MC.Classification.AUTO_CONFIRMED
    assert cand.status == MC.Status.ACCEPTED
    assert item(a).product_id == item(b).product_id


def test_offers_kept_separately(imported, item):
    product = item("A-001").product
    offers = SupplierOffer.objects.filter(item__product=product, is_current=True)
    assert {(o.item.supplier.code, str(o.price), o.currency, o.moq) for o in offers} == {
        ("A", "42.6700", "USD", 2), ("B", "46.5800", "USD", 5)}


@pytest.mark.parametrize(
    ("a", "b", "fields"),
    [
        ("A-021", "B-019", {"category"}),   # OE-SHARED-01 (package size differs too: soft)
        ("A-022", "B-020", {"category"}),   # OE-SHARED-02 housing vs cap
        ("A-023", "B-021", {"position"}),           # OE-SHARED-03 left vs right
        ("A-026", "B-025", {"category"}),           # OE-CONFLICT-01 same size, other type
        ("A-027", "B-026", {"position"}),           # OE-CONFLICT-02
        ("A-012", "B-013", {"position"}),           # OE-VN-1010
    ],
)
def test_shared_oe_conflicts_are_not_merged(imported, pair, item, a, b, fields):
    cand = pair(a, b)
    assert cand.status == MC.Status.PENDING
    assert "SHARED_OE_CONFLICT" in cand.reasons
    assert {c["field"] for c in cand.conflicts if c["hard"]} == fields
    assert cand.confidence <= 0.3
    assert cand.suggested_action
    assert item(a).product_id != item(b).product_id


def test_same_size_different_type_is_rejected(imported, pair, item):
    cand = pair("A-008", "A-010")
    assert cand.classification == MC.Classification.AUTO_REJECTED
    assert cand.reasons == ["DIFFERENT_CATEGORY"]
    assert item("A-008").product_id != item("A-010").product_id


@pytest.mark.parametrize(("a", "b"), [("A-001", "A-013"), ("A-002", "A-014"), ("A-012", "A-020")])
def test_same_supplier_lookalikes_are_suspects(imported, pair, a, b):
    cand = pair(a, b)
    assert cand.status == MC.Status.PENDING
    assert {"NO_OE_ATTR_MATCH", "SAME_SUPPLIER_DUP"} <= set(cand.reasons)


def test_one_question_per_cluster_pair(imported, pair):
    # A-013 looks like both A-001 and B-001, which already form one product: ask once.
    open_ = [c for c in (pair("A-013", "A-001"), pair("A-013", "B-001")) if c and
             c.status == MC.Status.PENDING]
    assert len(open_) == 1


def test_nothing_merged_without_shared_oe(imported):
    for cand in MC.objects.filter(status=MC.Status.ACCEPTED):
        assert cand.comparisons["oe"]["shared"]
        assert cand.item_a.supplier_id != cand.item_b.supplier_id


def test_missing_fields_become_issues(imported):
    def codes(ref):
        return set(Issue.objects.filter(item__source_ref=ref, status="open")
                   .values_list("code", flat=True))

    assert {"MISSING_PRICE", "MISSING_CURRENCY"} <= codes("A-024")
    assert {"MISSING_PRICE", "MISSING_CURRENCY"} <= codes("B-022")
    assert {"MISSING_FITMENT", "MISSING_DIMS", "INSUFFICIENT_FOR_MATCHING"} <= codes("A-025")
    assert "MISSING_FITMENT" in codes("B-023")
    assert "MISSING_MOQ" in codes("B-024")
    assert "MISSING_DIMS" in codes("B-013")


def test_short_number_coincidence_is_ignored(imported, pair):
    # A-40-L / B41R share nothing but "4x": they only meet through OE-CONFLICT-02.
    assert pair("A-027", "B-026").reasons == ["SHARED_OE_CONFLICT"]
    # A-014 (A-16-00) and B-014 (B15X) are different products that happen to align by row.
    cand = pair("A-014", "B-014")
    assert cand is None or cand.status != MC.Status.ACCEPTED


def test_matching_is_idempotent(imported):
    from apps.matching.engine import run_matching

    before = sorted(MC.objects.values_list("item_a", "item_b", "status"))
    summary = run_matching()
    after = sorted(MC.objects.values_list("item_a", "item_b", "status"))
    assert before == after
    assert summary.reopened == 0


def test_suspected_name_errors(imported):
    flagged = set(Issue.objects.filter(code="SUSPECTED_NAME_ERROR", status="open")
                  .values_list("item__source_ref", flat=True))
    # B-025 "Bug Screen" and B-022 "Bug Screen" carry front-grille carton sizes.
    assert {"B-025", "B-022"} <= flagged
    # Shared cartons across types are fine when each also matches its own type.
    assert not flagged & {"A-008", "A-010", "A-026", "A-006"}


def test_name_error_check_is_recomputed(imported, item):
    from apps.matching.engine import run_matching

    run_matching()
    assert Issue.objects.filter(code="SUSPECTED_NAME_ERROR", item=item("B-025")).count() == 1
