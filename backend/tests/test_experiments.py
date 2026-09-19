def _create(client, name="测试实验"):
    r = client.post(
        "/experiments",
        json={
            "name": name,
            "question": "减少短视频后注意力是否改善？",
            "hypothesis": "减少无限信息流可提高注意力",
            "variable": "短视频使用时长",
            "indicator": "学习时长、精力、烦躁",
            "metrics": [
                {"key": "focus", "name": "注意力", "unit": "分", "direction": "up_good"},
                {"key": "phone_min", "name": "手机时长", "unit": "分钟", "direction": "down_good"},
            ],
            "expected_days": 14,
            "baseline_note": "基线：每天手机 4 小时",
        },
    )
    assert r.status_code == 201
    return r.json()


def _set_status(client, exp_id, status, **extra):
    return client.post(f"/experiments/{exp_id}/status", json={"status": status, **extra})


def _to_completed(client, exp_id):
    assert _set_status(client, exp_id, "RUNNING").status_code == 200
    r = _set_status(
        client, exp_id, "COMPLETED", completion_analysis="后半段注意力明显提升"
    )
    assert r.status_code == 200
    return r


def _to_concluded(client, exp_id):
    _to_completed(client, exp_id)
    r = _set_status(
        client,
        exp_id,
        "CONCLUDED",
        verdict="SUPPORTED",
        conclusion="减少短视频确实提升注意力",
        conclusion_reason="数据后半段 focus 均值上升、phone_min 下降",
        conclusion_confidence=0.7,
    )
    assert r.status_code == 200
    return r


def test_create_experiment_defaults_draft(client):
    exp = _create(client)
    assert exp["status"] == "DRAFT"
    assert exp["question"].startswith("减少短视频")
    assert exp["started_at"] is None
    assert exp["metrics"][0]["key"] == "focus"
    assert exp["expected_days"] == 14
    assert exp["baseline_note"].startswith("基线")


def test_list_and_get_experiment(client):
    created = _create(client, name="A")
    _create(client, name="B")
    lst = client.get("/experiments")
    assert lst.status_code == 200
    assert len(lst.json()) == 2

    one = client.get(f"/experiments/{created['id']}")
    assert one.status_code == 200
    body = one.json()
    assert body["name"] == "A"
    # 详情 = 本体 + 状态历史 + 统计
    assert body["status_events"] == []
    assert body["stats"]["total_logs"] == 0
    assert body["stats"]["metric_stats"] == []


def test_status_lifecycle(client):
    exp = _create(client)
    exp_id = exp["id"]

    # DRAFT -> RUNNING
    r = _set_status(client, exp_id, "RUNNING")
    assert r.status_code == 200
    assert r.json()["status"] == "RUNNING"
    assert r.json()["started_at"] is not None

    # RUNNING -> PAUSED（必须给原因）
    r = _set_status(client, exp_id, "PAUSED", reason="出差三天无法执行")
    assert r.json()["status"] == "PAUSED"

    # PAUSED -> RUNNING (恢复)
    assert _set_status(client, exp_id, "RUNNING", reason="出差结束恢复").json()[
        "status"
    ] == "RUNNING"

    # RUNNING -> COMPLETED（必须给效果分析）
    done = _set_status(
        client, exp_id, "COMPLETED", completion_analysis="整体注意力有改善"
    )
    assert done.json()["status"] == "COMPLETED"
    assert done.json()["ended_at"] is not None
    assert done.json()["completion_analysis"] == "整体注意力有改善"

    # COMPLETED -> CONCLUDED（必须给判定/结论/依据）
    concl = _set_status(
        client,
        exp_id,
        "CONCLUDED",
        verdict="PARTIALLY_SUPPORTED",
        conclusion="部分支持假设",
        conclusion_reason="专注上升但手机时长未明显下降",
        conclusion_confidence=0.6,
    )
    assert concl.json()["status"] == "CONCLUDED"
    assert concl.json()["result_verdict"] == "PARTIALLY_SUPPORTED"
    assert concl.json()["concluded_at"] is not None


