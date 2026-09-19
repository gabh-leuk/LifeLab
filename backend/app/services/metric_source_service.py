"""指标名 → 数据源（source）识别。

用户在设计实验时只写指标名（「睡前手机时长」），由这里判断它该从哪取数，
填成 `MetricDef.source`（`usage_platform:android@22-24`）。

**授权例外**（记在 `docs/MASTER_PLAN.md` 的「AI 绝对红线」节下）：
这是 AI 写库字段的一条路，边界四条 —— 只写 `source` 一个路由字段、
只填调用方显式提名的那几个 key、输出必过 `validate_metric_source` 白名单、
任何失败都落回 `manual`（绝不挡住实验创建）。详见调用方
`app/routers/experiments.py` 与 `app/agent/tools.py`。

第四条边界上另有一道**专门补的闸**：`usage_duration:<app>` 里的 app 必须来自
`known_apps` —— 白名单对它只查长度（它没法知道库里有哪些应用）。
"""

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.behavior_categories import CATEGORIES, ONLINE_CATEGORIES
from app.llm import chat_json
from app.schemas.experiment import split_time_window, validate_metric_source
from app.services import device_service
from app.services.event_type_service import creatable_builtins
from app.utils import local_today

logger = logging.getLogger(__name__)

# 应用清单回看多久、给 LLM 看多少个（多了 prompt 长，少了识别不到长尾应用）
_APP_LOOKBACK_DAYS = 30
_APP_LIMIT = 60

_STATE_FIELDS = ("energy", "focus", "irritation")


def _source_grammar() -> str:
    """把 source 语法从常量生成 —— 手抄一份必然与白名单漂移。"""
    event_types = "|".join(creatable_builtins())
    online_cats = "|".join(k for k in CATEGORIES if k in ONLINE_CATEGORIES)
    return f"""- manual
    手动录入（找不到合适数据源时用；主观评分、无法自动测量的指标就用它）
- event_duration:<事件类型>    某类记录当天总时长（分钟）
- event_count:<事件类型>       某类记录当天次数
    事件类型取值：{event_types}
- event_duration_category:<行为大类>   设备实际活动某大类当天时长（分钟，自动采集）
- event_count_category:<行为大类>      某大类设备段当天次数
    行为大类取值：{online_cats}
- state_avg:{"|".join(_STATE_FIELDS)}   当天状态均值（0-5）
- usage_platform:android|pc    手机/电脑当天使用总时长（分钟，设备自动采集）
- usage_total                  全部设备当天使用总时长（分钟）
- usage_duration:<应用>        某应用当天使用时长（分钟，设备自动采集）
    以上 usage_* 可加时段后缀 @起-止（0<=起<止<=24，不跨午夜），如 usage_platform:android@22-24"""


def _system_prompt(known_apps: list[dict]) -> str:
    if known_apps:
        lines = []
        for a in known_apps:
            label = a.get("label") or ""
            tail = f"（{label}）" if label and label != a["app"] else ""
            lines.append(f"  {a['app']}{tail}")
        app_block = "该用户近期实际用过的应用/网站（**原样照抄这里的 key**）：\n" + "\n".join(lines)
    else:
        app_block = "（该用户近期没有采集到任何应用，不要使用 usage_duration）"

    return f"""你是 LifeLab 的数据源识别器。用户给指标起了个中文名字，
你要判断它每天的数字该从哪个数据源来。

可用数据源（source 只能取下列值）：
{_source_grammar()}

{app_block}

要求：
1. 只针对每个指标名做最合理的映射，不要发明数据源、不要发明应用 key。
2. `usage_duration:<应用>` 的应用部分**必须从上表原样照抄**，写中文名或猜测的包名都会取不到数。
3. 拿不准、或这个指标本质上只能人工打分（如「主观感受」「满意度」）→ 用 `manual`。
4. 名字里含时段暗示就加后缀：「睡前」≈ 22-24，「早上」≈ 6-9，「深夜」≈ 0-3。
5. 只输出 JSON，形如：
{{"sources": {{"指标key": "source", "另一个指标key": "manual"}}}}"""


def _is_known_app(source: str, known: set[str]) -> bool:
    """`usage_duration:<app>` 的 app 必须来自 `known_apps` —— 白名单管不了这个。

    `validate_metric_source` 对 `usage_duration:` 只查长度（它没法知道库里有哪些
    应用），所以模型凭空编一个包名也能过闸、却永远取不到数 —— 一个静默的零。
    prompt 里那句「必须原样照抄」是**软约束**，这里落成硬约束。
    `apps` 为空时全部拒绝，正合 prompt 里「没采集到任何应用就不要用
    usage_duration」那句。
    """
    base, _ = split_time_window(source)
    kind, sep, arg = base.partition(":")
    if not sep or kind != "usage_duration":
        return True
    return arg in known


def infer_sources(
    db: Session,
    metrics: list[dict],
    *,
    user_id: int,
    context: str = "",
    timeout: float = 20.0,
) -> dict[str, str]:
    """把指标名识别成 source；返回 {key: source}，没识别出来的 key 不出现。

    `metrics` 为 `[{"key","name","unit"}]`（unit 可缺）。`context` 是实验名/问题等
    背景，给模型判断用，可为空。每个返回值都过了 `validate_metric_source`，
    非法值一律丢弃（调用方见到 key 缺失就保持 manual）。

    只有 chat 调用本身失败（含超时）才抛 `LLMError`；解析不出东西**不算失败**，
    返回空 dict —— 识别不出来是常态，不该让调用方当异常处理。

    `timeout` 定在 20s：prompt 里有几十个应用 key，10s 实测**真的会超**
    （超时 = 来源留空 = 又是一个静默的零）。20s 仍远低于 SDK 默认的 600s。
    """
    asked = [m for m in metrics if m.get("key") and m.get("name")]
    if not asked:
        return {}

    end = local_today()
    apps = device_service.known_apps(
        db, _days_before(end, _APP_LOOKBACK_DAYS), end, limit=_APP_LIMIT, user_id=user_id
    )

    lines = []
    if context.strip():
        lines.append(f"实验背景：{context.strip()}")
    lines.append("要识别的指标：")
    for m in asked:
        unit = m.get("unit")
        lines.append(f"- key={m['key']}  名称={m['name']}" + (f"  单位={unit}" if unit else ""))

    raw = chat_json(
        _system_prompt(apps),
        "\n".join(lines),
        max_tokens=800,
        timeout=timeout,
        caller="metric.infer",
    )

    sources = raw.get("sources")
    if not isinstance(sources, dict):
        # 模型偶尔直接给平铺的 {key: source}，容忍这一种形状
        sources = raw

    allowed = {m["key"] for m in asked}
    known_apps = {a["app"] for a in apps}
    out: dict[str, str] = {}
    for key, value in sources.items():
        if key not in allowed or not isinstance(value, str):
            continue
        try:
            source = validate_metric_source(value.strip())
        except ValueError:
            logger.info("识别结果非法，丢弃：%s -> %s", key, value)
            continue
        if not _is_known_app(source, known_apps):
            logger.info("识别出的应用不在已知清单里，丢弃：%s -> %s", key, source)
            continue
        out[key] = source
    return out


def _days_before(day: str, n: int) -> str:
    return (date.fromisoformat(day) - timedelta(days=n)).isoformat()
