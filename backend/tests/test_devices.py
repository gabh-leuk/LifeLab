


def _make_device(client, name="我的手机", platform="android"):
    r = client.post("/devices", json={"name": name, "platform": platform})
    assert r.status_code == 201
    return r.json()


def _ingest_hourly(client, token, date, entries):
    return client.post(
        "/ingest/usage/hourly",
        json={"date": date, "entries": entries},
        headers={"X-Device-Token": token},
    )


def _ingest(client, token, date, entries):
    """上报「某天某应用用了多久」——旧日粒度端点的兼容壳。

    日粒度表已下线（见 alembic d1e2f3a4b5c6），把每条 entry 摊到连续小时再走
    hourly：seconds 超过 3600 时拆到相邻小时（hourly 每小时上限 3600），
    天合计不变，所以调用方的断言口径与旧日粒度完全一致。
    非法载荷（空 app、负时长）原样透传，由 hourly 的 schema 报 422。
    """
    hourly = []
    for e in entries:
        h = 9
        remaining = e["seconds"]
        while remaining > 0 and h <= 23:
            chunk = min(3600, remaining)
            hourly.append({**e, "hour": h, "seconds": chunk})
            remaining -= chunk
            h += 1
    return _ingest_hourly(client, token, date, hourly)


def test_create_device_returns_token_once(client, db_session):
    body = _make_device(client)
    assert body["token"]
    assert len(body["token"]) > 20
    assert body["device"]["platform"] == "android"

    listed = client.get("/devices").json()
    assert len(listed) == 1
    assert "token" not in listed[0]
    assert "token_hash" not in listed[0]

    # 库中只存 hash
    from app.models.device import Device

    row = db_session.get(Device, body["device"]["id"])
    assert row is not None
    assert row.token_hash != body["token"]
    assert len(row.token_hash) == 64


def test_create_device_rejects_bad_platform(client):
    r = client.post("/devices", json={"name": "x", "platform": "ios"})
    assert r.status_code == 422


def test_ingest_requires_valid_token(client):
    body = _make_device(client)
    # 无 token
    r = client.post(
        "/ingest/usage/hourly",
        json={"date": "2026-09-11", "entries": [{"app": "微信", "hour": 9, "seconds": 60}]},
    )
    assert r.status_code == 401
    # 错误 token
    r2 = _ingest(client, "not-a-real-token", "2026-09-11", [{"app": "微信", "seconds": 60}])
    assert r2.status_code == 401
    # 正确 token
    r3 = _ingest(client, body["token"], "2026-09-11", [{"app": "微信", "seconds": 60}])
    assert r3.status_code == 200


def test_ingest_replaces_same_day(client, db_session):
    body = _make_device(client)
    token = body["token"]

    r1 = _ingest(
        client, token, "2026-09-11",
        [{"app": "微信", "label": "微信", "seconds": 3600, "switches": 10}],
    )
    assert r1.json()["received"] == 1 and r1.json()["replaced"] == 0

    # 同日重报（整日替换），数据变化
    r2 = _ingest(
        client, token, "2026-09-11",
        [
            {"app": "微信", "seconds": 1800},
            {"app": "bilibili", "label": "哔哩哔哩", "seconds": 1200},
        ],
    )
    assert r2.json()["received"] == 2 and r2.json()["replaced"] == 1

    day = client.get("/devices/usage-hourly/2026-09-11").json()
    assert day["total_seconds"] == 3000
    assert [i["app"] for i in day["items"]] == ["微信", "bilibili"]

    # 再次同日报，仍旧 2 条（幂等）
    r3 = _ingest(
        client, token, "2026-09-11",
        [
            {"app": "微信", "seconds": 1800},
            {"app": "bilibili", "seconds": 1200},
        ],
    )
    assert r3.json()["replaced"] == 2
    assert len(client.get("/devices/usage-hourly/2026-09-11").json()["items"]) == 2


def test_ingest_merges_duplicate_apps(client):
    body = _make_device(client)
    r = _ingest(
        client, body["token"], "2026-09-11",
        [
            {"app": "微信", "seconds": 600},
            {"app": "微信", "seconds": 900, "switches": 3},
        ],
    )
    assert r.json()["received"] == 1
    day = client.get("/devices/usage-hourly/2026-09-11").json()
    assert day["total_seconds"] == 1500
    assert day["items"][0]["switches"] == 3


