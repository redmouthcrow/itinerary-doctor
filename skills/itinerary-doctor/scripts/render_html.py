#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_html.py — 把 Trip Schema 渲染成单文件交互式路线图。

设计约束（重要）：
  * 零第三方依赖：只用 Python 标准库 + 同目录 ../assets/ 下内置的 Leaflet。
    输出是一个自包含 HTML，双击即开，可直接转发给客户。
  * 底图用高德瓦片（GCJ-02）。因此**内部坐标统一存 WGS-84，渲染时转换**，
    避免坐标系在不同环节反复漂移。
  * 时间/政策类信息不在这里生成，渲染器只负责表现，不负责判断。

用法：
    python render_html.py trip.json -o 路线图.html
    python render_html.py trip.json -o 路线图.html --offset-m 1500 --satellite
"""
import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, os.pardir, "assets")
sys.path.insert(0, HERE)
from _schema import (load_trip, resolve_placeholders, to_html, audit)  # noqa: E402

DEFAULT_THEME = {
    "bg": "#faf9f6", "ink": "#1f2029", "accent": "#b25c3c",
    "sidebar_width": "408px",
    "day_palette": ["#c8363a", "#e27c2e", "#8c56ce", "#c4981a",
                    "#6e9642", "#267ca4", "#147a74", "#564684"],
}


# ---------------------------------------------------------------- 载入
def asset(name):
    p = os.path.join(ASSETS, name)
    if not os.path.exists(p):
        sys.exit("缺少内置资源：%s（应与 scripts/ 同级的 assets/ 目录一起分发）" % p)
    with open(p, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- 坐标
# 坐标与几何工具统一放在 _geo.py（与 render_poster.py 共享，避免两处实现漂移）。
# 约定：内部一律 WGS-84，出图时才转 GCJ-02。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _geo import (wgs84_to_gcj02, offset_polyline, decimate,  # noqa: E402
                  polyline_length_km, bbox_of)

# ---------------------------------------------------------------- 渲染
def build_payload(trip, offset_m_default):
    theme = dict(DEFAULT_THEME)
    theme.update(trip.get("theme") or {})
    palette = theme["day_palette"]
    day_color = {d.get("id"): (d.get("color") or palette[i % len(palette)])
                 for i, d in enumerate(trip.get("days", []))}

    legs_out = []
    for lg in trip.get("legs", []):
        geom = lg.get("geometry") or []
        if not geom:
            print("  ! leg 缺少 geometry，已跳过：%s → %s（请先跑 fetch_routes.py）"
                  % (lg.get("from"), lg.get("to")), file=sys.stderr)
            continue
        off = lg.get("offset_m", offset_m_default)
        pts = decimate([(float(a), float(b)) for a, b in geom], lg.get("decimate_m", 60))
        pts = offset_polyline(pts, off)
        pts = [wgs84_to_gcj02(la, lo) for la, lo in pts]
        dk = lg.get("day")
        legs_out.append({
            "id": "%s-%s" % (lg.get("from"), lg.get("to")),
            "day": dk,
            "color": lg.get("color") or day_color.get(dk, "#666"),
            "dashed": bool(lg.get("dashed")),
            "weight": lg.get("weight", 4 if lg.get("dashed") else 5),
            "label": lg.get("label") or "%s → %s" % (lg.get("from"), lg.get("to")),
            "km": lg.get("km") or polyline_length_km([(float(a), float(b)) for a, b in geom]),
            "source": lg.get("km_source", "road"),
            "pts": [[round(a, 5), round(b, 5)] for a, b in pts],
        })

    stops_out = []
    for s in trip.get("stops", []):
        if s.get("lat") is None or s.get("lon") is None:
            print("  ! stop 缺坐标，已跳过：%s" % s.get("name"), file=sys.stderr)
            continue
        la, lo = wgs84_to_gcj02(float(s["lat"]), float(s["lon"]))
        stops_out.append({
            "n": s.get("n"), "name": s.get("name"),
            "lat": round(la, 5), "lon": round(lo, 5),
            "color": s.get("color") or day_color.get(s.get("day"), "#666"),
            "popup": to_html(s.get("popup") or ""),
            "size": s.get("size", "big"),
        })

    days_out = [{
        "id": d.get("id"), "date": d.get("date"), "route": d.get("route"),
        "km": d.get("km_text") or d.get("km") or "—",
        "lodging": d.get("lodging") or "—",
        "action": d.get("lodging_action") or "",
        "tip": to_html(d.get("tip") or ""),
        "color": day_color.get(d.get("id"), "#8c8c96"),
    } for d in trip.get("days", [])]

    return {
        "trip": trip.get("trip", {}),
        "theme": theme,
        "booking_actions": trip.get("booking_actions", []),
        "days": days_out,
        "stops": stops_out,
        "legs": legs_out,
        "auto_fit": trip.get("map", {}).get("fit", "auto"),
        "default_base": trip.get("map", {}).get("base", "vec"),
    }


HTML_TEMPLATE = u"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>__LEAFLET_CSS__</style>
<style>
:root{--ink:__INK__;--bg:__BG__;--accent:__ACCENT__;--line:#e3e1dc}
*{box-sizing:border-box}
html,body{margin:0;height:100%;font-family:"Microsoft YaHei","PingFang SC","Hiragino Sans GB",system-ui,sans-serif;color:var(--ink);background:var(--bg)}
#app{display:flex;height:100%;min-height:100vh}
#side{width:__SW__;flex:0 0 __SW__;height:100vh;overflow-y:auto;background:#fff;border-right:1px solid var(--line);padding:22px 20px 40px}
#side h1{font-size:22px;margin:0 0 6px;letter-spacing:.5px}
.sub{font-size:13px;color:#6b6c78;line-height:1.7;margin-bottom:12px}
.sub b{color:var(--accent)}
.facts{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 14px}
.fact{background:#f4f2ee;border:1px solid var(--line);border-radius:8px;padding:6px 10px;font-size:12px;color:#41424e}
.bk{border-left:4px solid #ccc;background:#fafafa;border-radius:7px;padding:7px 10px;margin-bottom:6px;font-size:12px;line-height:1.6}
.bk b{display:block;font-size:12.5px;color:#2c2d38}
.bk span{color:#5c5d69}
.day{border:1px solid var(--line);border-left:5px solid #ccc;border-radius:10px;padding:11px 13px;margin-bottom:10px;cursor:pointer;transition:.15s;background:#fff}
.day:hover{box-shadow:0 3px 14px rgba(0,0,0,.09);transform:translateY(-1px)}
.day.on{background:#fffaf4;box-shadow:0 3px 16px rgba(178,92,60,.22)}
.day .h{display:flex;align-items:center;gap:8px;margin-bottom:5px;flex-wrap:wrap}
.chip{font-size:11px;font-weight:700;color:#fff;border-radius:6px;padding:2px 8px;white-space:nowrap}
.day .date{font-size:12px;color:#6b6c78}
.day .r{font-size:13.5px;font-weight:600;line-height:1.5;margin-bottom:5px}
.day .m{font-size:12px;color:#4a4b57;line-height:1.6}
.day .tip{font-size:11.5px;color:#96613f;background:#fdf4ec;border-radius:6px;padding:6px 8px;margin-top:7px;line-height:1.6}
#map{flex:1;height:100vh;position:relative;background:#e9e7e2}
#toggles{position:absolute;z-index:800;top:12px;left:12px;display:flex;gap:6px;background:rgba(255,255,255,.94);padding:5px;border-radius:9px;box-shadow:0 2px 10px rgba(0,0,0,.16)}
#toggles button{border:0;background:transparent;font:600 13px/1 "Microsoft YaHei",sans-serif;color:#4a4b57;padding:7px 11px;border-radius:6px;cursor:pointer}
#toggles button.on{background:#3a3b46;color:#fff}
#hint{position:absolute;z-index:800;bottom:14px;left:12px;background:rgba(255,255,255,.94);border-radius:9px;padding:8px 12px;font-size:12px;color:#4a4b57;box-shadow:0 2px 10px rgba(0,0,0,.16);line-height:1.8;max-width:60%}
#netwarn{display:none;position:absolute;z-index:900;top:12px;left:50%;transform:translateX(-50%);background:#b3403c;color:#fff;padding:8px 14px;border-radius:8px;font-size:12.5px;box-shadow:0 2px 12px rgba(0,0,0,.25)}
.num{display:flex;align-items:center;justify-content:center;border-radius:50%;color:#fff;font:700 13px/1 "Microsoft YaHei",sans-serif;border:2.5px solid #fff;box-shadow:0 1px 6px rgba(0,0,0,.45)}
.num.big{width:32px;height:32px;font-size:15px}
.num.small{width:26px;height:26px}
.leaflet-popup-content{font-family:"Microsoft YaHei",sans-serif;font-size:13px;line-height:1.7}
.leaflet-tooltip{font-family:"Microsoft YaHei",sans-serif;font-size:12.5px}
#credit{position:absolute;z-index:800;right:8px;bottom:6px;font-size:11px;color:#5c5d69;background:rgba(255,255,255,.8);padding:2px 7px;border-radius:5px}
@media(max-width:900px){#app{flex-direction:column}#side{width:100%;flex:none;height:46vh;border-right:0;border-bottom:1px solid var(--line)}#map{height:54vh}}
@media print{#side{display:none}#map{height:100vh}}
</style>
</head>
<body>
<div id="app">
  <aside id="side">
    <h1>__H1__</h1>
    <div class="sub">__SUBTITLE__</div>
    __FACTS__
    __BOOKING__
    <div id="days" style="margin-top:14px"></div>
    <div class="sub" style="margin-top:14px;border-top:1px solid var(--line);padding-top:12px;font-size:12px">
      __FOOTNOTE__
    </div>
  </aside>
  <main id="map"><div id="toggles"></div><div id="netwarn">底图瓦片加载失败，请检查网络（路线与标注仍可查看）</div><div id="credit">__CREDIT__</div></main>
</div>
<script>__LEAFLET_JS__</script>
<script>
var LEGS=__LEGS__, STOPS=__STOPS__, DAYS=__DAYS__, NOTES=__NOTES__;
var map=L.map('map',{zoomControl:true,attributionControl:true});
var base=null, tilesFailed=0, tilesOk=0;
function mkBase(kind){
  var url = kind==='sat'
    ? 'https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}'
    : 'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}';
  var l=L.tileLayer(url,{subdomains:['1','2','3','4'],maxZoom:18,
    attribution:kind==='sat'?'&copy; 高德卫星影像':'&copy; 高德地图'});
  l.on('tileload',function(){tilesOk++;});
  l.on('tileerror',function(){tilesFailed++;
    if(tilesOk===0&&tilesFailed>=3){document.getElementById('netwarn').style.display='block';}});
  return l;
}
base=mkBase(__DEFAULT_BASE__); base.addTo(map);
var layers={};
LEGS.forEach(function(l){
  var g=L.polyline(l.pts,{color:'#fff',weight:l.weight+5,opacity:.95,lineCap:'round'}).addTo(map);
  var p=L.polyline(l.pts,{color:l.color,weight:l.weight,opacity:1,lineCap:'round',
      dashArray:l.dashed?'9,7':null}).addTo(map);
  p.bindTooltip(l.label+(l.km?'　'+l.km+' km':''),{sticky:true});
  layers[l.id]=[g,p];
  p.on('click',function(){focusDay(l.day);});
});
STOPS.forEach(function(s){
  var cls='num '+(s.size==='small'?'small':'big');
  var px=(s.size==='small'?26:32);
  var icon=L.divIcon({className:'',iconSize:[px,px],iconAnchor:[px/2,px/2],
    html:'<div class="'+cls+'" style="background:'+s.color+'">'+s.n+'</div>'});
  L.marker([s.lat,s.lon],{icon:icon,title:s.name}).addTo(map)
   .bindPopup('<b>'+s.n+'. '+s.name+'</b>'+(s.popup?'<br>'+s.popup:''));
});
var bounds=L.latLngBounds([]);
LEGS.forEach(function(l){l.pts.forEach(function(p){bounds.extend(p);});});
map.fitBounds(bounds,{padding:[26,26]});
// 自愈：容器在页面加载时可能是 0×0（后台标签页 / 隐藏面板 / 会话恢复），
// 此时 fitBounds 会算出退化视图（实测 z=18、只加载 1 张瓦片）。
// 注意必须读**实时 DOM 尺寸**——map.getSize() 返回的是 Leaflet 的缓存值，
// 正是那个 0，拿它做判断会永远提前返回。
// 定时器 + ResizeObserver 都依赖时序，实测在本环境里都赶不上，所以这里用轮询兜底。
var didFit = map.getSize().x >= 50;
function ensureSized(){
  var el = map.getContainer();
  if (el.clientWidth < 50 || el.clientHeight < 50) return false;
  map.invalidateSize();
  if (!didFit){ map.fitBounds(bounds,{padding:[26,26]}); didFit = true; }
  return true;
}
(function waitForSize(tries){
  if (ensureSized()) return;
  if (tries > 0) setTimeout(function(){ waitForSize(tries - 1); }, 150);
})(200);                                                   // 最多轮询约 30 秒
if (window.ResizeObserver){
  try { new ResizeObserver(function(){ ensureSized(); }).observe(map.getContainer()); } catch(e){}
}
window.addEventListener('resize', function(){ map.invalidateSize(); });
document.addEventListener('visibilitychange', function(){ if(!document.hidden) ensureSized(); });
(function(){
  var tb=document.getElementById('toggles');
  tb.innerHTML='<button data-base="vec" class="'+(__DEFAULT_BASE__==='vec'?'on':'')+'">矢量路网</button>'+
               '<button data-base="sat" class="'+(__DEFAULT_BASE__==='sat'?'on':'')+'">卫星影像</button>';
  tb.addEventListener('click',function(e){var b=e.target.closest('button');if(!b)return;
    [].forEach.call(tb.children,function(x){x.classList.remove('on');});b.classList.add('on');
    map.removeLayer(base);base=mkBase(b.dataset.base);base.addTo(map);base.bringToBack();});
})();
function focusDay(d){
  var hit=false,b=L.latLngBounds([]);
  LEGS.forEach(function(l){ if(l.day===d){hit=true;l.pts.forEach(function(p){b.extend(p);});} });
  if(!hit)return;
  map.fitBounds(b,{padding:[70,70],maxZoom:11});
  LEGS.forEach(function(l){var pr=layers[l.id],on=(l.day===d);
    pr[1].setStyle({opacity:on?1:.18,weight:on?l.weight+2:l.weight});
    pr[0].setStyle({opacity:on?1:.10});});
  [].forEach.call(document.querySelectorAll('.day'),function(el){
    el.classList.toggle('on',el.dataset.id===d);});
}
function resetView(){
  LEGS.forEach(function(l){var pr=layers[l.id];
    pr[1].setStyle({opacity:1,weight:l.weight});pr[0].setStyle({opacity:.95});});
  [].forEach.call(document.querySelectorAll('.day'),function(el){el.classList.remove('on');});
  map.fitBounds(bounds,{padding:[26,26]});
}
(function(){
  var wrap=document.getElementById('days');
  DAYS.forEach(function(d){
    var el=document.createElement('div');
    el.className='day';el.dataset.id=d.id;el.style.borderLeftColor=d.color;
    el.innerHTML='<div class="h"><span class="chip" style="background:'+d.color+'">'+d.id+'</span>'+
      '<span class="date">'+(d.date||'')+'</span></div>'+
      '<div class="r">'+(d.route||'')+'</div>'+
      '<div class="m">'+(d.km||'—')+(d.lodging&&d.lodging!=='—'?'　·　住 '+d.lodging:'')+
        (d.action?'　<b style="color:#b25c3c">'+d.action+'</b>':'')+'</div>'+
      (d.tip?'<div class="tip">避坑：'+d.tip+'</div>':'');
    el.addEventListener('click',function(){focusDay(d.id);});
    wrap.appendChild(el);
  });
  var hint=document.getElementById('hint')||document.createElement('div');
  hint.id='hint';
  hint.innerHTML='点击左侧日程聚焦当日路线　·　<button style="border:0;background:#eee;border-radius:5px;padding:4px 9px;cursor:pointer" onclick="resetView()">显示全部</button>'
    + (NOTES&&NOTES.length?'<br><span style="color:#96613f">'+NOTES.join('　|　')+'</span>':'');
  document.getElementById('map').appendChild(hint);
})();
</script>
</body>
</html>
"""


