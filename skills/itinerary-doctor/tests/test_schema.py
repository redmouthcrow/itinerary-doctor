#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_schema.py — 占位符回填、markdown 桥、交付前自检的回归测试（零依赖）。

这两类问题在前两次真实测试里反复出现：
  * 里程先手填估值、跑完 OSRM 再手改（subtitle 写 700 km → 实测 670 km）
  * tip 里的 `**加粗**` 在 HTML 卡片里显示成字面星号
把它们锁住，避免第三次再犯。

跑：python tests/test_schema.py
"""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(HERE, os.pardir)
sys.path.insert(0, os.path.join(SKILL, "scripts"))

from _schema import (resolve_placeholders, resolve_text, to_html, strip_md,  # noqa: E402
                     audit, total_km, fmt_num)

TRIP = {
    "trip": {"subtitle": "自驾约 {{total_km}} km ｜ {{days}} 天",
             "facts": ["共 {{stops}} 个点位"], "notes": ["区间车 {{legs}} 段"]},
    "days": [{"id": "D1", "km_text": "约 {{day_km}} km", "tip": "**重点**：先订房"},
             {"id": "D2", "km_text": "约 {{day_km}} km", "tip": "无占位符"}],
    "stops": [{"name": "A", "lat": 1.0, "lon": 2.0}],
    "legs": [
        {"from": "A", "to": "B", "day": "D1", "km": 100.5, "hours": 1.5, "km_source": "road"},
        {"from": "B", "to": "C", "day": "D1", "km": 20.0, "hours": 0.3, "km_source": "road",
         "counts_toward_total": False},                       # 区间车，不计入自驾
        {"from": "C", "to": "D", "day": "D2", "km": 200.0, "hours": 3.0, "km_source": "road"},
        {"from": "D", "to": "E", "day": "D2", "km": 99.0, "hours": 2.0, "km_source": "straight"},
    ],
}


class TestPlaceholders(unittest.TestCase):

    def test_total_km_excludes_marked_legs(self):
        # 100.5 + 200.0 + 99.0（退化直线仍计入总里程，但报告里会单独标出）
        self.assertEqual(total_km(TRIP), 399.5)
        self.assertEqual(total_km(TRIP, include_all=True), 419.5)   # 含被排除的 20
        self.assertEqual(total_km(TRIP, only_road=True), 300.5)     # 只算 road

    def test_day_km(self):
        self.assertEqual(total_km(TRIP, "D1"), 100.5)                # 20 被排除
        self.assertEqual(total_km(TRIP, "D2"), 299.0)

    def test_resolve_in_place(self):
        t = {"days": [{"id": "D2"}], "legs": TRIP["legs"], "trip": {}}
        self.assertEqual(resolve_text("约 {{day_km}} km", t, day="D2"), "约 299 km")
        self.assertEqual(resolve_text("第 {{day_km:D1}} km", t), "第 100 km")

    def test_unknown_placeholder_kept(self):
        t = {"legs": [], "trip": {}, "days": []}
        self.assertEqual(resolve_text("{{nope}}", t), "{{nope}}")

    def test_resolve_placeholders_walks_all_fields(self):
        trip = resolve_placeholders(dict(TRIP))
        self.assertEqual(trip["trip"]["subtitle"], "自驾约 400 km ｜ 2 天")
        self.assertEqual(trip["trip"]["facts"], ["共 1 个点位"])
        self.assertEqual(trip["days"][0]["km_text"], "约 100 km")
        self.assertEqual(trip["days"][0]["km_text"], "约 100 km")    # 幂等

    def test_fmt_num(self):
        self.assertEqual([fmt_num(x) for x in (0, 16.4, 49.9, 50, 1171.8)],
                         ["0", "16.4", "49.9", "50", "1172"])


class TestMarkdownBridge(unittest.TestCase):

    def test_to_html(self):
        self.assertEqual(to_html("**重点**：先订房"), "<b>重点</b>：先订房")
        self.assertEqual(to_html("用 `原行网` 预约"), "用 <code>原行网</code> 预约")
        # 数据里本来就用 HTML 的（弹窗）不能被破坏
        self.assertEqual(to_html("已订，<b>保留不动</b>"), "已订，<b>保留不动</b>")

    def test_strip_md(self):
        self.assertEqual(strip_md("**重点**"), "重点")
        self.assertEqual(strip_md("a **b** c"), "a b c")

    def test_asterisks_do_not_leak_to_html(self):
        trip = resolve_placeholders(dict(TRIP))
        self.assertIn("**", trip["days"][0]["tip"])                   # 数据里保留原样
        self.assertNotIn("**", to_html(trip["days"][0]["tip"]))       # 渲染时才转换


class TestAudit(unittest.TestCase):

    def test_clean_trip(self):
        trip = resolve_placeholders(dict(TRIP))
        # 只剩"退化直线"一条提醒（99 km 那段 km_source=straight）
        probs = audit(trip)
        self.assertTrue(any("真实路网" in p for p in probs))

    def test_detects_leftover_placeholder(self):
        trip = {"trip": {"subtitle": "{{total_km}} km"}, "days": [], "stops": [], "legs": []}
        probs = audit(trip)
        self.assertTrue(any("未解析的占位符" in p for p in probs))

    def test_detects_missing_coords(self):
        trip = {"trip": {}, "days": [], "legs": [], "stops": [{"name": "无坐标点"}]}
        self.assertTrue(any("缺坐标" in p for p in audit(trip)))


class TestConstraintData(unittest.TestCase):
    """约束库本身要能过校验器——防止"0 命中"那类静默失败再回来。"""

    def test_validator_passes(self):
        r = subprocess.run([sys.executable,
                            os.path.join(SKILL, "scripts", "validate_constraints.py"), "--strict"],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, "约束库校验未通过：\n%s\n%s" % (r.stdout, r.stderr))
        self.assertIn("全部通过", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
