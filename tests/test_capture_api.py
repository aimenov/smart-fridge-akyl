from io import BytesIO

from tests.conftest import make_test_jpeg_bytes


def test_scan_upload_returns_scan_id(client):
    jpeg = make_test_jpeg_bytes()
    files = [
        ("files", ("a.jpg", BytesIO(jpeg), "image/jpeg")),
        ("files", ("b.jpg", BytesIO(jpeg), "image/jpeg")),
    ]
    r = client.post("/api/scan/upload?phase=product", files=files)
    assert r.status_code == 200
    data = r.json()
    assert "scan_id" in data
    assert data["stage"] == "done"
    assert "confidence_tier" in data


def test_scan_confirm_roundtrip(client):
    jpeg = make_test_jpeg_bytes()
    up = client.post(
        "/api/scan/upload?phase=product",
        files=[("files", ("x.jpg", BytesIO(jpeg), "image/jpeg"))],
    )
    scan_id = up.json()["scan_id"]

    body = {
        "scan_id": scan_id,
        "product": {
            "canonical_name": "Roundtrip Yogurt",
            "barcode": None,
            "brand": None,
            "default_unit": None,
            "category": None,
        },
        "quantity": 2,
        "unit": "cup",
        "expiry_date": "2099-06-01",
        "location": "fridge",
        "inferred_date_type": "best_before",
    }
    r = client.post("/api/scan/confirm", json=body)
    assert r.status_code == 200
    row = r.json()
    assert row["canonical_name"] == "Roundtrip Yogurt"
    assert row["quantity"] == 2

    listed = client.get("/api/items").json()
    assert len(listed) == 1


def test_scan_confirm_unknown_scan(client):
    r = client.post(
        "/api/scan/confirm",
        json={
            "scan_id": 999999,
            "product": {"canonical_name": "x"},
            "quantity": 1,
            "unit": "each",
            "location": "fridge",
        },
    )
    assert r.status_code == 404
