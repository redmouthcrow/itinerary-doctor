# -*- coding: utf-8 -*-
"""_schema.py — Trip Schema 的共享工具：载入、占位符回填、markdown/HTML 桥。

为什么需要这一层（两次真实测试踩出来的）：
  * **数字靠手填**：trip.json 里的逐日里程和总里程是我先写估值、跑完 OSRM 再手改。
    两次测试都出现"subtitle 写 700 km → 实测 670 km → 手动回填"。现在改成占位符，
    渲染时从 legs 现算，永远和几何数据一致。
  * **markdown 桥断了**：tip 里写 `**加粗**`，HTML 卡片显示成字面星号（手工清了 4 处）。
    这里按输出目标分别处理：HTML 转成 <b>，海报剥掉记号，报告保持 markdown。

占位符语法（写在任意面向用户的文本字段里）：
    {{total_km}}          总驾驶里程（默认排除 counts_toward_total:false 的 leg）
    {{total_km_all}}      总里程（含所有 leg）
    {{total_hours}}       总驾驶小时
    {{day_km}}            当前这一天的里程（只能用在 days[] 里）
    {{day_km:D3}}         指定某天的里程
    {{day_hours:D3}}
    {{days}} / {{stops}} / {{legs}}     计数
排除规则：区间车/备选路线这类**不算自驾里程**的 leg，在数据里写 "counts_toward_total": false。
"""
import json
import os
import re
import sys

PLACEHOLDER = re.compile(r'\{\{\s*([a-z_]+)\s*(?::\s*([A-Za-z0-9_]+))?\s*\}\}')
MD_BOLD = re.compile(r'\*\*(.+?)\*\*')
MD_ITALIC = re.compile(r'(?<![\w*])\*(?!\*)([^*\n]+?)\*(?!\*)')
MD_CODE = re.compile(r'`([^`\n]+)`')


# ---------------------------------------------------------------- 载入
def load_trip(path):
    """原生支持 JSON；装了 PyYAML 时也支持 YAML（JSON 本身是 YAML 子集）。"""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if path.lower().endswith(".json") or text.lstrip()[:1] in "{[":
        return json.loads(text)
    try:
        import yaml                                              # type: ignore
    except ImportError:
        sys.exit("需要 PyYAML 才能读 .yaml —— 请改用 .json，或 pip install pyyaml")
    return yaml.safe_load(text)


# ---------------------------------------------------------------- 里程统计
def _km(leg, only_road=False):
    if leg.get("counts_toward_total") is False:
        return 0.0
    if only_road and leg.get("km_source") not in (None, "road"):
        return 0.0
    try:
        return float(leg.get("km") or 0)
    except (TypeError, ValueError):
        return 0.0


def _hours(leg):
    if leg.get("counts_toward_total") is False:
        return 0.0
    try:
        return float(leg.get("hours") or 0)
    except (TypeError, ValueError):
        return 0.0


def total_km(trip, day=None, only_road=False, include_all=False):
    legs = trip.get("legs", [])
    if include_all:
        return round(sum(float(l.get("km") or 0) for l in legs
                         if not day or l.get("day") == day), 1)
    return round(sum(_km(l, only_road) for l in legs
                     if not day or l.get("day") == day), 1)


def total_hours(trip, day=None):
    return round(sum(_hours(l) for l in trip.get("legs", [])
                     if not day or l.get("day") == day), 1)


def fmt_num(x):
    """里程/小时的显示格式：小于 50 保留一位小数，否则取整。"""
    if x is None:
        return "—"
    return ("%d" % round(x)) if abs(x) >= 50 else ("%g" % round(x, 1))


# ---------------------------------------------------------------- 占位符
def resolve_text(text, trip, day=None):
    if not isinstance(text, str) or "{{" not in text:
        return text

    def sub(m):
        key, arg = m.group(1).lower(), m.group(2)
        target = arg or day
        if key in ("day_km", "day_km_all"):
            return fmt_num(total_km(trip, target, include_all=(key == "day_km_all")))
        if key == "day_hours":
            return fmt_num(total_hours(trip, target))
        if key == "total_km":
            return fmt_num(total_km(trip))
        if key == "total_km_all":
            return fmt_num(total_km(trip, include_all=True))
        if key == "total_km_road":
            return fmt_num(total_km(trip, only_road=True))
        if key == "total_hours":
            return fmt_num(total_hours(trip))
        if key == "days":
            return str(len(trip.get("days", [])))
        if key == "stops":
            return str(len(trip.get("stops", [])))
        if key == "legs":
            return str(len(trip.get("legs", [])))
        print("  ! 未知占位符 %s（原样保留）" % m.group(0), file=sys.stderr)
        return m.group(0)

    return PLACEHOLDER.sub(sub, text)


