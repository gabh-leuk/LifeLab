"""应用/域名分类规则：RuleSet 优先级 + 规则 API + 重算 + 未识别清单。"""

from datetime import datetime, timezone

from app.behavior_categories import (
    AppRule,
    RuleSet,
    classify_app,
    resolve_label,
)
from app.services.app_rule_service import normalize_match_value

# ── RuleSet 纯逻辑（不碰库） ───────────────────────────────


def _rs(*rules: AppRule) -> RuleSet:
    """按 build_ruleset 的排序规则手工造一个 RuleSet。"""
    ordered = sorted(
        rules, key=lambda r: (0 if r.match_type == "exact" else 1, -r.priority, -len(r.match_value))
    )
    return RuleSet(tuple(ordered))


def test_exact_beats_substring():
    rs = _rs(
        AppRule("substring", "app", "code", "video"),
        AppRule("exact", "app", "code", "study_work"),
    )
    assert classify_app("code", None, rs) == "study_work"
    # 非完全相等时 exact 不命中，落回子串
    assert classify_app("code.exe", None, rs) == "video"


def test_longer_substring_wins():
    rs = _rs(
        AppRule("substring", "app", "qq", "social"),
        AppRule("substring", "app", "qqmusic", "audio"),
    )
    assert classify_app("com.tencent.qqmusic", None, rs) == "audio"
    assert classify_app("com.tencent.qq", None, rs) == "social"


def test_priority_beats_length():
    # 两个规则都能命中：「long」更短但优先级更高，应胜出
    rs = _rs(
        AppRule("substring", "app", "verylongkeyword", "study_work", priority=100),
        AppRule("substring", "app", "long", "game", priority=200),
    )
    assert classify_app("verylongkeyword", None, rs) == "game"
    # 同优先级才轮到「更长者胜」
    rs2 = _rs(
        AppRule("substring", "app", "verylongkeyword", "study_work"),
        AppRule("substring", "app", "long", "game"),
    )
    assert classify_app("verylongkeyword", None, rs2) == "study_work"


def test_scope_filters_app_vs_domain():
    rs = _rs(
        AppRule("substring", "domain", "bilibili", "shopping"),
        AppRule("substring", "app", "bilibili", "study_work"),
    )
    # 应用行只吃 scope=app
    assert classify_app("com.bilibili.app", None, rs) == "study_work"
    # web: 行只吃 scope=domain
    assert classify_app("web:bilibili.com", None, rs) == "shopping"


def test_scope_any_matches_both():
    rs = _rs(AppRule("substring", "any", "bilibili", "shopping"))
    assert classify_app("com.bilibili.app", None, rs) == "shopping"
    assert classify_app("web:bilibili.com", None, rs) == "shopping"


def test_match_is_case_insensitive():
    rs = _rs(AppRule("substring", "app", "foobar", "utility"))
    assert classify_app("COM.FooBar.App", None, rs) == "utility"
    assert classify_app("Com.FooBar", "FooBar 桌面版", rs) == "utility"


def test_label_used_as_match_text():
    """采集到的显示名也参与匹配（应用名是包名时可凭 label 认出来）。"""
    rs = _rs(AppRule("substring", "app", "哔哩哔哩", "video"))
    assert classify_app("tv.danmaku.bili2", "哔哩哔哩", rs) == "video"


def test_user_rule_overrides_builtin():
    """内置说 bilibili 是视频，用户规则说了算。"""
    assert classify_app("com.bilibili.app", None) == "video"
    rs = _rs(AppRule("substring", "any", "bilibili", "study_work"))
    assert classify_app("com.bilibili.app", None, rs) == "study_work"


def test_empty_ruleset_falls_back_to_builtin():
    assert classify_app("web:youtube.com", None, RuleSet()) == "video"
    assert classify_app("unknown.app", None, RuleSet()) == "other_online"


def test_resolve_label_prefers_display_label():
    rs = _rs(AppRule("substring", "app", "tv.danmaku.bili2", "video", label="B站"))
    assert resolve_label("tv.danmaku.bili2", "哔哩哔哩", rs) == "B站"
    # 规则命中但没给别名 → 保留采集到的原名
    rs2 = _rs(AppRule("substring", "app", "tv.danmaku.bili2", "video"))
    assert resolve_label("tv.danmaku.bili2", "哔哩哔哩", rs2) == "哔哩哔哩"
    # 没命中 → 原名
    assert resolve_label("other.app", None, rs) is None


