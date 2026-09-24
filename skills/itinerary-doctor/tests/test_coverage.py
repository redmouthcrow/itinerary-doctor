#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_coverage.py — 库覆盖度的回归测试。

为什么要有：覆盖度是这个工具的核心资产，但它是最容易"悄悄退化"的东西——
改数据、清缓存、合错文件都可能让某条走廊变成 0 覆盖，而功能测试全绿。
这里给每条走廊设一个下限，掉了就报错。

跑：python tests/test_coverage.py
"""
import io
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(HERE, os.pardir)
DATA = os.path.join(SKILL, "data")

# 每条走廊的最低门槛：点位 / 约束
MIN_NEW = {           # 四条新走廊各自的门槛（点位 / 约束）
    "西藏": (25, 15),
    "川西": (25, 15),
    "青甘": (25, 15),
    "内蒙": (25, 15),
}
MIN_XINJIANG = (55, 40)   # 北疆+东疆+伊犁三条走廊合计（同一片地名库，按组看总量）
XINJIANG = "北疆 东疆 伊犁".split()
# 约束没有 corridor 字段时的兜底关键词（按 place 猜）
CORRIDOR_HINT = {
    "北疆": ("喀纳斯", "禾木", "白哈巴", "布尔津", "独库", "魔鬼城", "赛里木湖"),
    "东疆": ("天山天池", "吐鲁番", "交河", "火焰山", "葡萄沟", "乌鲁木齐", "坎儿井"),
    "伊犁": ("伊宁", "霍城", "昭苏", "夏塔", "特克斯", "琼库什台", "恰西", "库尔德宁",
             "吐尔根", "喀拉峻", "那拉提"),
    "西藏": ("西藏", "拉萨", "日喀则", "阿里", "林芝", "昌都", "纳木", "珠峰", "布达拉宫"),
    "川西": ("川西", "甘孜", "阿坝", "亚丁", "稻城", "康定", "九寨", "黄龙", "四姑娘",
             "色达", "折多", "317", "318"),
    "青甘": ("青海", "甘肃", "西宁", "敦煌", "张掖", "嘉峪关", "青海湖", "茶卡", "大柴旦",
             "莫高窟", "祁连", "门源", "河西"),
    "内蒙": ("内蒙", "呼伦贝尔", "额济纳", "阿尔山", "乌兰布统", "锡林郭勒", "满洲里", "海拉尔"),
}


def load(name):
    with io.open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


class TestCoverage(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.places = load("places.json")["places"]
        cls.constraints = load("constraints.json")["constraints"]

    def _count(self, field):
        counts = {}
        for v in (self.places.values() if field == "places" else self.constraints):
            c = v.get("corridor") or "新疆(未标)"
            counts[c] = counts.get(c, 0) + 1
        return counts

    def test_each_new_corridor_has_enough(self):
        pc, cc = self._count("places"), self._count("constraints")
        pr = "、".join("%s %d" % (k, v) for k, v in sorted(pc.items()))
        cr = "、".join("%s %d" % (k, v) for k, v in sorted(cc.items()))
        for corridor, (min_p, min_c) in MIN_NEW.items():
            self.assertGreaterEqual(pc.get(corridor, 0), min_p,
                                    "走廊「%s」点位只有 %d（下限 %d）。分布：%s"
                                    % (corridor, pc.get(corridor, 0), min_p, pr))
            self.assertGreaterEqual(cc.get(corridor, 0), min_c,
                                    "走廊「%s」约束只有 %d（下限 %d）。分布：%s"
                                    % (corridor, cc.get(corridor, 0), min_c, cr))

    def test_xinjiang_corridors_together(self):
        pc, cc = self._count("places"), self._count("constraints")
        np_ = pc.get("新疆(未标)", 0) + sum(pc.get(c, 0) for c in XINJIANG)
        nc_ = cc.get("新疆(未标)", 0) + sum(cc.get(c, 0) for c in XINJIANG)
        self.assertGreaterEqual(np_, MIN_XINJIANG[0], "新疆点位只有 %d（下限 %d）" % (np_, MIN_XINJIANG[0]))
        self.assertGreaterEqual(nc_, MIN_XINJIANG[1], "新疆约束只有 %d（下限 %d）" % (nc_, MIN_XINJIANG[1]))

    def test_season_constraints_have_windows(self):
        """时令类约束应尽量带结构化窗口，否则季节匹配器判不了它。"""
        seasons = [c for c in self.constraints if c.get("category") == "season"]
        with_win = [c for c in seasons if c.get("window")]
        ratio = len(with_win) / len(seasons) if seasons else 1
        self.assertGreaterEqual(ratio, 0.5,
                                "时令约束里只有 %d/%d 带结构化窗口" % (len(with_win), len(seasons)))

    def test_no_placeholder_coords(self):
        bad = [n for n, v in self.places.items()
               if v.get("lat") is None or v.get("lon") is None
               or not (-90 <= v["lat"] <= 90) or not (-180 <= v["lon"] <= 180)]
        self.assertEqual(bad, [], "坐标缺失或越界的点位：%s" % "、".join(bad[:8]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
