"""LifeLab PC 采集器（Windows）：前台应用使用时长 → 分小时上报。

纯标准库实现（ctypes 调 Win32 + urllib 上传），无需安装任何依赖。

用法（推荐直接双击 `scripts\\pc_collector.cmd`，等价于下列命令）：
    # 1. 注册设备（在 /docs 或前端也可创建；这里一步完成）
    .venv\\Scripts\\python.exe scripts\\pc_collector.py --setup --name 我的电脑

    # 2. 常驻采集（默认每 5 分钟上传一次今日累计；同设备同日整日替换）
    .venv\\Scripts\\python.exe scripts\\pc_collector.py

    # 3. 安装/卸载/查看 登录自启动（隐藏运行，日志见 ~/.lifelab/collector.log）
    .venv\\Scripts\\python.exe scripts\\pc_collector.py --install-autostart
    .venv\\Scripts\\python.exe scripts\\pc_collector.py --uninstall-autostart
    .venv\\Scripts\\python.exe scripts\\pc_collector.py --autostart-status

    # 4. 快速验证：采集 30 秒后上传
    .venv\\Scripts\\python.exe scripts\\pc_collector.py --once --duration 30

说明：
- 累计按时段分桶（本地小时 0-23），上报 POST /ingest/usage/hourly；每次上传当天全部小时
- 同时上报精确前台会话（POST /ingest/usage/sessions）：浏览器能推断出站点时以站点为准，
  否则用进程名；服务端据此自动生成行为时间段（真实测量，非推测）
- 在场判断：锁屏/息屏立即停计；否则键鼠静止超过阈值（默认 900s=15 分钟）才判离开。
  取 15 分钟是为了覆盖「看视频/看文档不动键鼠」的被动消费（人在看但没输入）
- 切出/切入应用会计 switches（任务切换频率，供专注度分析）
- 浏览器网页：读前台窗口标题推断站点，记为 `web:<域名>`（词表 + 显式域名，推断不出则不记）
- 单实例：已有采集器在跑时再启动会直接退出（避免重复上报互相覆盖）
- 上传失败（后端未启动/断网）只记日志、不退出，下个间隔自动重试
- 配置保存在 ~/.lifelab/pc_collector.json（含 token，不要提交到仓库）
- 站点精度：只读标题、无 URL，冷门站会漏（漏的仍计入浏览器进程总时长）；
  网页内具体站点仍受限于标题，精确站点/完整 URL 需浏览器扩展（后续）
"""

import argparse
import ctypes
import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.request
from collections import defaultdict
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

POLL_SECONDS = 5
UPLOAD_SECONDS = 300
# 在场判断：键鼠静止超过该秒数（且屏幕未锁）才判为离开。取 15 分钟是为了
# 覆盖「看视频/看文档不动键鼠」的被动消费——这类场景没有输入，但人在看。
IDLE_THRESHOLD_SECONDS = 900
STATE_VERSION = 3  # v3 = 分时段 + 精确前台会话；旧格式读到即丢弃
MAX_SESSIONS = 2500  # 单日会话上限（超限保留最近的部分，防止状态/上报过大）
CONFIG_PATH = Path.home() / ".lifelab" / "pc_collector.json"
STATE_PATH = Path.home() / ".lifelab" / "pc_collector_state.json"
LOG_PATH = Path.home() / ".lifelab" / "collector.log"
STARTUP_DIR = (
    Path(os.environ.get("APPDATA", ""))
    / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
)
AUTOSTART_VBS = STARTUP_DIR / "lifelab_collector.vbs"

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
user32.OpenInputDesktop.restype = wintypes.HANDLE
user32.CloseDesktop.argtypes = [wintypes.HANDLE]
user32.CloseDesktop.restype = wintypes.BOOL

ERROR_ALREADY_EXISTS = 183
_instance_mutex = None  # 持有句柄，防止被 GC 回收导致互斥失效

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DESKTOP_READOBJECTS = 0x0001

# 常见进程的展示名（其余用进程名去扩展名）
LABELS = {
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "firefox.exe": "Firefox",
    "Code.exe": "VS Code",
    "Cursor.exe": "Cursor",
    "pycharm64.exe": "PyCharm",
    "WindowsTerminal.exe": "终端",
    "powershell.exe": "PowerShell",
    "pwsh.exe": "PowerShell",
    "WeChat.exe": "微信",
    "QQ.exe": "QQ",
    "Telegram.exe": "Telegram",
    "Discord.exe": "Discord",
    "Steam.exe": "Steam",
    "explorer.exe": "资源管理器",
    "WINWORD.EXE": "Word",
    "EXCEL.EXE": "Excel",
}