def test_pause_requires_reason(client):
    exp = _create(client)
    eid = exp["id"]
    _set_status(client, eid, "RUNNING")
    r = _set_status(client, eid, "PAUSED")  # 缺 reason
    assert r.status_code == 422
    assert "暂停原因" in r.json()["detail"]


def test_complete_requires_analysis(client):
    exp = _create(client)
    eid = exp["id"]
    _set_status(client, eid, "RUNNING")
    r = _set_status(client, eid, "COMPLETED")  # 缺 completion_analysis
    assert r.status_code == 422
    assert "效果分析" in r.json()["detail"]


def test_conclude_requires_verdict_conclusion_reason(client):
    exp = _create(client)
    eid = exp["id"]
    _to_completed(client, eid)
    r = _set_status(client, eid, "CONCLUDED", conclusion="只有结论")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "判定" in detail and "结论依据" in detail


def test_invalid_transition_409(client):
    exp = _create(client)  # DRAFT
    r = _set_status(client, exp["id"], "COMPLETED", completion_analysis="x")
    assert r.status_code == 409  # DRAFT 不能直接完成


def test_invalid_verdict_422(client):
    exp = _create(client)
    eid = exp["id"]
    _to_completed(client, eid)
    r = _set_status(
        client,
        eid,
        "CONCLUDED",
        verdict="BOGUS",
        conclusion="x",
        conclusion_reason="y",
    )
    assert r.status_code == 422


def test_revert_from_completed(client):
    """误触完成回退：撤销最后一条 COMPLETED 历史，清 ended_at/效果分析，回到进行中。"""
    exp = _create(client)
    eid = exp["id"]
    _to_completed(client, eid)

    before = client.get(f"/experiments/{eid}/status-events").json()
    assert before[-1]["to_status"] == "COMPLETED"

    r = client.post(f"/experiments/{eid}/revert")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "RUNNING"
    assert body["ended_at"] is None
    assert body["completion_analysis"] is None  # 误触的完成分析一并清除
    assert body["started_at"] is not None  # 真实开始时间保留

    # 误触历史被撤销，时间轴只剩真实过程
    after = client.get(f"/experiments/{eid}/status-events").json()
    assert [e["to_status"] for e in after] == ["RUNNING"]


def test_revert_from_concluded_only_back_to_completed(client):
    """下结论后回退：只能回到 COMPLETED（保留完成时间与效果分析），清结论字段。"""
    exp = _create(client)
    eid = exp["id"]
    _to_concluded(client, eid)
    ended_at = client.get(f"/experiments/{eid}").json()["ended_at"]

    r = client.post(f"/experiments/{eid}/revert")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "COMPLETED"
    assert body["ended_at"] == ended_at  # 完成是真实的
    assert body["completion_analysis"] == "后半段注意力明显提升"
    assert body["result_verdict"] is None
    assert body["conclusion"] is None
    assert body["conclusion_reason"] is None
    assert body["conclusion_confidence"] is None
    assert body["concluded_at"] is None

    # 误触的 CONCLUDED 历史被撤销
    events = client.get(f"/experiments/{eid}/status-events").json()
    assert [e["to_status"] for e in events] == ["RUNNING", "COMPLETED"]


def test_concluded_cannot_jump_back_to_running_directly(client):
    """下结论不能再直接回退到 RUNNING/PAUSED（只能回退一步到 COMPLETED）。"""
    exp = _create(client)
    eid = exp["id"]
    _to_concluded(client, eid)
    r = _set_status(client, eid, "RUNNING")
    assert r.status_code == 409
    assert "回退请用 /revert" in r.json()["detail"]
    r2 = _set_status(client, eid, "PAUSED", reason="想继续")
    assert r2.status_code == 409


def test_revert_from_completed_after_pause_keeps_timeline_consistent(client):
    """完成前处于暂停 → 撤销完成后回到进行中，补记一条真实恢复历史。"""
    exp = _create(client)
    eid = exp["id"]
    _set_status(client, eid, "RUNNING")
    _set_status(client, eid, "PAUSED", reason="出差")
    _set_status(client, eid, "COMPLETED", completion_analysis="误触完成")

    r = client.post(f"/experiments/{eid}/revert")
    assert r.status_code == 200
    assert r.json()["status"] == "RUNNING"

    events = client.get(f"/experiments/{eid}/status-events").json()
    assert [(e["from_status"], e["to_status"]) for e in events] == [
        ("DRAFT", "RUNNING"),
        ("RUNNING", "PAUSED"),
        ("PAUSED", "RUNNING"),  # 补记：暂停结束（撤销误触完成导致）
    ]
    # 当前状态与最后一条历史一致（时间轴连贯）
    assert events[-1]["to_status"] == r.json()["status"]


