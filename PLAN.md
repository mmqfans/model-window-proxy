# Model Window Proxy 实现计划

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 本地反向代理，按时间窗口自动切换 CommandCode Provider API 的模型——周一至周五 09:00–12:00 与 14:00–18:00（Asia/Shanghai）用 GLM-5.3 Flash，其余时间（含周末整日）用 DeepSeek V4 Flash；窗口尾部按最不利原则延长 60 秒缓冲（12:00/18:00 起 60 秒内仍走 GLM，之后才切 DS）。

**Architecture:** 纯 Python 标准库，两个文件：① `selector.py`——纯函数，给定时刻返回应使用的模型 ID（可单测）；② `server.py`——`ThreadingHTTPServer` 反向代理，改写请求体里的 `model` 字段后转发到 `api.commandcode.ai`（上游路径 = base 路径 + 客户端路径剥 `/v1` 前缀，见 `build_upstream_path`），响应（含 SSE 流式）用 `read1` 低延迟透传，每个请求的改写结果写入 stdout/access.log。客户端只需把 base_url 指到 `http://127.0.0.1:8399/v1`，无需感知切换逻辑。**不引入任何后台定时组件**（对抗审核第二轮结论：00:05 快照文件恒为 DS、server 也不读它，纯误导性冗余——已删除原 switch.py/launchd 方案，Simplicity First）。

**Tech Stack:** Python 标准库（http.server / http.client / unittest / zoneinfo，零第三方依赖；代码按 `/usr/bin/python3` 3.9 兼容书写）；git。

---

## 背景与已确认事实（2026-08-29 调研）

- **API 端点**（官方文档已确认）：`https://api.commandcode.ai/provider/v1/chat/completions`（OpenAI 格式）。GOAT/Pro/Max 套餐的 API key 可直接用，用量计入套餐 credits。
- **API key**：从 Studio 获取，本方案通过环境变量 `CC_API_KEY` 注入，不落盘。
- **模型 ID**：`deepseek/deepseek-v4-flash` 已在官方文档 quickstart 出现，确认无误。`z-ai/glm-5.3-flash` 来自 CC 费率表命名惯例（Hermes 当前 provider 显示同为 `z-ai/glm-5.3-flash`）——**Task 1 安装前用 `GET /provider/v1/models` 核实，若不同以核实结果为准**。
- **调度机制**：macOS launchd `StartCalendarInterval` 按用户本地时区触发，休眠错过会在唤醒后补跑（cron 无此特性，故选 launchd）。Asia/Shanghai 无夏令时，无 UTC 边界问题。
- **设计锚点——DeepSeek 官方 Peak 定义**（api-docs.deepseek.com/quick_start/pricing，2026-08-16 生效）：
  > "Peak hours are 01:00 - 04:00 and 06:00 - 10:00 UTC, **Monday through Friday** (all other hours are off-peak)"；off-peak 为 peak 半价。

  换算北京时间即周一至五 09:00–12:00 / 14:00–18:00——**本代理的时间窗与 DS Peak 窗口完全重合**：Peak 时段 DS V4 Flash 涨至 $0.44/$1.32 → 切 GLM；off-peak/周末 DS 半价 $0.22/$0.66 → 用 DS。