def render(trip, offset_m_default):
    p = build_payload(trip, offset_m_default)
    t = p["trip"]
    theme = p["theme"]
    bc = {"keep": "#2f7d5c", "cancel": "#b3403c", "move": "#b5793a", "new": "#2e6ca8"}
    booking_html = "".join(
        '<div class="bk" style="border-left-color:%s"><b>%s</b><span>%s</span></div>'
        % (bc.get(b.get("kind"), "#999"), b.get("label", ""), b.get("detail", ""))
        for b in p["booking_actions"])
    facts_html = "".join('<span class="fact">%s</span>' % to_html(f) for f in t.get("facts", []))
    notes = t.get("notes", [])
    html = (HTML_TEMPLATE
            .replace("__LEAFLET_CSS__", asset("leaflet.css").replace(
                "url(images/", "url(data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==#"))
            .replace("__LEAFLET_JS__", asset("leaflet.js"))
            .replace("__TITLE__", t.get("title", "路线图"))
            .replace("__H1__", t.get("title", "路线图"))
            .replace("__SUBTITLE__", to_html(t.get("subtitle", "")))
            .replace("__FOOTNOTE__", t.get("footnote",
                     "底图：高德地图（GCJ-02）。路线几何来自 OpenStreetMap 路网，里程为纯驾驶里程。"
                     "政策与开放时间以景区当日公告为准。"))
            .replace("__CREDIT__", t.get("credit", "Generated by itinerary-doctor"))
            .replace("__INK__", theme["ink"]).replace("__BG__", theme["bg"])
            .replace("__ACCENT__", theme["accent"]).replace("__SW__", theme["sidebar_width"])
            .replace("__FACTS__", facts_html).replace("__BOOKING__", booking_html)
            .replace("__NOTES__", json.dumps([to_html(x) for x in notes], ensure_ascii=False))
            .replace("__DEFAULT_BASE__", "'%s'" % p["default_base"])
            .replace("__LEGS__", json.dumps(p["legs"], ensure_ascii=False))
            .replace("__STOPS__", json.dumps(p["stops"], ensure_ascii=False))
            .replace("__DAYS__", json.dumps(p["days"], ensure_ascii=False))
            .replace("__BOOKING__", booking_html))
    return html, p


def main():
    ap = argparse.ArgumentParser(description="Trip Schema → 单文件交互式路线图")
    ap.add_argument("trip", help="trip.json（或 .yaml）")
    ap.add_argument("-o", "--out", default="route-map.html", help="输出 HTML")
    ap.add_argument("--offset-m", type=float, default=1500.0,
                    help="往返同路段的平行偏移（米），放大时分开显示，默认 1500")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    trip = load_trip(args.trip)
    resolve_placeholders(trip)
    for msg in audit(trip):
        print("  ! " + msg, file=sys.stderr)
    html, payload = render(trip, args.offset_m)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    if not args.quiet:
        km = sum(l.get("km") or 0 for l in payload["legs"])
        unverified = [l["id"] for l in payload["legs"] if l.get("source") != "road"]
        print("已生成 %s" % args.out)
        print("  途经点 %d 个 · 路段 %d 条 · 几何合计约 %s km"
              % (len(payload["stops"]), len(payload["legs"]), round(km, 1)))
        if unverified:
            print("  ! 以下路段里程未经真实路网核实：%s" % ", ".join(unverified))


if __name__ == "__main__":
    main()
