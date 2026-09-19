from datetime import datetime, timezone

from sqlalchemy import select

from app.models.event import Event, EventType
from app.models.experiment import (
    Experiment,
    ExperimentLog,
    ExperimentStatus,
    ExperimentStatusEvent,
)
from app.services import memory_service

GOOD_JSON = {
    "day_summary": "写了不少代码。",
    "highlights": ["完成同步"],
    "concerns": [],
    "patterns": [],
    "suggestions": [],
    "insights": [],
}

WEEK_JSON = {
    "period_summary": "本周稳定。",
    "patterns": [],
    "missed_insights": [],
    "suggestions": [],
}

TZ = 480


def _make_experiment(
    db_session,
    *,
    key: str = "learn_count",
    source: str = "event_count:LEARNING_START",
    started_at: datetime = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc),
    status: str = ExperimentStatus.RUNNING.value,
) -> Experiment:
    exp = Experiment(
        user_id=1,
        name="复盘同步实验",
        status=status,
        started_at=started_at,
        metrics=[{"key": key, "name": "学习次数", "direction": "up_good", "source": source}],
    )
    db_session.add(exp)
    db_session.commit()
    db_session.refresh(exp)
    return exp


def _add_event(db_session, day: int, hour_utc: int) -> None:
    db_session.add(
        Event(
            user_id=1,
            timestamp=datetime(2026, 9, day, hour_utc, 0, tzinfo=timezone.utc),
            type=EventType.LEARNING_START.value,
        )
    )
    db_session.commit()