def test_ingest_validates_payload(client):
    body = _make_device(client)
    token = body["token"]

    def bad(date, entries):
        return _ingest_hourly(client, token, date, entries)

    assert bad("bad-date", [{"app": "x", "hour": 9, "seconds": 1}]).status_code == 422
    assert bad("2026-09-11", []).status_code == 422
    assert bad("2026-09-11", [{"app": "", "hour": 9, "seconds": 1}]).status_code == 422
    assert bad("2026-09-11", [{"app": "x", "hour": 9, "seconds": -1}]).status_code == 422
    assert bad("2026-09-11", [{"app": "x", "hour": 24, "seconds": 1}]).status_code == 422
    assert bad("2026-09-11", [{"app": "x", "hour": 9, "seconds": 99999}]).status_code == 422


def test_usage_hourly_day_404(client):
    body = _make_device(client)
    _ingest(
        client, body["token"], "2026-09-11",
        [
            {"app": "微信", "seconds": 600},
            {"app": "bilibili", "seconds": 300},
        ],
    )
    assert client.get("/devices/usage-hourly/2026-09-10").status_code == 404
    assert client.get("/devices/usage-hourly/2026-09-11").json()["total_seconds"] == 900


def test_delete_device_cascades_usage(client):
    body = _make_device(client)
    device_id = body["device"]["id"]
    _ingest(client, body["token"], "2026-09-11", [{"app": "微信", "seconds": 60}])

    assert client.delete(f"/devices/{device_id}").status_code == 204
    assert client.get("/devices").json() == []
    assert client.get("/devices/usage-hourly/2026-09-11").status_code == 404
    # token 随之失效
    assert _ingest(client, body["token"], "2026-09-11", [{"app": "微信", "seconds": 60}]).status_code == 401


def test_ingest_touches_last_seen(client, db_session):
    body = _make_device(client)
    _ingest(client, body["token"], "2026-09-11", [{"app": "微信", "seconds": 60}])
    from app.models.device import Device

    row = db_session.get(Device, body["device"]["id"])
    assert row.last_seen_at is not None


# ── 与实验自动聚合联动（usage_duration / usage_total） ─────


def _make_usage_experiment(client, metrics):
    r = client.post(
        "/experiments",
        json={
            "name": "设备使用实验",
            "metrics": metrics,
            "expected_days": 14,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_metric_source_whitelist_accepts_usage(client):
    exp = _make_usage_experiment(
        client,
        [
            {"key": "wechat", "name": "微信时长", "direction": "down_good", "source": "usage_duration:微信"},
            {"key": "total", "name": "总使用", "direction": "neutral", "source": "usage_total"},
        ],
    )
    assert [m["source"] for m in exp["metrics"]] == ["usage_duration:微信", "usage_total"]
    # 非法 usage 来源被拒
    bad = client.post(
        "/experiments",
        json={
            "name": "x",
            "metrics": [{"key": "x", "name": "x", "source": "usage_duration:"}],
        },
    )
    assert bad.status_code == 422


def test_aggregate_usage_sources_into_experiment(client, db_session):
    body = _make_device(client)
    _ingest(
        client, body["token"], "2026-09-11",
        [
            {"app": "微信", "seconds": 3600},
            {"app": "bilibili", "seconds": 1800},
        ],
    )
    exp = _make_usage_experiment(
        client,
        [
            {"key": "wechat_min", "name": "微信时长", "direction": "down_good", "source": "usage_duration:微信"},
            {"key": "total_min", "name": "总使用", "direction": "neutral", "source": "usage_total"},
        ],
    )
    # 把实验窗口固定在 2026-09-11（该日有使用数据）
    from datetime import datetime, timezone

    from app.models.experiment import (
        Experiment,
        ExperimentStatus,
        ExperimentStatusEvent,
    )

    exp_row = db_session.get(Experiment, exp["id"])
    exp_row.started_at = datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc)
    exp_row.ended_at = datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)
    exp_row.status = ExperimentStatus.RUNNING.value
    db_session.add(
        ExperimentStatusEvent(
            user_id=1,
            experiment_id=exp["id"],
            from_status=None,
            to_status="RUNNING",
            created_at=datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc),
        )
    )
    db_session.commit()

    r = client.post(f"/experiments/{exp['id']}/aggregate?tz_offset=0")
    assert r.status_code == 200
    logs = client.get(f"/experiments/{exp['id']}/logs").json()
    by_metric = {row["metric"]: row for row in logs if row["source"] == "AUTO"}
    assert by_metric["wechat_min"]["value"] == 60.0  # 3600s → 60 分钟
    assert by_metric["total_min"]["value"] == 90.0  # 5400s → 90 分钟