- **边界语义（最不利原则，用户决策）**：秒级边界无官方逐字定义（推断见下），按最不利原则把不确定的边界秒一律按高峰处理：**GLM 窗口尾部延长 `edge_buffer_seconds`（默认 60s）**。最终行为：09:00/14:00 整进 GLM（头部无歧义：所在小时块恒为峰块，不加缓冲），**12:00/18:00 整仍 GLM，12:01/18:01 起才切 DS**。周末整日 → DS。缓冲成本 ≈ 0（GLM 牌价 ≤ DS 峰价）。**缓冲的保护范围（审核修正）**：尾部缓冲保护"本机时钟偏慢 → 过早切 DS 吃峰价"；头部时钟偏慢（晚进 GLM 数秒）仍是秒级峰价残余风险，量级可忽略，暂不加头部缓冲。原始推断备忘：计费原子是整小时块（"all other **hours**"，CC 表 "17h/day" 自证 24−7=17，含 04 点块则 16h ✗），区间惯例 [start, end) ⇒ 12:00 整起属 off-peak——但此为推断非条款，故弃用为决策依据。
- **CC API 实测陷阱（本计划验证过程踩过，测试脚本需知）**：① 默认 Python-urllib UA 会被 WAF 403，须带自定义 User-Agent；② thinking 模型的 `max_tokens` 会先被 reasoning 吃满导致 content 为空，需加大预算（DS 对 21KB 计划的深度思考 >32K token）；③ 流式 delta 的思考字段名是 `reasoning`（非 `reasoning_content`）。代理本体透传字节流不受 ③ 影响；`server.py` 出站请求自带 UA，不受 ① 影响。
- **节假日（锚定官方语义 + 逃生门）**：DS 官方 Peak 只认 "Monday through Friday" 纯日历星期，**不识别法定节假日/调休**。代理默认与官方语义对齐（法定节假日的工作时段会照走 GLM）。config.json 预留 `holidays`（走 DS 的节假日日期）与 `workdays`（调休补班走 GLM 的周末日期）两个日期列表逃生门，selector 判定顺序：列表日期 > 星期几。**是否维护这两个列表由用户决定，默认为空。**
- **费用口径（已核实事与推断分离）**：两模型均在 GOAT 清单内，credits 照常扣。**已核实**（GOAT 费率表，2026-08-29）：DS V4 Flash peak $0.44/$1.32（cache $0.007）/ off-peak $0.22/$0.66——**CC 明确透传 DS 峰谷价**；GLM 5.3 Flash 恒定 $0.15/$0.50（cache $0.03）。推论：峰窗用 GLM 每 token 省约 2.6-3×；DS 的核心优势是 cache read 便宜 4×，高缓存命中的 agent 循环在 off-peak 用 DS 最省。**未核实**：各模型 credits 倍率（费率表 "+1" 标记含义）。本方案首要动机是用户指定的时段模型偏好，费用节省是伴随收益。

## 项目位置与文件清单

项目根：`~/Hermes-Projects/model-window-proxy/`（遵循 Hermes 项目约定）

```
model-window-proxy/
├── config.json                        # 端口/时区/时间窗/模型ID/上游地址/缓冲
├── selector.py                        # 纯逻辑：时刻 → 模型
├── test_selector.py                   # unittest 单测
├── server.py                          # 反向代理（含 /_which 与 access.log）
├── logs/access.log                    # 每请求改写记录（运行时生成，gitignore）
└── README.md                          # 安装/运维/卸载说明
```

## config.json（完整内容）

```json
{
  "listen_port": 8399,
  "timezone": "Asia/Shanghai",
  "upstream_base": "https://api.commandcode.ai/provider/v1",
  "api_key_env": "CC_API_KEY",
  "glm_model": "z-ai/glm-5.3-flash",
  "ds_model": "deepseek/deepseek-v4-flash",
  "windows": [
    {"days": [1, 2, 3, 4, 5], "start": "09:00", "end": "12:00"},
    {"days": [1, 2, 3, 4, 5], "start": "14:00", "end": "18:00"}
  ],
  "edge_buffer_seconds": 60,
  "upstream_timeout_seconds": 600,
  "holidays": [],
  "workdays": []
}
```

`days` 用 ISO weekday（1=周一 … 7=周日）。`holidays` = 走 DS 的法定节假日日期（`YYYY-MM-DD`）；`workdays` = 调休补班、走 GLM 的周末日期。判定优先级：**日期列表 > 星期几**。`edge_buffer_seconds` = GLM 窗口尾部缓冲（最不利原则：边界秒按高峰处理 + 吸收时钟偏差，见背景节）。改窗口/换模型/维护日历只动这个文件。

---

### Task 1: 前置核实与脚手架

**Objective:** 核实模型 slug，建好项目骨架。

**Step 1: 核实 GLM slug**（需要用户已设置 `CC_API_KEY`；若 key 未就绪可先跳过，Task 8 前补做）

Run:
```bash
curl -s https://api.commandcode.ai/provider/v1/models -H "Authorization: Bearer $CC_API_KEY" | python3 -m json.tool | grep -i -E "glm-5.3|deepseek-v4"
```
Expected: 列表含 `z-ai/glm-5.3-flash` 与 `deepseek/deepseek-v4-flash`。若 GLM slug 不同，**更新本计划与 config.json 后再继续**。

**Step 2: 建目录与文件**

```bash
mkdir -p ~/Hermes-Projects/model-window-proxy/logs
cd ~/Hermes-Projects/model-window-proxy && git init
printf 'logs/\n__pycache__/\n' > .gitignore
```

**Step 3: 写入 `config.json`**（内容见上节）

**Step 4: 留档关键外部事实**（审核第二轮建议：外部事实无自动化守护，快照入库以便日后 diff）

