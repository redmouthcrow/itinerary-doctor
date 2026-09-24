#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
add_places.py — 补地名库的助手。把"手工查坐标"这步从半小时压到几分钟。

为什么需要它：两次真实测试各补了 16 个点位，全是手工做的事——
开浏览器查、挑 OSM 节点、判断该用哪个、再手写 confidence 和 alias。
这是"库外单"最贵的环节，也是扩到其他走廊（W2）的前置条件。

流程（默认只查不写，人工确认后再落库）：
    1) 查候选：Overpass 按中文名精确/包含匹配，命中不到就退回原名再试一次
    2) 打分排序：名字完全相等 > 带 tourism/historic 标签 > place 节点；同名多个时列出来
    3) 给置信度：唯一且精确 = high；多个候选 = medium（要人挑）；只有包含匹配 = low
    4) --apply 才写回 data/places.json（地名库是资产，错误坐标会静默毁掉路线和里程）

用法：
    python add_places.py --names "库尔德宁,恰西,吐尔根杏花沟"
    python add_places.py --names-file _todo.txt --bbox 42.5,80.5,44.5,85.5
    python add_places.py --names "六星街" --json cand.json     # 输出候选，人工确认
    python add_places.py --apply cand.json                     # 按确认结果落库
    python add_places.py --names "X" --apply-inline            # 只采纳唯一高置信候选
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, os.pardir, "data")
ENDPOINT = "https://overpass-api.de/api/interpreter"
CHINA_BBOX = "18.0,73.0,54.0,135.5"


