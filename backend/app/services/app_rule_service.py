"""应用/域名分类规则：用户纠正「这个词表认不出的应用该算什么」。

分类仍是**确定性规则映射**（不是模型推断）；本模块只是把规则来源从
「硬编码词表」扩展为「硬编码词表 + 用户规则」，用户规则优先。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.behavior_categories import RuleSet, build_ruleset, normalize_domain
from app.models.app_rule import AppCategoryRule
from app.schemas.app_rule import AppRuleCreate, AppRuleUpdate


def load_rules(db: Session, user_id: int) -> RuleSet:
    """加载用户规则集。**每个请求/每次重建加载一次**，不要放进循环。"""
    rows = db.scalars(
        select(AppCategoryRule).where(
            AppCategoryRule.user_id == user_id,
            AppCategoryRule.enabled.is_(True),
        )
    ).all()
    return build_ruleset(rows)


def _item(row: AppCategoryRule) -> dict:
    return {
        "id": row.id,
        "match_type": row.match_type,
        "scope": row.scope,
        "match_value": row.match_value,
        "category": row.category,
        "display_label": row.display_label,
        "priority": row.priority,
        "enabled": row.enabled,
    }


def list_rules(db: Session, user_id: int) -> list[dict]:
    rows = db.scalars(
        select(AppCategoryRule)
        .where(AppCategoryRule.user_id == user_id)
        .order_by(AppCategoryRule.priority.desc(), AppCategoryRule.id)
    ).all()
    return [_item(r) for r in rows]


def normalize_match_value(match_type: str, scope: str, raw: str) -> str:
    """入库前统一形状：小写；域名规则再过一次根域归并（匹配侧也用的是归并后的 host）。"""
    value = raw.strip().lower()
    if scope == "domain" and "." in value:
        value = normalize_domain(value)
    return value


def _find_duplicate(
    db: Session, user_id: int, match_type: str, scope: str, match_value: str
) -> AppCategoryRule | None:
    return db.scalar(
        select(AppCategoryRule).where(
            AppCategoryRule.user_id == user_id,
            AppCategoryRule.match_type == match_type,
            AppCategoryRule.scope == scope,
            AppCategoryRule.match_value == match_value,
        )
    )


def create_rule(
    db: Session, payload: AppRuleCreate, user_id: int
) -> dict:
    """新建规则。同 (match_type, scope, match_value) 已存在则覆盖其分类/别名（upsert）。"""
    value = normalize_match_value(payload.match_type, payload.scope, payload.match_value)
    row = _find_duplicate(db, user_id, payload.match_type, payload.scope, value)
    if row is None:
        row = AppCategoryRule(
            user_id=user_id,
            match_type=payload.match_type,
            scope=payload.scope,
            match_value=value,
        )
        db.add(row)
    row.category = payload.category
    row.display_label = payload.display_label
    row.priority = payload.priority
    row.enabled = True
    db.commit()
    db.refresh(row)
    return _item(row)


def update_rule(
    db: Session, rule_id: int, payload: AppRuleUpdate, user_id: int
) -> dict | None:
    row = db.scalar(
        select(AppCategoryRule).where(
            AppCategoryRule.id == rule_id, AppCategoryRule.user_id == user_id
        )
    )
    if row is None:
        return None
    data = payload.model_dump(exclude_unset=True)
    if "match_value" in data:
        row.match_value = normalize_match_value(
            data.get("match_type", row.match_type),
            data.get("scope", row.scope),
            data["match_value"],
        )
    for field in ("match_type", "scope", "category", "display_label", "priority", "enabled"):
        if field in data:
            setattr(row, field, data[field])
    db.commit()
    db.refresh(row)
    return _item(row)


def delete_rule(db: Session, rule_id: int, user_id: int) -> bool:
    row = db.scalar(
        select(AppCategoryRule).where(
            AppCategoryRule.id == rule_id, AppCategoryRule.user_id == user_id
        )
    )
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True