def test_aggregate_usage_no_data_day_produces_nothing(client, db_session):
    _make_device(client)
    exp = _make_usage_experiment(
        client,
        [
            {"key": "total_min", "name": "总使用", "direction": "neutral", "source": "usage_total"},
        ],
    )
    from datetime import datetime, timezone

    from app.models.experiment import (
        Experiment,
        ExperimentStatus,
        ExperimentStatusEvent,
    )

    exp_row = db_session.get(Experiment, exp["id"])
    exp_row.started_at = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)
    exp_row.ended_at = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
    exp_row.status = ExperimentStatus.RUNNING.value
    db_session.add(
        ExperimentStatusEvent(
            user_id=1,
            experiment_id=exp["id"],
            from_status=None,
            to_status="RUNNING",
            created_at=datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc),
        )
    )
    db_session.commit()

    client.post(f"/experiments/{exp['id']}/aggregate?tz_offset=0")
    logs = client.get(f"/experiments/{exp['id']}/logs").json()
    assert [row for row in logs if row["source"] == "AUTO"] == []  # 无采集数据 → 不产点


def test_usage_duration_unknown_app_zero_when_day_has_data(client, db_session):
    """当天有采集数据但没有该 app → 0（真实的"没使用"），而非无数据。"""
    body = _make_device(client)
    _ingest(client, body["token"], "2026-09-11", [{"app": "微信", "seconds": 600}])
    exp = _make_usage_experiment(
        client,
        [
            {"key": "bili_min", "name": "B站时长", "direction": "down_good", "source": "usage_duration:bilibili"},
        ],
    )
    from datetime import datetime, timezone

    from app.models.experiment import (
        Experiment,
        ExperimentStatus,
        ExperimentStatusEvent,
    )

    exp_row = db_session.get(Experiment, exp["id"])
    exp_row.started_at = datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc)
    exp_row.ended_at = datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)
    exp_row.status = ExperimentStatus.RUNNING.value
    db_session.add(
        ExperimentStatusEvent(
            user_id=1,
            experiment_id=exp["id"],
            from_status=None,
            to_status="RUNNING",
            created_at=datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc),
        )
    )
    db_session.commit()

    client.post(f"/experiments/{exp['id']}/aggregate?tz_offset=0")
    logs = client.get(f"/experiments/{exp['id']}/logs").json()
    auto = [row for row in logs if row["source"] == "AUTO"]
    assert len(auto) == 1 and auto[0]["value"] == 0.0


# ── usage_platform（按设备平台过滤） ───────────────────────


