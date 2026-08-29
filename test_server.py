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
