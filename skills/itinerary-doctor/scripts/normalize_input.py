#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
normalize_input.py — 把"各种形状的行程输入"归一化成结构化天数行。

为什么单独一层：真实用户的行程来自自己做的表、AI 生成的 markdown、微信聊天记录、
手打的一段话……形状完全不固定。早先的解析器只认"首行标准表头 + 日期/路线/住宿"一种，
其余一律报"没找到表头行"（实测 6 种常见脏输入，6 种全挂）。

三级策略，按可靠性从高到低依次尝试，全部跑完后取"解析出天数最多"的那个，
并记录用的是哪一级：
    1. table   表格类：表头映射；表头认不出时按列位置猜
    2. heading 标题式：`## Day 3 · 9月27日` / `**第3天**` / `D3 布尔津→白哈巴`
    3. prose   散文/聊天式：**以日期为锚点切段**，每段归一天（处理"26号"这类省略月份）

输出统一的 rows：[{date_text, date_iso, day_no, route, lodging, note, ...}]

单独使用：
    python normalize_input.py 行程.txt --start 2026-09-25
    python normalize_input.py 行程.xlsx --json
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import zipfile

XL_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# 表头关键词。注：早先 find_header 与 map_columns 用了两套不一致的词表，
# 这是"表格明明有表头却认不出"的根因，这里合并成一组。
DATE_KEYS = ("日期", "时间", "date", "day", "天数")
ROUTE_KEYS = ("路线", "行程", "安排", "玩法", "计划", "route", "plan", "itinerary")
TRAVEL_KEYS = ("交通", "驾驶", "车程", "耗时", "travel", "drive", "duration")
INTENSITY_KEYS = ("强度", "intensity")
LODGING_KEYS = ("住宿", "酒店", "住", "hotel", "stay", "lodging")

# 标题式：markdown 标题 / 加粗 / 行首 Day、第N天
HEADING_RE = re.compile(
    r'^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*'
    r'(?:day|d|第)\s*([0-9]+|[一二三四五六七八九十]+)\s*(?:天|日)?'
    r'[\s:：.、\-—|]*', re.I)

CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9}


def cn2int(s):
    """中文数字 → int：三→3，十→10，十二→12，二十三→23。"""
    s = (s or "").strip()
    if s.isdigit():
        return int(s)
    if "十" in s:
        a, _, b = s.partition("十")
        return (CN_DIGIT.get(a, 1) if a else 1) * 10 + (CN_DIGIT.get(b, 0) if b else 0)
    return CN_DIGIT.get(s, 0)


# --------------------------------------------------------------- 日期
def _iso(y, mo, d):
    if not y:
        return None
    try:
        return dt.date(int(y), int(mo), int(d)).isoformat()
    except (ValueError, TypeError):
        return None


def parse_date_any(text, default_year=None):
    """从一段文本里认日期/第几天。返回 (iso|None, kind, raw)。

    覆盖：2026-09-25 / 2026年9月25日 / 9月25日 / 9/25 / 9.25 / 第3天 / Day 3 / D3 / 第三天
    """
    if not text:
        return None, None, None
    t = str(text)
    m = re.search(r'(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})', t)
    if m:
        return _iso(int(m.group(1)), int(m.group(2)), int(m.group(3))), "ymd", m.group(0)
    m = re.search(r'(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?', t)
    if m:
        return _iso(default_year, int(m.group(1)), int(m.group(2))), "月日", m.group(0)
    m = re.search(r'(?<![\d.\-/])(\d{1,2})\s*[-/.]\s*(\d{1,2})(?![\d.\-/])', t)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return _iso(default_year, mo, d), "月日", m.group(0)
    m = re.search(r'(?:day|d)\s*0*(\d{1,2})\b', t, re.I)
    if m:
        return None, "天序号", int(m.group(1))
    m = re.search(r'第\s*([0-9]+|[一二三四五六七八九十]+)\s*天', t)
    if m:
        return None, "天序号", cn2int(m.group(1))
    return None, None, None


