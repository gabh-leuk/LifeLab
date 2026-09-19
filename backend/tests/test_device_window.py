"""时段×应用摊销纯函数（device_service._apps_in_window）单测。

设备数据只有小时桶，无法知道小时内分布；函数按窗口与桶的重叠比例摊销。
"""

from app.models.device_usage import DeviceUsageHourly
from app.services.device_service import _apps_in_window

PLAT = {1: "pc", 2: "android"}


def _row(app, hour, seconds, *, device_id=1, label=None):
    # 纯内存构造，不落库（无 DB 亦可单测）
    return DeviceUsageHourly(
        user_id=1, device_id=device_id, date="2026-09-02",
        hour=hour, app=app, label=label, seconds=seconds,
    )


def test_full_hour_hit():
    rows = [_row("Code.exe", 22, 1800)]
    out = _apps_in_window(rows, PLAT, 22.0, 23.0, top=4)
    assert out == [
        {
            "platform": "pc",
            "app": "Code.exe",
            "label": None,
            "seconds": 1800,
            "category": "study_work",
        }
    ]


def test_partial_overlap_prorates():
    # 窗口 22:15-22:45 命中 22 点桶的一半
    rows = [_row("Code.exe", 22, 1800)]
    out = _apps_in_window(rows, PLAT, 22.25, 22.75, top=4)
    assert out[0]["seconds"] == 900


def test_spans_two_hours():
    # 窗口 22:30-23:30：22 点桶摊 0.5、23 点桶摊 0.5
    rows = [_row("Code.exe", 22, 1800), _row("Code.exe", 23, 600)]
    out = _apps_in_window(rows, PLAT, 22.5, 23.5, top=4)
    assert len(out) == 1
    assert out[0]["seconds"] == 1200  # 900 + 300


def test_web_rows_excluded():
    rows = [_row("web:youtube.com", 22, 1200), _row("chrome.exe", 22, 600)]
    out = _apps_in_window(rows, PLAT, 22.0, 23.0, top=4)
    assert [a["app"] for a in out] == ["chrome.exe"]


def test_platform_split_and_top_n():
    # 同一 app 名来自两端 → 分平台各一条；top 截断
    rows = [
        _row("wechat", 22, 900, device_id=2, label="微信"),
        _row("wechat", 22, 300, device_id=1, label="WeChat"),
        _row("chrome.exe", 22, 600, device_id=1),
    ]
    out = _apps_in_window(rows, PLAT, 22.0, 23.0, top=4)
    assert out[0] == {
        "platform": "android", "app": "wechat", "label": "微信", "seconds": 900,
        "category": "social",
    }
    assert out[1] == {
        "platform": "pc", "app": "chrome.exe", "label": None, "seconds": 600,
        "category": "browsing",
    }
    assert out[2] == {
        "platform": "pc", "app": "wechat", "label": "WeChat", "seconds": 300,
        "category": "social",
    }
    assert _apps_in_window(rows, PLAT, 22.0, 23.0, top=1) == out[:1]  # top 截断


def test_empty_and_degenerate_window():
    assert _apps_in_window([], PLAT, 22.0, 23.0, top=4) == []
    rows = [_row("Code.exe", 22, 1800)]
    assert _apps_in_window(rows, PLAT, 23.0, 22.0, top=4) == []  # end<=start
    assert _apps_in_window(rows, PLAT, 22.0, 22.0, top=4) == []
    # 完全不重叠的小时
    assert _apps_in_window(rows, PLAT, 10.0, 11.0, top=4) == []
