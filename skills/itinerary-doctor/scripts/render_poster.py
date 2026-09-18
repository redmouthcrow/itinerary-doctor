#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_poster.py — Trip Schema → 静态路线海报 PNG（可选交付物）。

为什么它是"可选"：它依赖 Pillow（第三方库），而交互式 HTML 是零依赖的。
装上 Pillow 就多一张能打印、能发群、能当营销素材的海报；没装就跳过。

用法：
    python render_poster.py trip.json -o 路线图.png
    python render_poster.py trip.json -o 路线图.png --width 3000 --max-tiles 300
"""
import argparse
import json
import math
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _geo import (wgs84_to_gcj02, offset_polyline, decimate,  # noqa: E402
                  polyline_length_km, bbox_of, lonlat_to_merc)

TILE = 256
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "Chrome/120 Safari/537.36", "Referer": "https://www.amap.com/"}
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "itinerary-doctor", "tiles")

FONT_CANDIDATES = [
    (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"),
    ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/PingFang.ttc"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
     "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
     "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
     "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
]


def _require_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont        # noqa: F401
        return True
    except ImportError:
        print("跳过海报生成：需要 Pillow。\n"
              "  安装：pip install pillow\n"
              "  （交互式 HTML 不需要任何第三方库，已正常产出）", file=sys.stderr)
        return False


def font(size, bold=False):
    from PIL import ImageFont
    for reg, bd in FONT_CANDIDATES:
        p = bd if bold else reg
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:                                     # noqa: BLE001
                continue
    print("  ! 没找到中文字体，海报里的中文可能显示为方块。\n"
          "    建议安装 Noto Sans CJK：apt install fonts-noto-cjk / brew install font-noto-sans-cjk",
          file=sys.stderr)
    return ImageFont.load_default()


# ------------------------------------------------------------------ 瓦片
def fetch_tile(z, x, y, style=8):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, "amap%d_%d_%d_%d.png" % (style, z, x, y))
    if not os.path.exists(p):
        url = ("https://webrd0%d.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=%d&x=%d&y=%d&z=%d"
               % (1 + (x + y) % 4, style, x, y, z))
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as r, open(p, "wb") as f:
            f.write(r.read())
        time.sleep(0.05)
    from PIL import Image
    return Image.open(p).convert("RGB")


def tile_xy(z, lat, lon):
    n = 1 << z
    gx, gy = lonlat_to_merc(lat, lon)
    return int(gx * n), int(gy * n)


def pick_zoom(bbox, max_tiles=220):
    """选一个能把整个行程装下、且瓦片数不爆的缩放级别。"""
    for z in range(12, 3, -1):
        x0, y0 = tile_xy(z, bbox[2], bbox[1])
        x1, y1 = tile_xy(z, bbox[0], bbox[3])
        if (x1 - x0 + 1) * (y1 - y0 + 1) <= max_tiles:
            return z
    return 5


def stitch(z, bbox):
    from PIL import Image
    x0, y0 = tile_xy(z, bbox[2], bbox[1])
    x1, y1 = tile_xy(z, bbox[0], bbox[3])
    img = Image.new("RGB", ((x1 - x0 + 1) * TILE, (y1 - y0 + 1) * TILE), (240, 240, 236))
    bad = 0
    for xi in range(x0, x1 + 1):
        for yi in range(y0, y1 + 1):
            try:
                img.paste(fetch_tile(z, xi, yi), ((xi - x0) * TILE, (yi - y0) * TILE))
            except Exception as e:                                # noqa: BLE001
                bad += 1
                print("  瓦片失败 %d/%d：%s" % (xi, yi, e), file=sys.stderr)
    if bad:
        print("  ! %d 张瓦片没下下来（网络），底图会有空白" % bad, file=sys.stderr)
    return img, x0, y0


# ------------------------------------------------------------------ 绘制
def dash_line(dr, pts, fill, width, on=24, off=16):
    if len(pts) < 2:
        return
    d, acc, prev, segs = on + off, 0.0, pts[0], []
    for cur in pts[1:]:
        dx, dy = cur[0] - prev[0], cur[1] - prev[1]
        seg = math.hypot(dx, dy)
        if seg <= 0:
            prev = cur
            continue
        t = 0.0
        while t < seg:
            ph = acc % d
            step = min((on - ph) if ph < on else (d - ph), seg - t)
            if ph < on:
                segs.append(((prev[0] + dx * t / seg, prev[1] + dy * t / seg),
                             (prev[0] + dx * (t + step) / seg, prev[1] + dy * (t + step) / seg)))
            t += step
            acc += step
        prev = cur
    for a, b in segs:
        dr.line([a, b], fill=fill, width=width)


def _overlap(b, boxes):
    tot = 0
    for o in boxes:
        ox = min(b[2], o[2]) - max(b[0], o[0])
        oy = min(b[3], o[3]) - max(b[1], o[1])
        if ox > 0 and oy > 0:
            tot += ox * oy
    return tot


CANDIDATES = [(46, -14, "l"), (46, 12, "l"), (-46, -14, "r"), (-46, 12, "r"),
              (46, -58, "l"), (-46, -58, "r"), (46, 46, "l"), (-46, 46, "r"),
              (6, -74, "l"), (-6, 46, "r"), (6, 40, "l"), (-6, -70, "r")]


def place_label(dr, xy, text, boxes, size_px, limits, fill=(25, 25, 35)):
    f = font(size_px, True)
    x, y = xy
    bb = dr.textbbox((0, 0), text, font=f)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    best, best_box, best_hit = None, None, None
    for dx, dy, anchor in CANDIDATES:
        tx = x + dx if anchor == "l" else x + dx - tw
        box = (tx - 6, y + dy - 6, tx + tw + 6, y + dy + th + 6)
        if box[0] < 6 or box[1] < 6 or box[2] > limits[0] - 6 or box[3] > limits[1] - 6:
            continue
        h = _overlap(box, boxes)
        if h == 0:
            best, best_box = (tx, y + dy), box
            break
        if best_hit is None or h < best_hit:
            best, best_box, best_hit = (tx, y + dy), box, h
    if best is None:
        print("  ! 图上没有空间放标签：%s" % text, file=sys.stderr)
        best, best_box = (x + 46, y - 14), (x, y, x, y)
    boxes.append(best_box)
    dr.text((best[0] - bb[0], best[1] - bb[1]), text, font=f, fill=fill,
            stroke_width=4, stroke_fill=(255, 255, 255))


def main():
    ap = argparse.ArgumentParser(description="trip.json → 路线海报 PNG（需 Pillow）")
    ap.add_argument("trip")
    ap.add_argument("-o", "--out", default="路线图.png")
    ap.add_argument("--width", type=int, default=2600)
    ap.add_argument("--max-tiles", type=int, default=220)
    ap.add_argument("--offset-m", type=float, default=None,
                    help="往返同路段平行偏移（米）；默认按缩放级别自动取")
    ap.add_argument("--zoom", type=int, help="强制指定瓦片缩放级别")
    args = ap.parse_args()

    if not _require_pillow():
        return 0
    from PIL import Image, ImageDraw

    with open(args.trip, encoding="utf-8") as f:
        trip = json.load(f)

    legs = [l for l in trip.get("legs", []) if l.get("geometry")]
    stops = [s for s in trip.get("stops", []) if s.get("lat") is not None]
    if not legs or not stops:
        sys.exit("trip.json 里没有可画的几何/点位：先跑 fetch_routes.py")

    pts = [(float(s["lat"]), float(s["lon"])) for s in stops]
    for l in legs:
        pts += [(float(a), float(b)) for a, b in l["geometry"]]
    bbox = bbox_of(pts, pad_deg=0.25)

    z = args.zoom or pick_zoom(bbox, args.max_tiles)
    print("渲染海报：缩放 z=%d，范围 lat %.2f—%.2f / lon %.2f—%.2f"
          % (z, bbox[0], bbox[2], bbox[1], bbox[3]))
    base, tx0, ty0 = stitch(z, bbox)
    n = 1 << z

    def proj(lat, lon):
        gx, gy = lonlat_to_merc(lat, lon)
        return (gx * n - tx0) * TILE, (gy * n - ty0) * TILE

    # 偏移量：按缩放级别换算，保证放大图里两条线能分开
    mpp = 156543.03 * math.cos(math.radians((bbox[0] + bbox[2]) / 2)) / (2 ** z)
    offset_m = args.offset_m if args.offset_m is not None else max(400.0, mpp * 6)

    dr = ImageDraw.Draw(base)
    for l in legs:
        g = decimate([(float(a), float(b)) for a, b in l["geometry"]], 40)
        g = offset_polyline(g, offset_m)
        g = [proj(*wgs84_to_gcj02(la, lo)) for la, lo in g]
        w = int(l.get("weight") or (4 if l.get("dashed") else 6))
        c = tuple(l.get("color_rgb") or (200, 60, 60))
        if l.get("dashed"):
            dash_line(dr, g, (255, 255, 255), w + 4)
            dash_line(dr, g, c, w)
        else:
            dr.line(g, fill=(255, 255, 255), width=w + 5, joint="curve")
            dr.line(g, fill=c, width=w, joint="curve")

    boxes = []
    limits = base.size
    for s in stops:
        xy = proj(*wgs84_to_gcj02(float(s["lat"]), float(s["lon"])))
        if not (30 < xy[0] < limits[0] - 30 and 30 < xy[1] < limits[1] - 30):
            print("  ! 标注落到图外：%s" % s.get("name"), file=sys.stderr)
            continue
        r = 24 if s.get("size", "big") == "big" else 18
        dr.ellipse([xy[0] - r - 3, xy[1] - r - 3, xy[0] + r + 3, xy[1] + r + 3], fill=(255, 255, 255))
        dr.ellipse([xy[0] - r, xy[1] - r, xy[0] + r, xy[1] + r],
                   fill=tuple(s.get("color_rgb") or (200, 60, 60)), outline=(255, 255, 255), width=2)
        f = font(int(r * 1.1), True)
        label = str(s.get("n", ""))
        bb = dr.textbbox((0, 0), label, font=f)
        dr.text((xy[0] - (bb[2] - bb[0]) / 2 - bb[0], xy[1] - (bb[3] - bb[1]) / 2 - bb[1]),
                label, font=f, fill=(255, 255, 255))
        boxes.append((xy[0] - r - 3, xy[1] - r - 3, xy[0] + r + 3, xy[1] + r + 3))
    for s in stops:
        xy = proj(*wgs84_to_gcj02(float(s["lat"]), float(s["lon"])))
        if 30 < xy[0] < limits[0] - 30 and 30 < xy[1] < limits[1] - 30:
            place_label(dr, xy, s.get("name", ""), boxes, 30, limits)

    # ------------------------------------------------------------ 合成海报
    t = trip.get("trip", {})
    W, M, HDR, FTR = args.width, 60, 340, 120 + 52 * max(1, len(trip.get("days", [])))
    mapw = W - 2 * M
    mh = int(base.height * mapw / base.width)
    poster = Image.new("RGB", (W, HDR + mh + FTR), (248, 247, 244))
    d = ImageDraw.Draw(poster)
    d.text((M, 50), t.get("title", "行程路线图"), font=font(76, True), fill=(30, 30, 42))
    if t.get("subtitle"):
        d.text((M, 148), t["subtitle"], font=font(34), fill=(70, 70, 84))
    for i, note in enumerate(t.get("notes", [])[:2]):
        d.text((M, 202 + i * 40), note, font=font(26), fill=(178, 92, 60))
    d.line([(M, 300), (W - M, 300)], fill=(210, 208, 202), width=2)
    poster.paste(base.resize((mapw, mh), Image.LANCZOS), (M, HDR))
    d.rectangle([M, HDR, M + mapw - 1, HDR + mh - 1], outline=(190, 188, 182), width=2)
    bar = int(100000.0 / (mpp * mapw / base.width))
    sbx, sby = M + mapw - bar - 70, HDR + mh - 60
    d.rectangle([sbx - 16, sby - 38, sbx + bar + 16, sby + 24], fill=(255, 255, 255),
                outline=(160, 160, 170), width=1)
    d.line([(sbx, sby), (sbx + bar, sby)], fill=(40, 40, 50), width=7)
    for e in (sbx, sbx + bar):
        d.line([(e, sby - 12), (e, sby + 12)], fill=(40, 40, 50), width=7)
    d.text((sbx - 4, sby - 32), "100 km", font=font(24, True), fill=(40, 40, 50))

    ty = HDR + mh + 26
    cw = [170, 1180, 260, 380]
    for i, h in enumerate(("日期", "路线", "里程", "住宿")):
        d.text((M + sum(cw[:i]), ty), h, font=font(28, True), fill=(40, 40, 52))
    ty += 44
    d.line([(M, ty), (M + sum(cw) + 60, ty)], fill=(200, 198, 192), width=2)
    ty += 12
    palette = {}
    for i, day in enumerate(trip.get("days", [])):
        c = tuple(day.get("color_rgb") or (140, 140, 150))
        palette[day.get("id")] = c
        d.rounded_rectangle([M, ty + 4, M + 84, ty + 40], radius=9, fill=c)
        d.text((M + 15, ty + 9), str(day.get("id", "")), font=font(24, True), fill=(255, 255, 255))
        d.text((M + 100, ty + 9), str(day.get("date", ""))[:16], font=font(25, True), fill=(60, 60, 72))
        d.text((M + cw[0] + 100, ty + 9), str(day.get("route", ""))[:46], font=font(24), fill=(45, 45, 58))
        d.text((M + cw[0] + cw[1], ty + 9), str(day.get("km_text") or day.get("km") or "—"),
               font=font(24), fill=(45, 45, 58))
        d.text((M + cw[0] + cw[1] + cw[2], ty + 9), str(day.get("lodging") or "—"),
               font=font(24), fill=(45, 45, 58))
        ty += 52
    d.text((M, ty + 12), t.get("poster_note", "里程为真实路网驾驶里程；政策与开放时间以景区当日公告为准。"),
           font=font(22), fill=(120, 120, 132))
    poster.save(args.out)
    print("已生成 %s（%d×%d，缩放 z=%d）" % (args.out, poster.width, poster.height, z))
    return 0


if __name__ == "__main__":
    sys.exit(main())
