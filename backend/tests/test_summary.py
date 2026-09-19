from datetime import datetime, timezone

from app.models.event import Event, EventType
from app.models.state import StateRecord
from app.models.thought import Thought


def test_empty_day_summary(client):
    r = client.get("/summary/2026-09-02")
    assert r.status_code == 200
    data = r.json()
    assert data["event_total"] == 0
    assert data["by_type"] == []
    assert data["avg_energy"] is None


def test_summary_counts_and_averages(client, db_session):
    base = datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc)
    db_session.add_all(
        [
            Event(user_id=1, timestamp=base, type=EventType.LEARNING_START.value),
            Event(user_id=1, timestamp=base, type=EventType.LEARNING_START.value),
            Event(user_id=1, timestamp=base, type=EventType.GAME_START.value),
            Thought(user_id=1, timestamp=base, content="测试想法"),
            StateRecord(user_id=1, timestamp=base, energy=4, focus=3, irritation=1),
            StateRecord(user_id=1, timestamp=base, energy=2, focus=2, irritation=3),
        ]
    )
    db_session.commit()

    data = client.get("/summary/2026-09-02").json()
    assert data["event_total"] == 3
    assert data["by_type"] == [
        {"type": "LEARNING_START", "count": 2},
        {"type": "GAME_START", "count": 1},
    ]
    assert data["thought_total"] == 1
    assert data["state_total"] == 2
    assert data["avg_energy"] == 3.0
    assert data["avg_focus"] == 2.5
    assert data["avg_irritation"] == 2.0


def test_summary_scoped_to_date(client, db_session):
    day = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)
    other = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)
    db_session.add_all(
        [
            Event(user_id=1, timestamp=day, type=EventType.MEAL_START.value),
            Event(user_id=1, timestamp=other, type=EventType.GAME_START.value),
        ]
    )
    db_session.commit()
    assert client.get("/summary/2026-09-02").json()["event_total"] == 1
    assert client.get("/summary/2026-09-03").json()["event_total"] == 1
