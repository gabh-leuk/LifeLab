from pydantic import BaseModel, Field, field_validator

from app.behavior_categories import CATEGORIES

# 图标 key 是前端精选图标集（ICON_CHOICES）里的 slug。后端不认识那份清单，
# 只校验形状；认不出的 key 由前端回退到默认图标，不会崩。
_ICON_PATTERN = r"^[A-Za-z][A-Za-z0-9]{0,31}$"


class EventTypeItem(BaseModel):
    """统一清单里的一项：内置（`id=None`）与自定义同构，前端无需分支。"""

    key: str
    label: str
    category: str
    icon: str | None = None
    # 全局展示序号：内置 0..n-1，自定义紧随其后
    order: int
    id: int | None = None
    builtin: bool
    archived: bool = False


class EventTypeCreate(BaseModel):
    label: str = Field(min_length=1, max_length=16)
    category: str = Field(max_length=32)
    icon: str | None = Field(default=None, pattern=_ICON_PATTERN)

    @field_validator("label")
    @classmethod
    def _label_clean(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("label 不能为空")
        return v

    @field_validator("category")
    @classmethod
    def _category_known(cls, v: str) -> str:
        if v not in CATEGORIES:
            raise ValueError("未知的行为大类 category")
        return v


class EventTypeUpdate(BaseModel):
    """只改显式传入的字段；`icon=None` 表示清空图标（回到大类默认）。"""

    label: str | None = Field(default=None, max_length=16)
    category: str | None = Field(default=None, max_length=32)
    icon: str | None = Field(default=None, pattern=_ICON_PATTERN)
    sort_order: int | None = Field(default=None, ge=0, le=9999)
    archived: bool | None = None

    @field_validator("label")
    @classmethod
    def _label_clean(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("label 不能为空")
        return v

    @field_validator("category")
    @classmethod
    def _category_known(cls, v: str | None) -> str | None:
        if v is not None and v not in CATEGORIES:
            raise ValueError("未知的行为大类 category")
        return v