def test_usage_platform_filters_device(client, db_session):
    """手机与电脑同一天都有数据时，usage_platform 分别只统计各自的时长。"""
    from datetime import datetime, timezone

    from app.models.experiment import (
        Experiment,
        ExperimentStatus,
        ExperimentStatusEvent,
    )

    phone = client.post("/devices", json={"name": "手机", "platform": "android"}).json()
    pc = client.post("/devices", json={"name": "电脑", "platform": "pc"}).json()
    _ingest(
        client, phone["token"], "2026-09-11",
        [{"app": "com.tencent.mm", "seconds": 3600}],
    )
    _ingest(
        client, pc["token"], "2026-09-11",
        [{"app": "Code.exe", "seconds": 7200}],
    )

    exp = _make_usage_experiment(
        client,
        [
            {"key": "phone_min", "name": "手机时长", "direction": "down_good", "source": "usage_platform:android"},
            {"key": "pc_min", "name": "电脑时长", "direction": "neutral", "source": "usage_platform:pc"},
            {"key": "total_min", "name": "总使用", "direction": "neutral", "source": "usage_total"},
        ],
    )
    start = datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc)
    row = db_session.get(Experiment, exp["id"])
    row.started_at = start
    row.ended_at = datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)
    row.status = ExperimentStatus.RUNNING.value
    db_session.add(
        ExperimentStatusEvent(
            user_id=1,
            experiment_id=exp["id"],
            from_status=None,
            to_status="RUNNING",
            created_at=start,
        )
    )
    db_session.commit()

    client.post(f"/experiments/{exp['id']}/aggregate?tz_offset=0")
    logs = client.get(f"/experiments/{exp['id']}/logs").json()
    by_metric = {row["metric"]: row["value"] for row in logs if row["source"] == "AUTO"}
    assert by_metric["phone_min"] == 60.0  # 3600s
    assert by_metric["pc_min"] == 120.0  # 7200s
    assert by_metric["total_min"] == 180.0


def test_usage_platform_rejects_unknown_platform(client):
    r = client.post(
        "/experiments",
        json={
            "name": "x",
            "metrics": [{"key": "x", "name": "x", "source": "usage_platform:ios"}],
        },
    )
    assert r.status_code == 422


# ── 时段级采集（hourly） ───────────────────────────────────


def _run_window(client, db_session, exp_id, start, end):
    """把实验设为在 [start, end) 运行并聚合，返回 {metric: value}（AUTO 点）。"""
    from app.models.experiment import (
        Experiment,
        ExperimentStatus,
        ExperimentStatusEvent,
    )

    row = db_session.get(Experiment, exp_id)
    row.started_at = start
    row.ended_at = end
    row.status = ExperimentStatus.RUNNING.value
    db_session.add(
        ExperimentStatusEvent(
            user_id=1,
            experiment_id=exp_id,
            from_status=None,
            to_status="RUNNING",
            created_at=start,
        )
    )
    db_session.commit()
    client.post(f"/experiments/{exp_id}/aggregate?tz_offset=0")
    logs = client.get(f"/experiments/{exp_id}/logs").json()
    return {r["metric"]: r["value"] for r in logs if r["source"] == "AUTO"}


def test_ingest_hourly_merges_clamps_and_replaces(client, db_session):
    body = _make_device(client, name="电脑", platform="pc")
    token = body["token"]

    r = _ingest_hourly(
        client, token, "2026-09-11",
        [
            {"app": "Code.exe", "hour": 22, "seconds": 1000},
            {"app": "Code.exe", "hour": 22, "seconds": 900},  # 同 (hour,app) 合并
            {"app": "Code.exe", "hour": 23, "seconds": 5000},  # 越界 → clamp 3600
        ],
    )
    assert r.status_code == 200 and r.json()["received"] == 2

    day = client.get("/devices/usage-hourly/2026-09-11").json()
    by = {(it["hour"], it["app"]): it["seconds"] for it in day["items"]}
    assert by[(22, "Code.exe")] == 1900
    assert by[(23, "Code.exe")] == 3600
    assert day["total_seconds"] == 5500

    # 同日重报 = 整日替换（幂等）
    r2 = _ingest_hourly(
        client, token, "2026-09-11", [{"app": "Code.exe", "hour": 22, "seconds": 60}]
    )
    assert r2.json()["replaced"] == 2
    day2 = client.get("/devices/usage-hourly/2026-09-11").json()
    assert len(day2["items"]) == 1 and day2["items"][0]["seconds"] == 60