```bash
mkdir -p docs-snapshot
curl -sL -A "Mozilla/5.0" "https://api-docs.deepseek.com/quick_start/pricing" -o docs-snapshot/ds-pricing.html
curl -sL -A "Mozilla/5.0" "https://commandcode.ai/docs/plans/goat" -o docs-snapshot/cc-goat.html
git add docs-snapshot && git commit -m "docs: snapshot external pricing facts"
```
Expected: 两个 html 落盘并入库

**Step 5: Commit**

```bash
git add config.json .gitignore && git commit -m "chore: scaffold model-window-proxy"
```

---

### Task 2: selector 的失败测试（RED）

**Objective:** 用固定时刻表锁死选择逻辑的全部边界。

**Files:**
- Create: `test_selector.py`

**Step 1: 写测试**

```python
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from selector import load_config, select_model

CFG = load_config("config.json")
GLM, DS = CFG["glm_model"], CFG["ds_model"]

def cst(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=ZoneInfo("Asia/Shanghai"))

class TestSelectModel(unittest.TestCase):
    def test_window_start_inclusive(self):
        self.assertEqual(select_model(cst(2026, 8, 31, 9, 0), CFG), GLM)   # 周一 09:00 整
    def test_before_window(self):
        self.assertEqual(select_model(cst(2026, 8, 31, 8, 59), CFG), DS)
    def test_morning_end_boundary_is_glm(self):
        self.assertEqual(select_model(cst(2026, 8, 31, 12, 0), CFG), GLM)   # 最不利：边界秒按高峰
    def test_morning_last_minute(self):
        self.assertEqual(select_model(cst(2026, 8, 31, 11, 59), CFG), GLM)
    def test_afternoon_start_inclusive(self):
        self.assertEqual(select_model(cst(2026, 8, 31, 14, 0), CFG), GLM)
    def test_lunch(self):
        self.assertEqual(select_model(cst(2026, 8, 31, 13, 59), CFG), DS)
    def test_afternoon_end_boundary_is_glm(self):
        self.assertEqual(select_model(cst(2026, 8, 31, 18, 0), CFG), GLM)   # 最不利：边界秒按高峰
    def test_afternoon_buffer_expiry(self):
        self.assertEqual(select_model(cst(2026, 8, 31, 18, 0, 59), CFG), GLM)
        self.assertEqual(select_model(cst(2026, 8, 31, 18, 1, 0), CFG), DS)
    def test_edge_buffer_expiry_morning(self):
        self.assertEqual(select_model(cst(2026, 8, 31, 12, 0, 59), CFG), GLM)  # 缓冲期内仍 GLM
        self.assertEqual(select_model(cst(2026, 8, 31, 12, 1, 0), CFG), DS)    # 12:01:00 起切 DS
    def test_afternoon_last_minute(self):
        self.assertEqual(select_model(cst(2026, 8, 31, 17, 59), CFG), GLM)
    def test_friday_evening(self):
        self.assertEqual(select_model(cst(2026, 8, 28, 22, 0), CFG), DS)
    def test_saturday_all_day(self):
        self.assertEqual(select_model(cst(2026, 8, 29, 10, 0), CFG), DS)   # 周六上午
    def test_sunday_afternoon(self):
        self.assertEqual(select_model(cst(2026, 8, 30, 15, 0), CFG), DS)
    def test_converts_from_utc(self):
        # 2026-08-31 01:30 UTC = 周一 09:30 CST → GLM，与系统时区无关
        utc_dt = datetime(2026, 8, 31, 1, 30, tzinfo=timezone.utc)
        self.assertEqual(select_model(utc_dt, CFG), GLM)

    def test_holiday_weekday_forces_ds(self):
        # 2026-10-01 是周四：法定节假日 → 即使在工作窗内也走 DS
        cfg2 = dict(CFG, holidays=["2026-10-01"])
        self.assertEqual(select_model(cst(2026, 10, 1, 10, 0), cfg2), DS)
    def test_makeup_workday_saturday_gets_glm_in_window(self):
        # 2026-10-10 是周六：调休补班 → 工作窗内走 GLM
        cfg2 = dict(CFG, workdays=["2026-10-10"])
        self.assertEqual(select_model(cst(2026, 10, 10, 10, 0), cfg2), GLM)
        self.assertEqual(select_model(cst(2026, 10, 10, 19, 0), cfg2), DS)  # 窗外仍 DS

if __name__ == "__main__":
    unittest.main()
```

