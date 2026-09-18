#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parse_itinerary.py — 把一份"人写的行程表"（xlsx / csv / markdown 表格 / 纯文本）
转成 Trip Schema 骨架（trip.json）。

它只做**机械转换**，不做判断：
  * xlsx 是零依赖读取（zipfile + xml，需要 openpyxl 时也能用）
  * 修掉两个真实踩过的坑：Excel 序列号日期（46290 不是 "46290"）、混在时间列里的
    datetime 值
  * 用 data/places.json 做贪心匹配，猜出 draft 版 stops/legs —— 一定是 draft，
    必须人工/agent 复核，脚本会把不确定项列出来

用法：
    python parse_itinerary.py 行程.xlsx -o trip.json
    python parse_itinerary.py 行程.md -o trip.json
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, os.pardir, "data")
XL_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

DATE_HINT = ("日期", "date", "时间", "day")
ROUTE_HINT = ("路线", "行程", "安排", "玩法", "route", "itinerary")
TRAVEL_HINT = ("交通", "驾驶", "车程", "耗时", "travel", "drive")
INTENSITY_HINT = ("强度", "intensity", "强度")
LODGING_HINT = ("住宿", "酒店", "住", "hotel", "lodging")
DEPART_HINT = ("出发", "depart", "时间")

EXCEL_EPOCH = dt.datetime(1899, 12, 30)


# ------------------------------------------------------------------ xlsx
def read_xlsx(path):
    """零依赖读取第一个工作表，返回 list[dict[列字母] = 值]。"""
    try:
        import openpyxl                                         # type: ignore
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb.worksheets[0]
        rows = []
        for r in ws.iter_rows():
            rows.append({c.column_letter: c.value for c in r if c.value is not None})
        return rows, "openpyxl"
    except ImportError:
        pass
    import zipfile
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(XL_NS + "si"):
                shared.append("".join(t.text or "" for t in si.iter(XL_NS + "t")))
        sheets = sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        if not sheets:
            sys.exit("这个 xlsx 里没找到工作表")
        root = ET.fromstring(z.read(sheets[0]))
    rows = []
    for row in root.iter(XL_NS + "row"):
        cells = {}
        for c in row.findall(XL_NS + "c"):
            ref, typ = c.get("r") or "", c.get("t")
            v, is_el = c.find(XL_NS + "v"), c.find(XL_NS + "is")
            if typ == "s" and v is not None and v.text is not None:
                val = shared[int(v.text)]
            elif typ == "inlineStr" and is_el is not None:
                val = "".join(t.text or "" for t in is_el.iter(XL_NS + "t"))
            elif v is not None and v.text is not None:
                try:
                    val = float(v.text)
                except ValueError:
                    val = v.text
            else:
                val = None
            if val is not None and val != "":
                cells["".join(ch for ch in ref if ch.isalpha())] = val
        rows.append(cells)
    return rows, "内置读取器"


def read_table(path):
    """csv / markdown 表格 / 制表符文本 → 与 read_xlsx 同构的行列表。"""
    with open(path, encoding="utf-8") as f:
        lines = [l.rstrip("\n") for l in f if l.strip()]
    rows = []
    for line in lines:
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
                continue
        else:
            cells = re.split(r"\t|,|;", line)
        rows.append({chr(65 + i): c for i, c in enumerate(cells) if c not in ("", None)})
    return rows, "文本表格"