def test_hourly_window_aggregation_and_day_resolution(client, db_session):
    """窗口只统计窗口内小时；全日指标统计所有小时。"""
    from datetime import datetime, timezone

    pc = _make_device(client, name="电脑", platform="pc")
    _ingest_hourly(
        client, pc["token"], "2026-09-11",
        [
            {"app": "Code.exe", "hour": 21, "seconds": 1800},
            {"app": "Code.exe", "hour": 22, "seconds": 3600},
            {"app": "Code.exe", "hour": 23, "seconds": 1200},
        ],
    )
    exp = _make_usage_experiment(
        client,
        [
            {"key": "night_min", "name": "夜间电脑", "direction": "neutral", "source": "usage_platform:pc@22-24"},
            {"key": "pc_day_min", "name": "电脑全日", "direction": "neutral", "source": "usage_platform:pc"},
        ],
    )
    by = _run_window(
        client, db_session, exp["id"],
        datetime(2026, 9, 11, tzinfo=timezone.utc),
        datetime(2026, 9, 12, tzinfo=timezone.utc),
    )
    assert by["night_min"] == 80.0  # (3600 + 1200) / 60
    assert by["pc_day_min"] == 110.0  # (1800 + 3600 + 1200) / 60，hourly 优先


def test_hourly_window_without_data_produces_no_point(client, db_session):
    """该平台当天没有任何采集 → 窗口为 None → 不产 AUTO 点（不造假 0）。

    「该平台当天有数据但在窗口内为空」是另一回事，见下一个用例（那种是真实的 0）。
    """
    from datetime import datetime, timezone

    phone = _make_device(client, name="手机", platform="android")
    # 有这台设备，但它的数据在**前一天**
    _ingest_hourly(
        client, phone["token"], "2026-09-10",
        [{"app": "com.tencent.mm", "hour": 22, "seconds": 3600}],
    )
    exp = _make_usage_experiment(
        client,
        [
            {"key": "phone_night", "name": "手机夜间", "direction": "down_good", "source": "usage_platform:android@22-24"},
        ],
    )
    by = _run_window(
        client, db_session, exp["id"],
        datetime(2026, 9, 11, tzinfo=timezone.utc),
        datetime(2026, 9, 12, tzinfo=timezone.utc),
    )
    assert by == {}


def test_hourly_window_empty_is_zero_point(client, db_session):
    """有 hourly 数据但窗口内为空 → 0（真实的「该时段没用」）。"""
    from datetime import datetime, timezone

    pc = _make_device(client, name="电脑", platform="pc")
    _ingest_hourly(
        client, pc["token"], "2026-09-11", [{"app": "Code.exe", "hour": 9, "seconds": 600}]
    )
    exp = _make_usage_experiment(
        client,
        [{"key": "night", "name": "夜", "direction": "neutral", "source": "usage_platform:pc@22-24"}],
    )
    by = _run_window(
        client, db_session, exp["id"],
        datetime(2026, 9, 11, tzinfo=timezone.utc),
        datetime(2026, 9, 12, tzinfo=timezone.utc),
    )
    assert by["night"] == 0.0


def test_metric_source_window_whitelist(client):
    ok = ["usage_platform:pc@22-24", "usage_duration:Code.exe@0-6", "usage_total@0-24"]
    for src in ok:
        r = client.post(
            "/experiments",
            json={"name": "x", "metrics": [{"key": "k", "name": "k", "source": src}]},
        )
        assert r.status_code == 201, (src, r.text)

    bad = [
        "usage_platform:pc@22-25",
        "usage_platform:pc@24-2",
        "usage_platform:pc@5-5",
        "state_avg:focus@22-24",  # 时段只允许 usage_*
        "usage_total@22",  # 缺 -止
        "usage_platform:pc@abc",
    ]
    for src in bad:
        r = client.post(
            "/experiments",
            json={"name": "x", "metrics": [{"key": "k", "name": "k", "source": src}]},
        )
        assert r.status_code == 422, (src, r.text)


# ── 使用概览（记忆页只读卡片） ─────────────────────────────