def resolve_placeholders(trip):
    """就地解析所有面向用户文本里的占位符。返回 trip（同一个对象）。"""
    for key in ("trip",):
        t = trip.get(key) or {}
        for f in ("title", "subtitle", "footnote", "credit", "poster_note"):
            if t.get(f):
                t[f] = resolve_text(t[f], trip)
        for lst in ("facts", "notes"):
            if t.get(lst):
                t[lst] = [resolve_text(x, trip) for x in t[lst]]
    for b in trip.get("booking_actions", []) or []:
        for f in ("label", "detail"):
            if b.get(f):
                b[f] = resolve_text(b[f], trip)
    for d in trip.get("days", []) or []:
        for f in ("km_text", "tip", "route", "lodging_action"):
            if d.get(f):
                d[f] = resolve_text(d[f], trip, day=d.get("id"))
    for s in trip.get("stops", []) or []:
        if s.get("popup"):
            s["popup"] = resolve_text(s["popup"], trip)
    return trip


# ---------------------------------------------------------------- markdown 桥
def to_html(text):
    """极简 markdown → HTML。只处理 **加粗**、*斜体*、`代码`，已有 HTML 标签原样保留。

    HTML 卡片/弹窗是用 innerHTML 插入的，把 markdown 记号直接塞进去会显示成字面星号。
    """
    if not isinstance(text, str):
        return text
    s = MD_CODE.sub(r'<code>\1</code>', text)
    s = MD_BOLD.sub(r'<b>\1</b>', s)
    s = MD_ITALIC.sub(r'<i>\1</i>', s)
    return s


def strip_md(text):
    """剥掉 markdown 记号，用于只能画纯文本的输出（如海报）。"""
    if not isinstance(text, str):
        return text
    s = MD_CODE.sub(r'\1', text)
    s = MD_BOLD.sub(r'\1', s)
    s = MD_ITALIC.sub(r'\1', s)
    return s.replace("**", "")


# ---------------------------------------------------------------- 自检
def audit(trip):
    """交付前的基本体检：返回问题列表（空列表 = 没问题）。

    这些检查本可以更早发现两次测试踩到的坑：
      * 文本里还有未解析的占位符 → 说明写了占位符但没有对应的 leg 数据
      * leg 的 km_source 不是 road → 里程不是真实路网算的
      * stop 缺坐标 → 图上会少一个点
      * 有 counts_toward_total:false 的 leg 时，提醒总里程会与"几何合计"不一致
    """
    problems = []
    leftovers = []

    def scan(text, where):
        if isinstance(text, str) and "{{" in text:
            leftovers.append("%s：%s" % (where, text[:40]))

    t = trip.get("trip") or {}
    for f in ("title", "subtitle", "footnote", "poster_note"):
        scan(t.get(f), "trip." + f)
    for i, d in enumerate(trip.get("days", []) or []):
        for f in ("km_text", "tip", "route"):
            scan(d.get(f), "days[%s].%s" % (d.get("id", i), f))
    if leftovers:
        problems.append("仍有未解析的占位符（检查是否缺 leg 数据）：%s" % "；".join(leftovers[:3]))

    bad_km = [l.get("id") or ("%s→%s" % (l.get("from"), l.get("to")))
              for l in trip.get("legs", []) or []
              if l.get("km_source") not in (None, "road")]
    if bad_km:
        problems.append("里程未走真实路网的 leg：%s" % "、".join(bad_km[:4]))

    missing = [s.get("name") for s in trip.get("stops", []) or []
               if s.get("lat") is None or s.get("lon") is None]
    if missing:
        problems.append("缺坐标的点位：%s" % "、".join(missing[:4]))

    excluded = [l for l in trip.get("legs", []) or [] if l.get("counts_toward_total") is False]
    if excluded:
        problems.append("有 %d 段被排除在总里程外（区间车/备选路线）—— "
                        "报告里的总里程会小于几何合计，属于预期" % len(excluded))
    return problems
