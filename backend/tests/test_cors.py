def test_cors_preflight_allowed(client):
    """浏览器跨域预检请求（OPTIONS）应返回 CORS 头，避免 405。"""
    r = client.options(
        "/events",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_actual_request_has_header(client):
    r = client.post(
        "/events",
        json={"type": "LEARNING_START"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert r.status_code == 201
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
