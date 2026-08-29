# model-window-proxy

本地反向代理：按时间窗自动切换 CommandCode Provider API 的模型。

**规则**（Asia/Shanghai）：周一至五 09:00–12:00 与 14:00–18:00 → `z-ai/glm-5.3-flash`；其余（晚间/周末）→ `deepseek/deepseek-v4-flash`。窗口尾部按最不利原则延长 60s 缓冲：12:00/18:00 整后 60 秒内仍走 GLM，之后才切 DS。设计动机与完整推理见 `PLAN.md`（含两轮对抗审核记录）。

## 运行

```bash
export CC_API_KEY=sk-...   # CommandCode key（CLI 与 API 同一把，Studio 获取）
python3 server.py          # 监听 127.0.0.1:8399
```

客户端把 base_url 指到 `http://127.0.0.1:8399/v1`（API key 随便填，代理会用环境变量覆盖）。

## 观测与控制

| 手段 | 用途 |
|---|---|
| `curl http://127.0.0.1:8399/_which` | 当前时间窗生效模型（实时真值） |
| 请求头 `X-Model-Override: <model-id>` | 单请求强制指定模型（不发给上游） |
| `logs/access.log` | 每请求记录：时间 / 上游路径 / 所选模型 |

## 运维须知

- **改了 `config.json` 必须重启 server**（配置在启动时读取一次）
- **Mac 重启后 server 不会自动拉起**（刻意 fail loud）；需要常驻再加一个 KeepAlive LaunchAgent
- 长思考场景若流被断，调大 `upstream_timeout_seconds`
- 系统时区建议为 Asia/Shanghai（selector 依赖 config 的时区而非系统时区，但前者便于人工对照）
- 法定节假日/调休：config 的 `holidays`（强制 DS）与 `workdays`（补班日按工作窗走 GLM）默认为空 = 与 DeepSeek 官方 peak 语义（纯日历星期）一致；每年放假安排公布后自行填 `YYYY-MM-DD`

## 测试

```bash
python3 -m unittest -v   # 23 tests
```
