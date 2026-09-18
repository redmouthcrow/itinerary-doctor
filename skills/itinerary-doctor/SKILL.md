---
name: itinerary-doctor
description: >-
  行程体检与重排 / itinerary auditor & road-trip mapper. Use when the user shares an
  existing travel plan (Excel / table / screenshot / pasted text) and asks whether it is
  workable, what to cut, which nights to cancel or rebook, which stops are past-season or
  closed, what needs advance reservation — or when they ask to plan a self-drive / road
  trip and want a route map, route poster or itinerary book. Checks seasonal timing,
  reservation channels and quotas, road closures, permit requirements and daily driving
  load; re-plans under already-booked flights and hotels; renders a self-contained
  interactive route map (single HTML file, no server needed).
  中文触发：行程体检、这行程合理吗、行程能不能走通、该不该退酒店、酒店已订怎么调整、
  国庆/暑期自驾怎么安排、X月去Y地值不值得去、帮我避开坑、帮我做路线图、路线海报、路书、
  行程怎么改、退订还是改期。
license: MIT
compatibility: Python 3.9+（渲染交互地图零第三方依赖）。网络建议可用（高德瓦片 + OSRM 路网）；离线时会自动降级并在输出里标注。海报 PNG 需 Pillow（可选）。
metadata:
  author: redmouthcrow
  version: "0.1.0"
  tags: travel itinerary roadtrip map self-drive china audit season reservation
---

# 行程体检（itinerary-doctor）

把一份**已经存在的行程**变成一份**能落地的行程**：查时令、查预约、查封路、查每日驾驶强度，
在"机票和部分酒店已经订了"的约束下，算出**该退哪晚、该改期哪晚、该新订哪晚**，
并产出一份可直接转发客户的单文件交互路线图。

## 支持的输入形状（不要求格式统一）

用户拿到的行程形状千奇百怪，`parse_itinerary.py` 会自动识别，并把命中的策略写进输出：

| 输入 | 例子 | 策略 |
|---|---|---|
| 标准/英文/非标准表头表格 | `日期/路线/住宿`、`时间,安排`、`Date,Plan,Stay` | 表头映射 |
| 无表头表格 | `9月25日,抵达乌鲁木齐,全季酒店` | 按列位置猜 |
| 天序号+日期+内容 | `第1天,9月25日,抵达乌鲁木齐,全季酒店` | 按列位置猜 |
| Excel 日期序列号 | 单元格是 `46290` | 自动还原为 2026-09-25 |
| markdown 标题式 | `## Day 3 · 9月27日 🏔 布尔津 → 白哈巴` | 标题式 |
| 散文 / 微信聊天记录 | "9月25号到了乌鲁木齐先住一晚，26号去布尔津……" | 散文式（按日期切片） |

- 日期写法支持：`2026-09-25` / `2026年9月25日` / `9月25日` / `9/25` / `9.25` / `26号`（省略月份自动顺延）/ `第3天` / `Day 3` / `D3` / `第三天`。
- **`第 N 天` 必须配 `--start 2026-09-25`** 才能换算成真实日期。
- **截图 / 照片不能自动处理**：本 skill 不做 OCR，且很多 agent 环境的模型没有视觉能力。
  让用户把图里的文字贴成文本（微信长按"提取文字"），或换有视觉能力的 agent 先转成文本。
- 自动判断不对劲时用 `--strategy table|heading|prose` 强制指定。
- 完整支持矩阵与限制见 `references/input-formats.md`；这层由 `tests/test_normalize.py` 锁住。

## 什么时候用

- 用户丢来一份行程表（Excel / 表格 / 截图 / 一段文字）问"这样安排合理吗"
- "9 月底去伊犁看草原值不值得"「国庆自驾北疆怎么排」这类**带具体日期**的行程判断
- 用户说"机票订了 9/25 的、酒店订了这几晚，现在想加一个 X，怎么调"
- 用户要"路线图 / 路书 / 行程海报"

**不要用**于：单纯的酒店比价、机票预订、实时导航、出境签证代办、无日期的泛泛攻略。

## 为什么需要它（不要只凭记忆回答）

行程类问题的错误几乎都来自**过期的实时信息**：缆车停运时刻、餐厅倒闭、限流规则、
季节性封路、节假日调休。公开案例里已经出现"按 AI 建议去了一个不存在的景点"这种事故。
本 skill 的做法是：**把判断建立在一份带来源和核实日期的约束库上，而不是模型记忆上。**
因此流程里"查约束库"这一步**不能跳过**。

## 工作流