def test_daily_review_writes_day_data_into_experiment(client, db_session, monkeypatch):
    exp = _make_experiment(db_session)
    _add_event(db_session, 2, 9)  # 东八区 9/2 17:00，落在 9/2 本地日
    monkeypatch.setattr(
        "app.services.review_service.chat_json", lambda *a, **k: GOOD_JSON
    )

    r = client.post("/reviews?tz_offset=480", json={"date": "2026-09-02"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    logs = client.get(f"/experiments/{exp.id}/logs").json()
    assert len(logs) == 1
    log = logs[0]
    assert log["metric"] == "learn_count"
    assert log["source"] == "AUTO"
    assert log["value"] == 1.0


def test_review_sync_preserves_manual_points(client, db_session, monkeypatch):
    exp = _make_experiment(db_session)
    _add_event(db_session, 2, 9)
    _add_event(db_session, 2, 10)
    db_session.add(
        ExperimentLog(
            user_id=1,
            experiment_id=exp.id,
            metric="learn_count",
            value=42.0,
            note="手动记录",
            timestamp=datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc),
            source="MANUAL",
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.review_service.chat_json", lambda *a, **k: GOOD_JSON
    )

    client.post("/reviews?tz_offset=480", json={"date": "2026-09-02"})

    logs = client.get(f"/experiments/{exp.id}/logs").json()
    manual = [row for row in logs if row["source"] == "MANUAL"]
    auto = [row for row in logs if row["source"] == "AUTO"]
    assert len(manual) == 1 and manual[0]["value"] == 42.0
    assert len(auto) == 1 and auto[0]["value"] == 2.0


def test_review_sync_skips_experiments_outside_window(client, db_session, monkeypatch):
    exp = _make_experiment(
        db_session,
        started_at=datetime(2026, 9, 5, 0, 0, tzinfo=timezone.utc),  # 该日尚未开始
    )
    _add_event(db_session, 2, 9)
    monkeypatch.setattr(
        "app.services.review_service.chat_json", lambda *a, **k: GOOD_JSON
    )

    client.post("/reviews?tz_offset=480", json={"date": "2026-09-02"})

    assert client.get(f"/experiments/{exp.id}/logs").json() == []


def test_regenerate_review_removes_stale_auto_points(client, db_session, monkeypatch):
    exp = _make_experiment(db_session)
    _add_event(db_session, 2, 9)
    monkeypatch.setattr(
        "app.services.review_service.chat_json", lambda *a, **k: GOOD_JSON
    )
    client.post("/reviews?tz_offset=480", json={"date": "2026-09-02"})
    assert len(client.get(f"/experiments/{exp.id}/logs").json()) == 1

    # 源数据消失后重新生成复盘 → 无记录日不产点，旧 AUTO 点被清除
    event = db_session.scalar(select(Event))
    db_session.delete(event)
    db_session.commit()

    client.post("/reviews?tz_offset=480", json={"date": "2026-09-02", "force": True})
    assert client.get(f"/experiments/{exp.id}/logs").json() == []


def test_week_review_syncs_each_day(client, db_session, monkeypatch):
    exp = _make_experiment(db_session)
    _add_event(db_session, 7, 3)  # 东八区 9/7 11:00
    _add_event(db_session, 9, 5)  # 东八区 9/9 13:00
    memory_service.store_memory(
        db_session,
        kind="insight",
        content="周素材",
        source_ref="insight:2026-09-07",
        embed_vectors=False,
        user_id=1,
    )
    monkeypatch.setattr(
        "app.services.period_review_service.chat_json", lambda *a, **k: WEEK_JSON
    )

    r = client.post(
        "/reviews/period",
        json={"period_type": "week", "date": "2026-09-09", "tz_offset": TZ},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    logs = client.get(f"/experiments/{exp.id}/logs").json()
    autos = [row for row in logs if row["source"] == "AUTO"]
    assert len(autos) == 2  # 两天各一个数据点
    assert all(row["value"] == 1.0 for row in autos)


# ── 暂停期排除 ─────────────────────────────────────────────


def _status_event(db_session, exp: Experiment, to_status: str, when: datetime) -> None:
    db_session.add(
        ExperimentStatusEvent(
            user_id=1,
            experiment_id=exp.id,
            from_status=None,
            to_status=to_status,
            reason=None,
            created_at=when,
        )
    )
    db_session.commit()


def test_fully_paused_day_produces_no_auto_point(client, db_session):
    exp = _make_experiment(db_session)
    _status_event(db_session, exp, "RUNNING", datetime(2026, 9, 1, tzinfo=timezone.utc))
    # UTC 9/1 16:00 = 东八区 9/2 00:00 → 本地 9/2 整天暂停
    _status_event(
        db_session, exp, "PAUSED", datetime(2026, 9, 1, 16, tzinfo=timezone.utc)
    )
    _add_event(db_session, 2, 9)  # 暂停日的记录不应进实验

    r = client.post(f"/experiments/{exp.id}/aggregate?tz_offset={TZ}")
    assert r.status_code == 200
    assert r.json()["created"] == 0
    assert client.get(f"/experiments/{exp.id}/logs").json() == []


def test_partially_running_day_still_produces_point(client, db_session):
    exp = _make_experiment(db_session)
    _status_event(db_session, exp, "RUNNING", datetime(2026, 9, 1, tzinfo=timezone.utc))
    # 9/2 UTC 12:00 才暂停 → 当天运行了半天，仍然算数
    _status_event(db_session, exp, "PAUSED", datetime(2026, 9, 2, 12, tzinfo=timezone.utc))
    _add_event(db_session, 2, 9)

    r = client.post(f"/experiments/{exp.id}/aggregate?tz_offset={TZ}")
    assert r.status_code == 200
    assert r.json()["created"] == 1
    logs = client.get(f"/experiments/{exp.id}/logs").json()
    assert len(logs) == 1 and logs[0]["value"] == 1.0


def test_pausing_removes_existing_auto_points_on_reaggregate(client, db_session):
    exp = _make_experiment(db_session)
    _status_event(db_session, exp, "RUNNING", datetime(2026, 9, 1, tzinfo=timezone.utc))
    _add_event(db_session, 2, 9)

    client.post(f"/experiments/{exp.id}/aggregate?tz_offset={TZ}")
    assert len(client.get(f"/experiments/{exp.id}/logs").json()) == 1

    # 事后补记"本地 9/2 整天暂停" → 重新聚合时旧 AUTO 点被清除
    _status_event(
        db_session, exp, "PAUSED", datetime(2026, 9, 1, 16, tzinfo=timezone.utc)
    )
    r = client.post(f"/experiments/{exp.id}/aggregate?tz_offset={TZ}")
    assert r.json()["deleted"] == 1
    assert client.get(f"/experiments/{exp.id}/logs").json() == []


def test_pause_exclusion_keeps_manual_points(client, db_session):
    exp = _make_experiment(db_session)
    _status_event(db_session, exp, "RUNNING", datetime(2026, 9, 1, tzinfo=timezone.utc))
    _status_event(
        db_session, exp, "PAUSED", datetime(2026, 9, 1, 16, tzinfo=timezone.utc)
    )
    db_session.add(
        ExperimentLog(
            user_id=1,
            experiment_id=exp.id,
            metric="learn_count",
            value=7.0,
            note="暂停日手动记录",
            timestamp=datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc),
            source="MANUAL",
        )
    )
    db_session.commit()

    client.post(f"/experiments/{exp.id}/aggregate?tz_offset={TZ}")
    logs = client.get(f"/experiments/{exp.id}/logs").json()
    assert len(logs) == 1 and logs[0]["source"] == "MANUAL"


def test_legacy_experiment_without_status_history_still_aggregates(client, db_session):
    """旧实验没有状态历史 → 不排除任何天，保持兼容。"""
    exp = _make_experiment(db_session)
    _add_event(db_session, 2, 9)

    r = client.post(f"/experiments/{exp.id}/aggregate?tz_offset={TZ}")
    assert r.status_code == 200
    assert r.json()["created"] == 1


def test_review_on_paused_day_writes_no_point(client, db_session, monkeypatch):
    """复盘联动也遵守暂停排除：暂停日不写 AUTO 点。"""
    exp = _make_experiment(db_session)
    _status_event(db_session, exp, "RUNNING", datetime(2026, 9, 1, tzinfo=timezone.utc))
    _status_event(
        db_session, exp, "PAUSED", datetime(2026, 9, 1, 16, tzinfo=timezone.utc)
    )
    _add_event(db_session, 2, 9)
    monkeypatch.setattr(
        "app.services.review_service.chat_json", lambda *a, **k: GOOD_JSON
    )

    r = client.post("/reviews?tz_offset=480", json={"date": "2026-09-02"})
    assert r.status_code == 200
    assert client.get(f"/experiments/{exp.id}/logs").json() == []