def overpass(query, tries=3, timeout=120):
    for i in range(tries):
        try:
            body = urllib.parse.urlencode({"data": query}).encode()
            req = urllib.request.Request(ENDPOINT, data=body,
                                         headers={"User-Agent": "itinerary-doctor/0.1"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except Exception as e:                                    # noqa: BLE001
            print("    重试 %d/%d：%s" % (i + 1, tries, e), file=sys.stderr)
            time.sleep(10 * (i + 1))
    return {"elements": []}


def query_name(name, bbox, exact=False):
    """查一个名字的候选。exact=True 用完全匹配（^name$），否则用包含匹配。"""
    pat = "^%s$" % re.escape(name) if exact else re.escape(name)
    q = ('[out:json][timeout:100];('
         'node["name"~"%s"](%s);way["name"~"%s"](%s););out center 40;' % (pat, bbox, pat, bbox))
    d = overpass(q)
    out = []
    for e in d.get("elements", []):
        t = e.get("tags", {})
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lon = e.get("lon") or (e.get("center") or {}).get("lon")
        if lat is None:
            continue
        out.append({"name": t.get("name", ""), "lat": round(lat, 5), "lon": round(lon, 5),
                    "osm": "%s/%s" % (e["type"], e["id"]),
                    "tags": {k: t[k] for k in ("place", "tourism", "natural", "historic",
                                               "amenity", "highway", "waterway") if k in t},
                    "exact": t.get("name", "") == name})
    return out


def score(c, query=""):
    """候选打分：名字完全相等最重要，其次"名字和查询一样短"（避免"可可托海镇"被
    "可可托海国际滑雪场"这类长名字挤下去），再看它像不像个景点/城镇。"""
    s = 0
    if c["exact"]:
        s += 100
    elif query:
        s -= min(24, max(0, len(c["name"]) - len(query)) * 3)   # 名字越长越不像本体
    tg = c["tags"]
    if tg.get("tourism") in ("attraction", "viewpoint", "museum", "hotel", "camp_site"):
        s += 30
    if tg.get("historic"):
        s += 25
    if tg.get("natural"):
        s += 20
    if tg.get("place") in ("city", "town", "village", "hamlet"):
        s += 15
    if tg.get("place") == "locality":
        s += 10
    if tg.get("amenity") == "place_of_worship":
        s -= 10
    return s


def pick(name, bbox, sleep=6):
    """返回 (选中候选, 全部候选, 置信度)。"""
    cands = query_name(name, bbox, exact=True)
    if not cands:
        time.sleep(sleep)
        cands = query_name(name, bbox, exact=False)
    if not cands:
        return None, [], "low"
    cands.sort(key=lambda c: score(c, name), reverse=True)
    best = cands[0]
    if len(cands) == 1 and best["exact"]:
        conf = "high"
    elif best["exact"]:
        conf = "medium"          # 同名多处，要人挑
    else:
        conf = "low"             # 只有包含匹配，可能完全不对
    return best, cands, conf


def load_places():
    with open(os.path.join(DATA, "places.json"), encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="补地名库：查候选 → 确认 → 落库")
    ap.add_argument("--names", help="逗号分隔的地名")
    ap.add_argument("--names-file", help="每行一个地名")
    ap.add_argument("--bbox", default=CHINA_BBOX,
                    help="搜索范围 south,west,north,east（默认全国；给走廊范围会快很多）")
    ap.add_argument("--sleep", type=float, default=6.0, help="每次查询间隔秒")
    ap.add_argument("--json", help="把候选写到这个文件供人工确认")
    ap.add_argument("--apply", help="按确认过的 JSON 落库")
    ap.add_argument("--apply-inline", action="store_true",
                    help="只采纳唯一 high 置信候选，直接落库（有风险，谨慎）")
    ap.add_argument("--force", action="store_true", help="覆盖 places.json 里已存在的同名点位")
    args = ap.parse_args()

    if args.apply:
        with open(args.apply, encoding="utf-8") as f:
            confirmed = json.load(f)
        db = load_places()
        n = 0
        for item in confirmed:
            name = item.get("name")
            if not name or item.get("lat") is None:
                continue
            if name in db["places"] and not args.force:
                print("  跳过（已存在）：%s" % name)
                continue
            rec = {"lat": item["lat"], "lon": item["lon"],
                   "confidence": item.get("confidence", "medium")}
            if item.get("osm"):
                rec["osm"] = item["osm"]
            if item.get("alias"):
                rec["alias"] = item["alias"]
            if item.get("note"):
                rec["note"] = item["note"]
            db["places"][name] = rec
            n += 1
        with open(os.path.join(DATA, "places.json"), "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
        print("已落库 %d 个点位，现共 %d 个" % (n, len(db["places"])))
        print("提示：跑一次 python scripts/validate_constraints.py 确认约束挂载仍然成立")
        return 0

    names = []
    if args.names:
        names += [x.strip() for x in args.names.split(",") if x.strip()]
    if args.names_file:
        with open(args.names_file, encoding="utf-8") as f:
            names += [x.strip() for x in f if x.strip()]
    if not names:
        sys.exit("请用 --names 或 --names-file 给出要查的地名")

    db = load_places()
    existing = set(db["places"].keys())
    results = []
    print("查 %d 个地名（范围 %s）\n" % (len(names), args.bbox))
    for name in names:
        if name in existing:
            print("  · %s —— 已在地名库里，跳过" % name)
            continue
        best, cands, conf = pick(name, args.bbox, args.sleep)
        if not best:
            print("  ✗ %-14s 没查到候选 —— 需要手工查或换写法（试试全称/别名）" % name)
            results.append({"name": name, "lat": None, "lon": None, "confidence": "low",
                            "note": "OSM 无候选，需手工补"})
            continue
        mark = {"high": "✓", "medium": "?", "low": "!"}[conf]
        print("  %s %-14s → %-22s %s,%s  [%s 置信] %s"
              % (mark, name, best["name"][:22], best["lat"], best["lon"], conf,
                 json.dumps(best["tags"], ensure_ascii=False)))
        if len(cands) > 1:
            for c in cands[1:4]:
                print("       另一候选：%-22s %s,%s %s"
                      % (c["name"][:22], c["lat"], c["lon"], json.dumps(c["tags"], ensure_ascii=False)))
        rec = {"name": name, "lat": best["lat"], "lon": best["lon"],
               "confidence": conf, "osm": best["osm"]}
        if best["name"] != name:
            rec["alias"] = [best["name"]]
        results.append(rec)
        if args.apply_inline:
            if conf != "high":
                print("       （非 high 置信，--apply-inline 不采纳，需人工确认）")
        time.sleep(args.sleep)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
        print("\n候选已写入 %s —— 请人工核对后再 --apply %s" % (args.json, args.json))
    else:
        print("\n（未写文件。加 --json cand.json 保存候选，人工确认后 --apply cand.json）")
    hi = len([r for r in results if r.get("confidence") == "high"])
    print("小结：high %d / medium %d / low %d —— medium 和 low 必须人工核验"
          % (hi, len([r for r in results if r.get("confidence") == "medium"]),
             len([r for r in results if r.get("confidence") == "low"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
