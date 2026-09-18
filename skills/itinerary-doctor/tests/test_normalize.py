#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_normalize.py — 输入形状的回归测试（零依赖，纯标准库 unittest）。

为什么要有这个文件：用户拿到的行程有自己做的表、AI 生成的 markdown、微信聊天记录……
形状完全不固定。早先的解析器只认"首行标准表头 + 日期/路线/住宿"，6 种常见脏输入全挂。
把那些样本固化成 fixtures，任何改动都必须让这 7 项继续通过。

跑：
    python tests/test_normalize.py
    python -m unittest discover -s tests
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "scripts"))

from normalize_input import normalize, parse_date_any, cn2int  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
XLSX = os.path.join(HERE, os.pardir, "examples", "xinjiang-2026-09", "input-行程v0.1.xlsx")


def run(name, **kw):
    rows, how = normalize(os.path.join(FIX, name), **kw)
    assert rows, "解析失败：%s → %s" % (name, how)
    return rows, how


class TestInputShapes(unittest.TestCase):

    def test_wechat_prose(self):
        """微信聊天式：日期和地点混在句子里，且一句话里含多个日期。"""
        rows, how = run("prose-wechat.txt", start="2026-09-25")
        self.assertIn("散文", how)
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0]["date_iso"], "2026-09-25")
        self.assertIn("乌鲁木齐", rows[0]["route"])
        # "26号"这种省略月份的写法要顺延到 9 月，而不是丢掉
        self.assertEqual(rows[1]["date_iso"], "2026-09-26")
        self.assertIn("布尔津", rows[1]["route"])
        # 跨月：10月1号
        self.assertIn("2026-10-01", [r["date_iso"] for r in rows])

    def test_ai_markdown_heading(self):
        """AI 生成的 markdown：## Day N · 日期 + emoji + 列表项。"""
        rows, how = run("heading-ai-markdown.md", start="2026-09-25")
        self.assertIn("标题式", how)
        self.assertEqual(len(rows), 3)                       # 不能因为"Day 1"和"9月25日"拆成 6 行
        self.assertEqual(rows[0]["date_iso"], "2026-09-25")
        self.assertIn("全季", rows[0]["lodging"])
        self.assertEqual(rows[2]["lodging"], "白哈巴村")

    def test_two_col_header(self):
        """两列表：表头是「时间/安排」——早先 find_header 与 map_columns 词表不一致时认不出。"""
        rows, how = run("table-two-col.csv", start="2026-09-25")
        self.assertIn("表头映射", how)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["date_iso"], "2026-09-25")

    def test_english_header(self):
        rows, how = run("table-english.csv", start="2026-09-25")
        self.assertIn("表头映射", how)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["route"], "Urumqi to Burqin via Wucaitan")
        self.assertEqual(rows[2]["lodging"], "Baihaba")

    def test_no_header_positional(self):
        """无表头：首列日期、次列内容、末列住宿。"""
        rows, how = run("table-no-header.csv", start="2026-09-25")
        self.assertIn("按列位置猜", how)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["route"], "抵达乌鲁木齐")
        self.assertEqual(rows[0]["lodging"], "全季酒店")

    def test_day_number_plus_date(self):
        """「第1天 | 9月25日 | 路线 | 住宿」——首列是天序号、次列才是日期，别把日期当路线。"""
        rows, how = run("table-day-num.csv", start="2026-09-25")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["route"], "抵达乌鲁木齐")
        self.assertEqual(rows[0]["date_iso"], "2026-09-25")

    def test_real_xlsx_regression(self):
        """真实案例：Excel 序列号日期必须还原（46290 → 2026-09-25），住宿列要认出来。"""
        rows, how = run(XLSX)
        self.assertIn("表头映射", how)
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0]["date_iso"], "2026-09-25")
        self.assertEqual(rows[-1]["date_iso"], "2026-10-04")
        self.assertIn("布尔津", rows[1]["lodging"])


class TestDateParsing(unittest.TestCase):

    def test_forms(self):
        for text, want in [("2026-09-25", "2026-09-25"),
                           ("2026年9月25日", "2026-09-25"),
                           ("9月25日", "2026-09-25"),            # 用默认年份补全
                           ("9/25", "2026-09-25"),
                           ("9.25", "2026-09-25")]:
            iso, _kind, _raw = parse_date_any(text, default_year=2026)
            self.assertEqual(iso, want, "解析 %r 得到 %r" % (text, iso))

    def test_no_default_year(self):
        """没有默认年份时，月日形式无法补全 → 返回 None，交给上层去提示人工补。"""
        iso, kind, _raw = parse_date_any("9月25日")
        self.assertIsNone(iso)
        self.assertEqual(kind, "月日")
        # 「第 N 天」本来就没有日期，kind 为天序号
        iso, kind, raw = parse_date_any("第3天")
        self.assertEqual((iso, kind, raw), (None, "天序号", 3))

    def test_cn_numbers(self):
        self.assertEqual([cn2int(x) for x in ("三", "十", "十二", "二十三")], [3, 10, 12, 23])


if __name__ == "__main__":
    unittest.main(verbosity=2)