def test_revert_only_for_completed_or_concluded(client):
    """进行中/暂停/草稿没有可撤销的误触，直接 409。"""
    exp = _create(client)
    eid = exp["id"]
    assert client.post(f"/experiments/{eid}/revert").status_code == 409  # DRAFT
    _set_status(client, eid, "RUNNING")
    assert client.post(f"/experiments/{eid}/revert").status_code == 409  # RUNNING
    _set_status(client, eid, "PAUSED", reason="测试")
    assert client.post(f"/experiments/{eid}/revert").status_code == 409  # PAUSED


def test_revert_does_not_touch_history_of_real_flow(client):
    """回退与再前进来回后，历史仍只含真实过程（误触不残留）。"""
    exp = _create(client)
    eid = exp["id"]
    _to_completed(client, eid)  # 内含 DRAFT → RUNNING
    client.post(f"/experiments/{eid}/revert")  # 撤销完成
    _set_status(client, eid, "COMPLETED", completion_analysis="这次是认真的")
    _set_status(
        client,
        eid,
        "CONCLUDED",
        verdict="SUPPORTED",
        conclusion="有效",
        conclusion_reason="数据支持",
    )
    client.post(f"/experiments/{eid}/revert")  # 撤销结论

    events = client.get(f"/experiments/{eid}/status-events").json()
    assert [(e["from_status"], e["to_status"]) for e in events] == [
        ("DRAFT", "RUNNING"),
        ("RUNNING", "COMPLETED"),
    ]


def test_status_events_recorded(client):
    exp = _create(client)
    eid = exp["id"]
    _set_status(client, eid, "RUNNING")
    _set_status(client, eid, "PAUSED", reason="生病")
    _set_status(client, eid, "RUNNING", reason="恢复")

    r = client.get(f"/experiments/{eid}/status-events")
    assert r.status_code == 200
    events = r.json()
    assert len(events) == 3
    assert events[0]["from_status"] == "DRAFT"
    assert events[0]["to_status"] == "RUNNING"
    assert events[1]["reason"] == "生病"
    assert events[2]["from_status"] == "PAUSED"