# ------------------------------------------------------------------ 值还原
def as_date(v, is_date_col=False):
    """Excel 序列号 —— 这是实际最常踩的坑（46290 必须变成 2026-09-25）。"""
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, dt.time):
        return v.strftime("%H:%M")
    if isinstance(v, (int, float)):
        f = float(v)
        if 20000 <= f <= 60000 and is_date_col:
            return (EXCEL_EPOCH + dt.timedelta(days=f)).strftime("%Y-%m-%d")
        if 0 < f < 1:                                            # 纯时间（当天零点起的比例）
            m = int(round(f * 24 * 60))
            return "%02d:%02d" % (m // 60, m % 60)
    return str(v).strip() if v is not None else ""


def cell(row, col):
    return row.get(col) if col else None


def find_header(rows):
    for i, r in enumerate(rows):
        vals = [str(v) for v in r.values()]
        joined = " ".join(vals).lower()
        if sum(1 for h in ("日期", "路线", "住宿", "行程", "day") if h in joined) >= 2:
            return i
    return None


def map_columns(header):
    cols = {}
    for c, v in header.items():
        s = str(v).strip().lower()
        for hints, key in ((DATE_HINT, "date"), (ROUTE_HINT, "route"), (TRAVEL_HINT, "travel"),
                          (INTENSITY_HINT, "intensity"), (LODGING_HINT, "lodging"),
                          (DEPART_HINT, "depart")):
            if key in cols:
                continue
            if any(h in s for h in hints):
                cols[key] = c
                break
    return cols


# ------------------------------------------------------------------ 地名匹配
def load_gazetteer():
    with open(os.path.join(DATA, "places.json"), encoding="utf-8") as f:
        raw = json.load(f)["places"]
    keys = []
    for name, v in raw.items():
        keys.append((name, name, v))
        for a in v.get("alias", []) or []:
            keys.append((a, name, v))
    keys.sort(key=lambda x: -len(x[0]))                          # 长名优先，避免"喀纳斯"吃掉"喀纳斯湖"
    return keys


def extract_places(text, keys):
    """在路线文本里按出现顺序贪心匹配地名（draft，必须复核）。"""
    found, used = [], []
    for name, canon, meta in keys:
        for m in re.finditer(re.escape(name), text):
            span = (m.start(), m.end())
            if any(not (span[1] <= s or span[0] >= e) for s, e in used):
                continue
            used.append(span)
            found.append((m.start(), canon, meta))
    found.sort(key=lambda x: x[0])
    out = []
    for _, canon, meta in found:
        if not out or out[-1][0] != canon:
            out.append((canon, meta))
    return out


def main():
    ap = argparse.ArgumentParser(description="行程表 → Trip Schema 骨架")
    ap.add_argument("input", help="xlsx / csv / md / txt")
    ap.add_argument("-o", "--out", default="trip.json")
    ap.add_argument("--title", help="行程标题（默认取文件名）")
    ap.add_argument("--start", help="出发日期 YYYY-MM-DD（若表里日期无法解析时用于推算）")
    args = ap.parse_args()

    if args.input.lower().endswith((".xlsx", ".xlsm")):
        rows, how = read_xlsx(args.input)
    else:
        rows, how = read_table(args.input)
    print("读取 %s（%s，%d 行）" % (args.input, how, len(rows)))

    hi = find_header(rows)
    if hi is None:
        sys.exit("没找到表头行：需要至少包含「日期 / 路线 / 住宿」中的两列。")
    cols = map_columns(rows[hi])
    print("识别到列：%s" % "、".join("%s=%s" % (k, v) for k, v in cols.items()))
    if "date" not in cols or "route" not in cols:
        sys.exit("至少需要识别出「日期」和「路线」两列，请检查表头写法。")

    keys = load_gazetteer()
    days, raw_stops, warnings = [], {}, []
    for r in rows[hi + 1:]:
        date_v = cell(r, cols.get("date"))
        route_v = cell(r, cols.get("route"))
        if date_v in (None, "") and route_v in (None, ""):
            continue
        date_s = as_date(date_v, is_date_col=True)
        # 第二列经常混着"建议出发"时间（可能是 10:00 文本，也可能是 Excel 的时间值）
        for c, v in r.items():
            if c != cols.get("date") and isinstance(v, (float, dt.time)) and (
                    isinstance(v, dt.time) or 0 < v < 1):
                date_s = ("%s %s" % (date_s, as_date(v))).strip()
        route_s = str(route_v or "").strip()
        day = {
            "id": "D%d" % (len(days) + 1),
            "date": date_s,
            "route": route_s,
            "km_text": as_date(cell(r, cols.get("travel"))),
            "intensity": as_date(cell(r, cols.get("intensity"))),
            "lodging": as_date(cell(r, cols.get("lodging"))),
        }
        days.append(day)
        if route_s:
            hit = extract_places(route_s, keys)
            if len(hit) < 2:
                warnings.append("%s：路线文本里只认出 %d 个地名，请手工补 stops/legs —— %s"
                                % (day["id"], len(hit), route_s))
            for name, meta in hit:
                raw_stops.setdefault(name, {"name": name, "lat": meta["lat"], "lon": meta["lon"],
                                            "confidence": meta.get("confidence", "high"),
                                            "days": []})
                raw_stops[name]["days"].append(day["id"])

    if args.start:
        try:
            base = dt.date.fromisoformat(args.start)
            for i, d in enumerate(days):
                if not re.match(r"\d{4}-\d{2}-\d{2}", str(d["date"])):
                    d["date"] = (base + dt.timedelta(days=i)).isoformat()
        except ValueError:
            warnings.append("--start 格式不是 YYYY-MM-DD，已忽略")

    # 按出现顺序编号
    order = []
    for d in days:
        for name, meta in extract_places(d["route"] or "", keys):
            if name not in order:
                order.append(name)
    stops = []
    for i, name in enumerate(order, 1):
        s = dict(raw_stops[name]); s["n"] = i; s["day"] = s["days"][0]
        stops.append(s)

    legs = []
    for d in days:
        hit = [n for n, _ in extract_places(d["route"] or "", keys)]
        for a, b in zip(hit, hit[1:]):
            legs.append({"from": a, "to": b, "day": d["id"], "dashed": False})

    trip = {
        "trip": {
            "title": args.title or os.path.splitext(os.path.basename(args.input))[0],
            "subtitle": "",
            "facts": [], "notes": [],
        },
        "booking_actions": [],
        "days": days,
        "stops": stops,
        "legs": legs,
        "_draft": {
            "generated_by": "parse_itinerary.py",
            "must_review": [
                "stops/legs 是贪心匹配的草稿：换乘、区间车、往返支线需要人工改写",
                "legs 的 geometry 还是空的，需跑 fetch_routes.py 补真实路网",
                "booking_actions（退/改/新订决策）与 alerts 需要结合约束库由 agent 判断生成",
                "km_text 直接来自原表，不等于真实里程",
            ],
        },
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(trip, f, ensure_ascii=False, indent=1)
    print("\n已生成 %s：%d 天 / 草稿点位 %d 个 / 草稿路段 %d 条"
          % (args.out, len(days), len(stops), len(legs)))
    if warnings:
        print("\n需要人工复核：")
        for w in warnings:
            print("  · " + w)


if __name__ == "__main__":
    main()