# 会在标题里暴露当前网页的浏览器；只有这些进程才去读窗口标题
BROWSERS = {
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "opera.exe",
    "brave.exe",
    "vivaldi.exe",
    "chromium.exe",
    "iexplore.exe",
    "360se.exe",
    "360chrome.exe",
    "qqbrowser.exe",
}

# 网站 token 前缀：网站记为 `web:<域名>` 的普通 usage 行，与浏览器进程行并存。
# 聚合取数（无 app 过滤）会排除 web:*，避免与浏览器进程行重复计总时长。
WEB_PREFIX = "web:"

# 窗口标题 → 站点域名 的推断词表（关键词统一小写匹配）。
# 标题里通常只有网页标题、没有域名，靠这张表 + 显式域名识别来推断；
# 命中不到就不产 web:*（只计浏览器进程总时长，宁缺毋滥）。
SITE_HINTS = {
    "youtube": "youtube.com",
    "bilibili": "bilibili.com",
    "哔哩哔哩": "bilibili.com",
    "知乎": "zhihu.com",
    "github": "github.com",
    "微博": "weibo.com",
    "weibo": "weibo.com",
    "百度": "baidu.com",
    "baidu": "baidu.com",
    "google": "google.com",
    "gmail": "mail.google.com",
    "小红书": "xiaohongshu.com",
    "douban": "douban.com",
    "豆瓣": "douban.com",
    "掘金": "juejin.cn",
    "juejin": "juejin.cn",
    # LeetCode 标题尾段就是站名（「Two Sum - LeetCode」），无点号过不了域名正则。
    # 力扣必须排在 leetcode 前：中文站标题是「… - 力扣（LeetCode）」，两者都命中时
    # 取先插入的，而它属于 leetcode.cn。
    "力扣": "leetcode.cn",
    "leetcode": "leetcode.com",
    "csdn": "csdn.net",
    "stack overflow": "stackoverflow.com",
    "stackoverflow": "stackoverflow.com",
    "wikipedia": "wikipedia.org",
    "维基百科": "wikipedia.org",
    "淘宝": "taobao.com",
    "京东": "jd.com",
    "网易云音乐": "music.163.com",
    "腾讯视频": "v.qq.com",
    "爱奇艺": "iqiyi.com",
    "优酷": "youku.com",
    "抖音": "douyin.com",
    "reddit": "reddit.com",
    "twitter": "x.com",
    "twitch": "twitch.tv",
    "netflix": "netflix.com",
    "pypi": "pypi.org",
    "mdn": "developer.mozilla.org",
}

# 去掉标题末尾的浏览器名 / 标签数量等噪声（大小写不敏感）
_TITLE_SUFFIX = re.compile(
    r"\s*[-–—]\s*(google chrome|microsoft.?\s*edge|mozilla firefox|brave|"
    r"opera|vivaldi|chromium|internet explorer|360.*浏览器|qq浏览器)"
    r".*$",
    re.IGNORECASE,
)
_TITLE_NOISE = re.compile(r"\s*[-–—]\s*and \d+ more.*$", re.IGNORECASE)
_DOMAIN_RE = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", re.IGNORECASE)
_TITLE_SEP = re.compile(r"\s+[-–—]\s+")


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def window_text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def foreground_window() -> tuple[str | None, str]:
    """前台窗口 (进程名, 窗口标题)。

    标题只在进程属于已知浏览器时读取（其它进程的标题无用，省一次系统调用）。
    """
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, ""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None, ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return None, ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None, ""
        app = os.path.basename(buf.value)
    finally:
        kernel32.CloseHandle(handle)
    title = window_text(hwnd) if app.lower() in BROWSERS else ""
    return app, title


def resolve_site(title: str) -> str | None:
    """从浏览器窗口标题推断站点域名；推断不出返回 None（不硬造站点）。

    标题通常只有网页标题、没有域名（如「<视频> - YouTube」），所以：
    去浏览器后缀 → 取 ` - ` 切分的尾段 → 先对词表、再找显式域名；尾段不成
    再拿整条标题兜底。都命中不到就放弃，只计入浏览器进程总时长。
    """
    if not title:
        return None
    cleaned = _TITLE_NOISE.sub("", _TITLE_SUFFIX.sub("", title)).strip()
    if not cleaned:
        return None
    segments = [s.strip() for s in _TITLE_SEP.split(cleaned) if s.strip()]
    candidates = list(reversed(segments)) + [cleaned]
    for text in candidates:
        low = text.lower()
        for keyword, domain in SITE_HINTS.items():
            if keyword in low:
                return domain
        m = _DOMAIN_RE.search(low)
        if m:
            return m.group(0)
    return None


