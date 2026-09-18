# -*- coding: utf-8 -*-
"""_geo.py — 无第三方依赖的地理工具（渲染器共享）。

坐标约定（重要）：**内部一律用 WGS-84 存储，只在出图时转 GCJ-02**。
高德/腾讯瓦片是 GCJ-02，直接拿 WGS-84 画会整体偏移 300—600 m。
"""
import math

DEG_KM_LAT = 110.54


def wgs84_to_gcj02(lat, lon):
    """WGS-84 → GCJ-02（火星坐标系）。境外坐标原样返回。"""
    if not (73.66 < lon < 135.05 and 3.86 < lat < 53.55):
        return lat, lon
    a, ee = 6378245.0, 0.00669342162296594323

    def _tlat(x, y):
        r = (-100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y
             + 0.2 * math.sqrt(abs(x)))
        r += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
        r += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
        r += (160.0 * math.sin(y / 12.0 * math.pi) + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
        return r

    def _tlon(x, y):
        r = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
        r += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
        r += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
        r += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
        return r

    dlat, dlon = _tlat(lon - 105.0, lat - 35.0), _tlon(lon - 105.0, lat - 35.0)
    rad = lat / 180.0 * math.pi
    m = 1 - ee * math.sin(rad) ** 2
    sq = math.sqrt(m)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (m * sq) * math.pi)
    dlon = (dlon * 180.0) / (a / sq * math.cos(rad) * math.pi)
    return lat + dlat, lon + dlon


def km_per_lon(lat):
    return 111.32 * math.cos(math.radians(lat))


def polyline_length_km(pts):
    total = 0.0
    for a, b in zip(pts, pts[1:]):
        total += math.hypot((b[1] - a[1]) * km_per_lon((a[0] + b[0]) / 2.0),
                            (b[0] - a[0]) * DEG_KM_LAT)
    return round(total, 1)


def decimate(pts, min_m=60.0):
    """抽稀几何点（OSRM 返回的点很密），控制输出体积。"""
    if not pts:
        return pts
    lat0 = sum(p[0] for p in pts) / len(pts)
    kx = km_per_lon(lat0) * 1000.0
    out = [pts[0]]
    for p in pts[1:]:
        if math.hypot((p[1] - out[-1][1]) * kx, (p[0] - out[-1][0]) * DEG_KM_LAT * 1000.0) >= min_m:
            out.append(p)
    if out[-1] != pts[-1]:
        out.append(pts[-1])
    return out


def offset_polyline(pts, offset_m):
    """把折线沿左侧法线平移，让"同一段路往返两次"分成平行双线。

    偏移是地理距离：总览缩放时两条线会合拢（符合直觉——那就是同一条走廊），
    放大后才分开显示去/回程。offset_m 为 0 时原样返回。
    """
    if not pts or abs(offset_m) < 1e-9 or len(pts) < 2:
        return pts
    lat0 = sum(p[0] for p in pts) / len(pts)
    kx, ky = km_per_lon(lat0) * 1000.0, DEG_KM_LAT * 1000.0
    m = [(p[1] * kx, p[0] * ky) for p in pts]
    out = []
    for i in range(len(m)):
        if i == 0:
            dx, dy = m[1][0] - m[0][0], m[1][1] - m[0][1]
        elif i == len(m) - 1:
            dx, dy = m[-1][0] - m[-2][0], m[-1][1] - m[-2][1]
        else:
            dx, dy = m[i + 1][0] - m[i - 1][0], m[i + 1][1] - m[i - 1][1]
        n = math.hypot(dx, dy) or 1.0
        out.append((m[i][0] + dy / n * offset_m, m[i][1] - dx / n * offset_m))
    return [(y / ky, x / kx) for x, y in out]


def bbox_of(points, pad_deg=0.0):
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return (min(lats) - pad_deg, min(lons) - pad_deg,
            max(lats) + pad_deg, max(lons) + pad_deg)


def lonlat_to_merc(lat, lon):
    """Web Mercator 归一化坐标（0—1），用于瓦片拼接。"""
    x = (lon + 180.0) / 360.0
    s = math.sin(math.radians(lat))
    y = 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)
    return x, y
