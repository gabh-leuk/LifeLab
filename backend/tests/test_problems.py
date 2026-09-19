def test_create_problem(client):
    r = client.post(
        "/problems",
        json={
            "title": "为什么长期精力不足？",
            "category": "精力",
            "note": "想通过实验找到原因",
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["title"] == "为什么长期精力不足？"
    assert data["category"] == "精力"
    assert data["status"] == "OPEN"
    assert "created_at" in data


def test_problem_title_blank_422(client):
    r = client.post("/problems", json={"title": "  "})
    assert r.status_code == 422


def test_list_problems_filter_by_status(client):
    client.post("/problems", json={"title": "问题A"})
    client.post("/problems", json={"title": "问题B"})
    pid = client.post("/problems", json={"title": "问题C"}).json()["id"]
    client.patch(f"/problems/{pid}", json={"status": "RESOLVED"})

    r = client.get("/problems", params={"status": "RESOLVED"})
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["title"] == "问题C"

    r2 = client.get("/problems", params={"status": "OPEN"})
    assert len(r2.json()) == 2


def test_update_problem(client):
    pid = client.post("/problems", json={"title": "初始"}).json()["id"]
    r = client.patch(f"/problems/{pid}", json={"status": "DORMANT", "note": "暂停关注"})
    assert r.status_code == 200
    assert r.json()["status"] == "DORMANT"
    assert r.json()["note"] == "暂停关注"


def test_delete_problem(client):
    pid = client.post("/problems", json={"title": "待删"}).json()["id"]
    r = client.delete(f"/problems/{pid}")
    assert r.status_code == 204
    assert client.get(f"/problems/{pid}").status_code == 404


def test_problem_not_found_404(client):
    assert client.get("/problems/99999").status_code == 404