def web_source(app: str, title: str) -> str | None:
    """前台为浏览器且能推断出站点时，返回 `web:<域名>`；否则 None。"""
    if not app or app.lower() not in BROWSERS:
        return None
    domain = resolve_site(title)
    return f"{WEB_PREFIX}{domain}" if domain else None


def idle_seconds() -> float:
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    user32.GetLastInputInfo(ctypes.byref(info))
    return (kernel32.GetTickCount() - info.dwTime) / 1000.0


def is_locked() -> bool:
    """会话是否锁定（含锁屏/切到安全桌面）。

    锁定时输入桌面切到 Winlogon，非系统进程打不开 → OpenInputDesktop 失败，
    据此判锁屏：锁屏即视为离开，立即停计（不等长空闲阈值）。
    """
    hdesk = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    if not hdesk:
        return True
    user32.CloseDesktop(hdesk)
    return False


def label_for(app: str) -> str:
    if app.startswith(WEB_PREFIX):
        return app[len(WEB_PREFIX):][:40]
    if app in LABELS:
        return LABELS[app]
    stem = app.rsplit(".", 1)[0]
    return stem[:40] if stem else app


def today_str() -> str:
    # 采集器按本机本地日切分（与复盘/采集协议一致）
    return datetime.now().strftime("%Y-%m-%d")  # noqa: DTZ005


def current_hour() -> int:
    return datetime.now().hour  # noqa: DTZ005