def test_conclude_writes_finding_when_authorized(client):
    """勾选 write_finding → 结论写入知识库 Finding（用户授权才写）。"""
    prob = client.post("/problems", json={"title": "如何提升注意力"}).json()
    exp = _create(client)
    eid = exp["id"]
    _to_completed(client, eid)

    r = client.post(
        f"/experiments/{eid}/conclude",
        json={
            "status": "CONCLUDED",
            "verdict": "SUPPORTED",
            "conclusion": "减少短视频提升注意力",
            "conclusion_reason": "focus 后半段升高",
            "conclusion_confidence": 0.8,
            "write_finding": True,
            "problem_id": prob["id"],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["experiment"]["status"] == "CONCLUDED"
    assert body["finding_id"] is not None

    findings = client.get(f"/findings?problem_id={prob['id']}").json()
    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == "CONCLUSION"
    assert f["title"].startswith("实验结论")
    assert f["observation"] == "减少短视频提升注意力"
    assert f["confidence"] == 0.8
    assert f["evidence"] == "后半段注意力明显提升"


def test_conclude_without_finding_keeps_kb_untouched(client):
    exp = _create(client)
    eid = exp["id"]
    _to_completed(client, eid)
    r = client.post(
        f"/experiments/{eid}/conclude",
        json={
            "status": "CONCLUDED",
            "verdict": "INCONCLUSIVE",
            "conclusion": "数据不足",
            "conclusion_reason": "样本太少",
        },
    )
    assert r.status_code == 200
    assert r.json()["finding_id"] is None
    assert client.get("/findings").json() == []


# ── 数据点 ─────────────────────────────────────────────────


def test_logs_crud_and_batch(client):
    exp = _create(client)
    eid = exp["id"]

    r = client.post(
        f"/experiments/{eid}/logs",
        json={"metric": "focus", "value": 3, "note": "第一天"},
    )
    assert r.status_code == 201
    log = r.json()
    assert log["metric"] == "focus"
    assert log["source"] == "MANUAL"

    r = client.post(
        f"/experiments/{eid}/logs/batch",
        json={
            "logs": [
                {"metric": "focus", "value": 4, "timestamp": "2026-09-09T08:00:00Z"},
                {"metric": "phone_min", "value": 180, "timestamp": "2026-09-09T20:00:00Z"},
            ]
        },
    )
    assert r.status_code == 201
    assert len(r.json()) == 2

    lst = client.get(f"/experiments/{eid}/logs").json()
    assert len(lst) == 3

    filtered = client.get(f"/experiments/{eid}/logs?metric=focus").json()
    assert len(filtered) == 2

    assert client.delete(f"/experiments/{eid}/logs/{log['id']}").status_code == 204
    assert len(client.get(f"/experiments/{eid}/logs").json()) == 2


def test_logs_404_for_other_experiment(client):
    a = _create(client, name="A")
    b = _create(client, name="B")
    log = client.post(
        f"/experiments/{a['id']}/logs", json={"metric": "focus", "value": 1}
    ).json()
    assert client.delete(f"/experiments/{b['id']}/logs/{log['id']}").status_code == 404


def test_stats_and_trend(client):
    exp = _create(client)
    eid = exp["id"]
    # up_good 指标：后半段均值上升 → improved
    client.post(
        f"/experiments/{eid}/logs/batch",
        json={
            "logs": [
                {"metric": "focus", "value": 2, "timestamp": "2026-09-01T08:00:00Z"},
                {"metric": "focus", "value": 2, "timestamp": "2026-09-02T08:00:00Z"},
                {"metric": "focus", "value": 5, "timestamp": "2026-09-03T08:00:00Z"},
                {"metric": "focus", "value": 5, "timestamp": "2026-09-04T08:00:00Z"},
                {"metric": "phone_min", "value": 240, "timestamp": "2026-09-01T20:00:00Z"},
            ]
        },
    )
    stats = client.get(f"/experiments/{eid}/stats").json()
    assert stats["total_logs"] == 5
    focus = next(m for m in stats["metric_stats"] if m["metric"] == "focus")
    assert focus["count"] == 4
    assert focus["mean"] == 3.5
    assert focus["min"] == 2 and focus["max"] == 5
    assert focus["first"] == 2 and focus["latest"] == 5
    assert focus["change"] == 3
    assert focus["trend"] == "up"
    assert focus["direction"] == "up_good"
    assert focus["improved"] is True
    assert focus["unit"] == "分"

    phone = next(m for m in stats["metric_stats"] if m["metric"] == "phone_min")
    assert phone["trend"] == "flat"  # 单点无法分半
    assert phone["improved"] is False  # flat 不算改善

    # 指标顺序：设计清单顺序优先（focus 在 phone_min 前）
    assert [m["metric"] for m in stats["metric_stats"]] == ["focus", "phone_min"]


def test_stats_running_and_paused_days(client, monkeypatch):
    """状态历史 → 有效运行时长/暂停时长。用固定 now 避免依赖真实时钟。"""
    from datetime import datetime, timezone

    from app.services import experiment_service as es

    exp = _create(client)
    eid = exp["id"]
    _set_status(client, eid, "RUNNING")
    _set_status(client, eid, "PAUSED", reason="暂停一周")
    _set_status(client, eid, "RUNNING", reason="恢复")

    events = client.get(f"/experiments/{eid}/status-events").json()
    # 手工构造已知时间点的 ORM 事件，验证算法而不是时钟
    from app.models.experiment import ExperimentStatusEvent as E

    class _Ev:
        def __init__(self, to_status, created_at):
            self.to_status = to_status
            self.created_at = created_at

    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    evs = [
        _Ev("RUNNING", t0),
        _Ev("PAUSED", t0.replace(day=3)),      # 运行 2 天
        _Ev("RUNNING", t0.replace(day=6)),     # 暂停 3 天
    ]
    now = t0.replace(day=8)                    # 再运行 2 天
    running, paused, elapsed = es.compute_running_days(evs, now=now)
    assert round(running, 1) == 4.0
    assert round(paused, 1) == 3.0
    assert round(elapsed, 1) == 7.0
    assert len(events) == 3
    assert E is not None


def test_delete_experiment_cascades_logs_and_events(client):
    exp = _create(client)
    eid = exp["id"]
    _set_status(client, eid, "RUNNING")
    client.post(f"/experiments/{eid}/logs", json={"metric": "focus", "value": 3})

    assert client.delete(f"/experiments/{eid}").status_code == 204
    assert client.get(f"/experiments/{eid}").status_code == 404
    assert client.get(f"/experiments/{eid}/logs").status_code == 404
    assert client.get(f"/experiments/{eid}/status-events").status_code == 404


def test_update_experiment_fields(client):
    exp = _create(client)
    r = client.patch(
        f"/experiments/{exp['id']}",
        json={
            "hypothesis": "新假设",
            "note": "补一条备注",
            "metrics": [{"key": "sleep", "name": "睡眠时长", "direction": "up_good"}],
        },
    )
    assert r.status_code == 200
    assert r.json()["hypothesis"] == "新假设"
    assert r.json()["note"] == "补一条备注"
    assert r.json()["metrics"][0]["key"] == "sleep"


def test_experiment_not_found_404(client):
    assert client.get("/experiments/999").status_code == 404
    assert _set_status(client, 999, "RUNNING").status_code == 404


# ── 数据自动聚合 ───────────────────────────────────────────


def _aggregate_create(client):
    r = client.post(
        "/experiments",
        json={
            "name": "聚合实验",
            "metrics": [
                {
                    "key": "study_min",
                    "name": "学习时长",
                    "unit": "分钟",
                    "direction": "up_good",
                    "source": "event_duration:LEARNING_START",
                },
                {
                    "key": "focus_avg",
                    "name": "日均专注",
                    "direction": "up_good",
                    "source": "state_avg:focus",
                },
            ],
        },
    )
    assert r.status_code == 201
    return r.json()


def _seed_events_and_states(db_session):
    from datetime import datetime, timezone

    from app.models.event import Event, EventType
    from app.models.state import StateRecord

    t0 = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    db_session.add_all(
        [
            Event(
                user_id=1,
                timestamp=t0,
                type=EventType.LEARNING_START.value,
                source="MANUAL",
                confidence=1.0,
                ended_at=t0.replace(hour=10),  # 学 2 小时
            ),
            Event(
                user_id=1,
                timestamp=t0.replace(hour=13),
                type=EventType.LEARNING_START.value,
                source="MANUAL",
                confidence=1.0,
                ended_at=t0.replace(hour=14),  # 学 1 小时
            ),
            StateRecord(user_id=1, timestamp=t0, energy=2, focus=3, irritation=1),
            StateRecord(
                user_id=1, timestamp=t0.replace(hour=20), energy=4, focus=5, irritation=0
            ),
        ]
    )
    db_session.commit()


def _set_run_window(db_session, exp_id: int, start_iso: str, end_iso: str):
    """把实验运行窗口固定到指定区间，让聚合只覆盖测试数据那一天。

    同时把最后一个 RUNNING 状态事件对齐到窗口起点——真实流程里状态事件
    与 started_at 同时产生；不对齐会因"暂停期排除"被判定为当天未运行。
    """
    from datetime import datetime

    from sqlalchemy import select

    from app.models.experiment import Experiment, ExperimentStatusEvent

    exp = db_session.get(Experiment, exp_id)
    start = datetime.fromisoformat(start_iso)
    exp.started_at = start
    exp.ended_at = datetime.fromisoformat(end_iso)
    running_event = db_session.scalars(
        select(ExperimentStatusEvent)
        .where(
            ExperimentStatusEvent.experiment_id == exp_id,
            ExperimentStatusEvent.to_status == "RUNNING",
        )
        .order_by(ExperimentStatusEvent.created_at.desc())
    ).first()
    if running_event is not None:
        running_event.created_at = start
    db_session.commit()


def test_aggregate_logs_from_events_and_states(client, db_session):
    exp = _aggregate_create(client)
    eid = exp["id"]
    _seed_events_and_states(db_session)
    _set_status(client, eid, "RUNNING")
    _set_run_window(db_session, eid, "2026-09-02T00:00:00+00:00", "2026-09-03T00:00:00+00:00")

    r = client.post(f"/experiments/{eid}/aggregate")
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == 2  # 2 指标 × 1 天
    assert body["metrics"] == ["study_min", "focus_avg"]

    logs = client.get(f"/experiments/{eid}/logs").json()
    by_metric = {log["metric"]: log for log in logs}
    assert by_metric["study_min"]["value"] == 180  # 2h + 1h
    assert by_metric["study_min"]["source"] == "AUTO"
    assert by_metric["focus_avg"]["value"] == 4  # (3+5)/2

    # 幂等：再聚合不新增
    again = client.post(f"/experiments/{eid}/aggregate").json()
    assert again["created"] == 0
    assert len(client.get(f"/experiments/{eid}/logs").json()) == 2


def test_aggregate_recomputes_and_updates(client, db_session):
    """源数据变化后重算覆盖 AUTO 点（updated），不重复插入。"""
    exp = _aggregate_create(client)
    eid = exp["id"]
    _seed_events_and_states(db_session)
    _set_status(client, eid, "RUNNING")
    _set_run_window(db_session, eid, "2026-09-02T00:00:00+00:00", "2026-09-03T00:00:00+00:00")
    client.post(f"/experiments/{eid}/aggregate")

    # 新增一条学习事件（10:00-12:00）→ 总时长 180 + 120 = 300
    from datetime import datetime, timezone

    from app.models.event import Event, EventType

    db_session.add(
        Event(
            user_id=1,
            timestamp=datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc),
            type=EventType.LEARNING_START.value,
            source="MANUAL",
            confidence=1.0,
            ended_at=datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc),
        )
    )
    db_session.commit()

    r = client.post(f"/experiments/{eid}/aggregate").json()
    assert r["updated"] == 1
    logs = client.get(f"/experiments/{eid}/logs?metric=study_min").json()
    assert len(logs) == 1
    assert logs[0]["value"] == 300


