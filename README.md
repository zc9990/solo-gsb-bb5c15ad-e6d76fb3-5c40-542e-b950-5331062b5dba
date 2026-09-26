# 夜间施工噪声核算 API

基于 FastAPI 的夜间施工噪声等效声级(Leq)核算服务。

- **夜间窗口**:按请求中的 IANA 时区,取当地 **22:00 ~ 次日 06:00**;
- **核算方法**:相邻读数构成时段并裁剪到窗口内,每段以**前一读数**代表声级,
  按持续秒数与 `10^(dBA/10)` 的声能做能量平均,得到 Leq;
- **数据不足**:相邻读数间隔超过 **15 分钟**,或窗口首尾未被读数覆盖,
  返回 `insufficient_data`;
- **输入拒绝**:乱序、重复时间、非数值声级、无时区偏移的时间、未知时区,
  一律返回 HTTP 422。

## 一键启动

```bash
docker compose up --build
```

启动后:

| 内容 | 地址 |
| --- | --- |
| 核算接口 | `POST http://localhost:8000/assess` |
| 交互文档 | `http://localhost:8000/docs` |
| 健康检查 | `GET http://localhost:8000/health` |

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `API_PORT` | `8000` | 宿主机映射端口,如 `API_PORT=9000 docker compose up --build` |

服务本身无需其他配置;时区、日期、限值均通过请求体传入。

## 接口说明

### `POST /assess`

请求体:

```json
{
  "timezone": "Asia/Shanghai",
  "night_start_date": "2026-09-26",
  "limit_db": 55,
  "readings": [
    {"time": "2026-09-26T21:55:00+08:00", "db": 48.2},
    {"time": "2026-09-26T22:05:00+08:00", "db": 49.0},
    {"time": "2026-09-26T22:15:00+08:00", "db": 51.3}
  ]
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `timezone` | string | IANA 时区名,如 `Asia/Shanghai` |
| `night_start_date` | date | 夜间起始日期,窗口为当日 22:00 ~ 次日 06:00(当地) |
| `limit_db` | number | 夜间噪声限值 dBA |
| `readings[].time` | datetime | 带时区偏移的 ISO 8601 时间,须严格升序 |
| `readings[].db` | number | A 计权声级读数 dBA,须为有限数值 |

覆盖充分的响应(HTTP 200):

```json
{
  "status": "ok",
  "leq_db": 49.87,
  "limit_db": 55.0,
  "exceeded": false,
  "conclusion": "未超限",
  "margin_db": -5.13,
  "window": {
    "start": "2026-09-26T22:00:00+08:00",
    "end": "2026-09-27T06:00:00+08:00",
    "seconds": 28800.0
  },
  "segments": 50
}
```

数据不足的响应(HTTP 200):

```json
{
  "status": "insufficient_data",
  "reason": "数据不足: 相邻读数间隔超过 15 分钟(2026-09-27T01:10:00+08:00 ~ 2026-09-27T01:40:00+08:00)"
}
```

输入非法(乱序、重复时间、非数值声级、无时区时间、未知时区等)返回 HTTP 422,
`detail` 字段说明原因。

### 示例

```bash
curl -X POST http://localhost:8000/assess \
  -H "Content-Type: application/json" \
  -d '{
    "timezone": "Asia/Shanghai",
    "night_start_date": "2026-09-26",
    "limit_db": 55,
    "readings": [
      {"time": "2026-09-26T21:55:00+08:00", "db": 48.2},
      {"time": "2026-09-26T22:05:00+08:00", "db": 49.0}
    ]
  }'
```

## 本地开发(不用 Docker)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
```

运行测试:

```bash
pytest
```