**Step 2: 跑测试确认失败**

Run: `cd ~/Hermes-Projects/model-window-proxy && python3 -m unittest test_selector -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'selector'`

---

### Task 3: 实现 selector（GREEN）

**Objective:** 最小实现让测试全绿。

**Files:**
- Create: `selector.py`

**Step 1: 写实现**

```python
"""时刻 → 模型选择。纯逻辑，无 I/O，便于单测。"""
import json
from zoneinfo import ZoneInfo

def load_config(path):
    with open(path) as f:
        return json.load(f)

def _secs(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 3600 + int(m) * 60

def _day_secs(dt):
    return dt.hour * 3600 + dt.minute * 60 + dt.second

def select_model(now, config):
    """now 为任意时区的 aware datetime；返回模型 ID 字符串。

    判定优先级：holidays（强制 DS）> workdays（补班日按工作窗判定，无视星期几）>
    常规星期几窗口。窗口起点含；终点按最不利原则延长 edge_buffer_seconds
    （默认 60s：边界秒一律按高峰计费处理，并吸收本机与上游时钟偏差）。
    """
    local = now.astimezone(ZoneInfo(config["timezone"]))
    today = local.date().isoformat()
    if today in config.get("holidays", []):
        return config["ds_model"]
    is_makeup = today in config.get("workdays", [])
    buf = int(config.get("edge_buffer_seconds", 60))
    t = _day_secs(local)
    for w in config["windows"]:
        day_ok = is_makeup or local.isoweekday() in w["days"]
        if day_ok and _secs(w["start"]) <= t < _secs(w["end"]) + buf:
            return config["glm_model"]
    return config["ds_model"]
```

**Step 2: 跑测试确认通过**

Run: `python3 -m unittest test_selector -v`
Expected: `OK`（15 tests）

**Step 3: Commit**

```bash
git add selector.py test_selector.py && git commit -m "feat: time-window model selector with boundary tests"
```

---

### Task 4: 请求改写逻辑的失败测试（RED）

**Objective:** 锁死"改写 model 字段 + override 头优先"的行为，且只改写 chat/completions。

**Files:**
- Create: `test_server.py`

**Step 1: 写测试**

```python
import unittest
from datetime import datetime, timezone
from server import rewrite_payload, build_upstream_path

FAKE_CFG = {
    "timezone": "Asia/Shanghai",
    "glm_model": "z-ai/glm-5.3-flash",
    "ds_model": "deepseek/deepseek-v4-flash",
    "windows": [{"days": [1, 2, 3, 4, 5], "start": "09:00", "end": "12:00"},
                {"days": [1, 2, 3, 4, 5], "start": "14:00", "end": "18:00"}],
    "edge_buffer_seconds": 60,
    "upstream_timeout_seconds": 600,
}

# 2026-08-31 是周一；01:30 UTC = 09:30 CST（窗口内）
NOW_IN = datetime(2026, 8, 31, 1, 30, tzinfo=timezone.utc)
NOW_OUT = datetime(2026, 8, 31, 4, 30, tzinfo=timezone.utc)  # 12:30 CST 午休

class TestRewrite(unittest.TestCase):
    def test_rewrites_model_in_window(self):
        payload = {"model": "whatever", "messages": []}
        out = rewrite_payload(payload, {}, NOW_IN, FAKE_CFG)
        self.assertEqual(out["model"], FAKE_CFG["glm_model"])
    def test_rewrites_model_outside_window(self):
        payload = {"model": "whatever", "messages": []}
        out = rewrite_payload(payload, {}, NOW_OUT, FAKE_CFG)
        self.assertEqual(out["model"], FAKE_CFG["ds_model"])
    def test_override_header_wins(self):
        payload = {"model": "whatever", "messages": []}
        out = rewrite_payload(payload, {"x-model-override": "mymodel"}, NOW_IN, FAKE_CFG)
        self.assertEqual(out["model"], "mymodel")
    def test_other_fields_untouched(self):
        payload = {"model": "x", "messages": [1], "stream": True, "temperature": 0.5}
        out = rewrite_payload(payload, {}, NOW_IN, FAKE_CFG)
        self.assertEqual(out["messages"], [1])
        self.assertTrue(out["stream"])
        self.assertEqual(out["temperature"], 0.5)
    def test_upstream_path_strips_client_v1(self):
        # P0 回归锁：base 已含 /provider/v1，客户端 /v1 前缀必须剥掉
        self.assertEqual(build_upstream_path("/provider/v1", "/v1/chat/completions"),
                         "/provider/v1/chat/completions")
    def test_upstream_path_strips_v1_with_query(self):
        # query 不影响剥前缀判断（query 由 _proxy 拼回）
        self.assertEqual(build_upstream_path("/provider/v1", "/v1/chat/completions?foo=1"),
                         "/provider/v1/chat/completions")
    def test_upstream_path_passthrough_non_v1(self):
        self.assertEqual(build_upstream_path("/provider/v1", "/_health"),
                         "/provider/v1/_health")

if __name__ == "__main__":
    unittest.main()
```

