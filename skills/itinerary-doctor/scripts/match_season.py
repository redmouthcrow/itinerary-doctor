#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
match_season.py — 把行程日期和时令窗口做程序化比对，输出"哪些点位在季节上对/不对"。

为什么需要它：真实测试里，"某点位这个季节值不值得去"完全靠 agent 逐条读约束文本自己算。
库里现在有 12 条 season 记录、11 条带结构化 window，这件事应该由代码做，
agent 只负责解释与决策。

判定分五挡：
    peak      落在最佳窗口里
    shoulder  落在可行窗口里（能去，但已过或未到最佳）
    off       明确不宜（数据里标了 kind=off，例如吐鲁番 6—7 月的地表高温）
    none      该点位有时令数据，但日期不在任何窗口内 —— 这才是要警惕的
    no-data   库里没有该点位的时令数据（城市/桥梁/街道常属此类）—— 不判定、不算问题

对 none / off 的点位，按坐标找 350 km 内的**同期可替代点位**。

用法：
    python match_season.py trip.json                  # 诊断表
    python match_season.py trip.json --json out.json  # 机器可读
    python match_season.py --places 夏塔,琼库什台 --on 2026-04-22
    python match_season.py trip.json --strict         # 有 none/off 就返回非 0
"""
import argparse
import datetime as dt
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, os.pardir, "data")
sys.path.insert(0, HERE)
from _schema import load_trip                                   # noqa: E402

LABEL = {"peak": "最佳", "shoulder": "可行", "off": "不宜",
         "none": "不在任何窗口内", "no-data": "无时令数据"}
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
MD_RE = re.compile(r'^(\d{1,2})-(\d{1,2})$')


# ---------------------------------------------------------------- 数据
def load_json(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def place_index(places):
    """{匹配键lower: (规范名, meta)}，含别名。"""
    idx = {}
    for name, v in places.items():
        idx[name.lower()] = (name, v)
        for a in v.get("alias", []) or []:
            idx[str(a).lower()] = (name, v)
    return idx


def match_constraints(place_name, constraints, idx):
    """找出挂在某个点位上的时令约束（与 fetch_constraints 同口径的双向包含匹配）。"""
    hits, low = [], place_name.lower()
    for c in constraints:
        if c.get("category") != "season" or not c.get("window"):
            continue
        if c.get("scope") == "meta":            # 决策说明不参与逐点判定
            continue
        for tok in re.split(r"[/、]", str(c.get("place") or "")):
            t = tok.strip().lower()
            if not t:
                continue
            canon = idx.get(t, (None, None))[0]
            if t in low or low in t or (canon and (canon == place_name or low in canon.lower())):
                hits.append(c)
                break
    return hits


# ---------------------------------------------------------------- 窗口
def _md(s):
    m = MD_RE.match(str(s).strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def in_window(win, day):
    """判断 (月,日) 是否落在窗口里；支持跨年（如 11-01 → 03-31）。"""
    a, b = _md(win.get("from")), _md(win.get("to"))
    if not a or not b:
        return False
    cur = (day.month, day.day)
    if a <= b:
        return a <= cur <= b
    return cur >= a or cur <= b


def judge(day, windows):
    """返回 (status, 命中窗口)。优先级 peak > shoulder > off > none。"""
    if not windows:
        return "none", None
    for want in ("peak", "shoulder"):
        for w in windows:
            if w.get("kind", "peak") == want and in_window(w, day):
                return want, w
    for w in windows:
        if w.get("kind") == "off" and in_window(w, day):
            return "off", w
    return "none", None


# ---------------------------------------------------------------- 替代点位
def km_between(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371 * math.asin(math.sqrt(h))


def alternatives(place_name, date, places, constraints, idx, limit_km=350, top=4):
    """同一日期落在窗口内的邻近点位（按距离排序）。"""
    base = places.get(place_name)
    if not base or base.get("lat") is None:
        return []
    out = []
    for name, meta in places.items():
        if name == place_name or meta.get("lat") is None:
            continue
        d = km_between(base, meta)
        if d > limit_km:
            continue
        st, w = judge(date, [x for c in match_constraints(name, constraints, idx)
                             for x in c["window"]])
        if st == "peak":
            out.append({"name": name, "km": round(d), "label": (w or {}).get("label", "")})
    out.sort(key=lambda x: x["km"])
    return out[:top]


# ---------------------------------------------------------------- 主流程
def places_of_day(trip, day, idx):
    """当天涉及的点位名（stops 与 legs 两头取，去重）。"""
    names = []
    for s in trip.get("stops", []) or []:
        days = ([s["day"]] if s.get("day") else []) + list(s.get("days") or [])
        if day.get("id") in days:
            names.append(s.get("name"))
    for l in trip.get("legs", []) or []:
        if l.get("day") == day.get("id") and l.get("counts_toward_total") is not False:
            names += [l.get("from"), l.get("to")]
    out = []
    for n in names:
        if not n:
            continue
        canon = idx.get(str(n).lower(), (n, None))[0]
        if canon not in out:
            out.append(canon)
    return out


def analyze(trip):
    """返回逐 (日期, 点位) 的判定行。render_report 直接复用这个函数。"""
    places = load_json("places.json")["places"]
    constraints = load_json("constraints.json")["constraints"]
    idx = place_index(places)
    rows = []
    for day in trip.get("days", []) or []:
        ds = str(day.get("date") or "")
        if not DATE_RE.match(ds):
            continue
        date = dt.date.fromisoformat(ds)
        for name in places_of_day(trip, day, idx):
            cons = match_constraints(name, constraints, idx)
            windows = [w for c in cons for w in c["window"]]
            st, w = judge(date, windows)
            row = {"day": day.get("id"), "date": ds, "place": name,
                   "has_window": bool(windows),
                   "status": st if windows else "no-data",
                   "label": (w or {}).get("label", ""),
                   "source": cons[0].get("source") if cons else None,
                   "verified_at": cons[0].get("verified_at") if cons else None}
            if windows and st in ("none", "off"):
                row["alternatives"] = alternatives(name, date, places, constraints, idx)
            rows.append(row)
    return rows


def markdown_section(rows):
    """生成可嵌进报告的 markdown 片段；没有可判定的点位时返回 None。"""
    judged = [r for r in rows if r["has_window"]]
    if not judged:
        return None
    lines = ["| 日期 | 点位 | 时令判定 | 窗口 |", "|---|---|---|---|"]
    for r in judged:
        lines.append("| %s | %s | %s | %s |" % (r["date"], r["place"], LABEL[r["status"]],
                                                r["label"] or "—"))
    flagged = [r for r in judged if r["status"] in ("none", "off")]
    if flagged:
        lines += ["", "**需要解释或调整：**"]
        for r in flagged:
            alts = r.get("alternatives") or []
            tail = ("；同期可替代：" + "、".join("%s（%d km，%s）" % (a["name"], a["km"], a["label"])
                                             for a in alts)) if alts else "（附近没有窗口覆盖的点位）"
            lines.append("- %s %s：%s%s" % (r["date"], r["place"], LABEL[r["status"]], tail))
    nodata = sorted({r["place"] for r in rows if not r["has_window"]})
    if nodata:
        lines += ["", "_无时令数据的点位（不做季节判定）：%s_" % "、".join(nodata)]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="行程日期 × 时令窗口 比对")
    ap.add_argument("trip", nargs="?", help="trip.json（也可只用 --places/--on）")
    ap.add_argument("--places", help="逗号分隔的点位名（与 --on 搭配）")
    ap.add_argument("--on", help="日期 YYYY-MM-DD（与 --places 搭配）")
    ap.add_argument("--json", help="结果写到这个文件")
    ap.add_argument("--strict", action="store_true", help="有 none/off 就返回非 0")
    args = ap.parse_args()

    if args.places and args.on:
        trip = {"days": [{"id": "—", "date": args.on}],
                "stops": [{"name": n.strip(), "day": "—"}
                          for n in args.places.split(",") if n.strip()]}
        rows = analyze(trip)
    elif args.trip:
        rows = analyze(load_trip(args.trip))
    else:
        sys.exit("给个 trip.json，或者用 --places 和 --on")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)

    judged = [r for r in rows if r["has_window"]]
    print("时令窗口核对：%d 个点位，其中 %d 个有时令数据可判定" % (len(rows), len(judged)))
    for r in judged:
        print("  %-4s %s  %-16s %-8s %s" % (r["day"], r["date"], r["place"],
                                            LABEL[r["status"]], r["label"]))
    nodata = sorted({r["place"] for r in rows if not r["has_window"]})
    if nodata:
        print("  （无时令数据的点，不判定：%s）" % "、".join(nodata[:12]))
    flagged = [r for r in judged if r["status"] in ("none", "off")]
    if flagged:
        print("\n需要解释或调整的 %d 项：" % len(flagged))
        for r in flagged:
            print("  · %s %s —— %s（%s）" % (r["date"], r["place"], LABEL[r["status"]], r["label"]))
            alts = r.get("alternatives") or []
            if alts:
                print("      同期可替代：%s" % "、".join(
                    "%s(%d km, %s)" % (a["name"], a["km"], a["label"]) for a in alts))
            else:
                print("      （附近没有窗口覆盖的点位，或库里缺该地时令数据——需要补库）")
    else:
        print("\n所有有时令数据的点位都落在窗口内 ✓")
    return 1 if (args.strict and flagged) else 0


if __name__ == "__main__":
    sys.exit(main())
