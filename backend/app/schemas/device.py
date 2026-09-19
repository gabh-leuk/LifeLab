from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DeviceCreate(BaseModel):
    """创建采集设备：返回的明文 token 只出现一次。"""

    name: str = Field(min_length=1, max_length=100)
    platform: str = Field(pattern="^(pc|android)$")


class DeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    name: str
    platform: str
    last_seen_at: datetime | None
    created_at: datetime


class DeviceCreateResponse(BaseModel):
    device: DeviceRead
    token: str  # 明文，仅此一次；请写入采集器配置


class UsageIngestResponse(BaseModel):
    device_id: int
    date: str
    received: int
    replaced: int  # 被替换掉的旧记录数


class UsageHourlyEntry(BaseModel):
    """一条应用使用记录（某日某小时的累计值）。

    seconds 上限放宽到 86400：一小时理论 ≤3600，但越界只会在服务端被 clamp
    到 3600，而不是让整日上传因单条越界而 422。
    """

    app: str = Field(min_length=1, max_length=200)
    label: str | None = Field(default=None, max_length=200)
    hour: int = Field(ge=0, le=23)
    seconds: int = Field(ge=0, le=86400)
    switches: int = Field(default=0, ge=0, le=100000)


class UsageHourlyIngestRequest(BaseModel):
    """按时段上报：同 (device, date) 整体替换当天小时数据，天然幂等。"""

    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    entries: list[UsageHourlyEntry] = Field(min_length=1, max_length=500)


class UsageHourlyItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    hour: int
    app: str
    label: str | None
    seconds: int
    switches: int


class UsageHourlyDayResponse(BaseModel):
    date: str
    total_seconds: int
    items: list[UsageHourlyItem]


class UsageOverviewApp(BaseModel):
    app: str
    label: str | None
    seconds: int
    switches: int
    # 按当前规则推导的大类；websites 里为站点规则结果（无规则则浏览）
    category: str = "other_online"


class UsageOverviewHour(BaseModel):
    hour: int
    seconds: int
    apps: list[UsageOverviewApp] = []  # 该小时 Top N 应用（供悬浮展示）


class UsageOverviewDevice(BaseModel):
    """某设备某日的使用概览（供只读展示）。

    hours 为完整 24 槽（0..23）。apps 为该设备当天按秒数降序的应用列表（已截断 Top N）。
    websites 为浏览器按窗口标题推断出的站点列表（app 字段形如 `web:<域名>`，
    已截断 Top N）；total_seconds 不含 websites（站点是浏览器时长的细分）。
    """

    device_id: int
    name: str
    platform: str
    total_seconds: int
    hours: list[UsageOverviewHour]
    apps: list[UsageOverviewApp]
    websites: list[UsageOverviewApp]


class UsageOverviewResponse(BaseModel):
    date: str
    devices: list[UsageOverviewDevice]


class KnownApp(BaseModel):
    """区间内用过的应用/网页 key —— 供指标数据源选择。

    `app` 是**原样**的 key（`com.tencent.mm` / `web:bilibili.com`），可直接写进
    指标的 `usage_duration:<app>`；`label` 只是给人看的名字。
    """

    app: str
    label: str | None
    platform: str | None
    seconds: int


class UsageSessionEntry(BaseModel):
    """一条精确前台会话（epoch 毫秒起止），真实测量。"""

    app: str = Field(min_length=1, max_length=200)
    label: str | None = Field(default=None, max_length=200)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _end_after_start(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms 必须大于 start_ms")
        return self


class UsageSessionIngestRequest(BaseModel):
    """会话级上报：同 (device, date) 整体替换，天然幂等。

    date 为采集器本地日；服务端据此把会话落成行为段（source=DEVICE 事件）。
    """

    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    entries: list[UsageSessionEntry] = Field(min_length=1, max_length=3000)


class UsageSessionIngestResponse(BaseModel):
    device_id: int
    date: str
    received: int
    replaced: int  # 被替换掉的旧会话数
    segments: int  # 合并出的行为段（DEVICE 事件）数
