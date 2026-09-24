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
    assert resp.status_code == 302
    assert resp["Location"].startswith("/accounts/login/?next=/search/")


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
                "/review/pairs/", "/review/issues/", "/imports/", "/admin/matching/matchcandidate/"]:
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


def test_upload_via_web_goes_through_preview(client, staff):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.catalog.models import SupplierItem

    from .conftest import FILE_A

    upload = SimpleUploadedFile(FILE_A.name, FILE_A.read_bytes())
    resp = client.post("/imports/", {"file": upload, "supplier": "A"})
    assert resp.status_code == 302 and "/imports/preview/" in resp["Location"]
    preview_url = resp["Location"]
    page = client.get(preview_url)
    assert page.status_code == 200
    assert not SupplierItem.objects.exists()  # nothing stored before confirmation
    assert {h["header"]: h["field"] for h in page.context["headers"]}["Supplier SKU"] == \
        "supplier_part_no"
    form = {"action": "confirm"}
    for i, h in enumerate(page.context["headers"], start=1):
        form[f"hdr_{i}"], form[f"map_{i}"] = h["header"], h["field"]
    resp = client.post(preview_url, form)
    assert resp.status_code == 302 and "/imports/" in resp["Location"]
    assert SupplierItem.objects.filter(supplier__code="A").count() == 27


def test_upload_rejects_xls_with_hint(client, staff):
    from django.core.files.uploadedfile import SimpleUploadedFile

    resp = client.post("/imports/", {"file": SimpleUploadedFile("old.xls", b"\xd0\xcf\x11\xe0"),
                                     "supplier": "X"}, follow=True)
    assert resp.status_code == 200
    assert "另存为 .xlsx" in resp.context["error"]


def test_group_review(client, staff, imported, pair):
    from apps.matching.selectors import review_groups

    groups = review_groups()
    pairs = sum(len(g.candidates) for g in groups)
    assert len(groups) < pairs  # related pairs are handled together
    # The VNL left side grille look-alikes (A-003/A-015/A-023/A-027 …) form a single group.
    target = pair("A-015", "A-027")
    group = next(g for g in groups if target in g.candidates)
    assert len(group.candidates) >= 3
    resp = client.get(f"/review/groups/{group.key}/")
    assert resp.status_code == 200
    resp = client.post(f"/review/groups/{group.key}/", {"bulk": "reject_all", "note": "不同品质档"})
    assert resp.status_code == 302
    target.refresh_from_db()
    assert (target.status, target.decided_by) == ("rejected", "human")


def test_group_merge_over_hard_conflict_needs_note(client, staff, imported, pair):
    cand = pair("A-021", "B-019")
    from apps.matching.selectors import review_groups

    group = next(g for g in review_groups() if cand in g.candidates)
    resp = client.post(f"/review/groups/{group.key}/", {f"decision_{cand.pk}": "merge"})
    assert resp.status_code == 200 and "必须填写备注" in resp.context["error"]
    cand.refresh_from_db()
    assert cand.status == "pending"


def test_demo_refuses_without_tty(db, monkeypatch):
    import io

    from django.core.management import CommandError, call_command

    from apps.core.reset import confirm

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with pytest.raises(CommandError, match="--noinput"):
        confirm("?", noinput=False)
    assert confirm("?", noinput=True)
    with pytest.raises(CommandError, match="--noinput"):
        call_command("reset_data")


def test_dashboard_is_search_first(client, staff, imported):
    html = client.get("/").content.decode()
    assert 'class="hero-search"' in html and 'action="/search/"' in html
    assert 'class="search"' not in html  # no second, small box in the header on this page
    assert "?q=OE-VNL-1001" in html  # clickable example queries
    other = client.get("/review/").content.decode()
    assert 'class="search"' in other  # the header box stays available elsewhere


@pytest.mark.parametrize(("q", "expected"), [
    ("Volvo VNL", "A:A-01-00"),             # words match different fields (make, model)
    ("left side grille volvo", "A:A-03-L"),
    ("OE VNL 1001", "A:A-01-00"),           # a part number typed with spaces
])
def test_multi_word_search(client, staff, imported, q, expected):
    resp = client.get("/search/", {"q": q})
    members = {str(m) for r in resp.context["results"] for m in r["members"]}
    assert expected in members


def test_every_word_must_match(client, staff, imported):
    resp = client.get("/search/", {"q": "grille freightliner"})
    assert resp.context["results"]
    assert all(any(m.fitment_make == "Freightliner" for m in r["members"])
               for r in resp.context["results"])


def test_dashboard_cards_show_counts(client, staff, imported):
    # Regression: the search box's choices once overwrote the per-category counts.
    resp = client.get("/")
    assert resp.context["states"] == {"已确认归一": 12, "独立产品": 23, "待补充": 6}
    html = resp.content.decode()
    for n in (12, 23, 6):
        assert f'<div class="n">{n}</div>' in html
    assert ">疑似重复</option>" in html  # search box choices still rendered


def test_site_name_everywhere(client, staff, imported):
    from django.conf import settings

    assert settings.SITE_NAME == "Fit Vehicle Parts 产品数据中心"
    for url in ["/", "/search/?q=b01x", "/review/", "/imports/", "/admin/"]:
        html = client.get(url).content.decode()
        assert settings.SITE_NAME in html, url
        assert "零件资料归一化" not in html, url


class TestLoginPage:
    @pytest.fixture
    def user(self, db, django_user_model):
        return django_user_model.objects.create_user("zhang.wei", password="pw-12345-login")

    def test_renders_with_system_name_and_csrf(self, client):
        html = client.get("/accounts/login/?next=/review/").content.decode()
        assert "登录 | Fit Vehicle Parts 产品数据中心" in html
        assert "csrfmiddlewaretoken" in html
        assert 'name="next" value="/review/"' in html
        assert "css/login.css" in html and "js/login.js" in html
        assert "https://" not in html  # no external requests (fonts, CDNs)

    def test_wrong_password_shows_error_and_keeps_username(self, client, user):
        resp = client.post("/accounts/login/", {"username": "zhang.wei", "password": "nope"})
        assert resp.status_code == 200
        html = resp.content.decode()
        assert resp.context["form"].non_field_errors()
        assert str(resp.context["form"].non_field_errors()[0]) in html
        assert 'value="zhang.wei"' in html

    def test_login_redirects_to_next(self, client, user):
        resp = client.post("/accounts/login/", {"username": "zhang.wei",
                                                "password": "pw-12345-login", "next": "/review/"})
        assert resp.status_code == 302 and resp["Location"] == "/review/"

    def test_remember_me(self, client, user):
        client.post("/accounts/login/", {"username": "zhang.wei", "password": "pw-12345-login"})
        assert client.session.get_expire_at_browser_close()
        client.post("/accounts/logout/")
        client.post("/accounts/login/", {"username": "zhang.wei", "password": "pw-12345-login",
                                         "remember": "1"})
        assert not client.session.get_expire_at_browser_close()

    def test_logout(self, client, user):
        client.force_login(user)
        assert 'action="/accounts/logout/"' in client.get("/").content.decode()
        resp = client.post("/accounts/logout/")
        assert resp.status_code == 302 and resp["Location"] == "/accounts/login/"
        assert client.get("/").status_code == 302
