# 行程体检 · itinerary-doctor

[![skills.sh](https://skills.sh/b/redmouthcrow/itinerary-doctor)](https://skills.sh/redmouthcrow/itinerary-doctor)

> 把一份**已经存在的行程**变成一份**能落地的行程**。
> 查时令、查预约、查封路、查每日驾驶强度；在"机票和部分酒店已经订了"的约束下，
> 算出**该退哪晚、该改期哪晚、该新订哪晚**，并产出可直接转发客户的交互路线图。

一个 Agent Skill（Claude Code / ZCode / Cursor / Codex 均可）。零第三方依赖即可产出核心交付物。

```bash
npx skills add redmouthcrow/itinerary-doctor
```

---

## 它解决什么（一个真实案例）

用户手里有一版"网上抄的"北疆 10 天行程（`examples/xinjiang-2026-09/input-行程v0.1.xlsx`），
机票和 9 晚酒店已经订好。原计划第 8—10 天去伊犁看草原、走独库公路回乌鲁木齐。

体检发现的问题：

| 发现 | 依据 |
|---|---|
| **9 月底—10 月初的伊犁草原已过季**，"看草原"的预期会落空 | 该点位核心窗口是 6—8 月绿草甸；10 月初转金黄/焦糖色 |
| **独库公路 2026 年昼通夜施，只 8:00—19:00 放行**，北段强制预约（每日 0 点放第 7 天名额） | 新疆交通运输 / 新疆交警公告 |
| **独库预计 10 月中旬冬季封闭**（大概率 10/10 前后），且因施工可能提前 | 年度通告 + 媒体转述 |
| 原计划"6:30 出发走独库"**无效**——8 点才放行 | 同上 |
| 白哈巴自驾每日仅 200 台，需**提前一日**在「原行网」抢，且逾期未取消会被禁止下次预约 | 喀纳斯景区管委会公告 |
| 白哈巴还需**边防证** + 每日限流 3500 人 | 移民局 12367 / 景区公告 |

调整结果：砍掉伊犁段、把禾木补进来、把喀纳斯核心期排进 9/28—9/30 的工作日低谷，
并且在"9 晚中 5 晚不动"的前提下给出退改决策表——**只退被砍掉的 3 晚，克拉玛依那晚申请改期**。

产出（都在 `examples/xinjiang-2026-09/output/`）：

| 文件 | 是什么 |
|---|---|
| `路线图.html` | 单文件交互地图：按天配色、编号途经点、点日程聚焦当天、矢量/卫星切换 |
| `行程报告.md` | 退改决策表 + 避坑清单（带来源与核实日期）+ 逐日计划 + 待核实项 |
| `路线图.png` | 2600px 宽的路线海报，可直接打印/发群 |

---

## 快速开始（零摩擦 demo）

```bash
# 克隆后直接用它自带的案例跑一遍，不需要准备任何数据
cd skills/itinerary-doctor
python scripts/render_html.py   examples/xinjiang-2026-09/trip.json -o /tmp/demo.html
python scripts/render_report.py examples/xinjiang-2026-09/trip.json -o /tmp/demo.md
open /tmp/demo.html        # macOS；Windows 用 start，Linux 用 xdg-open
```

处理自己的行程：

```bash
python scripts/parse_itinerary.py 我的行程.xlsx -o trip.json   # 表 → 骨架
python scripts/fetch_routes.py     trip.json                   # 真实路网里程
python scripts/fetch_constraints.py trip.json                  # 挂约束与告警
python scripts/render_html.py      trip.json -o 路线图.html
python scripts/render_report.py    trip.json -o 行程报告.md
python scripts/render_poster.py    trip.json -o 路线图.png     # 可选，需 Pillow
```

---

## 为什么不是"再问一次 AI"

行程类事故几乎都来自**过期的实时信息**：缆车按错误时刻表安排导致被困、餐厅倒闭两年还在推荐、
限流规则变了不知道、季节性封路没查。公开报道里甚至有人按建议去了一个不存在的景点。

本 skill 把判断建立在**一份带来源和核实日期的约束库**上，而不是模型记忆上：

- 每条约束都有 `source` + `verified_at`，超过时效自动降级为「待核实」
- 时效数据走 **远程优先 → 本地缓存 → 内置快照** 三级结构
  （因为 skill 装上后不会被重装，硬编码的时效数据从装上那天就开始过期）
- 里程来自真实路网（OSRM），退化过的路段会被显式标注，不假装成功

## 目录结构

```
skills/itinerary-doctor/
├── SKILL.md                 # 给 agent 的说明书（触发条件 + 工作流 + 质量红线）
├── scripts/
│   ├── parse_itinerary.py   # xlsx/csv/md → Trip Schema（零依赖读 xlsx）
│   ├── fetch_routes.py      # 真实路网几何与里程（含降级链）
│   ├── fetch_constraints.py # 约束库三级取数 + 告警挂载
│   ├── render_html.py       # → 单文件交互地图（零依赖，主交付物）
│   ├── render_report.py     # → Markdown 报告
│   ├── render_poster.py     # → 路线海报 PNG（可选，需 Pillow）
│   └── _geo.py              # 坐标/几何工具（WGS-84 → GCJ-02、平行偏移等）
├── data/
│   ├── places.json          # 校准过的地名坐标库
│   └── constraints.json     # 约束库种子数据（新疆北疆走廊）
├── references/              # 体检清单 / 约束库字段定义 / 交付物规格
└── examples/xinjiang-2026-09/
```

## 状态

- ✅ 可用：新疆北疆走廊（案例已端到端跑通）
- 🚧 进行中：约束库扩展到西藏 / 川西 / 青甘 / 内蒙；地名库同步扩充
- 📋 计划：远程约束库定期更新、更多交付物模板

## 许可与声明

代码 MIT。**地图瓦片来自高德，仅供个人自用**，商用请自行取得授权或替换底图；
路线几何来自 OpenStreetMap（ODbL）。约束库内容仅供参考，**以景区与交通部门当日公告为准**。
