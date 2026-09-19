def test_create_state(client):
    r = client.post("/states", json={"energy": 4, "focus": 3, "irritation": 1})
    assert r.status_code == 201
    data = r.json()
    assert data["energy"] == 4
    assert data["focus"] == 3
    assert data["irritation"] == 1
    assert "timestamp" in data


def test_state_out_of_range_422(client):
    r = client.post("/states", json={"energy": 6, "focus": 3, "irritation": 1})
    assert r.status_code == 422
    r = client.post("/states", json={"energy": -1, "focus": 3, "irritation": 1})
    assert r.status_code == 422


def test_list_states(client):
    client.post("/states", json={"energy": 4, "focus": 3, "irritation": 1})
    client.post("/states", json={"energy": 2, "focus": 2, "irritation": 4})
    r = client.get("/states")
    assert r.status_code == 200
    states = r.json()
    assert len(states) == 2
    assert states[0]["irritation"] == 4  # 倒序
