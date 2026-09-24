# Trip Schema 约定

写 trip.json 时遵守这几条，能避免两次真实测试里反复出现的三类问题。

## 1. 数字不手写 —— 用占位符

**问题**：里程、天数、点位数手填，跑完真实路网后又得回填。两次测试都出现
（subtitle 写"约 700 km"，实测 670；伊犁某天手写 260 km，实际 330 km）。

**做法**：任何面向用户的文本里都可以写占位符，渲染时从 `legs` 现算。

| 占位符 | 含义 |
|---|---|
| `{{total_km}}` | 总驾驶里程（自动排除 `counts_toward_total: false` 的 leg） |
| `{{total_km_all}}` | 总里程（含所有 leg） |
| `{{total_km_road}}` | 只统计真实路网算出的 leg |
| `{{total_hours}}` | 总驾驶小时 |
| `{{day_km}}` | **当前这一天的**里程（只能写在 `days[]` 里） |
| `{{day_km:D3}}` | 指定某天的里程 |
| `{{day_hours:D3}}` | 指定某天的驾驶小时 |
| `{{days}}` / `{{stops}}` / `{{legs}}` | 计数 |

可用的字段：`trip.title/subtitle/facts/notes/footnote/poster_note`、
`booking_actions[].label/detail`、`days[].km_text/tip/route/lodging_action`、`stops[].popup`。

显示格式：里程 ≥50 取整，<50 保留一位小数。

## 2. 区间车与备选路线要标出来

**问题**：北疆案例里"方案 A（自驾进白哈巴）"和"方案 B（车停贾登峪走区间车）"
是互斥的两种走法，同时画在图上会把里程重复计算。

**做法**：不算自驾里程的 leg 加 `"counts_toward_total": false`。

- 景区区间车、摆渡车 → 排除
- 备选路线（实际不走的）→ 排除
- 往返支线（真的会去）→ 保留
- 环湖这类自己开的路 → 保留

渲染与报告都会写明"排除了几段"，避免和"几何合计"对不上时引起误解。

## 3. markdown 与 HTML 的桥

**问题**：tip 里写 `**加粗**`，HTML 卡片（innerHTML 插入）会显示成字面星号。

**做法**：数据里照常写 markdown，渲染器按输出去向分别处理——
HTML 转成 `<b>`，海报剥掉记号，Markdown 报告保持原样。
详情见 `scripts/_schema.py` 的 `to_html()` / `strip_md()`。

弹窗字段（`stops[].popup`）里可以**直接写 HTML**（如 `<br>`、`<b>`），已有的标签不会被破坏。

## 4. 渲染前会自动体检

三个渲染器都会调用 `_schema.audit()`，把这些问题打到 stderr：

- 文本里还有**未解析的占位符**（通常意味着缺 leg 数据）
- 有 leg 的 `km_source` 不是 `road`（里程不是真实路网算的）
- 有 stop 缺坐标（图上会少一个点）
- 有被排除在总里程外的 leg（属预期，但提醒一声）

## 5. 非 high 置信度的坐标

`places.json` 里 `confidence` 为 `medium`/`low` 的点位（比如 OSM 上没有中文节点、
只能手估的交河故城）必须在 popup 或报告里标出来，并注明"交付前须核验"。
错误坐标会**静默**毁掉路线和里程——禾木曾经差 25 km，画出来才发现。
