"""登录失败限流（进程内内存计数，无外部依赖）。

**按用户名，不按 IP**：公网入口是 Tailscale Funnel，它在本机做反向代理，
`request.client.host` 永远是回环地址，真实客户端 IP 拿不到——IP 维度在这个
拓扑下完全失效。库里只有 demo / personal 两个账号，按用户名正好。

**口径取 casefold**：否则 `demo` / `Demo` / `DEMO` / ` demo ` 各自累积计数，
等于没有限流。

**这只是纵深防御**：主防线是 scrypt（n=16384，单次校验几十毫秒）加足够强的
口令，在线爆破本来就不划算。限流的作用是让「猜」在时间尺度上不成立，顺便
挡住那些直接冲着 LLM 额度来的脚本。

单进程部署（serve.cmd 不传 --workers）→ 这份内存计数就是权威的。将来真上了
多 worker 或 VPS，这里要换成共享存储。
"""

import math
import time
from collections import deque

# 15 分钟内失败 5 次即锁上；登录成功立刻清零。
WINDOW_SECONDS = 15 * 60
MAX_FAILURES = 5

# 上限保护：拿随机用户名喷洒不能把字典撑爆（真账号只有 2 个，正常远小于此）。
MAX_KEYS = 1024

_failures: dict[str, deque[float]] = {}


def _key(username: str) -> str:
    return username.strip().casefold()


def _drop_expired(q: deque[float], now: float) -> None:
    """丢掉窗口外的记录（原地点）。"""
    while q and now - q[0] >= WINDOW_SECONDS:
        q.popleft()


def _evict(now: float) -> None:
    """腾位置：先清空了的键，仍超上限就按插入顺序丢最旧的。"""
    for key in list(_failures):
        _drop_expired(_failures[key], now)
        if not _failures[key]:
            del _failures[key]
    while len(_failures) >= MAX_KEYS:
        del _failures[next(iter(_failures))]


def retry_after(username: str, *, now: float | None = None) -> int:
    """还要等多少秒才允许再试；0 表示现在就可以试。

    `now` 只在测试里显式传（monotonic 秒），生产走默认值。
    """
    now = time.monotonic() if now is None else now
    q = _failures.get(_key(username))
    if q is None:
        return 0
    _drop_expired(q, now)
    if len(q) < MAX_FAILURES:
        return 0
    # 最早那次滑出窗口时就解锁
    return math.ceil(WINDOW_SECONDS - (now - q[0]))


def record_failure(username: str, *, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    key = _key(username)
    q = _failures.get(key)
    if q is None:
        if len(_failures) >= MAX_KEYS:
            _evict(now)
        q = _failures[key] = deque()
    _drop_expired(q, now)
    q.append(now)


def clear(username: str) -> None:
    """登录成功后清零该用户名的计数。"""
    _failures.pop(_key(username), None)


def reset() -> None:
    """清空全部计数。测试隔离用，也可在维护时手工调用。"""
    _failures.clear()