def resolve_day_no(n, start):
    """把"第 N 天"换算成真实日期（需要 --start）。"""
    if not start or not n:
        return None
    try:
        return (dt.date.fromisoformat(str(start)) + dt.timedelta(days=int(n) - 1)).isoformat()
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------- 读文件
def read_xlsx_sheets(path):
    """返回 ([(sheet_name, rows)], reader_name)，rows = list[dict[列字母]=值]。"""
    sheets = []
    try:
        import openpyxl                                          # type: ignore
        wb = openpyxl.load_workbook(path, data_only=True)
        for ws in wb.worksheets:
            rows = [{c.column_letter: c.value for c in r if c.value is not None}
                    for r in ws.iter_rows()]
            sheets.append((ws.title, rows))
        return sheets, "openpyxl"
    except ImportError:
        pass
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(XL_NS + "si"):
                shared.append("".join(t.text or "" for t in si.iter(XL_NS + "t")))
        for n in sorted(x for x in names if re.match(r"xl/worksheets/sheet\d+\.xml$", x)):
            root = ET.fromstring(z.read(n))
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
                    if val not in (None, ""):
                        cells["".join(ch for ch in ref if ch.isalpha())] = val
                rows.append(cells)
            sheets.append((os.path.basename(n), rows))
    return sheets, "内置读取器"


def read_text_rows(path):
    """csv / md / txt → (行单元格列表, 原始行列表)。"""
    with open(path, encoding="utf-8", errors="replace") as f:
        raw = f.read()
    rows, raw_lines = [], []
    for line in raw.splitlines():
        if not line.strip():
            continue
        raw_lines.append(line.rstrip())
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
                continue
            rows.append({chr(65 + i): c for i, c in enumerate(cells) if c})
        elif "\t" in line:
            rows.append({chr(65 + i): c.strip() for i, c in enumerate(line.split("\t")) if c.strip()})
        elif line.count(",") >= 1 and not re.search(r'[。！？]$', line.strip()):
            rows.append({chr(65 + i): c.strip() for i, c in enumerate(line.split(",")) if c.strip()})
    return rows, raw_lines


# --------------------------------------------------------------- 策略 1：表格
def _val(v):
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, dt.time):
        return v.strftime("%H:%M")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = float(v)                                              # openpyxl 对整数返回 int，必须一起处理
        if 20000 <= v <= 60000:                                   # Excel 日期序列号
            return (dt.datetime(1899, 12, 30) + dt.timedelta(days=v)).strftime("%Y-%m-%d")
        if 0 < v < 1:
            m = int(round(v * 24 * 60))
            return "%02d:%02d" % (m // 60, m % 60)
        return "%g" % v
    return "" if v is None else str(v).strip()


HTML_TAG = re.compile(r'<br\s*/?>|</?(?:b|strong|em|i|p|div|span|ul|ol|li)\s*>|&nbsp;|&amp;|&lt;|&gt;', re.I)


def _clean_cell(s):
    """清掉单元格里的 markdown/HTML 痕迹。

    AI 生成的行程几乎都带这些：`**Day 1**`、`<br><br>`、行首 `•`。
    不清理的话日期认不出、地名也匹配不上（实测就是这么挂的）。
    """
    s = HTML_TAG.sub(" ", str(s or ""))
    s = re.sub(r'\*\*(.+?)\*\*', r'\1', s)
    s = re.sub(r'__(.+?)__', r'\1', s)
    s = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'\1', s)
    s = re.sub(r'^\s*[•·●○\-–—]\s*', '', s)
    s = s.replace("⇄", "↔")
    return re.sub(r'\s{2,}', " ", s).strip()


def _map_header(header):
    cols = {}
    for c, v in header.items():
        s = str(v).strip().lower()
        for keys, key in ((DATE_KEYS, "date"), (ROUTE_KEYS, "route"), (TRAVEL_KEYS, "travel"),
                          (INTENSITY_KEYS, "intensity"), (LODGING_KEYS, "lodging")):
            if key in cols:
                continue
            if any(k in s for k in keys):
                cols[key] = c
                break
    return cols


def _find_header(rows):
    """表头行 = 同时命中「日期类」与「内容/住宿类」关键词的那一行。"""
    for i, r in enumerate(rows):
        s = " ".join(str(v).strip().lower() for v in r.values())
        if (any(k in s for k in DATE_KEYS)
                and (any(k in s for k in ROUTE_KEYS) or any(k in s for k in LODGING_KEYS))):
            return i
    return None


def _day_no_of(raw):
    m = re.search(r'\d{1,2}|[一二三四五六七八九十]+', str(raw or ""))
    return cn2int(m.group(0)) if m else None


