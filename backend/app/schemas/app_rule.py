from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.behavior_categories import ONLINE_CATEGORIES

MatchType = Literal["exact", "substring"]
Scope = Literal["app", "domain", "any"]


class AppRuleCreate(BaseModel):
    """给某应用/域名指定行为大类（可选同时给一个展示名）。

    `scope='app'` 匹配应用包名/进程名，`'domain'` 匹配 `web:<域名>` 的根域，
    `'any'` 两者都试。`match_type='substring'`（默认）按子串命中，
    `'exact'` 要求与 app 名或 label 完全相等。
    """

    match_type: MatchType = "substring"
    scope: Scope = "any"
    match_value: str = Field(min_length=1, max_length=200)
    # 只允许在线大类：设备观测到的活动不可能落「上床/吃饭」这类线下行为
    category: str = Field(max_length=32)
    display_label: str | None = Field(default=None, max_length=64)
    priority: int = Field(default=100, ge=0, le=1000)

    @field_validator("match_value")
    @classmethod
    def _value_clean(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("match_value 不能为空")
        return v

    @field_validator("category")
    @classmethod
    def _category_online(cls, v: str) -> str:
        if v not in ONLINE_CATEGORIES:
            raise ValueError("category 必须是设备在线类大类")
        return v


class AppRuleUpdate(BaseModel):
    match_type: MatchType | None = None
    scope: Scope | None = None
    match_value: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=32)
    display_label: str | None = Field(default=None, max_length=64)
    priority: int | None = Field(default=None, ge=0, le=1000)
    enabled: bool | None = None

    @field_validator("match_value")
    @classmethod
    def _value_clean(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("match_value 不能为空")
        return v

    @field_validator("category")
    @classmethod
    def _category_online(cls, v: str | None) -> str | None:
        if v is not None and v not in ONLINE_CATEGORIES:
            raise ValueError("category 必须是设备在线类大类")
        return v


class AppRuleItem(BaseModel):
    id: int
    match_type: MatchType
    scope: Scope
    match_value: str
    category: str
    display_label: str | None = None
    priority: int
    enabled: bool


class UnclassifiedApp(BaseModel):
    """一段时间内仍没被认出大类的应用（用于逐条标记）。"""

    app: str
    label: str | None = None
    platform: str | None = None
    seconds: int
    sessions: int
    category: str
