"""LLM 调用用量汇总：读 `app/llm.py` 写出的 JSONL 埋点，出延迟 / token / 来源分布。

用法（backend 目录下）：
    .venv\\Scripts\\python.exe scripts\\llm_usage.py
    .venv\\Scripts\\python.exe scripts\\llm_usage.py --path D:\\tmp\\llm_calls.jsonl

数据来自 `~/.lifelab/llm_calls.jsonl`（每次 LLM 调用一行，见 app/llm.py）。
退出码：0 有数据，1 文件不存在或一行都没读到（方便放进脚本判断）。
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm import _trace_path


def _load(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # 半行（进程被杀）直接跳过
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _pct(values: list[float], p: float) -> float:
    """最近秩百分位；样本少时也稳定，不引额外依赖。"""
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, round((p / 100) * (len(s) - 1)))
    return s[idx]


def _fmt_ts(raw: str | None) -> str:
    if not raw:
        return "?"
    try:
        return datetime.fromisoformat(raw).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return "?"


def _section(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM 调用用量汇总")
    parser.add_argument("--path", default="", help="埋点文件路径（默认 ~/.lifelab/llm_calls.jsonl）")
    parser.add_argument("--top", type=int, default=20, help="按来源列出前 N 项")
    args = parser.parse_args()

    path = Path(args.path) if args.path else _trace_path()
    if not path.exists():
        print(f"没有埋点文件：{path}")
        print("LLM 调用过一次之后再看（或把它写进 .env 的 LLM_TRACE_PATH）。")
        return 1

    rows = _load(path)
    if not rows:
        print(f"埋点文件是空的：{path}")
        return 1

    ok_rows = [r for r in rows if r.get("ok")]
    bad_rows = [r for r in rows if not r.get("ok")]
    latencies = [r["latency_ms"] for r in rows if isinstance(r.get("latency_ms"), int | float)]
    tokens = [r["total_tokens"] for r in rows if isinstance(r.get("total_tokens"), int)]

    print(f"埋点文件：{path}")
    print(f"时间范围：{_fmt_ts(rows[0].get('ts'))} → {_fmt_ts(rows[-1].get('ts'))}")

    _section("总览")
    print(f"调用次数：{len(rows)}")
    print(f"成功 / 失败：{len(ok_rows)} / {len(bad_rows)}"
          f"（成功率 {len(ok_rows) / len(rows) * 100:.1f}%）")
    if latencies:
        print(f"延迟 ms：p50 {_pct(latencies, 50):.0f} · p95 {_pct(latencies, 95):.0f}"
              f" · max {max(latencies):.0f}")
    if tokens:
        print(f"Token 总量：{sum(tokens)}（有计数的调用 {len(tokens)} 次，"
              f"均 {sum(tokens) / len(tokens):.0f}/次）")
    else:
        print("Token 总量：无（供应商未返回 usage）")

    _section(f"按来源（前 {args.top}）")
    by_caller: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_caller[r.get("caller") or "unknown"].append(r)

    print(f"{'来源':<22}{'次数':>6}{'失败':>6}{'p50ms':>8}{'p95ms':>8}{'tokens':>10}")
    for caller, group in sorted(by_caller.items(), key=lambda kv: -len(kv[1]))[: args.top]:
        lat = [r["latency_ms"] for r in group if isinstance(r.get("latency_ms"), int | float)]
        tok = sum(r["total_tokens"] for r in group if isinstance(r.get("total_tokens"), int))
        fails = sum(1 for r in group if not r.get("ok"))
        print(f"{caller:<22}{len(group):>6}{fails:>6}{_pct(lat, 50):>8.0f}{_pct(lat, 95):>8.0f}{tok:>10}")

    if bad_rows:
        _section("失败明细")
        for r in bad_rows[: args.top]:
            print(f"{_fmt_ts(r.get('ts'))}  {r.get('caller') or 'unknown'}"
                  f"  {r.get('error') or '(无错误信息)'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