def strategy_table(rows, start=None):
    """表格策略：先试表头映射，失败再按列位置猜。返回 (rows_out, note) 或 None。"""
    if len(rows) < 2:
        return None
    hi = _find_header(rows)
    if hi is not None:
        cols = _map_header(rows[hi])
        out = []
        for r in rows[hi + 1:]:
            date_raw = _clean_cell(_val(r.get(cols.get("date"))))
            route = _clean_cell(_val(r.get(cols.get("route"))))
            if not date_raw and not route:
                continue
            iso, kind, _ = parse_date_any(date_raw, _year_of(start))
            day_no = _day_no_of(date_raw) if kind == "天序号" else None
            extra = " ".join(_clean_cell(_val(r.get(c))) for c in r
                             if c not in (cols.get("date"), cols.get("route"),
                                          cols.get("lodging"), cols.get("travel")))
            out.append({"date_text": date_raw, "date_iso": iso or resolve_day_no(day_no, start),
                        "day_no": day_no, "route": route,
                        "lodging": _clean_cell(_val(r.get(cols.get("lodging")))),
                        "note": extra.strip(), "source_line": None,
                        "travel": _clean_cell(_val(r.get(cols.get("travel"))))})
        if len([x for x in out if x["route"]]) >= 2:
            return out, ("表头映射（%s）"
                         % "、".join("%s=%s" % (k, v) for k, v in cols.items()))
    # 无表头兜底：按列位置猜（首列日期 + 次列内容 + 末列住宿）
    start_row = 0 if hi is None else hi + 1
    guess, ok = [], 0
    for r in rows[start_row:]:
        keys = sorted(r.keys(), key=lambda c: (len(c), c))
        vals = [_val(r[k]) for k in keys]
        vals = [v for v in vals if v != ""]
        if len(vals) < 2:
            continue
        iso, kind, _ = parse_date_any(vals[0], _year_of(start))
        if not iso and kind != "天序号":
            continue
        # 常见第三形状："第1天 | 9月25日 | 路线 | 住宿" —— 首列是天序号、次列才是日期，
        # 不识别出来的话路线会错拿成"9月25日"这个日期单元格。
        if kind == "天序号" and len(vals) >= 3:
            iso2, kind2, _x = parse_date_any(vals[1], _year_of(start))
            if iso2:
                vals, iso, kind = [vals[1]] + vals[2:], iso2, kind2
        ok += 1
        day_no = _day_no_of(vals[0]) if kind == "天序号" else None
        guess.append({"date_text": vals[0], "date_iso": iso or resolve_day_no(day_no, start),
                      "day_no": day_no, "route": vals[1],
                      "lodging": vals[-1] if len(vals) > 2 else "",
                      "note": " ".join(vals[2:-1]) if len(vals) > 3 else "",
                      "source_line": None, "travel": ""})
    if ok >= 2:
        return guess, "无表头·按列位置猜（首列 %d 行认出日期）" % ok
    return None


def _year_of(start):
    try:
        return int(str(start)[:4])
    except (ValueError, TypeError):
        return dt.date.today().year


# --------------------------------------------------------------- 策略 2：标题式
def _strip_md(s):
    s = re.sub(r'^\s*(?:[-*+]|\d+[.)])\s+', '', s)                # 列表符号
    s = re.sub(r'[*_`>#]', '', s)
    s = re.sub(r'[\U0001F300-\U0001FAFF\u2600-\u27BF]', '', s)    # emoji
    return s.strip()


def strategy_heading(lines, start=None):
    """markdown / 带 Day 标题的文本：每个标题开一天。"""
    heads = [i for i, l in enumerate(lines) if HEADING_RE.match(_strip_md(l))]
    if len(heads) < 2:
        return None
    out = []
    for idx, i in enumerate(heads):
        head = _strip_md(lines[i])
        nxt = heads[idx + 1] if idx + 1 < len(heads) else len(lines)
        # 末尾的非当日内容（`---` 分隔线、`### 出行小贴士` 之类的标题）要截断，
        # 否则会被吞进最后一天，把"飞乌鲁木齐"这种顺带提到的地方误当成行程点位。
        for j in range(i + 1, nxt):
            raw_line = lines[j].strip()
            if raw_line.startswith("---") or re.match(r'^\s*#{1,6}\s', raw_line):
                nxt = j
                break
        body = [_strip_md(l) for l in lines[i + 1:nxt] if _strip_md(l)]
        m = HEADING_RE.match(head)
        day_no = cn2int(m.group(1)) if (m and m.group(1)) else None
        iso, _kind, _ = parse_date_any(head, _year_of(start))
        if not iso:
            for b in body:
                iso, _kind, _ = parse_date_any(b, _year_of(start))
                if iso:
                    break
        # 第一条 bullet 常是散文（"抵达伊宁机场，入住酒店。"），不一定像路线；
        # 所以 route 取首行，但 note 保留**全部**内容，别把 POI 漏掉。
        route = body[0] if body else re.sub(HEADING_RE, "", head).strip(" :：·-—|")
        out.append({"date_text": head, "date_iso": iso or resolve_day_no(day_no, start),
                    "day_no": day_no, "route": route, "lodging": "",
                    "note": " ".join(body), "source_line": i + 1, "travel": ""})
    for r in out:                                                  # 从正文抓"住 X"
        ml = LODGING_INLINE.search((r["note"] or "") + " " + (r["route"] or ""))
        if ml:
            r["lodging"] = ml.group(1)
    return out, "标题式（识别到 %d 个 Day 标题）" % len(heads)


