#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_constraints.py — 把约束库挂到行程上，产出 alerts（这是"避坑"的来源）。

三级取数（这是本项目的核心设计：skill 安装后不会重装，硬编码的时效数据
从装上那天就开始过期）：
    1. 远程约束库（--source URL 或环境变量 ITINERARY_CONSTRAINTS_URL）
    2. 本地缓存（~/.cache/itinerary-doctor/constraints.json，上次成功拉取的）
    3. 内置快照（data/constraints.json，随 skill 分发）
无论用哪一级，都会在输出里写明 active_source 与数据日期；超过 stale_after_days
的记录会被标成「待核实」，绝不静默当成本事实。

用法：
    python fetch_constraints.py trip.json
    python fetch_constraints.py trip.json --source https://raw.githubusercontent.com/OWNER/REPO/main/constraints.json
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, os.pardir, "data")
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "itinerary-doctor")
CACHE = os.path.join(CACHE_DIR, "constraints.json")
ENV_URL = "ITINERARY_CONSTRAINTS_URL"


def fetch_remote(url):
    req = urllib.request.Request(url, headers={"User-Agent": "itinerary-doctor/0.1"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def load_db(source):
    """按 远程 → 缓存 → 内置 的顺序取数，返回 (db, 来源说明)。"""
    if source:
        try:
            db = fetch_remote(source)
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(CACHE, "w", encoding="utf-8") as f:
                json.dump(db, f, ensure_ascii=False, indent=1)
            return db, "远程约束库 %s（已写入缓存）" % source
        except Exception as e:                                    # noqa: BLE001
            print("  ! 远程约束库拉取失败：%s" % e, file=sys.stderr)
    if os.path.exists(CACHE):
        try:
            with open(CACHE, encoding="utf-8") as f:
                db = json.load(f)
            age = (dt.datetime.now() - dt.datetime.fromtimestamp(os.path.getmtime(CACHE))).days
            return db, "本地缓存（%d 天前拉取）" % age
        except Exception as e:                                    # noqa: BLE001
            print("  ! 缓存损坏：%s" % e, file=sys.stderr)
    with open(os.path.join(DATA, "constraints.json"), encoding="utf-8") as f:
        return json.load(f), "内置快照（随 skill 分发，可能过期）"


def age_days(verified_at, today=None):
    try:
        d = dt.date.fromisoformat(str(verified_at)[:10])
    except Exception:                                             # noqa: BLE001
        return 9999
    return ((today or dt.date.today()) - d).days


def matches(constraint, haystack):
    """约束挂在 place 上；行程文本里出现该地名（或反向包含）即命中。"""
    place = (constraint.get("place") or "").strip()
    if not place:
        return False
    for token in [t.strip() for t in place.replace("、", "/").split("/") if t.strip()]:
        if token in haystack or (len(haystack) >= 2 and haystack in token):
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description="为行程挂载约束与告警")
    ap.add_argument("trip")
    ap.add_argument("--source", default=os.environ.get(ENV_URL),
                    help="远程约束库 URL（也可用环境变量 %s）" % ENV_URL)
    ap.add_argument("--date", help="以哪一天为基准判断时效（默认今天），格式 YYYY-MM-DD")
    args = ap.parse_args()

    with open(args.trip, encoding="utf-8") as f:
        trip = json.load(f)
    db, src_desc = load_db(args.source)
    stale_after = int(db.get("meta", {}).get("stale_after_days", 60))
    today = dt.date.fromisoformat(args.date) if args.date else dt.date.today()

    constraints = db.get("constraints", [])
    print("约束库来源：%s（%d 条记录）" % (src_desc, len(constraints)))

    used, stale = 0, 0
    for day in trip.get("days", []):
        hay = " ".join(str(day.get(k) or "") for k in ("route", "title", "tips", "lodging"))
        hit = []
        for c in constraints:
            if not matches(c, hay):
                continue
            a = age_days(c.get("verified_at"), today)
            level = c.get("level", "soft")
            if a > stale_after:
                stale += 1
                level = "stale"
            hit.append({
                "id": c.get("id"), "category": c.get("category"), "level": level,
                "text": c.get("text"), "source": c.get("source"),
                "verified_at": c.get("verified_at"), "age_days": a,
                "impact": c.get("impact"),
            })
        if hit:
            day["alerts"] = hit
            used += len(hit)
            hard = sum(1 for h in hit if h["level"] == "hard")
            print("  %s（%s）：%d 条告警，其中硬约束 %d 条"
                  % (day.get("id"), day.get("date"), len(hit), hard))

    trip.setdefault("constraints_meta", {})
    trip["constraints_meta"].update({
        "active_source": src_desc, "record_count": len(constraints),
        "stale_after_days": stale_after, "evaluated_on": today.isoformat(),
        "attached": used, "stale": stale,
        "disclaimer": db.get("meta", {}).get("disclaimer", ""),
    })

    with open(args.trip, "w", encoding="utf-8") as f:
        json.dump(trip, f, ensure_ascii=False, indent=1)
    print("已写回 %s（挂载 %d 条，其中 %d 条已过时效、标记为待核实）" % (args.trip, used, stale))
    if used == 0:
        print("提示：没有任何约束命中——先确认 places.json 与行程里的地名写法一致。")


if __name__ == "__main__":
    main()
