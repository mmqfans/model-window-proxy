# model-window-proxy

一个零依赖的本地反向代理：让 OpenAI 兼容客户端在**不同时段自动使用不同的 LLM**，客户端完全无感。

## 1. 这个项目解决什么问题

很多模型服务有**分时计费**（如 DeepSeek 的 peak/off-peak 价格差一倍）、不同模型也各有强弱时段表现，但绝大多数客户端（CLI agent、IDE 插件、SDK）只让你配一个静态的 `model` + `base_url`。想按时段换模型，就得人肉改配置、重启会话。

`model-window-proxy` 在本地起一个小代理，把"按时段选模型"这件事从客户端剥离：

```
你的客户端（任意 OpenAI 兼容工具）
      │  base_url 指向 http://127.0.0.1:8399/v1
      ▼
model-window-proxy（按时间窗改写 model 字段）
      │  转发 + 注入你的 API key
      ▼
上游 API（默认 CommandCode Provider，可换任何 OpenAI 兼容服务）
```

默认时间窗按 DeepSeek 官方 peak 时段设计（周一至五 09:00–12:00 / 14:00–18:00，北京时间）：peak 时用 GLM-5.3 Flash，其余时间（晚间/周末/午休）用 DeepSeek V4 Flash。**窗口、模型、上游全部可配置**，不限于这两个模型或这家服务商。

## 2. 主要功能

- **按时间窗自动切换模型**：多个窗口、按星期几生效，窗口精确到秒
- **客户端零改动**：任何能把 `base_url` 指到 `http://127.0.0.1:8399/v1` 的工具都能直接用
- **`X-Model-Override` 请求头**：单个请求强制指定模型，临时绕过时间窗（该头不会转发给上游）
- **`GET /_which` 观测端点**：随时查询当前时间窗生效的模型
- **API key 注入**：key 只存在环境变量里，客户端随便填占位符即可；key 不落盘、不进日志
- **SSE 流式透传**：用 `read1` 逐块转发，流式响应不被代理攒批缓冲
- **每请求审计日志**：`logs/access.log` 记录时间、上游路径、所选模型
- **节假日/调休逃生门**：`holidays`（强制走 ds_model）与 `workdays`（补班日按工作窗）两个日期列表，优先级高于星期几
- **边界缓冲**：窗口尾部默认延长 60 秒才切换（最不利原则：把无法确认归属的边界秒按高价时段处理，同时吸收本机时钟偏差），可配置
- **防御性校验**：非法 JSON / 非对象 body 返回 400 而不是转发垃圾
- **零依赖**：纯 Python 3.9+ 标准库，无 pip install；23 个单元测试

## 3. 安装方法

要求：Python 3.9+（仅标准库；macOS 自带，Linux 需系统 tzdata，一般都有）。

```bash
git clone https://github.com/<you>/model-window-proxy.git
cd model-window-proxy

# 可选：跑测试（23 个，应全部通过）
python3 -m unittest -v
```

无需安装任何第三方包。

## 4. 使用方法

**第 1 步：配置上游与时间窗**（编辑 `config.json`，默认值如下，可按需修改）：

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

字段说明：

| 字段 | 含义 |
|---|---|
| `windows[].days` | ISO 星期几，1=周一 … 7=周日 |
| `glm_model` / `ds_model` | 窗口内 / 窗口外使用的模型 ID（写上游的真实 slug） |
| `edge_buffer_seconds` | 窗口尾部缓冲秒数，`end` 之后仍走 `glm_model`，之后才切 `ds_model` |
| `holidays` | 日期列表（`YYYY-MM-DD`），命中则全天走 `ds_model` |
| `workdays` | 日期列表（调休补班的周末），命中则当天按 `windows` 判定、无视星期几 |
| `api_key_env` | 存放上游 API key 的环境变量名 |

判定优先级：`holidays` > `workdays` > 常规星期几窗口。

**第 2 步：设置 key 并启动**：

```bash
export CC_API_KEY=sk-你的key      # 变量名与 config 的 api_key_env 对应
python3 server.py
# => model-window-proxy on :8399 -> https://api.commandcode.ai/provider/v1
```

**第 3 步：把客户端指过来**——把任何 OpenAI 兼容工具的 `base_url` 改为：

