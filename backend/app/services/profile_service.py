"""用户背景服务（边界二：向量库 → 画像）。

规则：
- 只收"跨话题稳定 + 用户确认"的事实；AI 推断未经确认不得进入
- 分类配额 + 总配额，保证注入 prompt 的体量有上界
- 去重：同分类下规范化文本相同则拒绝（语义合并留给 AI 晋升流程）
- 归档保留可查但不注入；只有显式删除才真删
"""

from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.profile import ProfileFact

# 分类中文名（注入 prompt 用）
CATEGORY_LABELS: dict[str, str] = {
    "identity": "身份",
    "habit": "习惯",
    "preference": "偏好",
    "context": "情境",
    "constraint": "限制",
}

# 配额：防止画像无限膨胀（总量 70 条，与 token 预算对应）
CATEGORY_LIMITS: dict[str, int] = {
    "identity": 10,
    "habit": 15,
    "preference": 15,
    "context": 20,
    "constraint": 10,
}
TOTAL_LIMIT = sum(CATEGORY_LIMITS.values())


class ProfileError(ValueError):
    """业务错误：路由层转成 409/422。"""


def _normalize(text: str) -> str:
    return "".join(text.split()).lower()


def list_facts(
    db: Session, *, include_archived: bool = False, user_id: int
) -> Sequence[ProfileFact]:
    stmt = (
        select(ProfileFact)
        .where(ProfileFact.user_id == user_id)
        .order_by(ProfileFact.category, ProfileFact.id)
    )
    if not include_archived:
        stmt = stmt.where(ProfileFact.status == "active")
    return db.scalars(stmt).all()


def get_fact(
    db: Session, fact_id: int, user_id: int
) -> ProfileFact | None:
    return db.scalar(
        select(ProfileFact).where(
            ProfileFact.id == fact_id, ProfileFact.user_id == user_id
        )
    )


def _check_duplicate(
    db: Session,
    category: str,
    content: str,
    *,
    user_id: int,
    exclude_id: int | None = None,
) -> None:
    rows = db.scalars(
        select(ProfileFact).where(
            ProfileFact.user_id == user_id,
            ProfileFact.category == category,
            ProfileFact.status == "active",
        )
    ).all()
    target = _normalize(content)
    for row in rows:
        if exclude_id is not None and row.id == exclude_id:
            continue
        if _normalize(row.content) == target:
            raise ProfileError("duplicate: 同分类下已有相同背景")


def _check_quota(db: Session, category: str, *, user_id: int) -> None:
    rows = db.scalars(
        select(ProfileFact).where(
            ProfileFact.user_id == user_id,
            ProfileFact.category == category,
            ProfileFact.status == "active",
        )
    ).all()
    if len(rows) >= CATEGORY_LIMITS.get(category, 10):
        raise ProfileError(
            f"quota: 「{CATEGORY_LABELS.get(category, category)}」已达上限 "
            f"{CATEGORY_LIMITS.get(category, 10)} 条，请先合并或归档"
        )
    all_rows = list_facts(db, user_id=user_id)
    if len(all_rows) >= TOTAL_LIMIT:
        raise ProfileError(f"quota: 画像总量已达上限 {TOTAL_LIMIT} 条，请先合并或归档")


def create_fact(
    db: Session,
    *,
    category: str,
    content: str,
    source: str = "manual",
    source_ref: str | None = None,
    confidence: float | None = None,
    confirmed: bool | None = None,
    user_id: int,
) -> ProfileFact:
    """写入背景。confirmed=None 时按来源推断（manual=已确认，ai=待确认）；
    AI 候选经用户在剖析页勾选写入 → 传 confirmed=True 刷新确认时间。"""
    _check_duplicate(db, category, content, user_id=user_id)
    _check_quota(db, category, user_id=user_id)
    if confirmed is None:
        confirmed = source == "manual"
    now = datetime.now(timezone.utc)
    fact = ProfileFact(
        user_id=user_id,
        category=category,
        content=content.strip(),
        source=source,
        source_ref=source_ref,
        confidence=confidence,
        status="active",
        last_confirmed_at=now if confirmed else None,
    )
    db.add(fact)
    db.commit()
    db.refresh(fact)
    return fact


def update_fact(
    db: Session,
    fact_id: int,
    *,
    category: str | None = None,
    content: str | None = None,
    status: str | None = None,
    user_id: int,
) -> ProfileFact | None:
    fact = get_fact(db, fact_id, user_id)
    if fact is None:
        return None
    new_category = category or fact.category
    new_content = content.strip() if content else fact.content
    if category is not None or content is not None:
        _check_duplicate(
            db, new_category, new_content, user_id=user_id, exclude_id=fact.id
        )
        if category is not None and category != fact.category:
            _check_quota(db, category, user_id=user_id)
    if category is not None:
        fact.category = category
    if content is not None:
        fact.content = content.strip()
    if status is not None:
        fact.status = status
    if category is not None or content is not None:
        # 用户编辑 = 再次确认
        fact.last_confirmed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(fact)
    return fact


def delete_fact(db: Session, fact_id: int, user_id: int) -> bool:
    fact = get_fact(db, fact_id, user_id)
    if fact is None:
        return False
    db.delete(fact)
    db.commit()
    return True


def prompt_context(db: Session, user_id: int) -> str:
    """生成注入 prompt 的常驻背景段；无 active 事实时返回空串。"""
    facts = list_facts(db, user_id=user_id)
    if not facts:
        return ""
    grouped: dict[str, list[str]] = {}
    for f in facts:
        grouped.setdefault(f.category, []).append(f.content)
    lines = ["关于用户的长期背景（用户确认过的事实，可作为既定前提）："]
    for category, label in CATEGORY_LABELS.items():
        contents = grouped.get(category)
        if not contents:
            continue
        lines.append(f"- [{label}] " + "；".join(contents))
    return "\n".join(lines)
