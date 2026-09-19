def test_create_finding(client):
    # 先建一个 problem 用于关联
    pid = client.post("/problems", json={"title": "精力问题"}).json()["id"]
    r = client.post(
        "/findings",
        json={
            "problem_id": pid,
            "kind": "OBSERVATION",
            "title": "晚睡后精力下降",
            "observation": "连续 3 天晚睡，次日平均精力 2.0",
            "evidence": "2026-09-01~03 的 state 记录",
            "interpretation": "可能因为睡眠不足（推测）",
            "confidence": 0.7,
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["kind"] == "OBSERVATION"
    assert data["problem_id"] == pid
    assert data["confidence"] == 0.7
    assert data["interpretation"].endswith("（推测）")


def test_finding_confidence_bounds_422(client):
    r = client.post(
        "/findings",
        json={"title": "t", "observation": "o", "confidence": 1.5},
    )
    assert r.status_code == 422


def test_list_findings_filter_by_problem(client):
    pid = client.post("/problems", json={"title": "P"}).json()["id"]
    client.post("/findings", json={"title": "f1", "observation": "o1", "problem_id": pid})
    client.post("/findings", json={"title": "f2", "observation": "o2"})

    r = client.get("/findings", params={"problem_id": pid})
    assert len(r.json()) == 1
    assert r.json()[0]["title"] == "f1"


def test_list_findings_filter_by_kind(client):
    client.post("/findings", json={"title": "a", "observation": "o", "kind": "OBSERVATION"})
    client.post("/findings", json={"title": "b", "observation": "o", "kind": "HYPOTHESIS"})
    r = client.get("/findings", params={"kind": "HYPOTHESIS"})
    assert len(r.json()) == 1
    assert r.json()[0]["kind"] == "HYPOTHESIS"


def test_update_finding(client):
    fid = client.post("/findings", json={"title": "旧", "observation": "o"}).json()["id"]
    r = client.patch(f"/findings/{fid}", json={"confidence": 0.9, "next_step": "再测一周"})
    assert r.status_code == 200
    assert r.json()["confidence"] == 0.9
    assert r.json()["next_step"] == "再测一周"


def test_delete_finding(client):
    fid = client.post("/findings", json={"title": "删", "observation": "o"}).json()["id"]
    assert client.delete(f"/findings/{fid}").status_code == 204
    assert client.get(f"/findings/{fid}").status_code == 404


def test_finding_not_found_404(client):
    assert client.get("/findings/99999").status_code == 404