def test_normalize_match_value_shapes():
    assert normalize_match_value("substring", "app", "  VS Code.exe ") == "vs code.exe"
    # 域名规则归并到根域
    assert normalize_match_value("substring", "domain", "WWW.Bilibili.COM") == "bilibili.com"
    assert normalize_match_value("substring", "domain", "m.b23.tv") == "b23.tv"
    # 非域名 scope 不归并
    assert normalize_match_value("substring", "app", "www.foo.com") == "www.foo.com"


# ── 规则 API ──────────────────────────────────────────────


def _create(client, **payload):
    body = {"match_type": "substring", "scope": "any", "category": "study_work"}
    body.update(payload)
    return client.post("/app-rules", json=body)


def test_rule_crud_roundtrip(client):
    r = _create(client, match_value="FooBar", display_label="示例")
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["match_value"] == "foobar"  # 已小写
    assert row["display_label"] == "示例"
    assert row["enabled"] is True

    listed = client.get("/app-rules").json()
    assert [x["id"] for x in listed] == [row["id"]]

    p = client.patch(f"/app-rules/{row['id']}", json={"category": "game", "priority": 5})
    assert p.status_code == 200
    assert p.json()["category"] == "game"
    assert p.json()["priority"] == 5
    assert p.json()["match_value"] == "foobar"  # 未传的字段不动

    assert client.delete(f"/app-rules/{row['id']}").status_code == 204
    assert client.get("/app-rules").json() == []


def test_rule_rejects_bad_payload(client):
    # 线下大类不能作为设备规则
    assert _create(client, match_value="x", category="bed").status_code == 422
    # match_type / scope 白名单
    assert _create(client, match_value="x", match_type="regex").status_code == 422
    assert _create(client, match_value="x", scope="window").status_code == 422
    # 空匹配值
    assert _create(client, match_value="   ").status_code == 422
    # priority 越界
    assert _create(client, match_value="x", priority=9999).status_code == 422


def test_rule_upsert_same_key(client):
    a = _create(client, match_value="FooBar", category="video").json()
    b = _create(client, match_value="foobar", category="game", display_label="别名").json()
    assert a["id"] == b["id"]  # 归一化后同键 → 覆盖而非新增
    assert b["category"] == "game"
    assert b["display_label"] == "别名"
    assert len(client.get("/app-rules").json()) == 1


def test_same_value_different_match_type_are_two_rows(client):
    a = _create(client, match_value="foo", match_type="exact").json()
    b = _create(client, match_value="foo", match_type="substring").json()
    assert a["id"] != b["id"]
    assert len(client.get("/app-rules").json()) == 2


def test_domain_scope_normalizes_match_value(client):
    row = _create(client, match_value="WWW.Bilibili.COM", scope="domain").json()
    assert row["match_value"] == "bilibili.com"


def test_update_and_delete_missing_404(client):
    assert client.patch("/app-rules/999", json={"category": "game"}).status_code == 404
    assert client.delete("/app-rules/999").status_code == 404


def test_disabled_rule_is_ignored_by_loader(client, db_session):
    from app.services.app_rule_service import load_rules

    row = _create(client, match_value="unknown.app", category="study_work").json()
    assert classify_app("unknown.app", None, load_rules(db_session, user_id=1)) == "study_work"

    client.patch(f"/app-rules/{row['id']}", json={"enabled": False})
    assert classify_app("unknown.app", None, load_rules(db_session, user_id=1)) == "other_online"
    # 仍列在清单里（前端要能重新启用）
    assert client.get("/app-rules").json()[0]["enabled"] is False


# ── 设备侧联动 ────────────────────────────────────────────

UNKNOWN = "com.example.unknown"


def _make_pc(client, name="电脑"):
    r = client.post("/devices", json={"name": name, "platform": "pc"})
    assert r.status_code == 201
    return r.json()