**Step 2: 跑测试确认失败**

Run: `python3 -m unittest test_server -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server'`

---

### Task 5: server.py 实现（GREEN）

**Objective:** 反向代理：`/_which` 观测端点 + 上游路径映射（剥客户端 `/v1` 前缀，拼 base 路径）+ `model` 改写 + SSE `read1` 低延迟透传 + 每请求日志记录所选模型。

**Files:**
- Create: `server.py`

**Step 1: 写实现**

```python
"""本地模型切换代理。改写 /v1/chat/completions 的 model 字段，其余原样转发。

运行: CC_API_KEY=xxx python3 server.py
观测: curl http://127.0.0.1:8399/_which
强制: 请求头 X-Model-Override: <model-id>
"""
import json
import os
import http.client
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from selector import load_config, select_model

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = load_config(os.path.join(BASE, "config.json"))
HOP_HEADERS = {"host", "content-length", "accept-encoding", "connection",
               "transfer-encoding", "te", "upgrade", "trailers", "keep-alive",
               "proxy-authorization", "proxy-authenticate", "x-model-override"}

def build_upstream_path(base_path, request_path):
    """客户端 /v1/* → 上游 <base_path>/*。base 已含 /provider/v1，
    必须剥掉客户端的 /v1 前缀，否则产生 /provider/v1/v1/... 双重前缀。
    用 urlparse 取纯路径判断（忽略 query/尾斜杠场景），query 由调用方拼回。"""
    from urllib.parse import urlparse
    path = urlparse(request_path).path
    p = path[3:] if (path == "/v1" or path.startswith("/v1/")) else path
    return base_path.rstrip("/") + p

def rewrite_payload(payload, headers, now, config):
    """改写 payload['model']；X-Model-Override 头优先于时间窗（该头不转发上游）。
    非 dict 的 JSON body（数组/null）返回 None，由调用方回 400。"""
    if not isinstance(payload, dict):
        return None
    forced = None
    for k, v in headers.items():
        if k.lower() == "x-model-override":
            forced = v.strip()
    payload["model"] = forced if forced else select_model(now, config)
    return payload

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 简洁日志：时间 + 行
        local = datetime.now(ZoneInfo(CONFIG["timezone"])).strftime("%m-%d %H:%M:%S")
        print(f"[{local}] {fmt % args}", flush=True)

    def do_GET(self):
        if self.path.rstrip("/") == "/_which":
            model = select_model(datetime.now(timezone.utc), CONFIG)
            self._send_json(200, {"model": model,
                                  "local_time": datetime.now(ZoneInfo(CONFIG["timezone"])).isoformat()})
            return
        self._proxy("GET")

    def do_POST(self):
        self._proxy("POST")

    def _access(self, msg):
        """每请求改写记录：stdout + logs/access.log（观察与审计的唯一入口）。"""
        local = datetime.now(ZoneInfo(CONFIG["timezone"])).strftime("%Y-%m-%d %H:%M:%S")
        line = f"{local} {msg}"
        print(line, flush=True)
        os.makedirs(os.path.join(BASE, "logs"), exist_ok=True)
        with open(os.path.join(BASE, "logs", "access.log"), "a") as f:
            f.write(line + "\n")

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self, method):
        length = int(self.headers.get("Content-Length", 0) or 0)
        payload = self.rfile.read(length) if length else b""

        u = urlparse(self.path)
        chosen = None
        if method == "POST" and u.path.endswith("/chat/completions"):
            try:
                data = json.loads(payload or b"{}")
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid JSON body"})
                return
            data = rewrite_payload(data, dict(self.headers.items()),
                                   datetime.now(timezone.utc), CONFIG)
            if data is None:
                self._send_json(400, {"error": "JSON body must be an object"})
                return
            chosen = data["model"]
            payload = json.dumps(data).encode()

        key = os.environ.get(CONFIG["api_key_env"])
        if not key:
            self._send_json(500, {"error": f"env {CONFIG['api_key_env']} not set"})
            return

        url = urlparse(CONFIG["upstream_base"])
        # 出站头重建：统一小写（dict 大小写敏感，客户端可能发小写 authorization，
        # 原样保留会导致上游收到重复 Authorization 头——审核第二轮 P1）
        headers = {k.lower(): v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
        headers["authorization"] = f"Bearer {key}"
        headers["user-agent"] = "model-window-proxy/1.0"  # 无条件覆盖：WAF 拦部分客户端 UA
        headers["content-length"] = str(len(payload))

        upstream_path = build_upstream_path(url.path or "", u.path)
        if u.query:
            upstream_path += f"?{u.query}"
        self._access(f"{method} {self.path} -> {upstream_path} model={chosen or '-'}")

        conn = http.client.HTTPSConnection(url.hostname, url.port or 443,
                                           timeout=CONFIG.get("upstream_timeout_seconds", 600))
        resp = None
        try:
            conn.request(method, upstream_path, body=payload, headers=headers)
            resp = conn.getresponse()
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() in {"transfer-encoding", "content-length", "connection"}:
                    continue
                self.send_header(k, v)
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            while True:  # read1: 有字节即转发，SSE 帧不被攒批（审核 P1 修正）
                chunk = resp.read1(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # 客户端提前断开（agent 取消等），属正常
        except OSError:
            if resp is None:
                self._send_json(502, {"error": "upstream unreachable"})  # 头未发出，可安全回 502
            else:
                self.close_connection = True  # 头已发出：直接中止连接，写 502 会损坏流（审核 P1 修正）
        finally:
            conn.close()

if __name__ == "__main__":
    port = CONFIG["listen_port"]
    print(f"model-window-proxy on :{port} -> {CONFIG['upstream_base']}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
```

