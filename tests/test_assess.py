"""POST /assess 的行为测试。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

TZ8 = timezone(timedelta(hours=8))
BASE = datetime(2026, 9, 26, 21, 50, tzinfo=TZ8)  # 窗口: 22:00 ~ 次日 06:00


def dense_readings(level_fn, start=BASE, end=None, step_minutes=10):
    """生成从 start 到 end(含)每 step_minutes 一条的读数。"""
    if end is None:
        end = datetime(2026, 9, 27, 6, 10, tzinfo=TZ8)
    out, t = [], start
    while t <= end:
        out.append({"time": t.isoformat(), "db": level_fn(t)})
        t += timedelta(minutes=step_minutes)
    return out


def payload(readings, limit=55.0):
    return {
        "timezone": "Asia/Shanghai",
        "night_start_date": "2026-09-26",
        "limit_db": limit,
        "readings": readings,
    }


# ---------- 正常核算 ----------

def test_ok_constant_level():
    r = client.post("/assess", json=payload(dense_readings(lambda t: 50.0)))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["leq_db"] == pytest.approx(50.0, abs=1e-6)
    assert body["exceeded"] is False
    assert body["conclusion"] == "未超限"
    assert body["window"]["seconds"] == 8 * 3600


def test_ok_two_levels_energy_average():
    # 22:00~02:00 为 50 dB,02:00~06:00 为 40 dB(段以前一读数为准)
    def level(t):
        return 50.0 if t < datetime(2026, 9, 27, 2, 0, tzinfo=TZ8) else 40.0

    r = client.post("/assess", json=payload(dense_readings(level)))
    assert r.status_code == 200
    # Leq = 10*lg((14400*10^5 + 14400*10^4)/28800) = 10*lg(55000)
    assert r.json()["leq_db"] == pytest.approx(47.40, abs=0.01)


def test_ok_exceeded():
    r = client.post("/assess", json=payload(dense_readings(lambda t: 60.0), limit=55.0))
    body = r.json()
    assert body["exceeded"] is True
    assert body["conclusion"] == "超限"
    assert body["margin_db"] == pytest.approx(5.0, abs=1e-6)


def test_ok_clipping_excludes_out_of_window_levels():
    # 窗口外读数声级 90 dB,窗口内恒为 50 dB,裁剪后 Leq 应仍为 50
    def level(t):
        ws = datetime(2026, 9, 26, 22, 0, tzinfo=TZ8)
        we = datetime(2026, 9, 27, 6, 0, tzinfo=TZ8)
        return 50.0 if ws <= t < we else 90.0

    readings = dense_readings(level)
    r = client.post("/assess", json=payload(readings))
    assert r.json()["leq_db"] == pytest.approx(50.0, abs=1e-6)


def test_ok_gap_exactly_15_minutes():
    r = client.post(
        "/assess",
        json=payload(dense_readings(lambda t: 50.0, step_minutes=15)),
    )
    assert r.json()["status"] == "ok"


# ---------- 数据不足 ----------

def test_insufficient_gap_over_15_minutes():
    readings = dense_readings(lambda t: 50.0)
    # 在窗口中段挖掉两条,制造 30 分钟空洞
    del readings[5:7]
    body = client.post("/assess", json=payload(readings)).json()
    assert body["status"] == "insufficient_data"
    assert "15 分钟" in body["reason"]


def test_insufficient_window_start_not_covered():
    readings = dense_readings(
        lambda t: 50.0, start=datetime(2026, 9, 26, 22, 5, tzinfo=TZ8)
    )
    body = client.post("/assess", json=payload(readings)).json()
    assert body["status"] == "insufficient_data"
    assert "首尾" in body["reason"]


def test_insufficient_window_end_not_covered():
    readings = dense_readings(
        lambda t: 50.0, end=datetime(2026, 9, 27, 5, 55, tzinfo=TZ8)
    )
    body = client.post("/assess", json=payload(readings)).json()
    assert body["status"] == "insufficient_data"


def test_insufficient_single_reading():
    body = client.post(
        "/assess",
        json=payload([{"time": "2026-09-26T23:00:00+08:00", "db": 50.0}]),
    ).json()
    assert body["status"] == "insufficient_data"


# ---------- 输入拒绝(422) ----------

def test_reject_out_of_order():
    readings = dense_readings(lambda t: 50.0)
    readings[3], readings[4] = readings[4], readings[3]
    r = client.post("/assess", json=payload(readings))
    assert r.status_code == 422
    assert "乱序" in str(r.json()["detail"])


def test_reject_duplicate_time():
    readings = dense_readings(lambda t: 50.0)
    readings.insert(4, dict(readings[4]))
    r = client.post("/assess", json=payload(readings))
    assert r.status_code == 422
    assert "重复" in str(r.json()["detail"])


def test_reject_non_numeric_db():
    readings = dense_readings(lambda t: 50.0)
    readings[2]["db"] = "loud"
    r = client.post("/assess", json=payload(readings))
    assert r.status_code == 422


def test_reject_nan_db():
    readings = dense_readings(lambda t: 50.0)
    readings[2]["db"] = "NaN"
    r = client.post("/assess", json=payload(readings))
    assert r.status_code == 422


def test_reject_naive_datetime():
    readings = dense_readings(lambda t: 50.0)
    readings[0]["time"] = "2026-09-26T21:50:00"  # 无时区偏移
    r = client.post("/assess", json=payload(readings))
    assert r.status_code == 422


def test_reject_unknown_timezone():
    r = client.post(
        "/assess", json=payload(dense_readings(lambda t: 50.0)) | {"timezone": "Mars/Olympus"}
    )
    assert r.status_code == 422
    assert "时区" in str(r.json()["detail"])


def test_reject_empty_readings():
    r = client.post("/assess", json=payload([]))
    assert r.status_code == 422