def test_usage_overview_devices(client):
    """两台设备都拿到 24 小时条带；无数据日返回空列表。"""
    pc = _make_device(client, name="电脑", platform="pc")
    phone = _make_device(client, name="手机", platform="android")
    _ingest_hourly(
        client, pc["token"], "2026-09-11",
        [
            {"app": "Code.exe", "label": "VS Code", "hour": 21, "seconds": 600},
            {"app": "Code.exe", "label": "VS Code", "hour": 22, "seconds": 900},
            {"app": "chrome.exe", "hour": 22, "seconds": 300},
        ],
    )
    _ingest_hourly(
        client, phone["token"], "2026-09-11",
        [{"app": "com.tencent.mm", "label": "微信", "hour": 20, "seconds": 1200}],
    )

    res = client.get("/devices/usage-overview/2026-09-11").json()
    assert res["date"] == "2026-09-11"
    devs = {d["name"]: d for d in res["devices"]}
    assert set(devs) == {"电脑", "手机"}
    assert res["devices"][0]["name"] == "电脑"  # 按总时长降序

    pc_o = devs["电脑"]
    assert pc_o["total_seconds"] == 1800  # 600 + 900 + 300
    assert len(pc_o["hours"]) == 24
    by_hour = {h["hour"]: h["seconds"] for h in pc_o["hours"]}
    assert by_hour[21] == 600 and by_hour[22] == 1200 and by_hour[0] == 0
    assert pc_o["apps"][0] == {
        "app": "Code.exe",
        "label": "VS Code",
        "seconds": 1500,
        "switches": 0,
        "category": "study_work",
    }

    phone_o = devs["手机"]
    assert phone_o["total_seconds"] == 1200
    assert len(phone_o["hours"]) == 24
    assert phone_o["apps"][0]["label"] == "微信"

    # 无数据日 → 空列表（不 404）
    empty = client.get("/devices/usage-overview/2026-09-10").json()
    assert empty["devices"] == []


def test_overview_partitions_web_rows(client):
    """web:* 行归入 websites，不计入 total_seconds / apps / 小时条带（避免重复）。"""
    pc = _make_device(client, name="电脑", platform="pc")
    _ingest_hourly(
        client, pc["token"], "2026-09-11",
        [
            {"app": "chrome.exe", "label": "Chrome", "hour": 22, "seconds": 1800},
            {"app": "web:youtube.com", "hour": 22, "seconds": 1200},
            {"app": "web:bilibili.com", "hour": 22, "seconds": 600},
            {"app": "Code.exe", "label": "VS Code", "hour": 23, "seconds": 900},
        ],
    )
    o = client.get("/devices/usage-overview/2026-09-11").json()["devices"][0]
    assert o["total_seconds"] == 2700  # 1800 + 900，不含 web
    assert [a["app"] for a in o["apps"]] == ["chrome.exe", "Code.exe"]
    assert [w["app"] for w in o["websites"]] == ["web:youtube.com", "web:bilibili.com"]
    assert o["websites"][0]["seconds"] == 1200
    # 当前大类随行返回（就地编辑面板的默认值，不是库里陈旧快照）
    assert [a["category"] for a in o["apps"]] == ["browsing", "study_work"]
    assert [w["category"] for w in o["websites"]] == ["video", "video"]
    by_hour = {h["hour"]: h["seconds"] for h in o["hours"]}
    assert by_hour[22] == 1800  # 条带不含 web，否则浏览器时长翻倍


def test_overview_hour_carries_apps(client):
    """分小时条每小时带 Top 应用（供前端悬浮展示）。"""
    pc = _make_device(client, name="电脑", platform="pc")
    _ingest_hourly(
        client, pc["token"], "2026-09-11",
        [
            {"app": "Code.exe", "label": "VS Code", "hour": 22, "seconds": 900},
            {"app": "chrome.exe", "label": "Chrome", "hour": 22, "seconds": 300},
            {"app": "Code.exe", "label": "VS Code", "hour": 9, "seconds": 600},
        ],
    )
    o = client.get("/devices/usage-overview/2026-09-11").json()["devices"][0]
    hours = {h["hour"]: h for h in o["hours"]}
    assert [a["app"] for a in hours[22]["apps"]] == ["Code.exe", "chrome.exe"]
    assert hours[22]["apps"][0]["label"] == "VS Code"
    assert [a["app"] for a in hours[9]["apps"]] == ["Code.exe"]
    assert hours[0]["apps"] == []  # 无数据的小时为空


