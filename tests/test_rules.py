"""Pure pair-evaluation rules (no database)."""

from apps.core import rules as rule_files
from apps.matching.rules import ItemView, evaluate


def view(i, supplier=1, **kw):
    base = dict(id=i, label=f"S{supplier}:{i}", supplier_id=supplier, name="Front Grille",
                category="front_grille", make="Volvo", model="VNL", year_from=2018, year_to=2023,
                dims=(122.0, 75.0, 7.0), oe=frozenset({"OEVNL1001"}), attr_hash=str(i))
    base.update(kw)
    return ItemView(**base)


def cfg():
    return rule_files.matching()


def test_full_match_across_suppliers_is_auto_confirmed():
    out = evaluate(view(1), view(2, supplier=2, name="FRONT GRILLE ASSY"), cfg())
    assert out.classification == "auto_confirmed"


def test_same_supplier_is_never_auto_confirmed():
    out = evaluate(view(1), view(2), cfg())
    assert out.classification == "suspect"
    assert "SAME_SUPPLIER_DUP" in out.reasons


def test_missing_years_blocks_auto_confirm():
    out = evaluate(view(1), view(2, supplier=2, year_from=None, year_to=None), cfg())
    assert out.classification == "suspect"
    assert "fitment_years" in out.missing


def test_shared_oe_with_hard_conflict_is_suspect_with_capped_confidence():
    out = evaluate(view(1), view(2, supplier=2, category="bug_screen", name="Bug Screen"), cfg())
    assert out.classification == "suspect"
    assert out.reasons == ["SHARED_OE_CONFLICT"]
    assert out.confidence <= 0.3
    assert "默认不合并" in out.suggested_action


def test_no_shared_oe_and_hard_conflict_is_rejected():
    out = evaluate(view(1, oe=frozenset()), view(2, supplier=2, oe=frozenset(),
                                                 category="fan_shroud"), cfg())
    assert out.classification == "auto_rejected"
    assert out.reasons == ["DIFFERENT_CATEGORY"]


def test_position_conflict_is_hard():
    a = view(1, category="side_grille", position="left")
    b = view(2, supplier=2, category="side_grille", position="right")
    out = evaluate(a, b, cfg())
    assert [c["field"] for c in out.conflicts if c["hard"]] == ["position"]


def test_sided_category_missing_position_is_missing_not_equal():
    a = view(1, category="side_grille", position="left")
    b = view(2, supplier=2, category="side_grille", position="")
    out = evaluate(a, b, cfg())
    assert out.classification == "suspect"
    assert "position" in out.missing


def test_unrelated_items_produce_nothing():
    a = view(1, oe=frozenset(), dims=None)
    b = view(2, supplier=2, oe=frozenset(), dims=None, make="Mack", model="Anthem")
    assert evaluate(a, b, cfg()) is None or evaluate(a, b, cfg()).classification == "auto_rejected"


def test_missing_oe_lowers_confidence():
    with_oe = evaluate(view(1), view(2, supplier=2), cfg())
    without = evaluate(view(1, oe=frozenset()), view(2, supplier=2, oe=frozenset()), cfg())
    assert without.confidence < with_oe.confidence


def test_package_size_is_soft():
    out = evaluate(view(1), view(2, supplier=2, dims=(122.0, 75.0, 20.0)), cfg())
    assert out.classification == "auto_confirmed"  # carton size alone never splits
    assert "DIMS_DIFFER" in out.reasons
    assert not [c for c in out.conflicts if c["hard"]]


def test_missing_package_size_does_not_block_auto_merge():
    out = evaluate(view(1), view(2, supplier=2, dims=None), cfg())
    assert out.classification == "auto_confirmed"


def test_overlapping_years_need_a_human():
    out = evaluate(view(1), view(2, supplier=2, year_from=2019, year_to=2023), cfg())
    assert out.classification == "suspect"
    assert out.comparisons["fitment"]["result"] == "partial"
    assert "fitment_years_overlap" in out.missing


def test_disjoint_years_conflict():
    out = evaluate(view(1), view(2, supplier=2, year_from=2004, year_to=2017), cfg())
    assert [c["field"] for c in out.conflicts if c["hard"]] == ["fitment"]


def test_open_ended_years_overlap():
    out = evaluate(view(1, year_to=None), view(2, supplier=2, year_from=2020, year_to=2023), cfg())
    assert out.comparisons["fitment"]["result"] == "partial"


def test_auto_merge_can_be_switched_off():
    c = dict(cfg())
    c["auto_confirm"] = {**c["auto_confirm"], "enabled": False}
    out = evaluate(view(1), view(2, supplier=2), c)
    assert out.classification == "suspect"
    assert out.reasons == ["SHARED_OE_FULL_MATCH"]
    assert "自动归一已关闭" in out.suggested_action
