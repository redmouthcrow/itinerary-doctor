# 约束库字段定义

约束库（`data/constraints.json`）是**这个项目里唯一真正的资产**：不是模型，不是渲染器。
新增地区时按本文件录入，每条都必须可追溯、可过期。

## 顶层结构

```json
{
  "meta": {
    "schema_version": 1,
    "updated_at": "2026-09-11",
    "scope": "已覆盖哪些走廊",
    "freshness_policy": "时效规则说明",
    "stale_after_days": 60,
    "source_priority": ["政府公告", "景区官方公众号", "12328/交通厅", "攻略（仅作线索）"],
    "disclaimer": "输出里会带上的免责声明"
  },
  "categories": { "…分类名…": "…含义…" },
  "constraints": [ … ]
}
```

## 单条记录

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✅ | 稳定短标识，如 `duku-season-close`。改名会破坏引用 |
| `place` | ✅ | 约束挂载的地点。多个地点用 `/` 或 `、` 分隔；匹配是双向子串匹配，所以写"白哈巴"能命中"白哈巴村" |
| `category` | ✅ | `reservation` / `quota` / `limited_access` / `closure` / `vehicle` / `document` / `price` / `parking` / `holiday` / `season` |
| `level` | ✅ | `hard` 违反会直接走不通 / `soft` 影响体验或成本 |
| `text` | ✅ | 约束原文。**写成可执行的句子**：渠道 + 提前量 + 数字 + 时段 |
| `source` | ✅ | 来源名称（具体到"某景区管委会公告"这种粒度） |
| `verified_at` | ✅ | 核实日期 `YYYY-MM-DD`。超过 `stale_after_days` 会被自动降级为待核实 |
| `impact` | 建议 | 一句"所以行程要怎么改"。这是体检报告里最值钱的部分 |
| `source_url` | 建议 | 可点开的原文链接 |

## 录入规范

1. **一条只讲一件事。** "白哈巴限流 3500 人"和"白哈巴需边防证"是两条，不要合并——
   它们失效的时间和概率不一样。
2. **数字必须写死**：不要写"需要提前预约"，要写"提前一日 0 点放票，每日 200 台"。
3. **互相矛盾的说法不要二选一**：把矛盾写进 `text`，或拆成两条并各自标注来源。
4. **季节性数据要给窗口**，例如"秋季最佳 9/15—10/5（当年气象台预报）"，
   而不是"秋天好看"。
5. **不写安全建议**（徒步难度、涉水风险等）——超出本工具的职责，也超出可核实范围。

## 时效机制

```
verified_at 距今 ≤ stale_after_days  → 按原 level 输出
verified_at 距今 >  stale_after_days  → level 降级为 "stale"（报告里显示为「待核实」）
```

`fetch_constraints.py` 每次运行都会把实际使用的 `active_source`、记录数、评估日期
写进 `trip.json` 的 `constraints_meta`，报告与 HTML 都会显示——**使用者永远知道
自己看的数据是哪一级、什么时候的**。

## 远程更新（避免"装上即过期"）

```bash
# 维护者：把最新库放在仓库根目录 constraints.json
# 使用者：用环境变量指过去，脚本会自动写本地缓存
export ITINERARY_CONSTRAINTS_URL=https://raw.githubusercontent.com/<owner>/<repo>/main/constraints.json
```

取数顺序：**远程 → 本地缓存（`~/.cache/itinerary-doctor/constraints.json`）→ 内置快照**。
远程失败会打警告但**不会中断流程**，保证离线可用。


## 必读：place 必须能匹配到地名库（否则约束等于没写）

东疆那单踩过：把约束的 `place` 写成 `吐鲁番`，而行程里只写点位名（交河故城/火焰山），
结果 `fetch_constraints` **一条都没挂上**，输出只有一句"没有任何约束命中"——报告看起来正常，
避坑清单却是空的。这是最危险的一类静默失败。

所以改完约束库必须跑：

```bash
python scripts/validate_constraints.py            # 看问题
python scripts/validate_constraints.py --strict   # CI 用，有警告也失败
```

它会检查：必填字段、`level`/`category` 取值、`verified_at` 格式与时效、id 重复、
`impact` 是否缺失、以及**`place` 能否匹配到 `places.json` 的点位或别名**。

匹配不到时有两个选择：
1. 用 `add_places.py` 把该地点补进地名库（推荐）；
2. 如果它本来就不该进地名库（公路、区域、全国性规则），在 `meta.place_allowlist`
   里**显式声明**——目的是把"我故意不放进库"和"我忘了放"区分开。

## 补地名库

```bash
python scripts/add_places.py --names "库尔德宁,恰西,吐尔根杏花沟" --bbox 42.5,80.5,44.5,85.5
python scripts/add_places.py --apply cand.json     # 人工核对置信度后再落库
```

置信度规则：唯一且名字完全相等 = `high`；同名多处 = `medium`（要人挑）；
只有"包含"匹配 = `low`（可能完全不对）。**medium 和 low 必须人工核验**，
错误坐标会静默毁掉路线和里程（禾木曾差 25 km）。

OSM 对中国西部景区的覆盖并不完整：景区常没有独立节点（可可托海就没有），
这时改用地名/镇名，或接受低置信并标注"需现场确认"。


## 三个补充字段（做多走廊覆盖时加的）

| 字段 | 用途 |
|---|---|
| `corridor` | 走廊标签（北疆/东疆/伊犁/西藏/川西/青甘/内蒙）。用于覆盖度统计与测试，不参与匹配 |
| `scope_text` | 原始的"适用范围描述"。当 `place` 被归一化成可匹配的点位键后，原始描述存这里供人看 |
| `scope` | 值为 `meta` 时表示**走廊级决策说明**：`fetch_constraints` 不挂载、`match_season` 不判定 |

为什么需要 `scope_text`：补库的调研把 `place` 写成了描述（如「冷湖—大柴旦—敦煌（G315 / G215 一带）」），
而 `place` 在 schema 里是**挂载键**，必须能匹配地名库或出现在 `place_allowlist`。
合并脚本会把描述里能匹配的点位抽出来放进 `place`，原描述挪到 `scope_text`，
抽不出来的就得补地名库或显式加进 allowlist —— 这条规则由 `validate_constraints.py` 强制。
