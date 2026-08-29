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
