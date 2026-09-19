"""行为分类体系：人工事件与设备使用共用的「行为大类」。

设备使用是**真实测量数据**（不是推测），这里只做应用/域名 → 大类的确定性映射，
供：
- 设备会话分段时打类别
- 设备事件与人工事件判重（同类才可能重复）
- 实验/复盘按类别汇总

分类是规则映射，可后续在数据里修正；未知应用/域名一律落到 other_online。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# 大类 code → 中文名（顺序即前端展示顺序）
CATEGORIES: dict[str, str] = {
    # 设备在线类
    "study_work": "学习工作",
    "video": "视频",
    "game": "游戏",
    "social": "社交",
    "reading": "阅读",
    "audio": "音频",
    "shopping": "购物",
    "browsing": "浏览",
    "creation": "创作",
    "utility": "工具系统",
    "finance": "金融理财",
    "life_service": "生活服务",
    "forum": "论坛社区",
    "other_online": "其他在线",
    # 线下人工类（设备无法观测）
    "bed": "上床",
    "meal": "吃饭",
    "exercise": "运动",
    "commute": "出门通勤",
    "chores": "家务",
    "social_offline": "线下社交",
    "other": "其他",
}

# 设备在线类（会话分类只会落在这里面）
ONLINE_CATEGORIES = {
    "study_work",
    "video",
    "game",
    "social",
    "reading",
    "audio",
    "shopping",
    "browsing",
    "creation",
    "utility",
    "finance",
    "life_service",
    "forum",
    "other_online",
}

# 人工事件类型 → 大类（判重与统计用；键为 EventType 值字符串，避免模型循环依赖）
# 含已撤下记录按钮的旧键（GAME_START/PHONE_START）：历史事件与旧实验指标仍要能解析。
EVENT_TYPE_CATEGORY: dict[str, str] = {
    "LEARNING_START": "study_work",
    "GAME_START": "game",
    "PHONE_START": "other_online",
    "MEAL_START": "meal",
    "BED_START": "bed",
    "OUT_START": "commute",
    "EXERCISE_START": "exercise",
    "CHORES_START": "chores",
    "SOCIAL_OFFLINE_START": "social_offline",
    "OTHER_START": "other",
    "SLEEP_START": "bed",
    "DEVICE_ACTIVITY": "other_online",
}

WEB_PREFIX = "web:"


def category_label(code: str | None) -> str:
    if not code:
        return ""
    return CATEGORIES.get(code, code)


def event_type_category(event_type: str | None) -> str | None:
    return EVENT_TYPE_CATEGORY.get(event_type or "")


# ── 应用/进程 → 大类 ──────────────────────────────────────
# 先精确（小写 app 名/包名），再子串关键词。顺序即优先级。
_APP_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (
        (
            "vscode", "visual studio", "code.exe", "pycharm", "intellij", "idea64",
            "webstorm", "goland", "clion", "android studio", "devenv", "sublime",
            "notepad++", "vim", "emacs", "jupyter", "matlab", "rstudio", "terminal",
            "powershell", "cmd.exe", "cmder", "windows terminal", "xshell", "putty",
            "word", "excel", "powerpoint", "wps", "notion", "obsidian", "typora",
            "onenote", "feishu", "lark", "飞书", "dingtalk", "钉钉", "teams",
            "slack", "zoom", "腾讯会议", "overleaf", "xmind", "anki", "zotero",
            "有道", "youdao", "mendeley", "endnote",
            # 手机端学习/办公（包名多为英文，中文标签匹配不到，得写英文串）
            "wework", "企业微信", "腾讯文档", "shimo", "石墨", "cnki", "知网",
            "chaoxing", "超星", "学习通", "xuexi", "学习强国", "grammarly",
            "duolingo", "多邻国", "baicizhan", "百词斩", "不背单词",
            "studio64", "jetbrains",
        ),
        "study_work",
    ),
    (
        (
            "bilibili", "哔哩哔哩", "youku", "优酷", "iqiyi", "爱奇艺", "tencentvideo",
            "腾讯视频", "youtube", "netflix", "douyin", "抖音", "tiktok", "huya",
            "虎牙", "douyu", "斗鱼", "mgtv", "芒果tv", "vlc", "potplayer", "iina",
            "mpv", "kmplayer", "twitch", "disney+", "prime video", "hbo",
            "ixigua", "西瓜视频", "kuaishou", "快手", "acfun", "搜狐视频",
        ),
        "video",
    ),
    (
        (
            "steam", "epicgames", "epic games", "genshin", "原神", "honkai", "崩坏",
            "league", "英雄联盟", "valorant", "dota", "csgo", "cs2", "minecraft",
            "battle.net", "blizzard", "ubisoft", "xbox", "playstation", "riot",
            "pubg", "王者荣耀", "和平精英", "网易游戏", "腾讯游戏", "wegame",
            "mihoyo", "hoyoverse", "roblox", "among us", "elden ring", "stardew",
            "taptap", "starrail", "绝区零", "zenless", "arknights", "明日方舟",
            "cygames", "supercell", "clash royale", "clash of clans",
        ),
        "game",
    ),
    (
        (
            "wechat", "weixin", "微信", "qq", "telegram", "whatsapp", "discord",
            "messenger", "line", "signal", "twitter", "x.com", "weibo", "微博",
            "instagram", "facebook", "xiaohongshu", "小红书", "threads",
            "bluesky", "mastodon", "snapchat", "linkedin", "脉脉", "陌陌", "soul",
        ),
        "social",
    ),
    (
        (
            "kindle", "微信读书", "weread", "ireader", "掌阅", "番茄小说",
            "fanqienovel", "wattpad", "reader", "阅读", "多看", "duokan", "ibooks",
            "books", "kobo", "scribd", "qidian", "起点", "jjwxc", "晋江",
            "ao3", "archiveofourown",
        ),
        "reading",
    ),
    (
        (
            "网易云音乐", "cloudmusic", "qqmusic", "qq音乐", "spotify", "music",
            "podcast", "喜马拉雅", "ximalaya", "得到", "dedao", "蜻蜓fm",
            "kugou", "酷狗", "kuwo", "酷我", "audible", "apple music", "foobar",
            "小宇宙", "xiaoyuzhou", "lizhifm", "荔枝", "soundcloud", "bandcamp",
        ),
        "audio",
    ),
    (
        (
            "taobao", "淘宝", "jd.com", "京东", "amazon", "亚马逊", "pinduoduo",
            "拼多多", "tmall", "天猫", "meituan", "美团", "ele.me", "饿了么",
            "xianyu", "闲鱼", "booking", "airbnb", "ebay", "shopee", "temu",
            "suning", "苏宁", "dianping", "大众点评", "唯品会", "vip.com",
            "得物", "dewu", "shein", "盒马", "hema",
        ),
        "shopping",
    ),
    (
        (
            "photoshop", "lightroom", "premiere", "after effects", "davinci",
            "final cut", "illustrator", "figma", "blender", "audacity", "obs",
            "capcut", "剪映", "sketch", "affinity", "krita", "gimp", "inkscape",
            "fl studio", "ableton", "logic pro", "cubase",
            "canva", "procreate", "美图秀秀", "meitu", "醒图", "xingtu", "audition",
        ),
        "creation",
    ),
    (
        (
            "explorer.exe", "finder", "settings", "设置", "control panel", "计算器",
            "calculator", "task manager", "任务管理器", "7-zip", "winrar",
            "bandizip", "驱动", "nvidia", "amd", "intel", "火绒", "360",
            "antivirus", "defender", "ccleaner", "geek uninstaller", "clash",
            "v2ray", "tailscale", "mihomo", "sing-box",
            "baidunetdisk", "百度网盘", "aliyundrive", "阿里云盘", "nutstore",
            "坚果云", "thunder", "迅雷", "qqpcmgr", "腾讯电脑管家", "鲁大师",
            "输入法", "sogouinput", "baiduime", "dism++", "rufus", "ventoy",
            "keepass", "bitwarden", "1password", "teamviewer", "anydesk",
            "todesk", "sunlogin", "向日葵", "parsec", "syncthing", "hwinfo",
            "aida64", "cpu-z", "gpu-z", "afterburner",
            "时钟", "alarmclock", "deskclock", "cc-switch",
        ),
        "utility",
    ),
    (
        (
            "chrome", "edge", "firefox", "msedge", "brave", "opera", "safari",
            "browser", "360se", "qqbrowser", "搜狗", "sogou", "vivaldi", "arc",
            "quark", "夸克", "ucmobile", "ucweb", "uc浏览器", "2345", "liebao",
            "猎豹", "maxthon", "傲游", "yandex", "duckduckgo", "waterfox",
            "librewolf", "mark.via",
        ),
        "browsing",
    ),
    (
        (
            "okx", "okex", "okinc", "binance", "coinbase", "huobi", "火币",
            "metamask", "支付宝", "alipay", "云闪付", "unionpay", "paypal",
            "stripe", "同花顺", "10jqka", "东方财富", "eastmoney", "雪球",
            "xueqiu", "涨乐", "招商银行", "cmbchina", "工商银行", "icbc",
            "建设银行", "ccb", "农业银行", "abchina", "中国银行", "掌上生活",
            "bank", "证券", "券商", "基金", "理财",
        ),
        "finance",
    ),
    (
        (
            "联通", "unicom", "sinovatech", "中国移动", "chinamobile", "10086",
            "中国电信", "国家电网", "网上国网", "sgcc", "电力", "燃气", "水务",
            "自来水", "12306", "交管12123", "12123", "粤省事", "随申办",
            "政务", "挂号", "健康云", "顺丰", "sf-express", "菜鸟", "cainiao",
            "快递100", "kuaidi100", "丰巢", "wisedu", "cpdaily",
        ),
        "life_service",
    ),
    (
        (
            "zhihu", "知乎", "tieba", "贴吧", "reddit", "v2ex", "hupu", "虎扑",
            "douban", "豆瓣", "quora", "ngabbs", "tianya", "天涯", "discuz",
        ),
        "forum",
    ),
]

# 域名 → 大类（web:<域名> 的细分；命中不了取 browsing）
_DOMAIN_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (
        (
            "bilibili.com", "youtube.com", "douyin.com", "youku.com", "iqiyi.com",
            "v.qq.com", "netflix.com", "twitch.tv", "mgtv.com", "sohu.com", "vimeo",
        ),
        "video",
    ),
    (
        ("github.com", "gitlab.com", "stackoverflow.com", "csdn.net", "juejin.cn",
         "leetcode", "arxiv.org", "wikipedia.org", "developer.mozilla.org",
         "docs.", "readthedocs", "openai.com", "deepseek.com", "anthropic.com",
         "coursera.org", "udemy.com", "zhihuishu.com", "icourse163.org"),
        "study_work",
    ),
    (
        ("weibo.com", "x.com", "twitter.com",
         "instagram.com", "facebook.com", "xiaohongshu.com"),
        "social",
    ),
    (
        ("taobao.com", "jd.com", "tmall.com", "pinduoduo.com", "amazon.",
         "ebay.com", "aliexpress.com"),
        "shopping",
    ),
    (("steampowered.com", "epicgames.com", "battle.net", "hoyolab.com"), "game"),
    (("music.163.com", "y.qq.com", "spotify.com", "xiami.com"), "audio"),
    (("qidian.com", "zongheng.com", "jjwxc.net", "fanqie", "weread.qq.com"), "reading"),
    (("figma.com", "canva.com", "photopea.com"), "creation"),
    (
        ("zhihu.com", "tieba.baidu.com", "reddit.com", "v2ex.com", "hupu.com",
         "douban.com", "quora.com", "nga.178.com", "ngabbs.com", "tianya.cn",
         "discuz.net", "gamer.com.tw"),
        "forum",
    ),
    (
        ("okx.com", "binance.com", "coinbase.com", "huobi.com", "gate.io",
         "oklink.com", "alipay.com", "paypal.com", "stripe.com",
         "eastmoney.com", "xueqiu.com", "10jqka.com.cn", "icbc.com.cn",
         "ccb.com", "abchina.com", "boc.cn", "cmbchina.com"),
        "finance",
    ),
    (
        ("10010.com", "10086.cn", "189.cn", "sgcc.com.cn", "95598.cn",
         "12306.cn", "122.gov.cn", "gov.cn", "sf-express.com", "cainiao.com",
         "yto.net.cn", "zto.com", "ems.com.cn", "kuaidi100.com"),
        "life_service",
    ),
]


def _match(rules: list[tuple[tuple[str, ...], str]], text: str) -> str | None:
    for keys, category in rules:
        for key in keys:
            if key in text:
                return category
    return None


# ── 用户规则（覆盖内置词表） ──────────────────────────────
# 匹配用**大小写不敏感子串**，不用 glob/正则：与内置 `_match` 一致，
# 敲 `bilibili` 就能同时命中 `com.bilibili.app` 与 `web:bilibili.com`；
# glob 得写 `*bilibili*` 容易写错，正则还有 ReDoS 风险。要锚定就用 exact。


@dataclass(frozen=True)
class AppRule:
    match_type: str  # exact | substring
    scope: str  # app | domain | any
    match_value: str  # 已小写；domain 规则已过 normalize_domain
    category: str
    label: str | None = None  # display_label（别名/改名）
    priority: int = 100


@dataclass(frozen=True)
class RuleSet:
    """不可变的用户规则集：每个请求/每次重建**加载一次**，别在循环里查库。

    `rules` 需按 (exact 在前, priority 降序, match_value 长度降序) 排好——
    `match` 依赖这个顺序取「最高优先级」。
    """

    rules: tuple[AppRule, ...] = ()

    def match(
        self, *, text: str, tokens: Sequence[str], is_domain: bool
    ) -> AppRule | None:
        substring_hit: AppRule | None = None
        for rule in self.rules:
            if rule.scope != "any" and rule.scope != (
                "domain" if is_domain else "app"
            ):
                continue
            if rule.match_type == "exact":
                if rule.match_value in tokens:
                    return rule  # exact 优先，且已排在最前
            elif substring_hit is None and rule.match_value in text:
                substring_hit = rule
        return substring_hit


EMPTY_RULES = RuleSet()

_RULE_TYPE_ORDER = {"exact": 0, "substring": 1}


def build_ruleset(rows: Sequence[object]) -> RuleSet:
    """ORM 行 → RuleSet（排序在这里统一做，查询侧不必关心顺序）。"""
    rules = [
        AppRule(
            match_type=row.match_type,
            scope=row.scope,
            match_value=row.match_value,
            category=row.category,
            label=row.display_label,
            priority=row.priority,
        )
        for row in rows
        if row.enabled
    ]
    rules.sort(
        key=lambda r: (_RULE_TYPE_ORDER.get(r.match_type, 9), -r.priority, -len(r.match_value))
    )
    return RuleSet(tuple(rules))


def _rule_for(app: str, label: str | None, rules: RuleSet | None) -> AppRule | None:
    if rules is None or not app:
        return None
    if app.startswith(WEB_PREFIX):
        host = normalize_domain(app[len(WEB_PREFIX):])
        return rules.match(text=host, tokens=(host,), is_domain=True)
    low_app = app.lower()
    low_label = (label or "").lower()
    return rules.match(
        text=f"{low_app} {low_label}".strip(),
        tokens=tuple(t for t in (low_app, low_label) if t),
        is_domain=False,
    )


def normalize_domain(host: str) -> str:
    """把域名归并到根域：去掉 www/m 等前缀与常见二级后缀差异。

    只做保守归并（同站点不同入口算一个），不做完整公共后缀表。
    """
    host = host.strip().lower().rstrip(".")
    if not host:
        return host
    for prefix in ("www.", "m.", "mobile.", "web.", "wap.", "www2.", "amp."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    # 处理 com.cn / co.uk / com.hk 等两段式后缀
    two_level = {"com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "co.uk",
                 "org.uk", "ac.uk", "co.jp", "com.hk", "com.tw", "co.kr"}
    tail2 = ".".join(parts[-2:])
    if tail2 in two_level:
        return ".".join(parts[-3:])
    return tail2


def classify_app(
    app: str, label: str | None = None, rules: RuleSet | None = None
) -> str:
    """应用/进程/网页 → 行为大类。命中不了返回 other_online。

    用户规则优先于内置词表（exact > 最长子串；见 `RuleSet.match`）。
    """
    if not app:
        return "other_online"
    if app.startswith(WEB_PREFIX):
        host = normalize_domain(app[len(WEB_PREFIX):])
        hit = _rule_for(app, label, rules)
        if hit:
            return hit.category
        return _match(_DOMAIN_KEYWORDS, host) or "browsing"
    hit = _rule_for(app, label, rules)
    if hit:
        return hit.category
    text = f"{app} {label or ''}".lower()
    return _match(_APP_KEYWORDS, text) or "other_online"


def resolve_label(app: str, label: str | None, rules: RuleSet | None = None) -> str | None:
    """展示名：命中规则且带 display_label 则用别名，否则原样返回采集到的 label。"""
    hit = _rule_for(app, label, rules)
    return hit.label if hit and hit.label else label