def test_aggregate_never_touches_manual_logs(client, db_session):
    exp = _aggregate_create(client)
    eid = exp["id"]
    _seed_events_and_states(db_session)
    _set_status(client, eid, "RUNNING")
    _set_run_window(db_session, eid, "2026-09-02T00:00:00+00:00", "2026-09-03T00:00:00+00:00")
    client.post(
        f"/experiments/{eid}/logs",
        json={"metric": "study_min", "value": 42, "timestamp": "2026-09-02T09:00:00Z"},
    )
    client.post(f"/experiments/{eid}/aggregate")

    logs = client.get(f"/experiments/{eid}/logs").json()
    manual = [log for log in logs if log["source"] == "MANUAL"]
    assert len(manual) == 1 and manual[0]["value"] == 42
    # 同一天 AUTO 点也写入完成（互不覆盖）
    assert any(log["source"] == "AUTO" for log in logs)


def test_aggregate_deletes_stale_auto_points(client, db_session):
    """源数据消失（事件被删）→ 对应 AUTO 点清除，不残留过期值。"""
    exp = _aggregate_create(client)
    eid = exp["id"]
    _seed_events_and_states(db_session)
    _set_status(client, eid, "RUNNING")
    _set_run_window(db_session, eid, "2026-09-02T00:00:00+00:00", "2026-09-03T00:00:00+00:00")
    client.post(f"/experiments/{eid}/aggregate")
    assert len(client.get(f"/experiments/{eid}/logs?metric=study_min").json()) == 1

    # 删除当天全部学习事件
    from sqlalchemy import select

    from app.models.event import Event, EventType

    for ev in db_session.scalars(
        select(Event).where(Event.type == EventType.LEARNING_START.value)
    ).all():
        db_session.delete(ev)
    db_session.commit()

    r = client.post(f"/experiments/{eid}/aggregate").json()
    assert r["deleted"] == 1
    assert client.get(f"/experiments/{eid}/logs?metric=study_min").json() == []