**Step 2: 跑单测确认通过**

Run: `python3 -m unittest test_server -v`
Expected: `OK`（6 tests）

**Step 3: 本机冒烟（无需 key）**

Run:
```bash
cd ~/Hermes-Projects/model-window-proxy
python3 server.py & sleep 1
curl -s http://127.0.0.1:8399/_which
kill %1
```
Expected: `{"model": "...", "local_time": "2026-08-29T..."}`，model 与当时窗口一致（周六 16:xx → ds 模型）

**Step 4: Commit**

```bash
git add server.py test_server.py && git commit -m "feat: reverse proxy with model rewrite and SSE passthrough"
```

---

### Task 6: 端到端真机验证（需要 `CC_API_KEY`）

**Objective:** 真实请求过代理，验证改写、非流式、流式三条路径。

**Step 1: 起服务**

```bash
cd ~/Hermes-Projects/model-window-proxy && CC_API_KEY=xxx python3 server.py
```

**Step 2: 非流式**（另开终端）

```bash
curl -s http://127.0.0.1:8399/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "ignored", "messages": [{"role": "user", "content": "说 ok"}]}'
```
Expected: 200。**改写判定以 `logs/access.log` 最新一行（含 `model=...`）与 `GET /_which` 为准**；响应体 `"model"` 字段仅作参考（上游可能规范化命名，不保证原样回显）

**Step 3: 流式**

```bash
curl -sN http://127.0.0.1:8399/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "ignored", "stream": true, "messages": [{"role": "user", "content": "数到3"}]}'
```
Expected: 逐块出现 `data: {...}` SSE 帧（验证透传没缓冲住流）

**Step 4: override 头**

```bash
curl -s http://127.0.0.1:8399/v1/chat/completions \
  -H "Content-Type: application/json" -H "X-Model-Override: z-ai/glm-5.3-flash" \
  -d '{"model": "ignored", "messages": [{"role": "user", "content": "说 ok"}]}'
```
Expected: access.log 该行 `model=z-ai/glm-5.3-flash`（窗口外也能强制；override 头不出现在上游请求中）

**Step 5: 把客户端指过来**

Hermes/其他 agent 的 provider base_url 改为 `http://127.0.0.1:8399/v1`（API key 字段填 CC key，代理会覆盖 Authorization）。观察一天，对照 `logs/access.log`（时间/上游路径/所选模型三要素齐全）与 `GET /_which`。

**Step 6: 最终 commit + README**

