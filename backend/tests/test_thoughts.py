def test_create_thought_minimal(client):
    r = client.post("/thoughts", json={"content": "我为什么害怕认真做？"})
    assert r.status_code == 201
    data = r.json()
    assert data["content"] == "我为什么害怕认真做？"
    assert data["tags"] == []
    assert data["source"] == "MANUAL"
    assert "timestamp" in data


def test_create_thought_with_tags(client):
    r = client.post(
        "/thoughts",
        json={"content": "测试标签", "tags": ["努力", "羞耻"]},
    )
    assert r.status_code == 201
    assert r.json()["tags"] == ["努力", "羞耻"]


def test_thought_blank_content_422(client):
    r = client.post("/thoughts", json={"content": "   "})
    assert r.status_code == 422


def test_list_thoughts(client):
    client.post("/thoughts", json={"content": "第一条"})
    client.post("/thoughts", json={"content": "第二条"})
    r = client.get("/thoughts")
    assert r.status_code == 200
    thoughts = r.json()
    assert len(thoughts) == 2
    assert thoughts[0]["content"] == "第二条"  # 倒序


def test_list_thoughts_by_date(client):
    client.post("/thoughts", json={"content": "今天的一条"})
    r = client.get("/thoughts", params={"date": "2099-01-01"})
    assert r.json() == []
