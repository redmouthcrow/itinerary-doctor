#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_render.py — 渲染层与约束匹配的回归测试（零依赖，不联网）。

为什么要有：前两次真实测试的 7 个 bug 里有 6 个在输入层（已锁住），
剩下的一个在渲染层——Leaflet 在 0×0 容器上初始化导致地图退化到 z=18、只加载 1 张瓦片。
那个 bug 当时是靠我手工开浏览器才发现的。能固化的部分应该固化：

  * HTML 产物必须是自包含的（内联 leaflet，不依赖 CDN）
  * 点位/路段数量要和数据一致
  * 不能有未解析的占位符
  * markdown 要转成 HTML（tip 里的 **加粗** 不能在页面上显示成星号）
  * 约束匹配的口径（东疆那单"place 写吐鲁番 → 0 命中"的静默失败）

跑：python tests/test_render.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(HERE, os.pardir)
SCRIPTS = os.path.join(SKILL, "scripts")
sys.path.insert(0, SCRIPTS)

from _schema import resolve_placeholders                              # noqa: E402
import fetch_constraints as FC                                        # noqa: E402
import match_season as MS                                             # noqa: E402

TRIP = {
    "trip": {"title": "测试行程", "subtitle": "共 {{days}} 天 ｜ 自驾约 {{total_km}} km",
             "facts": ["{{stops}} 个点位"], "notes": ["**注意**：这是测试"]},
    "days": [{"id": "D1", "date": "2026-04-01", "route": "A → B", "km_text": "约 {{day_km}} km",
              "tip": "**必看**：提前预约", "color": "#c8363a"},
             {"id": "D2", "date": "2026-04-02", "route": "B → C", "km_text": "约 {{day_km}} km",
              "tip": "普通提示", "color": "#267ca4"}],
    "stops": [{"n": 1, "name": "A", "lat": 43.8, "lon": 87.6, "day": "D1"},
              {"n": 2, "name": "B", "lat": 44.0, "lon": 88.0, "day": "D1"},
              {"n": 3, "name": "C", "lat": 44.2, "lon": 88.4, "day": "D2"}],
    "legs": [{"from": "A", "to": "B", "day": "D1", "km": 40.0, "hours": 0.8, "km_source": "road",
              "geometry": [[43.8, 87.6], [43.9, 87.8], [44.0, 88.0]]},
             {"from": "B", "to": "C", "day": "D2", "km": 60.0, "hours": 1.0, "km_source": "road",
              "geometry": [[44.0, 88.0], [44.1, 88.2], [44.2, 88.4]]},
             {"from": "B", "to": "A", "day": "D2", "km": 40.0, "hours": 0.8, "km_source": "road",
              "counts_toward_total": False,
              "geometry": [[44.0, 88.0], [43.9, 87.8], [43.8, 87.6]]}],
    "booking_actions": [{"kind": "new", "label": "必约", "detail": "提前 3 天"}],
}


def run(script, trip_path, out_path):
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, script), trip_path, "-o", out_path],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    if r.returncode != 0:
        raise AssertionError("%s 失败：\n%s\n%s" % (script, r.stdout, r.stderr))
    with open(out_path, encoding="utf-8") as f:
        return f.read()


def _js_array(html, marker):
    """取 marker 之后的那个 JSON 数组：括号配对 + 跳过字符串内的括号。

    不能只用正则 —— 三个数组在同一个 var 语句里声明，且元素里含大量中文与括号。
    """
    i = html.index(marker) + len(marker)
    while html[i] != "[":
        i += 1
    depth, j, in_str, esc = 0, i, False, False
    while j < len(html):
        c = html[j]
        if in_str:
            if esc:
                esc = False
            elif c == chr(92):
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return json.loads(html[i:j + 1])
        j += 1
    raise AssertionError("没找到 %s 对应的数组" % marker)


class TestRenderHtml(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="itdoc-")
        cls.trip = os.path.join(cls.tmp, "trip.json")
        with open(cls.trip, "w", encoding="utf-8") as f:
            json.dump(TRIP, f, ensure_ascii=False)
        cls.html = run("render_html.py", cls.trip, os.path.join(cls.tmp, "o.html"))

    def test_self_contained(self):
        """必须内联 leaflet —— 单文件、双击即开，不能依赖 CDN。"""
        self.assertIn("Leaflet", self.html)
        self.assertNotIn("unpkg.com/leaflet", self.html)
        self.assertNotIn("cdn.jsdelivr.net/npm/leaflet", self.html)

    def test_counts_match_data(self):
        """三个数组在同一个 var 语句里声明，所以按括号配对取值而不是用正则。"""
        self.assertEqual(len(_js_array(self.html, "var LEGS=")), len(TRIP["legs"]))
        self.assertEqual(len(_js_array(self.html, ", STOPS=")), len(TRIP["stops"]))
        self.assertEqual(len(_js_array(self.html, ", DAYS=")), len(TRIP["days"]))

    def test_no_leftover_placeholder(self):
        self.assertNotIn("{{", self.html)

    def test_placeholders_resolved_in_subtitle(self):
        # 100 = 40 + 60，最后一段 40 km 被 counts_toward_total:false 排除
        self.assertIn("自驾约 100 km", self.html)
        self.assertIn("共 2 天", self.html)

    def test_markdown_bridge(self):
        """tip 会以 innerHTML 插入，`**加粗**` 不能原样出现。"""
        self.assertIn("<b>必看</b>", self.html)
        body = self.html.split(", DAYS=")[1].split(";")[0]
        self.assertNotIn("**", body)

    def test_mobile_and_print_rules_present(self):
        self.assertIn("@media", self.html)


