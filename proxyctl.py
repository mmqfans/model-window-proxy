#!/usr/bin/env python3
"""model-window-proxy 便捷开关。

用法:
  python3 proxyctl start     后台启动代理（脱离终端，关窗口不死）
  python3 proxyctl stop      停止代理
  python3 proxyctl restart   重启
  python3 proxyctl status    状态总览（进程/端口/当前窗口模型）
  python3 proxyctl which     只打印当前生效的模型 ID
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
PIDFILE = os.path.join(BASE, "logs", "proxy.pid")
CONFIG = json.load(open(os.path.join(BASE, "config.json")))
PORT = CONFIG["listen_port"]


def _port_listening():
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def _which_model(timeout=2):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/_which", timeout=timeout) as r:
            return json.load(r)
    except OSError:
        return None


def _pid_running_is_ours(pid):
    """pid 活着且确实是本项目的 server.py（防止 PID 复用误杀无辜进程）。"""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True, timeout=5).stdout
        return "server.py" in out
    except (subprocess.SubprocessError, OSError):
        return False


def _read_pid():
    """读 pidfile；PID 已死或已被复用则清掉并返回 None。"""
    try:
        pid = int(open(PIDFILE).read().strip())
    except (OSError, ValueError):
        return None
    if _pid_running_is_ours(pid):
        return pid
    try:
        os.remove(PIDFILE)
    except OSError:
        pass
    return None


def cmd_start():
    pid = _read_pid()
    if pid:
        print(f"已在运行 (pid {pid})，无需重复启动。查看状态: python3 proxyctl status")
        return 0
    if _port_listening():
        print(f"端口 {PORT} 已被其他进程占用，且不是本代理。请先排查: lsof -i :{PORT}")
        return 1
    os.makedirs(os.path.join(BASE, "logs"), exist_ok=True)
    log = open(os.path.join(BASE, "logs", "server.out"), "ab")
    proc = subprocess.Popen([sys.executable, os.path.join(BASE, "server.py")],
                            stdout=log, stderr=log, start_new_session=True)
    for _ in range(30):  # 最多等 3 秒让端口就绪
        if _port_listening():
            with open(PIDFILE, "w") as f:
                f.write(str(proc.pid))
            info = _which_model()
            model = info["model"] if info else "?"
            print(f"已启动 (pid {proc.pid})，监听 127.0.0.1:{PORT}，当前窗口模型: {model}")
            return 0
        time.sleep(0.1)
    print("启动超时：端口未就绪，详情见 logs/server.out")
    return 1


def cmd_stop():
    pid = _read_pid()
    if pid is None:
        if _port_listening():
            print(f"代理未在运行（无 pidfile），但端口 {PORT} 被其他进程占用，请手动排查")
            return 1
        print("代理未在运行。")
        return 0
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):  # 最多等 5 秒优雅退出
        if not _pid_running_is_ours(pid):
            break
        time.sleep(0.1)
    else:
        os.kill(pid, signal.SIGKILL)
        print("未在 5 秒内退出，已强制结束")
    try:
        os.remove(PIDFILE)
    except OSError:
        pass
    print(f"已停止 (pid {pid})")
    return 0


def cmd_status():
    pid = _read_pid()
    print(f"进程: {'运行中 (pid %d)' % pid if pid else '未运行'}")
    print(f"端口: {PORT} {'监听中' if _port_listening() else '无监听'}")
    info = _which_model()
    if info:
        print(f"当前窗口模型: {info['model']}")
        print(f"本地时间: {info['local_time']}")
    elif pid:
        print("进程在但 /_which 无响应（可能正在启动或已僵死，可 restart）")
        return 1
    return 0


def cmd_which():
    info = _which_model()
    if not info:
        print("代理未运行", file=sys.stderr)
        return 1
    print(info["model"])
    return 0


if __name__ == "__main__":
    actions = {"start": cmd_start, "stop": cmd_stop, "restart": None,
               "status": cmd_status, "which": cmd_which}
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        print(__doc__)
        sys.exit(2)
    if sys.argv[1] == "restart":
        cmd_stop()
        sys.exit(cmd_start())
    sys.exit(actions[sys.argv[1]]())
