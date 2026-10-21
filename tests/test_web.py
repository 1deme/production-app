"""ვებ-გვერდების შემოწმება (რეალური HTTP მოთხოვნებით)."""
from sqlalchemy import select

from app.models import Ingredient, Unit


def kg_id(session_factory):
    with session_factory() as s:
        return s.scalar(select(Unit.id).where(Unit.code == "kg"))


def create(client, session_factory, name="შაქარი", barcodes=("4860019876543",), min_stock="5", action="save"):
    data = {"name": name, "unit_id": str(kg_id(session_factory)), "min_stock": min_stock,
            "note": "", "action": action, "barcode": list(barcodes)}
    return client.post("/ingredients/new", data=data, follow_redirects=False)


def test_pages_render_in_georgian(client):
    for url in ["/", "/ingredients", "/ingredients/new", "/settings", "/audit"]:
        r = client.get(url)
        assert r.status_code == 200, url
        assert 'lang="ka"' in r.text
        assert "წარმოების აღრიცხვა" in r.text


def test_static_assets_available_offline(client):
    for path in ["/static/vendor/bootstrap.min.css", "/static/vendor/htmx.min.js",
                 "/static/vendor/chart.umd.min.js", "/static/fonts/NotoSansGeorgian-Regular.ttf",
                 "/static/vendor/fonts/bootstrap-icons.woff2"]:
        assert client.get(path).status_code == 200, path


def test_create_flow_and_flash(client, session_factory):
    r = create(client, session_factory)
    assert r.status_code == 303
    detail = client.get(r.headers["location"])
    assert "შაქარი" in detail.text
    assert "დაემატა" in detail.text  # შეტყობინება
    assert "4860019876543" in detail.text
    with session_factory() as s:
        assert s.scalar(select(Ingredient.min_stock)) == 5000


def test_save_and_new_redirects_to_empty_form(client, session_factory):
    r = create(client, session_factory, action="save_new")
    assert r.headers["location"] == "/ingredients/new"


def test_validation_errors_shown_and_values_kept(client, session_factory):
    create(client, session_factory)
    r = create(client, session_factory, name="  შაქარი ", barcodes=("123456",), min_stock="abc")
    assert r.status_code == 422
    assert "ასეთი დასახელებით ინგრედიენტი უკვე არსებობს" in r.text
    assert "შეიყვანეთ სწორი რიცხვი" in r.text
    assert 'value="123456"' in r.text  # შეყვანილი მონაცემები არ იკარგება


def test_search_enter_opens_single_barcode_match(client, session_factory):
    create(client, session_factory)
    r = client.get("/ingredients", params={"q": "4860019876543", "go": "1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/ingredients/")


def test_live_search_returns_partial(client, session_factory):
    create(client, session_factory)
    r = client.get("/ingredients", params={"q": "შაქ"}, headers={"HX-Request": "true"})
    assert "შაქარი" in r.text
    assert "<html" not in r.text  # მხოლოდ ცხრილი


def test_not_found_offers_prefilled_add(client):
    r = client.get("/ingredients", params={"q": "4860555555555", "go": "1"}, follow_redirects=False)
    assert r.status_code == 200
    assert "/ingredients/new?barcode=4860555555555" in r.text
    form = client.get("/ingredients/new", params={"barcode": "4860555555555"})
    assert 'value="4860555555555"' in form.text


def test_not_found_but_inactive_exists(client, session_factory):
    r = create(client, session_factory)
    ing_url = r.headers["location"]
    client.post(ing_url + "/deactivate")
    r = client.get("/ingredients", params={"q": "შაქარი"})
    assert "status=all" in r.text  # გვთავაზობს სხვა სტატუსში ჩვენებას, დუბლიკატის შექმნის ნაცვლად


def test_edit_and_history(client, session_factory):
    url = create(client, session_factory).headers["location"]
    data = {"name": "შაქარი (თეთრი)", "unit_id": str(kg_id(session_factory)), "min_stock": "7,5",
            "note": "", "barcode": ["4860019876543", "111222333"]}
    r = client.post(url + "/edit", data=data, follow_redirects=True)
    assert "შაქარი (თეთრი)" in r.text
    assert "111222333" in r.text
    assert "7,5 კგ" in r.text  # ისტორიაში ცვლილება
    audit = client.get("/audit")
    assert "რედაქტირება" in audit.text


def test_delete_unused(client, session_factory):
    url = create(client, session_factory).headers["location"]
    r = client.post(url + "/delete", follow_redirects=True)
    assert "წაიშალა" in r.text
    assert client.get(url).status_code == 404


def test_404_page_in_georgian(client):
    r = client.get("/ingredients/99999")
    assert r.status_code == 404
    assert "ვერ მოიძებნა" in r.text