def test_web_site_metric_source_and_total_exclusion(client, db_session):
    """站点数据源只取该站；usage_platform:pc 不因 web 行翻倍。"""
    from datetime import datetime, timezone

    pc = _make_device(client, name="电脑", platform="pc")
    _ingest_hourly(
        client, pc["token"], "2026-09-11",
        [
            {"app": "chrome.exe", "hour": 22, "seconds": 1800},
            {"app": "web:youtube.com", "hour": 22, "seconds": 900},
            {"app": "web:bilibili.com", "hour": 22, "seconds": 600},
        ],
    )
    exp = _make_usage_experiment(
        client,
        [
            {"key": "yt", "name": "油管", "direction": "neutral", "source": "usage_duration:web:youtube.com"},
            {"key": "yt_night", "name": "夜间油管", "direction": "neutral", "source": "usage_duration:web:youtube.com@22-24"},
            {"key": "pc", "name": "电脑", "direction": "neutral", "source": "usage_platform:pc"},
            {"key": "pc_night", "name": "夜间电脑", "direction": "neutral", "source": "usage_platform:pc@22-24"},
        ],
    )
    by = _run_window(
        client, db_session, exp["id"],
        datetime(2026, 9, 11, tzinfo=timezone.utc),
        datetime(2026, 9, 12, tzinfo=timezone.utc),
    )
    assert by["yt"] == 15.0  # 900 / 60
    assert by["yt_night"] == 15.0
    assert by["pc"] == 30.0  # 1800 / 60，不含站点行
    assert by["pc_night"] == 30.0


def test_metric_source_whitelist_accepts_web_duration():
    """usage_duration:web:<域名> 走既有 usage_duration 分支，应被接受（防回归）。"""
    from app.schemas.experiment import validate_metric_source

    assert validate_metric_source("usage_duration:web:youtube.com") == (
        "usage_duration:web:youtube.com"
    )
    assert validate_metric_source("usage_duration:web:youtube.com@22-24") == (
        "usage_duration:web:youtube.com@22-24"
    )


def test_ingest_auto_syncs_experiment_points(client, db_session, monkeypatch):
    """上报即自动重算 AUTO 点，无需手动点「立即聚合」（含 @起-止 窗口源）。"""
    from datetime import datetime, timezone

    from app.models.experiment import (
        Experiment,
        ExperimentStatus,
        ExperimentStatusEvent,
    )

    # 固定聚合日界线，避免依赖跑测试的机器时区
    monkeypatch.setattr("app.routers.ingest._local_tz_offset_minutes", lambda: 0)

    pc = _make_device(client, name="电脑", platform="pc")
    exp = _make_usage_experiment(
        client,
        [
            {
                "key": "night_min",
                "name": "夜间电脑",
                "direction": "neutral",
                "source": "usage_platform:pc@22-24",
            }
        ],
    )
    # 固定运行窗口覆盖该日，但**不**手动聚合
    row = db_session.get(Experiment, exp["id"])
    row.started_at = datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc)
    row.ended_at = datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)
    row.status = ExperimentStatus.RUNNING.value
    db_session.add(
        ExperimentStatusEvent(
            user_id=1,
            experiment_id=exp["id"],
            from_status=None,
            to_status="RUNNING",
            created_at=datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc),
        )
    )
    db_session.commit()
    assert client.get(f"/experiments/{exp['id']}/logs").json() == []  # 尚未聚合

    _ingest_hourly(
        client, pc["token"], "2026-09-11",
        [
            {"app": "Code.exe", "hour": 21, "seconds": 1800},  # 窗口外
            {"app": "Code.exe", "hour": 22, "seconds": 3600},  # 窗口内
            {"app": "Code.exe", "hour": 23, "seconds": 1200},  # 窗口内
        ],
    )

    auto = [
        r
        for r in client.get(f"/experiments/{exp['id']}/logs").json()
        if r["source"] == "AUTO"
    ]
    assert len(auto) == 1
    assert auto[0]["value"] == 80.0  # (3600+1200)/60


# ── known-apps：指标数据源的候选清单（①b） ──────────────────