```
http://127.0.0.1:8399/v1
```

API key 字段随便填（如 `ollama`），代理会用环境变量里的真实 key 覆盖 `Authorization`。

**日常运维**：

- 查当前生效模型：`curl http://127.0.0.1:8399/_which`
- 改了 `config.json` 需**重启** server（配置仅在启动时读取）
- server 是前台进程，Mac 重启后不会自动拉起（刻意 fail loud；需要常驻可自行包一层 LaunchAgent/systemd）
- 注意：只对路径以 `/chat/completions` 结尾的 POST 做模型改写；其他请求（如 Anthropic 格式的 `/v1/messages`）原样转发、不改写

## 5. 输入输出示例

以下均为真实运行输出。

**查询当前窗口**：

```console
$ curl http://127.0.0.1:8399/_which
{"model": "deepseek/deepseek-v4-flash", "local_time": "2026-08-29T20:19:06.750310+08:00"}
```

（周六 20:19，不在窗口内 → 正确返回 `ds_model`）

**普通对话请求**（客户端发什么 `model` 都行，会被改写）：

```console
$ curl http://127.0.0.1:8399/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model": "ignored", "max_tokens": 50,
         "messages": [{"role": "user", "content": "reply with one word: pong"}]}'
```

响应（上游原样回显代理改写后的模型名）：

```json
{
  "id": "gen_01M...",
  "model": "deepseek/deepseek-v4-flash",
  "choices": [{"message": {"role": "assistant", "content": "..."}, "finish_reason": "stop"}],
  "usage": {"prompt_tokens": 10, "completion_tokens": 50, "total_tokens": 60}
}
```

**流式请求**（`"stream": true`，SSE 帧逐块透传）：

```console
$ curl -N http://127.0.0.1:8399/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model": "ignored", "stream": true, "max_tokens": 80,
         "messages": [{"role": "user", "content": "count 1 to 3"}]}'
```

```
data: {"id":"gen_01M...","model":"deepseek/deepseek-v4-flash","choices":[{"delta":{"role":"assistant"},...}]}

data: {"id":"gen_01M...","choices":[{"delta":{"reasoning":"We"},...}]}

data: {"id":"gen_01M...","choices":[{"delta":{"reasoning":" need"},...}]}
...
```

**强制指定模型**（窗口外也能覆盖，本例在周六晚上强制 GLM）：

```console
$ curl http://127.0.0.1:8399/v1/chat/completions \
    -H "Content-Type: application/json" \
    -H "X-Model-Override: z-ai/glm-5.3-flash" \
    -d '{"model": "ignored", "max_tokens": 60,
         "messages": [{"role": "user", "content": "reply one word: pong"}]}'
```

响应中 `"model": "z-ai/glm-5.3-flash"` —— 覆盖生效。

**审计日志**（`logs/access.log`，每请求一行）：

```
2026-08-29 20:20:10 POST /v1/chat/completions -> /provider/v1/chat/completions model=deepseek/deepseek-v4-flash
2026-08-29 20:20:27 POST /v1/chat/completions -> /provider/v1/chat/completions model=deepseek/deepseek-v4-flash
2026-08-29 20:20:29 POST /v1/chat/completions -> /provider/v1/chat/completions model=z-ai/glm-5.3-flash
```

**非法请求**：

```console
$ curl -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8399/v1/chat/completions \
    -H "Content-Type: application/json" -d 'not-json'
400
$ curl -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8399/v1/chat/completions \
    -H "Content-Type: application/json" -d '[1,2]'
400
```

## 已知限制

- 仅改写 OpenAI Chat Completions 格式（`/chat/completions` 结尾的 POST）；Anthropic Messages 等其他端点原样转发、不改模型
- 不支持 `Transfer-Encoding: chunked` 请求体（绝大多数客户端默认发送 `Content-Length`，不受影响）
- 配置启动时读取一次，改动需重启
- 认证固定走 `Authorization: Bearer <key>`，不支持 query 参数传 key 的上游
- 无自动重试/多上游故障转移——保持单薄，故障原样返回

## 设计文档

完整的设计推理（时间窗边界的官方语义锚定、最不利原则的取舍、两轮对抗式审核的记录与修正）见 [`PLAN.md`](PLAN.md)。

## License

MIT