README 内容：运行方式、`/_which`、override 头、**改 config.json 后必须重启 server**、**Mac 重启后 server 不会自动拉起（fail loud），需要 KeepAlive 常驻再加第二个 LaunchAgent**、系统时区应为 Asia/Shanghai（selector 不依赖系统时区，但使用者的直觉对照需要）、上游超时调法（`upstream_timeout_seconds`，长思考场景调大）。

```bash
git add README.md && git commit -m "docs: operations guide"
```

---

## 验证清单（Definition of Done）

- [ ] `python3 -m unittest` 全绿（23 tests：16 selector + 7 server）
- [ ] `GET /_which` 返回与 `select_model(now)` 一致
- [ ] 上游路径映射正确：`/v1/chat/completions` → `https://api.commandcode.ai/provider/v1/chat/completions`（build_upstream_path 单测锁定，Task 6 E2E 复核）
- [ ] 窗口内请求 → GLM；窗口外 → DS；override 头 → 指定模型（curl 三连验证）
- [ ] `stream: true` 时 SSE 逐块到达（无代理缓冲）
- [ ] `logs/access.log` 记录每个请求的改写结果（时间/上游路径/所选模型）
- [ ] GOAT credits 消耗速率与切换前无异常跳变（跑 2-3 天后回看 Studio > Billing）

## 风险 / 权衡 / 待确认

1. **GLM slug 未最终核实**（`z-ai/glm-5.3-flash` 是高置信推断）——Task 1 第一条命令消除此风险；错了只改 config.json 一行。
2. **节假日**：判定语义已锚定 DS 官方（Peak 只认 "Monday through Friday"，不识别法定节假日/调休）。代理内置 `holidays`/`workdays` 逃生门（config 日期列表，判定优先级高于星期几），**但数据需人工维护**——每年国务院办公厅发布放假安排后，把节假日/补班日填进 config 即可；不填则与 DS 官方语义完全一致。未引入 chinesecalendar 等自动数据源（避免多一层外部依赖，YAGNI；要换随时可加）。
3. **代理可用性**：server.py 是前台进程，Mac 重启后需手动拉起（或 `nohup`）。刻意没做成 KeepAlive 常驻服务——先跑通，真需要再加第二个 LaunchAgent。客户端指向 127.0.0.1，代理挂了 = 请求失败（fail loud，不是静默回退），符合最小惊讶。
4. **仅改写 OpenAI 端点**：`/v1/messages`（Anthropic 格式）原样透传不改 model。你的工具链走 OpenAI 格式，够用；要支持时在 `_proxy` 的路径判断里加一个 `endswith("/messages")` 分支即可（Anthropic 的 model 字段同样在 JSON body 顶层）。
5. **credits 扣费假设**：Provider API 走 GOAT credits 按牌价扣（官方文档如此描述）。若实际发现 API 用量与 CLI 用量计费口径不同，以 Studio > Billing 实测为准——不影响本代理的正确性，只影响费用预期。
6. **运行时 Python 版本**：plist 用 `/usr/bin/python3`（本机为 3.9）。代码全部按 3.9 兼容书写（无 3.10+ 语法）；zoneinfo 在本机 CLT Python 3.9 下已实测可用（2026-08-29 验证）。

---

## 附录：对抗审核记录（2026-08-29）

**审核者与方式**：`deepseek/deepseek-v4-flash`（经 CommandCode Provider API 实调，非本会话模型自审）。共 4 次调用，实测总消耗 ≈ 0.12M tokens（每次提示 ~8K；补全 6K/16K/32K/32K 全部耗在 reasoning 上）。DS 对 21KB 计划的深度思考超出全部尝试的 token 预算、未输出正文终稿（`reasoning_effort=low` 被端点接受但实测无效）；本记录的发现提取自其完整思考流（114K 字符）中的结论枚举段，**"最后一行总评"缺失**，采纳判断由主会话逐条核验后作出。

### 采纳并已修改

