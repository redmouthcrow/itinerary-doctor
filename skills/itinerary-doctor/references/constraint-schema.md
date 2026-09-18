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
