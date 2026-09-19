"""导出真实设备数据留档（迁移 B 删除前的必做一步）。

用法（backend 目录下）：
    .venv\\Scripts\\python.exe scripts\\export_real_device_data.py
    .venv\\Scripts\\python.exe scripts\\export_real_device_data.py --out D:\\bak\\dev.json

导出 devices、device_app_sessions、device_usage_hourly
以及 source='DEVICE' 的 events，落成一个 JSON。

**默认写到 ~/.lifelab/archive/ 而不是仓库里**：这些是你真实的应用使用痕迹
（应用集合、时段分布），留在仓库有误提交的风险。要落到别处用 --out。

本脚本只读，不改任何数据。
"""

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import engine
from app.models import Event, EventSource
from app.models.device import Device
from app.models.device_usage import (
    DeviceAppSession,
    DeviceUsageHourly,
)

DEFAULT_DIR = Path.home() / ".lifelab" / "archive"


def _dump(db: Session, stmt) -> list[dict]:
    return [
        {c.name: _jsonable(getattr(row, c.name)) for c in row.__table__.columns}
        for row in db.scalars(stmt).all()
    ]


def _jsonable(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="导出真实设备数据留档（只读）")
    parser.add_argument("--out", help="输出文件路径；缺省为 ~/.lifelab/archive/real_device_data_<时间戳>.json")
    args = parser.parse_args()

    out = Path(args.out) if args.out else (
        DEFAULT_DIR / f"real_device_data_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)

    with Session(engine) as db:
        payload = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "note": "迁移 own_existing_data_to_demo 删除前的留档；重建 demo 设备数据时参考过它",
            "devices": _dump(db, select(Device)),
            "device_app_sessions": _dump(db, select(DeviceAppSession)),
            "device_usage_hourly": _dump(db, select(DeviceUsageHourly)),
            "device_events": _dump(
                db, select(Event).where(Event.source == EventSource.DEVICE)
            ),
        }

    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"已导出 → {out}")
    for key in ("devices", "device_app_sessions", "device_usage_hourly",
                "device_events"):
        print(f"  {key:22} {len(payload[key])} 条")


if __name__ == "__main__":
    main()
