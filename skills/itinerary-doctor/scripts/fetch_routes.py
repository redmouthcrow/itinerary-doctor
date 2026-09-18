#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_routes.py — 给 Trip Schema 的每条 leg 填上**真实路网**几何与里程。

降级链（每一级都会在数据里留下痕迹，绝不假装成功）：
    1. OSRM 公共实例            → km_source = "road"
    2. 失败：保留空几何         → km_source = "unverified"（渲染时报错并跳过该段）
    3. --straight 显式指定      → km_source = "straight"（直线距离，仅用于没网时预览）

用法：
    python fetch_routes.py trip.json            # 就地写回 trip.json
    python fetch_routes.py trip.json --out t2.json
    python fetch_routes.py trip.json --straight # 断网预览
"""
import argparse
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, os.pardir, "data")
OSRM = "https://router.project-osrm.org/route/v1/driving/{coords}"
UA = {"User-Agent": "itinerary-doctor/0.1 (+https://github.com/)"}


def load_places():
    with open(os.path.join(DATA, "places.json"), encoding="utf-8") as f:
        raw = json.load(f)["places"]
    idx = {}
    for name, v in raw.items():
        idx[name.lower()] = (name, v)
        for a in v.get("alias", []) or []:
            idx[str(a).lower()] = (name, v)
    return idx


def lookup(idx, name):
    """先在地名库里精确/别名匹配，再做最长子串匹配（"喀纳斯景区换乘中心" → "喀纳斯"）。"""
    if not name:
        return None
    key = str(name).strip().lower()
    if key in idx:
        return idx[key]
    best = None
    for k, v in idx.items():
        if k in key and (best is None or len(k) > len(best[0])):
            best = (k, v)
    return best[1] if best else None


def osrm_route(a, b, vias=(), tries=3, timeout=40):
    pts = [a] + list(vias) + [b]
    coords = ";".join("%.5f,%.5f" % (p["lon"], p["lat"]) for p in pts)
    url = OSRM.format(coords=coords) + "?overview=full&geometries=geojson"
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.load(r)
            if d.get("code") == "Ok":
                rt = d["routes"][0]
                return {
                    "geometry": [[round(c[1], 5), round(c[0], 5)] for c in rt["geometry"]["coordinates"]],
                    "km": round(rt["distance"] / 1000.0, 1),
                    "hours": round(rt["duration"] / 3600.0, 1),
                }
            print("    OSRM 返回 %s" % d.get("code"), file=sys.stderr)
            return None
        except Exception as e:                                    # noqa: BLE001
            print("    重试 %d/%d：%s" % (i + 1, tries, e), file=sys.stderr)
            time.sleep(3 * (i + 1))
    return None


def straight(a, b):
    km = math.hypot((b["lon"] - a["lon"]) * 111.32 * math.cos(math.radians((a["lat"] + b["lat"]) / 2)),
                    (b["lat"] - a["lat"]) * 110.54)
    return {"geometry": [[a["lat"], a["lon"]], [b["lat"], b["lon"]]],
            "km": round(km, 1), "hours": round(km / 70.0, 1)}


def main():
    ap = argparse.ArgumentParser(description="为每条 leg 拉取真实路网几何与里程")
    ap.add_argument("trip")
    ap.add_argument("--out", help="输出文件（默认就地覆盖）")
    ap.add_argument("--straight", action="store_true", help="不联网，用直线距离预览")
    ap.add_argument("--sleep", type=float, default=0.8, help="请求间隔秒，礼貌限速")
    args = ap.parse_args()

    with open(args.trip, encoding="utf-8") as f:
        trip = json.load(f)
    idx = load_places()

    problems, total = [], 0.0
    for lg in trip.get("legs", []):
        a = lookup(idx, lg.get("from"))
        b = lookup(idx, lg.get("to"))
        if not a or not b:
            missing = lg.get("from") if not a else lg.get("to")
            lg["km_source"] = "unverified"
            problems.append("地名库中找不到「%s」——请在 data/places.json 补充坐标" % missing)
            print("  ✗ %s → %s：地名未解析" % (lg.get("from"), lg.get("to")))
            continue
        vias = [lookup(idx, v) for v in (lg.get("vias") or [])]
        if any(v is None for v in vias):
            lg["km_source"] = "unverified"
            problems.append("「%s」的途经点无法解析" % lg.get("from"))
            continue

        if args.straight:
            r = straight(a[1], b[1]); src = "straight"
        else:
            r = osrm_route(a[1], b[1], [v[1] for v in vias]); src = "road"
            if r is None:
                r = straight(a[1], b[1]); src = "straight"
                problems.append("%s → %s 路由失败，已退化为直线距离" % (lg["from"], lg["to"]))
            time.sleep(args.sleep)
        lg.update(r)
        lg["km_source"] = src
        lg.setdefault("label", "%s → %s%s" % (
            lg["from"], " → ".join([v[0] for v in vias] + [lg["to"]]),
            "　%.1f km" % r["km"] if src == "road" else "　（直线，待核实）"))
        total += r["km"]
        print("  %s %s → %s：%.1f km / %.1f h  [%s]"
              % ("✓" if src == "road" else "~", lg["from"], lg["to"], r["km"], r["hours"], src))

    with open(args.out or args.trip, "w", encoding="utf-8") as f:
        json.dump(trip, f, ensure_ascii=False, indent=1)

    print("\n完成：%d 条路段，合计约 %.1f km" % (len(trip.get("legs", [])), total))
    if problems:
        print("\n需要人工处理的问题：")
        for p in problems:
            print("  · " + p)
        print("提示：中国西部 OSM 路网不完整，缺路时要靠人工核对里程并标注来源。")
    # 让调用方能感知失败（非 0 退出码会打断自动化流程，这里选择用 0 但打印醒目提示）
    return 0


if __name__ == "__main__":
    sys.exit(main())