# --------------------------------------------------------------- 策略 3：散文
DATE_SCAN = re.compile(
    r'(20\d{2}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2})'          # 2026-09-25 / 2026年9月25日
    r'|(\d{1,2}\s*月\s*\d{1,2}\s*[日号]?)'                        # 9月25日 / 9月25号
    r'|(?<![\d.\-/])(\d{1,2}\s*[-/.]\s*\d{1,2})(?![\d.\-/])'      # 9/25 / 9.25
    r'|(?<![\d.\-/])(\d{1,2}\s*[日号])'                            # 26号（省略月份，顺延）
    r'|(第\s*(?:\d+|[一二三四五六七八九十]+)\s*天)'                    # 第3天
    r'|(?:day|d)\s*(\d{1,2})\b', re.I)                             # Day 3 / D3
LODGING_INLINE = re.compile(r'(?:住|入住|住宿|订了|宿|住进)[:：\s]*([^\s，。；,;、]{2,14})')


def _scan_dates_with_carryover(text, start=None):
    """扫描全部日期锚点；给省略月份的写法（"26号"）按上文月份顺延，跨月自动进位。"""
    out, cur_y, cur_m, last_d = [], _year_of(start), None, None
    for m in DATE_SCAN.finditer(text):
        raw = m.group(0)
        iso = None
        if m.group(1) or m.group(2) or m.group(3):                 # 显式带月份
            iso, _k, _r = parse_date_any(raw, cur_y)
            mm = re.search(r'(\d{1,2})\D+(\d{1,2})', raw)
            if mm:
                cur_m, last_d = int(mm.group(1)), int(mm.group(2))
        elif m.group(4):                                           # 26号 —— 顺延
            d = int(re.sub(r'\D', '', raw))
            mm = cur_m
            if not mm:                                             # 没有月份上下文（如标题里的"10日游"）
                continue                                           # → 不作为日期锚点
            if last_d and d < last_d:                              # 跨月
                mm += 1
                if mm > 12:
                    mm, cur_y = 1, cur_y + 1
            cur_m, last_d = mm, d
            iso = _iso(cur_y, mm, d)
        else:                                                      # 第 N 天 / Day N
            _iso0, _k, n = parse_date_any(raw, cur_y)
            if isinstance(n, int):
                iso = resolve_day_no(n, start)
        out.append((m.start(), m.end(), iso, raw))
    return out


def strategy_prose(lines, places, start=None):
    """聊天记录 / 一段话：**以日期为锚点切段**，每段归一天。

    这样"9月25号到乌鲁木齐，26号去布尔津"会被正确拆成两天，
    而不是按句号切成"一句一天"。
    """
    text = "\n".join(lines)
    anchors = _scan_dates_with_carryover(text, start)
    if len(anchors) < 2:
        return None
    days = []
    for i, (a_s, a_e, iso, raw) in enumerate(anchors):
        seg_end = anchors[i + 1][0] if i + 1 < len(anchors) else len(text)
        seg = text[a_e:seg_end]
        found = [n for n, _m in _places_in(seg, places)]
        ml = LODGING_INLINE.search(seg)
        days.append({
            "date_text": raw, "date_iso": iso, "day_no": None,
            "route": " → ".join(dict.fromkeys(found)),
            "lodging": ml.group(1) if ml else "",
            "note": re.sub(r'^[，,。；;、\s]+', '', seg.strip())[:60],
            "source_line": None, "travel": "",
        })
    return days, "散文/聊天式（%d 个日期锚点）" % len(anchors)


# --------------------------------------------------------------- 主流程
def _load_places(places_json=None):
    """返回 [(匹配键, meta)]，meta 里带 _canonical 规范名。

    别名命中时要归一到规范名，否则「国际大巴扎」和「新疆国际大巴扎」会被当成两个点。
    """
    p = places_json or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    os.pardir, "data", "places.json")
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)["places"]
    except Exception:                                             # noqa: BLE001
        return []
    keys = []
    for name, v in raw.items():
        m = dict(v)
        m["_canonical"] = name
        keys.append((name, m))
        for a in v.get("alias", []) or []:
            keys.append((str(a), m))
    keys.sort(key=lambda x: -len(x[0]))                           # 长名优先
    return keys


