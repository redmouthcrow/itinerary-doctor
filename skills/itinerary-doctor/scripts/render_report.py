#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_report.py — Trip Schema → Markdown 报告（退改决策表 / 避坑清单 / 逐日计划）。

这一份是"能直接发给客户或同事"的文字交付物，也是把渲染器生成不出来的判断
（为什么这么改、哪些是待核实）显式写下来的地方。

用法：
    python render_report.py trip.json -o 行程报告.md
"""
import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _schema import load_trip, resolve_placeholders, audit     # noqa: E402

LEVEL_LABEL = {"hard": "硬约束", "stale": "待核实（数据已过期）", "soft": "参考"}
LEVEL_ORDER = ["hard", "stale", "soft"]
ACTION_LABEL = {"keep": "保持不变", "cancel": "退订", "move": "改期", "new": "新订"}


def main():
    ap = argparse.ArgumentParser(description="trip.json → Markdown 报告")
    ap.add_argument("trip")
    ap.add_argument("-o", "--out", default="行程报告.md")
    args = ap.parse_args()

    trip = load_trip(args.trip)
    resolve_placeholders(trip)
    for msg in audit(trip):
        print("  ! " + msg, file=sys.stderr)
    t = trip.get("trip", {})
    days = trip.get("days", [])
    L = []
    A = L.append

    A("# %s" % t.get("title", "行程报告"))
    if t.get("subtitle"):
        A("")
        A(t["subtitle"])
    if t.get("facts"):
        A("")
        A("　|　".join(t["facts"]))
    for n in t.get("notes", []):
        A("")
        A("> %s" % n)

    # ---- 订房 / 退改决策 ----
    if trip.get("booking_actions"):
        A("")
        A("## 一、订房与退改决策")
        A("")
        A("| 动作 | 范围 | 具体 |")
        A("|---|---|---|")
        for b in trip["booking_actions"]:
            A("| **%s** | %s | %s |" % (ACTION_LABEL.get(b.get("kind"), b.get("kind", "")),
                                        b.get("label", ""), b.get("detail", "")))
        A("")
        A("> 处理顺序建议：先订稀缺资源（旺季房源 / 限量门票），再谈改期，最后退订——"
          "退订越晚越可能错过免费取消窗口。")

    # ---- 逐日计划 ----
    A("")
    A("## 二、逐日计划")
    A("")
    A("| 天 | 日期 | 路线 | 里程 / 驾驶 | 住宿 | 订房动作 |")
    A("|---|---|---|---|---|---|")
    for d in days:
        A("| %s | %s | %s | %s | %s | %s |" % (
            d.get("id", ""), d.get("date", ""), d.get("route", ""),
            d.get("km_text") or d.get("km") or "—", d.get("lodging") or "—",
            d.get("lodging_action") or ""))

    # ---- 避坑清单 ----
    A("")
    A("## 三、避坑清单（按日期）")
    A("")
    grouped = {}
    for d in days:
        for a in d.get("alerts", []) or []:
            grouped.setdefault(a.get("level", "soft"), []).append((d, a))
    if not grouped:
        A("_本次没有命中约束库记录。若行程确实涉及限流 / 预约 / 封路风险，"
          "请检查 data/constraints.json 是否覆盖该地区。_")
    for lvl in LEVEL_ORDER:
        items = grouped.get(lvl)
        if not items:
            continue
        A("")
        A("### %s" % LEVEL_LABEL.get(lvl, lvl))
        A("")
        for d, a in items:
            A("- **%s（%s）** %s" % (d.get("id", ""), d.get("date", ""), a.get("text", "")))
            meta = []
            if a.get("source"):
                meta.append("来源：%s" % a["source"])
            if a.get("verified_at"):
                meta.append("核实日期 %s（%d 天前）" % (a["verified_at"], a.get("age_days", 0)))
            if a.get("impact"):
                meta.append("影响：%s" % a["impact"])
            if meta:
                A("  - " + "；".join(meta))

    # ---- 时令窗口核对（有窗口数据的点位才判定）----
    try:
        from match_season import analyze, markdown_section
        season_md = markdown_section(analyze(trip))
    except Exception as e:                                        # noqa: BLE001
        season_md = None
        print("  ! 时令核对跳过：%s" % e, file=sys.stderr)
    if season_md:
        A("")
        A("## 四、时令窗口核对")
        A("")
        A(season_md)

    # ---- 待核实与风险 ----
    A("")
    A("## 五、待核实与风险项")
    A("")
    cm = trip.get("constraints_meta", {})
    if cm:
        A("- 约束库来源：%s（共 %d 条，评估日期 %s）"
          % (cm.get("active_source", "?"), cm.get("record_count", 0), cm.get("evaluated_on", "?")))
        if cm.get("stale"):
            A("- **其中 %d 条已超过 %d 天时效，标记为待核实**"
              % (cm["stale"], cm.get("stale_after_days", 60)))
    unverified = [l for l in trip.get("legs", []) if l.get("km_source") not in ("road", None)]
    excluded = [l for l in trip.get("legs", []) if l.get("counts_toward_total") is False]
    if excluded:
        A("- 有 %d 段未计入总里程（区间车 / 备选路线）：%s"
          % (len(excluded), "、".join("%s→%s" % (l.get("from"), l.get("to")) for l in excluded[:5])))
    if unverified:
        A("- **以下路段里程未经真实路网核实**（渲染图里也会标注）：")
        for l in unverified:
            A("  - %s → %s：%s km（来源：%s）"
              % (l.get("from"), l.get("to"), l.get("km"), l.get("km_source")))
    draft = trip.get("_draft", {})
    if draft:
        A("- 骨架来源：%s，以下内容必须人工复核：" % draft.get("generated_by", "?"))
        for m in draft.get("must_review", []):
            A("  - %s" % m)

    # ---- 免责 ----
    A("")
    A("## 六、免责与核实提示")
    A("")
    A("- 政策、票价、开放时间与预约规则会随时调整，**以景区当日公告为准**。")
    A("- 建议在出发前 24 小时再核对一次：预约是否出票、道路是否封闭、天气是否影响行程。")
    if cm.get("disclaimer"):
        A("- %s" % cm["disclaimer"])
    A("")
    A("---")
    A("")
    A("_生成时间 %s ｜ Generated by itinerary-doctor_"
      % dt.datetime.now().strftime("%Y-%m-%d %H:%M"))

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    n_alerts = sum(len(d.get("alerts", []) or []) for d in days)
    print("已生成 %s（%d 天 · %d 条避坑提示 · %d 条待核实路段）"
          % (args.out, len(days), n_alerts, len(unverified)))


if __name__ == "__main__":
    main()
