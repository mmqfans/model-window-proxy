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