def _ms(hour, minute=0):
    return int(datetime(2026, 9, 11, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)


def _ingest_sessions(client, token, entries, date="2026-09-11"):
    return client.post(
        "/ingest/usage/sessions",
        json={"date": date, "entries": entries},
        headers={"X-Device-Token": token},
    )


def test_uncategorized_apps_lists_then_rule_clears_it(client, monkeypatch):
    monkeypatch.setattr("app.routers.ingest._local_tz_offset_minutes", lambda: 0)
    pc = _make_pc(client)
    r = _ingest_sessions(
        client, pc["token"],
        [
            {"app": UNKNOWN, "label": "未知应用", "start_ms": _ms(10), "end_ms": _ms(10, 30)},
            {"app": "Code.exe", "label": "VS Code", "start_ms": _ms(11), "end_ms": _ms(11, 30)},
        ],
    )
    assert r.status_code == 200, r.text

    listed = client.get(
        "/devices/uncategorized-apps?start=2026-09-11&end=2026-09-11"
    ).json()
    assert [a["app"] for a in listed] == [UNKNOWN]
    assert listed[0]["seconds"] == 1800
    assert listed[0]["category"] == "other_online"

    # 标记之后清单里就没有它了
    _create(client, match_value=UNKNOWN, scope="app", category="study_work")
    assert client.get(
        "/devices/uncategorized-apps?start=2026-09-11&end=2026-09-11"
    ).json() == []


def test_uncategorized_apps_skips_known_and_web(client, monkeypatch):
    monkeypatch.setattr("app.routers.ingest._local_tz_offset_minutes", lambda: 0)
    pc = _make_pc(client)
    _ingest_sessions(
        client, pc["token"],
        [
            {"app": "Code.exe", "label": "VS Code", "start_ms": _ms(10), "end_ms": _ms(10, 30)},
            {"app": "web:unknown-site.com", "start_ms": _ms(11), "end_ms": _ms(11, 30)},
            {"app": "tiny.app", "start_ms": _ms(12), "end_ms": _ms(12) + 10_000},
        ],
    )
    listed = client.get(
        "/devices/uncategorized-apps?start=2026-09-11&end=2026-09-11&min_seconds=60"
    ).json()
    assert listed == []  # 已识别 / 站点 / 太短，都不列


def test_uncategorized_apps_validates_params(client):
    assert client.get("/devices/uncategorized-apps?start=2026-9-11&end=2026-09-11").status_code == 422
    assert client.get("/devices/uncategorized-apps?start=2026-09-11").status_code == 422


def test_rule_change_reclassifies_history_after_rebuild(client, db_session, monkeypatch):
    """改规则后重算区间 → 历史 DEVICE 事件的 category/note 跟着变（幂等）。"""
    from app.services import behavior_service

    monkeypatch.setattr("app.routers.ingest._local_tz_offset_minutes", lambda: 0)
    pc = _make_pc(client)
    _ingest_sessions(
        client, pc["token"],
        [{"app": UNKNOWN, "label": "未知应用", "start_ms": _ms(10), "end_ms": _ms(10, 30)}],
    )

    rows = client.get("/events?limit=50").json()
    device_events = [e for e in rows if e["source"] == "DEVICE"]
    assert len(device_events) == 1
    assert device_events[0]["category"] == "other_online"

    _create(client, match_value=UNKNOWN, scope="app", category="study_work", display_label="示例应用")

    first = behavior_service.rebuild_range(
        db_session, "2026-09-11", "2026-09-11", user_id=1
    )
    device_events = [
        e for e in client.get("/events?limit=50").json() if e["source"] == "DEVICE"
    ]
    assert len(device_events) == 1
    assert device_events[0]["category"] == "study_work"
    assert device_events[0]["note"] == "示例应用"

    # 再跑一次：幂等，不累积
    second = behavior_service.rebuild_range(
        db_session, "2026-09-11", "2026-09-11", user_id=1
    )
    assert second["created"] == first["created"]
    assert len(
        [e for e in client.get("/events?limit=50").json() if e["source"] == "DEVICE"]
    ) == 1


def test_display_label_shows_in_usage_overview(client):
    pc = _make_pc(client)
    client.post(
        "/ingest/usage/hourly",
        json={"date": "2026-09-11", "entries": [{"app": UNKNOWN, "hour": 9, "seconds": 1800}]},
        headers={"X-Device-Token": pc["token"]},
    )
    _create(client, match_value=UNKNOWN, scope="app", category="utility", display_label="内部工具")

    o = client.get("/devices/usage-overview/2026-09-11").json()["devices"][0]
    assert o["apps"][0]["app"] == UNKNOWN  # app id 保持采集原名（规则编辑要用）
    assert o["apps"][0]["label"] == "内部工具"
    # 当前大类随行返回：就是就地编辑面板的默认值
    assert o["apps"][0]["category"] == "utility"


def test_overview_app_category_follows_rules(client):
    """编辑面板默认值取自接口的 category，改规则必须立刻反映（不是库里陈旧快照）。

    这正是 bug：前端三处入口曾写死 `other_online`/`browsing`，导致「设完再点开
    还是其他在线」，接口也压根没返回 category。
    """
    pc = _make_pc(client)
    client.post(
        "/ingest/usage/hourly",
        json={
            "date": "2026-09-11",
            "entries": [{"app": UNKNOWN, "hour": 21, "seconds": 600}],
        },
        headers={"X-Device-Token": pc["token"]},
    )
    o = client.get("/devices/usage-overview/2026-09-11").json()["devices"][0]
    assert o["apps"][0]["category"] == "other_online"

    _create(client, match_value=UNKNOWN, scope="app", category="forum")
    o = client.get("/devices/usage-overview/2026-09-11").json()["devices"][0]
    assert o["apps"][0]["category"] == "forum"


def test_overview_site_category_follows_domain_rules(client):
    """站点行没规则时是「浏览」；建域名规则后跟着变（同样供编辑面板回填）。"""
    pc = _make_pc(client)
    client.post(
        "/ingest/usage/hourly",
        json={"date": "2026-09-11", "entries": [{"app": "web:example.org", "hour": 9, "seconds": 600}]},
        headers={"X-Device-Token": pc["token"]},
    )
    o = client.get("/devices/usage-overview/2026-09-11").json()["devices"][0]
    assert o["websites"][0]["category"] == "browsing"

    _create(client, match_value="example.org", scope="domain", category="forum")
    o = client.get("/devices/usage-overview/2026-09-11").json()["devices"][0]
    assert o["websites"][0]["category"] == "forum"


def test_rebuild_range_endpoint_queues(client, monkeypatch):
    calls = []

    def _fake(start, end, *, user_id, tz_offset=0):
        calls.append((start, end, tz_offset))

    monkeypatch.setattr("app.services.rebuild_queue.rebuild_activity_range", _fake)
    r = client.post("/devices/rebuild-activity-range?start=2026-09-01&end=2026-09-03")
    assert r.status_code == 202, r.text
    assert r.json() == {
        "queued": True,
        "start": "2026-09-01",
        "end": "2026-09-03",
        "days": 3,
    }
    assert calls == [("2026-09-01", "2026-09-03", 0)]


def test_rebuild_range_endpoint_rejects_bad_range(client):
    # 格式（由 Query pattern 拦下）
    assert client.post("/devices/rebuild-activity-range?start=2026-9-1&end=2026-09-03").status_code == 422
    # end 早于 start
    r = client.post("/devices/rebuild-activity-range?start=2026-09-05&end=2026-09-03")
    assert r.status_code == 422 and "end 不能早于 start" in r.text
    # 超过 90 天
    r2 = client.post("/devices/rebuild-activity-range?start=2026-01-01&end=2026-12-31")
    assert r2.status_code == 422 and "最多 90 天" in r2.text


def test_rule_write_queues_rebuild(client, monkeypatch):
    """写规则时带上 rebuild_start/rebuild_end 会顺手入队重算。"""
    calls = []
    monkeypatch.setattr(
        "app.services.rebuild_queue.rebuild_activity_range",
        lambda start, end, *, user_id, tz_offset=0: calls.append((start, end)),
    )
    r = client.post(
        "/app-rules?rebuild_start=2026-09-11&rebuild_end=2026-09-11",
        json={"match_value": "x", "category": "game"},
    )
    assert r.status_code == 201, r.text
    assert calls == [("2026-09-11", "2026-09-11")]

    # 不带参数则不入队
    client.post("/app-rules", json={"match_value": "y", "category": "game"})
    assert len(calls) == 1

    # 区间非法 → 422（规则本身已存下来了）
    bad = client.post(
        "/app-rules?rebuild_start=2026-09-11&rebuild_end=2026-09-01",
        json={"match_value": "z", "category": "game"},
    )
    assert bad.status_code == 422