```bash
# 0) 环境（零依赖，Python 标准库即可）
ls scripts/
python tests/test_normalize.py        # 可选：确认输入解析层正常（10 项断言）

# 1) 行程表 → Trip Schema 骨架（xlsx / csv / md / txt / 聊天记录都行）
python scripts/parse_itinerary.py 用户的行程.xlsx -o trip.json --start 2026-09-25
#    自动识别输入形状；产出里的 _draft.input_strategy 记录命中哪一级
#    stops/legs 是**草稿**：脚本会把需要人工复核的地方列出来

# 2) 补真实路网几何与里程（不要用直线距离或"大约"）
python scripts/fetch_routes.py trip.json
#    降级链：OSRM → 失败则退化直线并标 km_source=straight（输出里会被标成待核实）

# 3) 挂载约束与告警（这一步产出"避坑清单"）
python scripts/fetch_constraints.py trip.json
#    取数：远程库(ITINERARY_CONSTRAINTS_URL) → 本地缓存 → 内置快照
#    超过时效的记录会被标成「待核实」，不要改写成确定语气

# 4) 出交付物
python scripts/render_html.py   trip.json -o 路线图.html    # 零依赖，主交付物
python scripts/render_report.py trip.json -o 行程报告.md    # 退改决策表 + 避坑清单 + 逐日计划
python scripts/render_poster.py trip.json -o 路线图.png     # 可选，需 Pillow
```

### 模型自己必须做的那部分（脚本不做）

脚本只做机械转换。**下面这些判断必须由你（agent）基于检索与推理完成，并写回 trip.json**：

1. **`booking_actions`** —— 退订/改期/新订决策。这是本 skill 的核心价值。
   判据：已订项的**退改弹性**与**稀缺性**（"这晚退了可能再也订不到"优先级最高）、
   退订成本（能改期就不要退）、以及哪几天是行程的瓶颈。
2. **`trip.notes` / `days[].tip`** —— 为什么这么改，用一句人话讲清楚。
3. **时令判断** —— 例如"9 月底的伊犁草原是否还值得去"。这类判断要结合约束库里的
   `season` 记录 + 检索当年物候预报（花期/秋叶），**并明确写出季节窗口**。
4. **补给 `days[].route` / `stops`** —— 脚本猜出来的点位经常不完整（换乘、区间车、
   往返支线），必须逐日核对。
5. **核实关键坐标** —— `data/places.json` 里 `confidence: medium/low` 的点，
   以及任何你怀疑的地名，都要在图上看一眼。**一个错坐标会同时毁掉路线和里程。**

### 检索时的硬要求

- 政策/封路/预约类信息**必须给出官方来源**（政府公告、景区官方公众号、12328/交通厅），
  攻略类只作线索。
- 同一事实出现互相矛盾的说法时，**写"待核实"并附两条来源**，不要二选一当作事实。
- 时效信息一律带 `verified_at`。

## 质量红线（每条都别省）

1. 里程必须来自真实路网；退化过的路段要标出来，不许当正常数据用。
2. 时效信息标注**来源 + 核实日期**；超过 `stale_after_days` 的标「待核实」。
3. 不确定项显式写"以现场/当日公告为准"，**不要写成确定语气**。
4. 交付前**在浏览器里打开一次 HTML**，确认瓦片能加载、标注数量对得上、点击日程能聚焦。
5. 交付物末尾保留免责段与官方咨询电话/12328。

## 参考文档

- `references/input-formats.md` —— **支持的输入形状与限制**（含截图/PDF 的处理办法）
- `references/checklist.md` —— 体检清单：时令 / 预约 / 证件 / 强度 / 停运 / 节假日六类检查项
- `references/constraint-schema.md` —— 约束库字段定义（要新增地区时按它录数据）
- `references/output-spec.md` —— 四件交付物的规格与验收标准

## 数据新鲜度（重要）

skill 安装后**不会被重新安装**，所以硬编码在包里的时效数据从装上那天就开始过期。
本 skill 因此采用**远程优先 + 缓存 + 内置快照**三级结构，并在每次输出里写明实际用的是哪一级。
维护者请把最新约束库放在仓库的 `constraints.json`，使用者可用环境变量覆盖：

```bash
export ITINERARY_CONSTRAINTS_URL=https://raw.githubusercontent.com/<owner>/<repo>/main/constraints.json
```

## 已知边界

- 内置地名库目前覆盖**新疆北疆环线**为主的点位；其他地区需先补 `data/places.json`，
  否则 `fetch_routes.py` 会报"地名未解析"（这是设计如此，不是故障）。
- 中国西部 OSM 路网不完整，缺路时会退化为直线并标注——**必须人工核对里程**。
- 约束库是种子集，不是全量；遇到库外地区要现查现补，并把结果沉淀回去。
