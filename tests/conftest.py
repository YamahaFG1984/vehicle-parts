from pathlib import Path

import pytest
from django.core.management import call_command

ROOT = Path(__file__).resolve().parent.parent
FILE_A = ROOT / "候选人材料_供应商A报价表.xlsx"
FILE_B = ROOT / "候选人材料_供应商B报价表.xlsx"
DEMO_DIR = ROOT / "samples" / "demo"


@pytest.fixture
def imported(db):
    """Both original sample files imported and matched."""
    from apps.ingestion.services import import_source
    from apps.matching.engine import run_matching

    import_source(FILE_A, "A")
    import_source(FILE_B, "B")
    run_matching()


@pytest.fixture
def item():
    from apps.catalog.models import SupplierItem

    def get(ref):
        return SupplierItem.objects.select_related("supplier", "product").get(source_ref=ref)

    return get


@pytest.fixture
def pair():
    """The stored candidate between two source refs (in either order), or None."""
    from django.db.models import Q

    from apps.matching.models import MatchCandidate

    def get(ref_a, ref_b):
        return MatchCandidate.objects.filter(
            Q(item_a__source_ref=ref_a, item_b__source_ref=ref_b)
            | Q(item_a__source_ref=ref_b, item_b__source_ref=ref_a)
        ).first()

    return get


@pytest.fixture
def reviewer(db, django_user_model):
    return django_user_model.objects.create_user("reviewer", password="pw-12345-review")


__all__ = ["call_command"]
