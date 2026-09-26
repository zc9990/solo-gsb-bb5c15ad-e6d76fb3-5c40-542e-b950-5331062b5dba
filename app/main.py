"""夜间施工噪声等效声级(Leq)核算 API。

规则:
- 夜间窗口为当地 22:00 至次日 06:00(按请求中的 IANA 时区换算);
- 相邻读数构成时段并裁剪到窗口内,每段以前一读数代表声级;
- 按持续秒数与 10^(dBA/10) 的声能作能量平均,得到 Leq;
- 相邻读数间隔超过 15 分钟,或窗口首尾未被读数覆盖,返回"数据不足";
- 乱序、重复时间、非数值声级一律拒绝(HTTP 422)。
"""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, HTTPException
from pydantic import AwareDatetime, BaseModel, Field, FiniteFloat

# 夜间窗口:当地 22:00 ~ 次日 06:00
NIGHT_START = time(22, 0)
NIGHT_END = time(6, 0)
# 相邻读数允许的最大间隔(秒)
MAX_GAP_SECONDS = 15 * 60


class Reading(BaseModel):
    """单条分贝读数,时间必须带时区偏移。"""

    time: AwareDatetime = Field(
        ..., description="带时区偏移的 ISO 8601 时间,如 2026-09-26T22:00:00+08:00"
    )
    db: FiniteFloat = Field(..., description="A 计权声级读数 dBA,必须为有限数值")


class AssessRequest(BaseModel):
    timezone: str = Field(
        ..., description="IANA 时区名,如 Asia/Shanghai", examples=["Asia/Shanghai"]
    )
    night_start_date: date = Field(
        ..., description="夜间起始日期(当地),窗口为当日 22:00 至次日 06:00"
    )
    limit_db: FiniteFloat = Field(..., description="夜间噪声限值 dBA")
    readings: list[Reading] = Field(
        ..., min_length=1, description="按时间严格升序排列的读数"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "timezone": "Asia/Shanghai",
                    "night_start_date": "2026-09-26",
                    "limit_db": 55,
                    "readings": [
                        {"time": "2026-09-26T21:55:00+08:00", "db": 48.2},
                        {"time": "2026-09-26T22:05:00+08:00", "db": 49.0},
                        {"time": "2026-09-26T22:15:00+08:00", "db": 51.3},
                    ],
                }
            ]
        }
    }


app = FastAPI(title="夜间施工噪声核算 API", version="1.0.0")


def _insufficient(reason: str) -> dict[str, Any]:
    return {"status": "insufficient_data", "reason": reason}


@app.post("/assess")
def assess(req: AssessRequest) -> dict[str, Any]:
    # 1. 时区与夜间窗口(当地 22:00 ~ 次日 06:00)
    try:
        tz = ZoneInfo(req.timezone)
    except ZoneInfoNotFoundError:
        raise HTTPException(
            status_code=422, detail=f"未知的 IANA 时区: {req.timezone}"
        )

    window_start = datetime.combine(req.night_start_date, NIGHT_START, tzinfo=tz)
    window_end = datetime.combine(
        req.night_start_date + timedelta(days=1), NIGHT_END, tzinfo=tz
    )

    # 2. 读数顺序校验:乱序、重复一律拒绝
    times = [r.time for r in req.readings]
    for prev, cur in zip(times, times[1:]):
        if cur == prev:
            raise HTTPException(
                status_code=422, detail=f"存在重复时间的读数: {cur.isoformat()}"
            )
        if cur < prev:
            raise HTTPException(
                status_code=422,
                detail=f"读数时间乱序: {prev.isoformat()} 之后出现 {cur.isoformat()}",
            )

    # 3. 窗口覆盖校验:首条读数须不晚于 22:00,末条读数须不早于次日 06:00
    if times[0] > window_start or times[-1] < window_end:
        return _insufficient(
            "数据不足: 读数未覆盖夜间窗口首尾(当地 22:00 ~ 次日 06:00)"
        )

    # 4. 间隔校验与声能累计:每段以前一读数代表声级,裁剪到窗口内
    energy = 0.0
    segments = 0
    for i in range(len(req.readings) - 1):
        t0, t1 = times[i], times[i + 1]
        seg_start = max(t0, window_start)
        seg_end = min(t1, window_end)
        if seg_end <= seg_start:
            continue
        if (t1 - t0).total_seconds() > MAX_GAP_SECONDS:
            return _insufficient(
                "数据不足: 相邻读数间隔超过 15 分钟"
                f"({t0.isoformat()} ~ {t1.isoformat()})"
            )
        seconds = (seg_end - seg_start).total_seconds()
        energy += seconds * 10.0 ** (req.readings[i].db / 10.0)
        segments += 1

    # 5. 等效声级 Leq = 10 * lg(总声能 / 窗口总时长)
    total_seconds = (window_end - window_start).total_seconds()
    leq = 10.0 * math.log10(energy / total_seconds)
    exceeded = leq > req.limit_db

    return {
        "status": "ok",
        "leq_db": round(leq, 2),
        "limit_db": req.limit_db,
        "exceeded": exceeded,
        "conclusion": "超限" if exceeded else "未超限",
        "margin_db": round(leq - req.limit_db, 2),
        "window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "seconds": total_seconds,
        },
        "segments": segments,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "up"}
