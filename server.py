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
