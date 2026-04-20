"""Integration-style flows: multipart scan → confirm → inventory → recipes."""

from __future__ import annotations

from datetime import date
from io import BytesIO

import pytest

from tests.conftest import make_test_jpeg_bytes


@pytest.mark.integration
def test_scan_confirm_inventory_recipes_chain(client):
    jpeg = make_test_jpeg_bytes()
    up = client.post(
        "/api/scan/upload",
        files=[("files", ("frame.jpg", BytesIO(jpeg), "image/jpeg"))],
    )
    assert up.status_code == 200
    scan_id = up.json()["scan_id"]

    cf = client.post(
        "/api/scan/confirm",
        json={
            "scan_id": scan_id,
            "product": {"canonical_name": "Integration Yogurt"},
            "quantity": 3,
            "unit": "pot",
            "expiry_date": str(date(2099, 3, 15)),
            "location": "fridge",
            "inferred_date_type": "best_before",
        },
    )
    assert cf.status_code == 200
    assert cf.json()["canonical_name"] == "Integration Yogurt"

    items = client.get("/api/items").json()
    assert len(items) == 1
    assert items[0]["canonical_name"] == "Integration Yogurt"

    recipes = client.get("/api/recipes/suggest").json()
    assert "can_cook_now" in recipes
    assert isinstance(recipes["can_cook_now"], list)


@pytest.mark.integration
def test_patch_item_then_list_reflects(client):
    jpeg = make_test_jpeg_bytes(text="PATCH 2099-01-01")
    up = client.post("/api/scan/upload", files=[("files", ("x.jpg", BytesIO(jpeg), "image/jpeg"))])
    sid = up.json()["scan_id"]
    cf = client.post(
        "/api/scan/confirm",
        json={
            "scan_id": sid,
            "product": {"canonical_name": "Patchable"},
            "quantity": 1,
            "unit": "each",
            "expiry_date": "2099-06-01",
            "location": "fridge",
        },
    )
    iid = cf.json()["id"]

    pr = client.patch(f"/api/items/{iid}", json={"status": "consumed"})
    assert pr.status_code == 200

    remaining = client.get("/api/items").json()
    assert remaining == []
