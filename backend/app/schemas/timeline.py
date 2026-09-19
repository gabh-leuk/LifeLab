from datetime import datetime

from pydantic import BaseModel

from app.schemas.event import EventRead
from app.schemas.state import StateRead
from app.schemas.thought import ThoughtRead

TimelineData = EventRead | ThoughtRead | StateRead


class SegmentApp(BaseModel):
    """某时段内某平台某应用的分摊使用时长（设备采集，按小时桶重叠摊销）。"""

    platform: str  # pc | android
    app: str
    label: str | None
    seconds: int
    # 按当前规则推导的大类（非库里陈旧快照），就地编辑面板用它回填默认值
    category: str = "other_online"


class DeviceActivity(BaseModel):
    """人工时间段内包含的设备真实活动（由会话合并的 DEVICE 事件）。

    设备数据是真实测量（非推测）：人工「上床」是容器，里面这些才是实际在做什么。
    """

    platform: str  # pc | android
    category: str
    label: str | None
    start: datetime
    end: datetime
    seconds: int


class EventSegment(BaseModel):
    """一个事件占用的时间段。"""

    kind: str = "event"  # event | device
    event: EventRead
    # 只有设备段有：这一段属于哪类设备（pc | android）。
    # 设备段是「一台设备的一段时间」，不写清是哪台，同一时刻的电脑段和手机段看着一模一样。
    platform: str | None = None
    start: datetime
    end: datetime
    duration_minutes: int
    # 结束边界类型：
    #   next_event - 被下一事件开始截断（end 是真实时刻）
    #   explicit   - 用户显式结束（end 是真实时刻）
    #   day_end    - 推断到当天最后（end 是 UTC 次日 0 点，前端应按"当日结束"渲染，
    #                不能直接按本地时区显示成次日 08:00）
    end_boundary: str
    # 结束是"推断"的（下一事件开始/当天末尾）且时长超阈值 → 模糊
    end_inferred: bool
    fuzzy: bool
    fuzzy_reason: str | None = None
    # 该时段内使用的设备应用（Top N，按小时桶重叠摊销；无 hourly 数据为空）
    device_apps: list[SegmentApp] = []
    # 该时段内包含的设备活动段（category 级，来自 DEVICE 事件）
    device_activities: list[DeviceActivity] = []


class FloatingItem(BaseModel):
    """事件段内的想法/状态：只带时间戳，不切段。"""

    kind: str  # thought | state
    timestamp: datetime
    data: ThoughtRead | StateRead


class DeviceHour(BaseModel):
    """设备使用按「本地小时」聚合的一天视图（跨设备合并）。

    两个口径同桶给出，避免同一批数据在不同模块算出不同的数：

    - `seconds` / `apps` 来自按小时采集（`DeviceUsageHourly`）——**对账基准**，
      逐小时相加等于当天 `usage_total`；
    - `categories` / `category_seconds` / `activities` 来自 DEVICE 事件，
      已按小时**重叠摊销**（不再整段记给起点小时）。

    桶的 `start` / `end` 就是该本地小时的起止，不随活动范围收缩。
    """

    hour: int  # 本地小时 0..23
    start: datetime
    end: datetime
    seconds: int
    # 该小时的应用明细（Top N，按 (平台, 应用) 合并）
    apps: list[SegmentApp] = []
    # 该小时出现过的行为大类，按摊销秒数降序（首个为主导）
    categories: list[str] = []
    # 大类 → 该小时摊销秒数（对账用；未截断）
    category_seconds: dict[str, int] = {}
    # 该小时包含的设备活动段（含落在人工段内的 —— 与实验指标同一口径）
    activities: list[DeviceActivity] = []


class TimelineResponse(BaseModel):
    date: str
    segments: list[EventSegment]
    floatings: list[FloatingItem]
    # 未被任何人工事件覆盖的设备活动段（自动记录的真实时间段）
    device_segments: list[EventSegment] = []
    # 上者按小时合并后的粗粒度视图（前端主列表用）
    device_hours: list[DeviceHour] = []