def test_aggregate_manual_only_returns_empty(client):
    exp = _create(client)  # 默认指标 source=manual
    eid = exp["id"]
    _set_status(client, eid, "RUNNING")
    r = client.post(f"/experiments/{eid}/aggregate").json()
    assert r == {
        "experiment_id": eid,
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "days": 0,
        "metrics": [],
    }


def test_edit_metrics_prunes_orphan_auto_points(client, db_session):
    """编辑指标移除某项 → 其 AUTO 点清除；手动点保留；保留项的 AUTO 点不动。"""
    exp = _aggregate_create(client)
    eid = exp["id"]
    _seed_events_and_states(db_session)
    _set_status(client, eid, "RUNNING")
    _set_run_window(db_session, eid, "2026-09-02T00:00:00+00:00", "2026-09-03T00:00:00+00:00")
    client.post(f"/experiments/{eid}/aggregate")
    auto_study = client.get(f"/experiments/{eid}/logs?metric=study_min").json()
    assert len(auto_study) == 1 and auto_study[0]["source"] == "AUTO"

    # 手动补一条 study_min（MANUAL）：移除指标时不应被误删
    r = client.post(
        f"/experiments/{eid}/logs",
        json={"metric": "study_min", "value": 42, "timestamp": "2026-09-02T09:00:00+00:00"},
    )
    assert r.status_code == 201

    # 移除 study_min，只保留 focus_avg
    r = client.patch(
        f"/experiments/{eid}",
        json={
            "metrics": [
                {
                    "key": "focus_avg",
                    "name": "日均专注",
                    "direction": "up_good",
                    "source": "state_avg:focus",
                }
            ]
        },
    )
    assert r.status_code == 200
    assert [m["key"] for m in r.json()["metrics"]] == ["focus_avg"]

    study = client.get(f"/experiments/{eid}/logs?metric=study_min").json()
    assert len(study) == 1 and study[0]["source"] == "MANUAL"  # AUTO 被清，MANUAL 留下
    focus = client.get(f"/experiments/{eid}/logs?metric=focus_avg").json()
    assert len(focus) == 1 and focus[0]["source"] == "AUTO"  # 保留项不受影响