def log(msg: str) -> None:
    """带时间戳输出：控制台 + 追加到日志文件（UTF-8，后台隐藏运行时唯一可见处）。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # noqa: DTZ005
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def acquire_single_instance() -> bool:
    """单实例互斥：已有一个采集器在跑时返回 False。

    自启动 + 手动启动同时运行会让两个进程各自"整日替换"上报、互相覆盖，
    造成时段数据错乱，所以只允许一个实例。
    """
    global _instance_mutex
    _instance_mutex = kernel32.CreateMutexW(None, False, "Local\\LifeLabPCCollector")
    return kernel32.GetLastError() != ERROR_ALREADY_EXISTS


# ── 配置与上传 ─────────────────────────────────────────────


def load_config() -> dict | None:
    if not CONFIG_PATH.exists():
        return None
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state(
    day: str,
) -> tuple[dict[int, dict[str, float]], dict[int, dict[str, int]], list[dict]]:
    """加载当日累计（重启续算；跨日/旧格式自动丢弃）。

    包含分时段时长、切换次数、以及精确前台会话（供自动时间段行为）。
    没有它，重启后重新计数会因"整日替换"冲掉之前已上传的数据。
    """
    if not STATE_PATH.exists():
        return {}, {}, []
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}, []
    if state.get("version") != STATE_VERSION or state.get("date") != day:
        return {}, {}, []
    try:
        hours = {
            int(h): {str(a): float(s) for a, s in apps.items()}
            for h, apps in state.get("hours", {}).items()
        }
        switches = {
            int(h): {str(a): int(n) for a, n in apps.items()}
            for h, apps in state.get("switches", {}).items()
        }
        sessions = [
            {
                "app": str(s["app"]),
                "label": s.get("label"),
                "start": int(s["start"]),
                "end": int(s["end"]),
            }
            for s in state.get("sessions", [])
            if isinstance(s, dict) and "app" in s and "start" in s and "end" in s
        ]
    except (ValueError, TypeError, AttributeError, KeyError):
        return {}, {}, []
    return hours, switches, sessions


def save_state(
    day: str,
    hours: dict[int, dict[str, float]],
    switches: dict[int, dict[str, int]],
    sessions: list[dict] | None = None,
) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(
            {
                "version": STATE_VERSION,
                "date": day,
                "hours": {str(h): apps for h, apps in hours.items()},
                "switches": {str(h): apps for h, apps in switches.items()},
                "sessions": (sessions or [])[-MAX_SESSIONS:],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def api_call(
    base_url: str,
    method: str,
    path: str,
    body: dict | None = None,
    token: str | None = None,
    timeout: int = 30,
) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Device-Token"] = token
    req = urllib.request.Request(
        base_url.rstrip("/") + path, data=data, method=method, headers=headers
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def setup(base_url: str, name: str) -> dict:
    result = api_call(base_url, "POST", "/devices", {"name": name, "platform": "pc"})
    cfg = {
        "base_url": base_url.rstrip("/"),
        "token": result["token"],
        "device_id": result["device"]["id"],
        "name": result["device"]["name"],
        "created_at": datetime.now().isoformat(timespec="seconds"),  # noqa: DTZ005
    }
    save_config(cfg)
    return cfg


def _python_exe() -> str:
    """优先用后端 venv 的解释器，缺失时退回当前解释器。"""
    venv_py = Path(__file__).resolve().parent.parent / ".venv" / "Scripts" / "python.exe"
    return str(venv_py) if venv_py.exists() else sys.executable


def install_autostart() -> int:
    """在「启动」文件夹放一个隐藏启动器（.vbs），登录即后台静默采集。

    生成时写入本机绝对路径，卸载只需删掉该文件。
    """
    python = _python_exe()
    script = Path(__file__).resolve()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 隐藏窗口直接拉起 python（日志由 Python 自己写 LOG_PATH，不依赖 shell 重定向）
    run_line = f'"{python}" -u "{script}"'
    vbs = (
        'Set sh = CreateObject("WScript.Shell")\r\n'
        f'sh.Run "{run_line.replace(chr(34), chr(34) * 2)}", 0, False\r\n'
    )
    STARTUP_DIR.mkdir(parents=True, exist_ok=True)
    AUTOSTART_VBS.write_text(vbs, encoding="utf-8")
    print(f"已安装自启动：{AUTOSTART_VBS}")
    print(f"登录 Windows 后后台静默采集，日志：{LOG_PATH}")
    print("卸载：python scripts\\pc_collector.py --uninstall-autostart")
    return 0


def uninstall_autostart() -> int:
    if AUTOSTART_VBS.exists():
        AUTOSTART_VBS.unlink()
        print(f"已移除自启动：{AUTOSTART_VBS}")
    else:
        print("未发现自启动项，无需移除。")
    return 0


def autostart_status() -> int:
    print(f"自启动：{'已安装' if AUTOSTART_VBS.exists() else '未安装'}")
    print(f"位置：{AUTOSTART_VBS}")
    print(f"日志：{LOG_PATH}")
    return 0


def upload_day(
    cfg: dict,
    day: str,
    hours: dict[int, dict[str, float]],
    switches: dict[int, dict[str, int]],
    *,
    dry_run: bool = False,
) -> dict | None:
    entries = []
    for hour, apps in hours.items():
        hour_switches = switches.get(hour, {})
        for app, seconds in apps.items():
            if seconds < 1:
                continue
            entries.append(
                {
                    "app": app,
                    "label": label_for(app),
                    "hour": hour,
                    "seconds": min(3600, round(seconds)),
                    "switches": hour_switches.get(app, 0),
                }
            )
    if not entries:
        return None
    if dry_run:
        return {"dry_run": True, "date": day, "entries": entries}
    return api_call(
        cfg["base_url"], "POST", "/ingest/usage/hourly",
        {"date": day, "entries": entries}, token=cfg["token"],
    )


def build_session_entries(sessions: list[dict]) -> list[dict]:
    """状态里的会话 → 上报条目（补 label、丢弃空区间）。"""
    entries: list[dict] = []
    for s in sessions:
        try:
            start = int(s["start"])
            end = int(s["end"])
            app = str(s["app"])
        except (KeyError, TypeError, ValueError):
            continue
        if not app or end <= start:
            continue
        entries.append(
            {
                "app": app,
                "label": s.get("label") or label_for(app),
                "start_ms": start,
                "end_ms": end,
            }
        )
    return entries[:3000]


def upload_sessions(
    cfg: dict,
    day: str,
    sessions: list[dict],
    *,
    dry_run: bool = False,
) -> dict | None:
    """上报精确前台会话（服务端合并为行为段；无会话返回 None）。"""
    entries = build_session_entries(sessions)
    if not entries:
        return None
    if dry_run:
        return {"dry_run": True, "date": day, "sessions": entries}
    return api_call(
        cfg["base_url"], "POST", "/ingest/usage/sessions",
        {"date": day, "entries": entries}, token=cfg["token"],
    )


def try_upload(
    cfg: dict,
    day: str,
    hours: dict[int, dict[str, float]],
    switches: dict[int, dict[str, int]],
    sessions: list[dict],
    *,
    dry_run: bool = False,
) -> tuple[dict | None, str | None]:
    """上传（会话 + 分小时）并吞掉异常：断网/后端未启动不应终止采集。

    返回 (分小时结果, 错误信息)；两者都成功时错误为 None。
    """
    err: str | None = None
    try:
        upload_sessions(cfg, day, sessions, dry_run=dry_run)
    except Exception as e:  # noqa: BLE001
        err = f"sessions:{type(e).__name__}: {e}"
    try:
        result = upload_day(cfg, day, hours, switches, dry_run=dry_run)
    except Exception as e:  # noqa: BLE001
        hourly_err = f"hourly:{type(e).__name__}: {e}"
        return None, f"{err}; {hourly_err}" if err else hourly_err
    return result, err


def fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


def flatten_hours(hours: dict[int, dict[str, float]]) -> dict[str, float]:
    """把分时段累计压平为按应用的当天合计（用于摘要展示）。"""
    out: dict[str, float] = defaultdict(float)
    for apps in hours.values():
        for app, seconds in apps.items():
            out[app] += seconds
    return dict(out)


def fmt_hours(counts: dict[str, float]) -> str:
    # 总时长只算进程行（web:* 是浏览器时长的细分，计进去会重复）
    total = sum(v for k, v in counts.items() if not k.startswith(WEB_PREFIX))
    top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:4]
    detail = "、".join(f"{label_for(k)} {fmt_duration(v)}" for k, v in top)
    return f"{fmt_duration(total)}（{detail}）"


def fmt_hour_breakdown(hours: dict[int, dict[str, float]]) -> str:
    parts = []
    for h, apps in sorted(hours.items()):
        total = sum(v for k, v in apps.items() if not k.startswith(WEB_PREFIX))
        if total >= 1:
            parts.append(f"{h:02d}点 {fmt_duration(total)}")
    return "、".join(parts) or "无"


# ── 主循环 ───────────────────────────────────────────────


def collect(
    cfg: dict,
    *,
    duration: float | None,
    interval: int,
    dry_run: bool,
    idle_threshold: float = IDLE_THRESHOLD_SECONDS,
) -> None:
    day = today_str()
    loaded_hours, loaded_switches, loaded_sessions = load_state(day)
    hours: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    switches: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for h, apps in loaded_hours.items():
        hours[h].update(apps)
    for h, apps in loaded_switches.items():
        switches[h].update(apps)
    sessions: list[dict] = list(loaded_sessions)

    current_app: str | None = None
    current_web: str | None = None
    # 精确前台会话：浏览器能推断出站点时以站点为准（更具体），否则用进程名。
    session_app: str | None = None
    session_start: float | None = None

    def close_session(end_ts: float) -> None:
        nonlocal session_app, session_start
        if (
            session_app is not None
            and session_start is not None
            and end_ts > session_start
        ):
            sessions.append(
                {
                    "app": session_app,
                    "label": label_for(session_app),
                    "start": int(session_start * 1000),
                    "end": int(end_ts * 1000),
                }
            )
            if len(sessions) > MAX_SESSIONS:
                del sessions[: len(sessions) - MAX_SESSIONS]
        session_app = None
        session_start = None

    last_tick = time.monotonic()
    last_upload = last_tick
    started = last_tick
    log(f"开始采集（设备 #{cfg['device_id']} {cfg['name']}，上传间隔 {interval}s）")
    log(
        f"提示：锁屏/息屏或键鼠静止 {idle_threshold:.0f} 秒才判离开；"
        f"分小时 + 精确会话上报；Ctrl+C 结束并上传"
    )
    if loaded_hours:
        log(f"已恢复当日累计：{fmt_hours(flatten_hours(hours))}")

    try:
        while True:
            now = time.monotonic()
            delta = max(0.0, min(now - last_tick, POLL_SECONDS * 2))
            last_tick = now

            app = None
            web = None
            if not is_locked() and idle_seconds() < idle_threshold:
                app, title = foreground_window()
                web = web_source(app, title) if app else None

            # 精确会话：站点优先于浏览器进程，切换/离开即收口一段
            effective = web or app
            wall = time.time()
            if effective != session_app:
                close_session(wall)
                if effective:
                    session_app = effective
                    session_start = wall

            if app is not None:
                hour = current_hour()
                if app != current_app:
                    switches[hour][app] += 1
                    current_app = app
                hours[hour][app] += delta
                if web is not None:
                    if web != current_web:
                        switches[hour][web] += 1
                        current_web = web
                    hours[hour][web] += delta

            new_day = today_str()
            if new_day != day:
                close_session(wall)
                result, err = try_upload(cfg, day, hours, switches, sessions, dry_run=dry_run)
                log(f"[{day}] 跨日上传：{err or result or '无数据'}")
                day = new_day
                hours = defaultdict(lambda: defaultdict(float))
                switches = defaultdict(lambda: defaultdict(int))
                sessions = []
                current_app = None
                current_web = None
                save_state(day, {}, {}, [])

            if now - last_upload >= interval:
                result, err = try_upload(cfg, day, hours, switches, sessions, dry_run=dry_run)
                if err:
                    log(f"[{day}] 上传失败（下次重试）：{err}")
                else:
                    status = "dry-run" if dry_run else ("已上传" if result else "无数据")
                    log(f"[{day}] {status}：{fmt_hours(flatten_hours(hours))}")
                save_state(day, dict(hours), dict(switches), sessions)
                last_upload = now

            if duration is not None and now - started >= duration:
                close_session(time.time())
                result, err = try_upload(cfg, day, hours, switches, sessions, dry_run=dry_run)
                save_state(day, dict(hours), dict(switches), sessions)
                log(f"[{day}] 结束上传：{err or result or '无数据'}")
                log(f"本次统计：{fmt_hours(flatten_hours(hours))}")
                log(f"分时段：{fmt_hour_breakdown(hours)}")
                return

            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        close_session(time.time())
        result, err = try_upload(cfg, day, hours, switches, sessions, dry_run=dry_run)
        save_state(day, dict(hours), dict(switches), sessions)
        log(f"[{day}] 退出上传：{err or result or '无数据'}")
        log(f"本次统计：{fmt_hours(flatten_hours(hours))}")


def main() -> int:
    parser = argparse.ArgumentParser(description="LifeLab PC 采集器")
    parser.add_argument("--setup", action="store_true", help="注册设备并保存配置")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="后端地址")
    parser.add_argument("--name", default="我的电脑", help="设备名（--setup 用）")
    parser.add_argument("--once", action="store_true", help="采指定时长后退出")
    parser.add_argument("--duration", type=float, default=30, help="--once 的采集秒数")
    parser.add_argument("--interval", type=int, default=UPLOAD_SECONDS, help="上传间隔秒")
    parser.add_argument("--dry-run", action="store_true", help="只打印不上传")
    parser.add_argument(
        "--idle-threshold", type=float, default=IDLE_THRESHOLD_SECONDS,
        help="键鼠静止多少秒（且未锁屏）判为离开（默认 900=15 分钟）",
    )
    parser.add_argument("--show", action="store_true", help="显示当前配置")
    parser.add_argument(
        "--install-autostart", action="store_true", help="安装开机/登录自启动（隐藏运行）"
    )
    parser.add_argument(
        "--uninstall-autostart", action="store_true", help="移除自启动"
    )
    parser.add_argument(
        "--autostart-status", action="store_true", help="查看自启动状态"
    )
    args = parser.parse_args()

    if sys.platform != "win32":
        print("仅支持 Windows（前台窗口采集依赖 Win32 API）")
        return 1

    if args.install_autostart:
        return install_autostart()
    if args.uninstall_autostart:
        return uninstall_autostart()
    if args.autostart_status:
        return autostart_status()

    if args.setup:
        cfg = setup(args.base_url, args.name)
        print(f"设备已注册：#{cfg['device_id']} {cfg['name']}")
        print(f"配置已保存：{CONFIG_PATH}")
        print("现在可以直接运行：python scripts/pc_collector.py")
        return 0

    cfg = load_config()
    if cfg is None:
        print("未找到配置，请先运行：python scripts/pc_collector.py --setup")
        return 1

    if args.show:
        safe = {**cfg, "token": cfg["token"][:8] + "..."}
        print(json.dumps(safe, ensure_ascii=False, indent=2))
        return 0

    if not acquire_single_instance():
        log("已有采集器实例在运行，退出（避免同设备重复上报互相覆盖）。")
        return 1

    try:
        collect(
            cfg,
            duration=args.duration if args.once else None,
            interval=args.interval,
            dry_run=args.dry_run,
            idle_threshold=args.idle_threshold,
        )
    except Exception:  # noqa: BLE001 - 顶层兜底：任何异常都写日志而非静默丢失
        log("采集器异常退出：\n" + traceback.format_exc())
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