def _places_in(text, places):
    """贪心匹配地名（长名优先，已匹配区间不重复占用）。返回 [(规范名, meta)]，按出现顺序。"""
    found, used = [], []
    for name, meta in places:
        for m in re.finditer(re.escape(name), text):
            span = (m.start(), m.end())
            if any(not (span[1] <= s or span[0] >= e) for s, e in used):
                continue
            used.append(span)
            found.append((m.start(), meta.get("_canonical", name), meta))
    found.sort(key=lambda x: x[0])
    out = []
    for _pos, canon, meta in found:
        # 连续同名折叠：同一处地方的不同别名会各匹配一次
        # （"天山天池" 与 "天池" 指同一处）；但保留 A→B→A 这种真实往返。
        if out and out[-1][0] == canon:
            continue
        out.append((canon, meta))
    return out


def _merge_same_date(rows):
    """合并相邻的同日期行，并丢掉"既没日期又没内容"的垃圾行。

    标题式输入会同时命中两个锚点（"## Day 1 · 9月25日" 里的 "Day 1" 和 "9月25日"），
    不合并就会同一天出现两行——实测 ai_generated.md / day_n.csv 都会这样。
    """
    out = []
    for r in rows:
        if out and r.get("date_iso") and r["date_iso"] == out[-1].get("date_iso"):
            prev = out[-1]
            if len(r.get("route") or "") > len(prev.get("route") or ""):
                prev["route"] = r["route"]
            for k in ("lodging", "note", "travel"):
                if r.get(k) and not prev.get(k):
                    prev[k] = r[k]
            continue
        out.append(r)
    return [r for r in out if r.get("date_iso") or r.get("route")]


def normalize(path, start=None, places_json=None, strategy=None):
    """任意输入 → (rows, strategy_note)。解析不出 ≥2 天则返回 (None, 原因)。

    按**可靠性优先、命中即停**：table → heading → prose。
    不要按"天数最多"来选——散文策略会把 10 天的表格拆成 14 天而"胜出"（实测踩过）。
    如果自动判断不对劲，用 strategy= 强制指定某一级。
    """
    places = _load_places(places_json)

    def table_candidates():
        if path.lower().endswith((".xlsx", ".xlsm")):
            sheets, _reader = read_xlsx_sheets(path)
            for name, rows in sheets:
                r = strategy_table(rows or [], start)
                if r:
                    yield r, "  [sheet: %s]" % name
        else:
            rows, _raw = read_text_rows(path)
            r = strategy_table(rows, start)
            if r:
                yield r, ""

    def line_candidates(fn):
        if path.lower().endswith((".xlsx", ".xlsm")):
            sheets, _reader = read_xlsx_sheets(path)
            for name, rows in sheets:
                raw = [" ".join(_val(v) for v in row.values()) for row in (rows or [])]
                r = fn(raw)
                if r:
                    yield r, "  [sheet: %s]" % name
        else:
            _rows, raw = read_text_rows(path)
            r = fn(raw)
            if r:
                yield r, ""

    chain = (("table", table_candidates),
             ("heading", lambda: line_candidates(lambda ls: strategy_heading(ls, start))),
             ("prose", lambda: line_candidates(lambda ls: strategy_prose(ls, places, start))))
    for name, gen in chain:
        if strategy and strategy != name:
            continue
        for (rows, how), tag in gen():
            rows = _merge_same_date(rows)
            if len(rows) >= 2:
                return rows, how + tag
    return None, ("三级策略都没能解析出 ≥2 天。请整理成任一种："
                  "① 带日期列的表格；② 每天一个 Day 标题；③ 每句话里带日期")


def main():
    ap = argparse.ArgumentParser(description="行程输入归一化（三级策略）")
    ap.add_argument("input")
    ap.add_argument("--start", help="出发日期 YYYY-MM-DD，用于把「第 N 天」换算成日期")
    ap.add_argument("--strategy", choices=["table", "heading", "prose"],
                    help="强制指定解析策略（自动判断不对劲时用）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()
    rows, how = normalize(args.input, args.start, strategy=args.strategy)
    if not rows:
        print("解析失败：%s" % how, file=sys.stderr)
        sys.exit(1)
    print("命中策略：%s" % how)
    print("解析出 %d 天：" % len(rows))
    for r in rows:
        print("  %-12s %-34s 住:%s" % (r.get("date_iso") or r.get("date_text") or "?",
                                       (r.get("route") or "")[:34], r.get("lodging") or "-"))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