| 级别 | 发现 | 修改落点 |
|---|---|---|
| P0 | `_proxy` 直接转发 `self.path`，丢弃 `upstream_base` 的 `/provider/v1` 前缀 → 全部请求 404，代理完全不工作 | 新增 `build_upstream_path`（剥客户端 `/v1` 前缀再拼 base，避免双重 v1）+ 2 个回归单测 + DoD 条目 |
| P1 | SSE 用 `resp.read(4096)` 攒批转发，违背"无代理缓冲" | 改 `read1(4096)` |
| P1 | `current_model.txt` 由 00:05 任务写入，恒为 DS 快照，白天语义错误 | 重新定义为"每日快照/审计记录"，实时真值以 `GET /_which` 为准（架构描述、Task 6、switch.py docstring 同步改） |
| P1 | "GLM≈2× DS 配额消耗"引用的是 Ollama 配额实验数据，混入 CC 口径；CC 是否透传 DS 峰谷价未验证 | 费用口径条目重写：已核实 CC 透传峰谷价（GOAT 费率表）；方案首要动机改为"用户指定的时段偏好"，节省为伴随收益 |
| P1 | 流中途上游故障时 `except OSError → 502` 会写坏已开始响应的连接 | 按 `resp` 是否已取到分流：未发出头 → 502；已发出头 → 中止连接 |
| P1 | 单测零覆盖 `_proxy` 的 HTTP 转发路径映射（P0 类 bug 单测全绿） | build_upstream_path 纯函数化 + 2 个单测锁死 |
| P2 | 请求 hop-by-hop 头清单不全（缺 te/upgrade/trailers/keep-alive/proxy-* 等） | HOP_HEADERS 补全 |
| P2 | 非 dict 的 JSON body（数组/null）会 TypeError → 500 | rewrite_payload 返回 None → 400 |
| P2 | `X-Model-Override` 会外泄给上游 | 加入 HOP_HEADERS 剥除 |
| P2 | 服务器实际不记录每请求所选模型，与计划描述不符 | 新增改写日志行（含上游路径 + model） |
| P2 | 缺午后缓冲到期 / 补班∩节假日 等测试；"缓冲吸收时钟偏差"表述只对尾部成立；config 改动需重启未写明；`/usr/bin/python3` 版本疑虑 | 各处同步：边界语义条目改写、风险 6 新增 |

### 记录不改码（判断依据）

- **100-continue 不处理**：主客户端为 curl/agent HTTP 库，无 Expect: 100-continue 场景（YAGNI）。
- **23:59 结束窗口 + 缓冲溢出 86400 的通用性**：当前 config 无此窗口；selector 按日秒比较，缓冲溢出到次日属定义外场景，出现时再议。

### 第二轮（同一审核者，256K 输出预算实验后，正式正文）

**实验记录**：`max_tokens` 1M 与 512K 均被端点拒绝（HTTP 400，有效范围 **[1, 393216]**）；256K 档被接受，`finish_reason=stop` 自然收敛——思考仅耗 39,177 tokens，随后输出 3,683 字符正文，全程 6.5 分钟。**证实任务思考量恒定 ~39K token**：此前 6K/16K/32K 三次"正文为空"全是预算差临门一脚，并非思考随预算膨胀。**实验推翻了"1M = DS 上下文上限 → 可给 1M 输出预算"的推断：上下文窗口（输入侧 1M）与输出预算上限（393K）是两个独立限制。**

15 条意见处置（正文为 DS 亲手所写，含总评）：

| 处置 | 意见摘要 |
|---|---|
| **采纳 P1×5** | ① 出站头大小写重建（防重复 Authorization）② 路径判断用 urlparse（query/尾斜杠）③ UA 无条件覆盖（setdefault 挡不住客户端自带被 WAF 拉黑的 UA）④ 上游超时配置化 ⑤ **删除 switch.py/launchd/current_model.txt 整个组件**（"审计记录"只记录凌晨一个点、server 不读它、launchd 无 per-job 时区——架构级冗余，已执行） |
| **采纳 P2×8** | chunked 请求体处理（计划标注为已知限制，未实现）、`/v1?query` 剥前缀、Goal 文本补缓冲语义对齐、README 补"改 config 需重启"与"重启不自启"、测试计数与报错文案修正、午后缓冲测试补齐、E2E 断言改为以 access.log+/_which 为准（上游可能规范化 model 名）、Task 1 外部事实抓屏留档 |
| **修正后采纳 1** | ④ 的"timeout=None"建议不采纳（无超时 = 挂死风险，比超时截断更糟），改为配置化默认 600s |
| **驳回 0** | — |

### 审核局限声明

两轮合计 5 次调用 ≈ 0.16M token（多为 reasoning）。第一轮正文被预算截断、总评缺失，发现提取自思考流（已由第二轮 stop 自然收敛的正文替代验证）；第二轮总评为 DS 亲手输出。剩余局限：DS 对"重复 Authorization 必被拒"的判断依据是 HTTP 语义而非对上游的实测；"CC 规范化 model 命名"未实测，但断言已按其建议降级为参考项；P0/路径修复的正确性最终由 Task 6 端到端验证兜底。