# ── AI 识别数据源（①b） ─────────────────────────────────────
#
# 授权例外：创建/编辑实验时 AI 可以写 `source` 一个字段。边界见
# `app/routers/experiments.py::_apply_inferred_sources`。下面几条就是那道边界。


def _metric(key, name, source=None):
    m = {"key": key, "name": name}
    if source is not None:
        m["source"] = source
    return m


def _stub_llm(monkeypatch, reply):
    """把识别服务的 LLM 换成桩；返回记录调用的列表（空列表 = 一次没调）。"""
    calls = []

    def _fake(system, user, **kw):
        calls.append({"system": system, "user": user})
        return reply

    monkeypatch.setattr("app.services.metric_source_service.chat_json", _fake)
    return calls


def test_infer_keys_fills_source_on_create(client, monkeypatch):
    """提名的指标由 AI 填 source；**没提名的原样不动**。"""
    _stub_llm(
        monkeypatch,
        {"sources": {"phone_min": "usage_platform:android@22-24"}},
    )
    r = client.post(
        "/experiments",
        json={
            "name": "睡前手机",
            "metrics": [
                _metric("focus", "注意力"),
                _metric("phone_min", "睡前手机时长"),
            ],
            "infer_keys": ["phone_min"],
        },
    )
    assert r.status_code == 201, r.text
    by_key = {m["key"]: m["source"] for m in r.json()["metrics"]}
    assert by_key == {"focus": "manual", "phone_min": "usage_platform:android@22-24"}


def test_infer_keys_rejects_illegal_source(client, monkeypatch):
    """LLM 编出来的非法 source 被白名单挡掉，落回 manual（闸门没因为「AI 填」而松）。"""
    _stub_llm(monkeypatch, {"sources": {"phone_min": "teleport:mars"}})
    r = client.post(
        "/experiments",
        json={
            "name": "越界",
            "metrics": [_metric("phone_min", "睡前手机时长")],
            "infer_keys": ["phone_min"],
        },
    )
    assert r.status_code == 201
    assert r.json()["metrics"][0]["source"] == "manual"