class TestRenderReport(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="itdoc-r-")
        cls.trip = os.path.join(cls.tmp, "trip.json")
        with open(cls.trip, "w", encoding="utf-8") as f:
            json.dump(TRIP, f, ensure_ascii=False)
        cls.md = run("render_report.py", cls.trip, os.path.join(cls.tmp, "o.md"))

    def test_sections(self):
        for sec in ("一、订房与退改决策", "二、逐日计划", "三、避坑清单",
                    "五、待核实与风险项", "六、免责与核实提示"):
            self.assertIn(sec, self.md, "缺章节：%s" % sec)

    def test_excluded_legs_disclosed(self):
        """被排除在总里程外的 leg 必须在报告里说明，避免和"几何合计"对不上时困惑。"""
        self.assertIn("未计入总里程", self.md)

    def test_markdown_bold_kept(self):
        self.assertIn("**注意**", self.md)          # 报告本身是 markdown，保留加粗


class TestConstraintMatching(unittest.TestCase):
    """东疆那单的静默失败：约束 place 写"吐鲁番"，行程里只有点位名 → 一条都没挂上。"""

    def test_matches_by_substring(self):
        c = {"place": "交河故城/火焰山/坎儿井"}
        self.assertTrue(FC.matches(c, "乌鲁木齐 → 交河故城 → 坎儿井 → 火焰山"))
        self.assertFalse(FC.matches(c, "乌鲁木齐 → 天山天池"))

    def test_city_level_place_does_not_match_poi_only_text(self):
        """把这条固定下来：这是已知的坑，所以校验器要求 place 必须能匹配到地名库。"""
        c = {"place": "吐鲁番"}
        self.assertFalse(FC.matches(c, "乌鲁木齐 → 交河故城 → 火焰山 → 葡萄沟"))

    def test_reverse_containment(self):
        """行程写全称、约束写简称时也要能命中。"""
        c = {"place": "喀纳斯"}
        self.assertTrue(FC.matches(c, "布尔津 → 贾登峪 →(区间车) 喀纳斯三湾"))


class TestSeasonWindows(unittest.TestCase):

    def test_in_window_normal(self):
        self.assertTrue(MS.in_window({"from": "04-05", "to": "04-20"},
                                     __import__("datetime").date(2026, 4, 10)))
        self.assertFalse(MS.in_window({"from": "04-05", "to": "04-20"},
                                      __import__("datetime").date(2026, 4, 21)))

    def test_in_window_cross_year(self):
        """蓝冰期 11-01 → 03-31 跨年，窗口边界要判对。"""
        import datetime as dt
        w = {"from": "11-01", "to": "03-31"}
        for d, want in ((dt.date(2026, 12, 1), True), (dt.date(2026, 1, 15), True),
                        (dt.date(2026, 4, 1), False), (dt.date(2026, 10, 31), False)):
            self.assertEqual(MS.in_window(w, d), want, "跨年窗口判错：%s" % d)

    def test_judge_priority(self):
        import datetime as dt
        ws = [{"from": "04-01", "to": "04-30", "kind": "shoulder", "label": "可行"},
              {"from": "04-10", "to": "04-20", "kind": "peak", "label": "最佳"}]
        st, w = MS.judge(dt.date(2026, 4, 15), ws)
        self.assertEqual((st, w["label"]), ("peak", "最佳"))       # peak 优先于 shoulder
        st, _ = MS.judge(dt.date(2026, 4, 25), ws)
        self.assertEqual(st, "shoulder")
        st, w = MS.judge(dt.date(2026, 6, 15),
                         [{"from": "06-01", "to": "07-31", "kind": "off", "label": "酷热"}])
        self.assertEqual(st, "off")
        self.assertEqual(MS.judge(dt.date(2026, 6, 15), [])[0], "none")

    def test_no_data_not_flagged(self):
        """城市/桥梁这类本来就没有观赏窗口，不能报成问题。"""
        rows = MS.analyze(TRIP)                                    # A/B/C 都不在库里
        self.assertTrue(all(not r["has_window"] for r in rows))
        self.assertTrue(all(r["status"] == "no-data" for r in rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
