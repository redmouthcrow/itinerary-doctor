#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parse_itinerary.py — 行程输入 → Trip Schema 骨架（trip.json）。

只做机械转换，不做判断：
  * 输入形状交给 normalize_input.py（表格 / Day 标题 / 散文聊天三种策略自动识别）
  * 用 data/places.json 做贪心匹配，猜出 **draft** 版 stops/legs —— 一定是草稿，
    必须人工/agent 复核，脚本会把不确定项列出来
  * 修掉两个真实踩过的坑：Excel 序列号日期（46290 → 2026-09-25）、混在时间列里的时间值

用法：
    python parse_itinerary.py 行程.xlsx  -o trip.json
    python parse_itinerary.py 行程.md    -o trip.json
    python parse_itinerary.py 聊天记录.txt -o trip.json --start 2026-09-25
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from normalize_input import normalize, _load_places, _places_in  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="行程表 → Trip Schema 骨架（支持多种输入形状）")
    ap.add_argument("input", help="xlsx / csv / md / txt（表格、Day 标题、聊天记录都可以）")
    ap.add_argument("-o", "--out", default="trip.json")
    ap.add_argument("--title", help="行程标题（默认取文件名）")
    ap.add_argument("--start", help="出发日期 YYYY-MM-DD（遇到「第 N 天」时用来换算成日期）")
    ap.add_argument("--strategy", choices=["table", "heading", "prose"],
                    help="强制指定解析策略（自动判断不对劲时用）")
    args = ap.parse_args()

    rows, how = normalize(args.input, args.start, strategy=args.strategy)
    if not rows:
        sys.exit("解析失败：%s" % how)
    print("读取 %s" % args.input)
    print("命中策略：%s" % how)

    places = _load_places()
    days, raw_stops, warnings = [], {}, []
    for i, r in enumerate(rows, 1):
        route = (r.get("route") or "").strip()
        day = {
            "id": "D%d" % i,
            "date": r.get("date_iso") or r.get("date_text") or "",
            "route": route,
            "km_text": (r.get("travel") or "").strip(),
            "intensity": "",
            "lodging": (r.get("lodging") or "").strip(),
            "note": (r.get("note") or "").strip(),
            "source": r.get("source_line"),
        }
        if not r.get("date_iso"):
            warnings.append("%s：日期没解析成确定值（原文「%s」）—— 若含「第 N 天」，"
                            "请加 --start 2026-09-25" % (day["id"], r.get("date_text")))
        days.append(day)
        hit = _places_in(route, places) if route else []
        if route and len(hit) < 2:
            warnings.append("%s：路线里只认出 %d 个地名，请手工补 stops/legs —— %s"
                            % (day["id"], len(hit), route[:40]))
        elif not route:
            warnings.append("%s：这一天没有路线文本（可能原文只写了日期/住宿）" % day["id"])
        for name, meta in hit:
            raw_stops.setdefault(name, {"name": name, "lat": meta["lat"], "lon": meta["lon"],
                                        "confidence": meta.get("confidence", "high"),
                                        "days": []})
            raw_stops[name]["days"].append(day["id"])
        # 住宿地名也进点位（但不算路线）
        if day["lodging"]:
            lh = _places_in(day["lodging"], places)
            for name, meta in lh:
                raw_stops.setdefault(name, {"name": name, "lat": meta["lat"], "lon": meta["lon"],
                                            "confidence": meta.get("confidence", "high"),
                                            "days": []})
                if day["id"] not in raw_stops[name]["days"]:
                    raw_stops[name]["days"].append(day["id"])

    order = []
    for d in days:
        for name, _m in _places_in(d["route"] or "", places):
            if name not in order:
                order.append(name)
    stops = []
    for n, name in enumerate(order, 1):
        s = dict(raw_stops[name]); s["n"] = n; s["day"] = s["days"][0]
        stops.append(s)

    legs = []
    for d in days:
        hit = [n for n, _m in _places_in(d["route"] or "", places)]
        for a, b in zip(hit, hit[1:]):
            legs.append({"from": a, "to": b, "day": d["id"], "dashed": False})

    trip = {
        "trip": {"title": args.title or os.path.splitext(os.path.basename(args.input))[0],
                 "subtitle": "", "facts": [], "notes": []},
        "booking_actions": [],
        "days": days,
        "stops": stops,
        "legs": legs,
        "_draft": {
            "generated_by": "parse_itinerary.py",
            "input_strategy": how,
            "must_review": [
                "stops/legs 是贪心匹配的草稿：换乘、区间车、往返支线需要人工改写",
                "legs 的 geometry 还是空的，需跑 fetch_routes.py 补真实路网",
                "booking_actions（退/改/新订决策）与 alerts 需要结合约束库由 agent 判断生成",
                "km_text 直接来自原表，不等于真实里程",
                "日期为「第 N 天」的请务必用 --start 传出发日期重新跑一次",
            ],
        },
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(trip, f, ensure_ascii=False, indent=1)
    print("已生成 %s：%d 天 / 草稿点位 %d 个 / 草稿路段 %d 条"
          % (args.out, len(days), len(stops), len(legs)))
    if warnings:
        print("\n需要人工复核（%d 项）：" % len(warnings))
        for w in warnings[:12]:
            print("  · " + w)
        if len(warnings) > 12:
            print("  · …还有 %d 项" % (len(warnings) - 12))


if __name__ == "__main__":
    main()
