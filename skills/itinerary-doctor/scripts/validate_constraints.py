#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_constraints.py — 约束库自检。改完 constraints.json 一定要跑一遍。

为什么需要它（东疆那单踩过的坑）：
  我把「吐鲁番」相关约束的 place 写成 `吐鲁番`，而行程里只写点位名（交河故城/火焰山），
  结果 fetch_constraints **一条都没挂上**，输出只有一句轻飘飘的"没有任何约束命中"——
  这种静默失败最危险：报告看起来正常，其实避坑清单是空的。

校验项：
  1. 必填字段齐全（id / place / category / level / text / source / verified_at）
  2. level 取值合法、category 在 categories 里定义过
  3. verified_at 是可解析日期且不在未来
  4. id 不重复
  5. **place 必须能匹配到 places.json 的点位或别名**；
     匹配不到就必须在 meta.place_allowlist 里显式声明（用来放"公路/区域/全国"这类
     本来就不该进地名库的东西）。这条是防 0 命中的关键。
  6. impact 建议填写（体检报告里最值钱的就是它）
  7. 时效：verified_at 超过 stale_after_days 的记录会被列出来（这些在输出里会降级为待核实）

用法：
    python validate_constraints.py                 # 校验 data/constraints.json
    python validate_constraints.py --strict        # 有警告也返回非 0（用于 CI）
    python validate_constraints.py --file x.json
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, os.pardir, "data")
LEVELS = ("hard", "soft", "stale")
REQUIRED = ("id", "place", "category", "level", "text", "source", "verified_at")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def place_keys(places):
    keys = set()
    for name, v in places.items():
        keys.add(name.lower())
        for a in v.get("alias", []) or []:
            keys.add(str(a).lower())
    return keys


def unmatched_tokens(place, keys):
    """把 place 按 / 和 、 拆开，返回匹配不到地名库的片段（与 fetch_constraints 同口径）。

    先剥掉括号内容：「西藏全境（重点：阿里、羌塘）」应看成「西藏全境」+「阿里」，
    而不是一个整体去匹配。
    """
    bad = []
    cleaned = re.sub(r'[（(【\[][^）)】\]]*[）)】\]]', "", str(place or ""))
    for tok in re.split(r"[/、·・\s]+", cleaned):
        t = tok.strip().lower()
        if not t:
            continue
        if not any(t in k or k in t for k in keys):
            bad.append(tok.strip())
    return bad


def main():
    ap = argparse.ArgumentParser(description="约束库自检")
    ap.add_argument("--file", help="默认 data/constraints.json")
    ap.add_argument("--places", help="默认 data/places.json")
    ap.add_argument("--strict", action="store_true", help="有警告也算失败（CI 用）")
    ap.add_argument("--today", help="以哪一天为基准判断时效，YYYY-MM-DD")
    args = ap.parse_args()

    cpath = args.file or os.path.join(DATA, "constraints.json")
    ppath = args.places or os.path.join(DATA, "places.json")
    db = load(cpath)
    places = load(ppath)["places"]
    meta = db.get("meta", {})
    cats = set((db.get("categories") or {}).keys())
    allow = {x.lower() for x in (meta.get("place_allowlist") or [])}
    stale_after = int(meta.get("stale_after_days", 60))
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()

    errors, warnings, stale = [], [], []
    seen = {}
    keys = place_keys(places)

    for i, c in enumerate(db.get("constraints", [])):
        cid = c.get("id") or "第 %d 条" % (i + 1)
        for f in REQUIRED:
            if not c.get(f):
                errors.append("%s：缺字段 %s" % (cid, f))
        if c.get("level") and c["level"] not in LEVELS:
            errors.append("%s：level=%r 不在 %s" % (cid, c["level"], "/".join(LEVELS)))
        if c.get("category") and cats and c["category"] not in cats:
            errors.append("%s：category=%r 未在 categories 里定义" % (cid, c["category"]))
        va = str(c.get("verified_at") or "")
        if va:
            try:
                d = dt.date.fromisoformat(va)
                if d > today:
                    errors.append("%s：verified_at=%s 是未来日期" % (cid, va))
                elif (today - d).days > stale_after:
                    stale.append("%s：核实于 %s（%d 天前）" % (cid, va, (today - d).days))
            except ValueError:
                errors.append("%s：verified_at=%r 不是 YYYY-MM-DD" % (cid, va))
        if cid in seen:
            errors.append("%s：id 重复（与第 %d 条冲突）" % (cid, seen[cid]))
        seen[cid] = i + 1
        if not c.get("impact"):
            warnings.append("%s：建议补 impact（体检报告里最值钱的一栏）" % cid)
        place_str = str(c.get("place") or "")
        if place_str.strip().lower() in allow:      # 整串显式声明过（如「中俄、中蒙边境全线」）
            continue
        bad = unmatched_tokens(place_str, keys)
        if bad and not any(b.lower() in allow for b in bad):
            errors.append("%s：place 里的 %s 既不在 places.json、也不在 meta.place_allowlist —— "
                          "这种约束在真实行程里可能一条都挂不上" % (cid, "、".join(bad)))
        if len(str(c.get("text") or "")) < 12:
            warnings.append("%s：text 太短，可能没写清具体数字/渠道" % cid)

    n = len(db.get("constraints", []))
    by_level, by_cat = {}, {}
    for c in db.get("constraints", []):
        by_level[c.get("level")] = by_level.get(c.get("level"), 0) + 1
        by_cat[c.get("category")] = by_cat.get(c.get("category"), 0) + 1
    print("约束库 %s" % cpath)
    print("  共 %d 条 ｜ 点位库 %d 个 ｜ 时效阈值 %d 天 ｜ 基准日 %s"
          % (n, len(places), stale_after, today.isoformat()))
    print("  等级分布：%s" % "、".join("%s %d" % (k, v) for k, v in sorted(by_level.items())))
    print("  类型分布：%s" % "、".join("%s %d" % (k, v) for k, v in sorted(by_cat.items())))
    for label, items in (("错误", errors), ("警告", warnings), ("已过时效", stale)):
        if items:
            print("\n%s（%d）：" % (label, len(items)))
            for x in items[:20]:
                print("  · " + x)
            if len(items) > 20:
                print("  · …还有 %d 条" % (len(items) - 20))
    if not (errors or warnings or stale):
        print("\n全部通过 ✓")
    return 1 if (errors or (args.strict and warnings)) else 0


if __name__ == "__main__":
    sys.exit(main())
