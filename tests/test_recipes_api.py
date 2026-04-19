def test_recipes_suggest_structure(client):
    r = client.get("/api/recipes/suggest")
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) >= {
        "can_cook_now",
        "need_one_or_two_items",
        "best_for_expiring_soon",
        "pantry_note",
    }