def test_known_apps_includes_websites(client):
    """要站点行 —— 指标的 usage_duration:web:<域名> 靠它才能被选出来。

    （`uncategorized_apps` 为了展示口径把站点滤掉了，不能拿它当候选清单。）
    """
    body = _make_device(client)
    _ingest_hourly(
        client, body["token"], "2026-09-11",
        [
            {"app": "com.tencent.mm", "label": "微信", "hour": 9, "seconds": 3600},
            {"app": "web:bilibili.com", "label": "哔哩哔哩", "hour": 10, "seconds": 1800},
        ],
    )
    rows = client.get(
        "/devices/known-apps?start=2026-09-11&end=2026-09-11"
    ).json()
    by_app = {r["app"]: r for r in rows}
    assert set(by_app) == {"com.tencent.mm", "web:bilibili.com"}
    # 原样 key（不是显示名）——指标要拿它精确匹配
    assert by_app["com.tencent.mm"]["label"] == "微信"
    assert by_app["web:bilibili.com"]["platform"] == "android"
    # 按时长降序
    assert [r["app"] for r in rows] == ["com.tencent.mm", "web:bilibili.com"]


def test_known_apps_empty_without_devices(client):
    assert client.get("/devices/known-apps?start=2026-09-11&end=2026-09-11").json() == []


def test_inference_prompt_carries_the_real_app_keys(client, monkeypatch):
    """识别 prompt 里必须带上**真实**的 app key，否则模型只会编中文名，永远取不到数。"""
    monkeypatch.setattr(
        "app.services.metric_source_service.local_today", lambda: "2026-09-11"
    )
    captured = {}

    def _fake(system, user, **kw):
        captured["system"] = system
        return {"sources": {"phone_min": "usage_duration:com.tencent.mm"}}

    monkeypatch.setattr("app.services.metric_source_service.chat_json", _fake)

    body = _make_device(client)
    _ingest_hourly(
        client, body["token"], "2026-09-11",
        [
            {"app": "com.tencent.mm", "label": "微信", "hour": 23, "seconds": 3600},
            {"app": "web:bilibili.com", "label": "哔哩哔哩", "hour": 23, "seconds": 600},
        ],
    )
    r = client.post(
        "/experiments/infer-sources",
        json={"metrics": [{"key": "phone_min", "name": "睡前手机时长"}]},
    )
    assert r.status_code == 200, r.text
    assert "com.tencent.mm" in captured["system"]
    assert "web:bilibili.com" in captured["system"]
    assert r.json()["sources"] == {"phone_min": "usage_duration:com.tencent.mm"}


def test_inference_drops_fabricated_app_key(client, monkeypatch):
    """模型编出来的 app key 要丢掉 —— 白名单只查长度，管不了「库里有没有这个应用」。

    编出来的值永远取不到数（`_day_value` 是拿 app 去和库列精确比），
    只会变成一个**静默的零** —— 界面上还看不出为什么没数。
    """
    monkeypatch.setattr(
        "app.services.metric_source_service.local_today", lambda: "2026-09-11"
    )
    monkeypatch.setattr(
        "app.services.metric_source_service.chat_json",
        lambda *a, **k: {
            "sources": {
                "real": "usage_duration:com.tencent.mm",
                "fake": "usage_duration:tv.danmaku.bili",
                "windowed": "usage_duration:com.tencent.mm@22-24",
                "platform": "usage_platform:android@22-24",
            }
        },
    )

    body = _make_device(client)
    _ingest_hourly(
        client, body["token"], "2026-09-11",
        [{"app": "com.tencent.mm", "label": "微信", "hour": 23, "seconds": 3600}],
    )
    r = client.post(
        "/experiments/infer-sources",
        json={
            "metrics": [
                {"key": "real", "name": "微信时长"},
                {"key": "fake", "name": "刷 B 站时长"},
                {"key": "windowed", "name": "睡前微信时长"},
                {"key": "platform", "name": "睡前手机时长"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    sources = r.json()["sources"]
    assert "fake" not in sources  # 编造的包名被丢掉 → 调用方保持 manual
    # 真实 key 照收；带时段后缀的也要认（比对时先剥 @起-止）；usage_platform 不受这道闸约束
    assert sources["real"] == "usage_duration:com.tencent.mm"
    assert sources["windowed"] == "usage_duration:com.tencent.mm@22-24"
    assert sources["platform"] == "usage_platform:android@22-24"
