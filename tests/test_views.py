import pytest

from apps.matching.models import MatchCandidate

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff(client, django_user_model):
    user = django_user_model.objects.create_user("staff", password="pw-12345-x", is_staff=True)
    client.force_login(user)
    return user


def test_login_required(client, imported):
    resp = client.get("/search/?q=OE-VNL-1001")
    assert resp.status_code == 302 and "/admin/login/" in resp["Location"]


@pytest.mark.parametrize("q", ["OE-VNL-1001", "oevnl1001", "B01X", "a-01-00"])
def test_search_by_any_number(client, staff, imported, q):
    resp = client.get("/search/", {"q": q})
    assert resp.status_code == 200
    codes = {r["product"].code for r in resp.context["results"]}
    assert len(codes) == 1
    members = {str(m) for r in resp.context["results"] for m in r["members"]}
    assert {"A:A-01-00", "B:B01X"} <= members


def test_search_by_keyword_and_supplier(client, staff, imported):
    assert client.get("/search/", {"q": "grille"}).context["results"]
    resp = client.get("/search/", {"supplier": "B"})
    assert all(any(m.supplier.code == "B" for m in r["members"]) for r in resp.context["results"])


def test_pages_render(client, staff, imported, item):
    product = item("A-001").product
    for url in ["/", f"/products/{product.code}/", f"/items/{item('A-001').pk}/", "/review/",
                "/review/issues/", "/imports/", "/admin/matching/matchcandidate/"]:
        assert client.get(url).status_code == 200, url
    cand = MatchCandidate.objects.filter(status="pending").first()
    assert client.get(f"/review/{cand.pk}/").status_code == 200


def test_decision_via_web(client, staff, imported, pair):
    cand = pair("A-021", "B-019")
    resp = client.post(f"/review/{cand.pk}/", {"action": "reject", "note": "不同"})
    assert resp.status_code == 302
    cand.refresh_from_db()
    assert cand.status == "rejected"


@pytest.mark.parametrize("key", ["master", "offers", "review", "report"])
def test_export_downloads(client, staff, imported, key):
    resp = client.get(f"/exports/{key}/")
    assert resp.status_code == 200
    assert b"".join(resp.streaming_content)[:2] == b"PK"


def test_original_download(client, staff, imported, item):
    source = item("A-001").current_record.batch.source_file
    resp = client.get(f"/sources/{source.pk}/download/")
    assert resp.status_code == 200