def test_create_experiment_survives_llm_failure(client, monkeypatch):
    """LLM 挂了也**绝不能挡住建实验** —— 这是这条授权例外的底线。"""
    from app.llm import LLMError

    def boom(*a, **k):
        raise LLMError("network down")

    monkeypatch.setattr("app.services.metric_source_service.chat_json", boom)
    r = client.post(
        "/experiments",
        json={
            "name": "断网也要建成",
            "metrics": [_metric("phone_min", "睡前手机时长")],
            "infer_keys": ["phone_min"],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["metrics"][0]["source"] == "manual"


def test_no_infer_keys_means_no_llm_call(client, monkeypatch):
    """不提名就一次 LLM 都不调 —— 老客户端/不需要识别的路径零成本。"""
    calls = _stub_llm(monkeypatch, {"sources": {"phone_min": "usage_platform:pc"}})
    r = client.post(
        "/experiments",
        json={"name": "不识别", "metrics": [_metric("phone_min", "手机时长")]},
    )
    assert r.status_code == 201
    assert calls == []
    assert r.json()["metrics"][0]["source"] == "manual"


def test_demo_user_skips_inference(client, as_demo, monkeypatch):
    """demo 不是被 403 挡住建实验，而是**照常建成、只是不识别**。"""
    calls = _stub_llm(monkeypatch, {"sources": {"phone_min": "usage_platform:pc"}})
    as_demo()
    r = client.post(
        "/experiments",
        json={
            "name": "演示号",
            "metrics": [_metric("phone_min", "手机时长")],
            "infer_keys": ["phone_min"],
        },
    )
    assert r.status_code == 201, r.text
    assert calls == []
    assert r.json()["metrics"][0]["source"] == "manual"


def test_update_infer_keys_fills_source(client, monkeypatch):
    """PATCH 路径同样生效，且只动提名的那个。"""
    exp = _create(client)
    _stub_llm(monkeypatch, {"sources": {"focus": "state_avg:focus"}})
    r = client.patch(
        f"/experiments/{exp['id']}",
        json={
            "metrics": [
                _metric("focus", "注意力"),
                _metric("phone_min", "手机时长"),
            ],
            "infer_keys": ["focus"],
        },
    )
    assert r.status_code == 200, r.text
    by_key = {m["key"]: m["source"] for m in r.json()["metrics"]}
    assert by_key == {"focus": "state_avg:focus", "phone_min": "manual"}


def test_infer_sources_endpoint_returns_mapping(client, monkeypatch):
    _stub_llm(
        monkeypatch,
        {"sources": {"a": "state_avg:focus", "b": "manual"}},
    )
    r = client.post(
        "/experiments/infer-sources",
        json={
            "metrics": [
                {"key": "a", "name": "专注"},
                {"key": "b", "name": "主观评分"},
                {"key": "c", "name": "说不清"},
            ],
            "context": "睡前手机实验",
        },
    )
    assert r.status_code == 200, r.text
    # 没认出来的 c 不出现（调用方保持 manual）
    assert r.json()["sources"] == {"a": "state_avg:focus", "b": "manual"}


def test_infer_sources_endpoint_blocks_demo(client, as_demo):
    """这条是用户专门来要 AI 结果的，demo 一律 403（与它不同：建实验不 403）。"""
    as_demo()
    r = client.post(
        "/experiments/infer-sources",
        json={"metrics": [{"key": "a", "name": "专注"}]},
    )
    assert r.status_code == 403


def test_infer_sources_endpoint_maps_llm_error_to_502(client, monkeypatch):
    from app.llm import LLMError

    def boom(*a, **k):
        raise LLMError("network down")

    monkeypatch.setattr("app.services.metric_source_service.chat_json", boom)
    r = client.post(
        "/experiments/infer-sources",
        json={"metrics": [{"key": "a", "name": "专注"}]},
    )
    assert r.status_code == 502
    assert "network down" in r.json()["detail"]
