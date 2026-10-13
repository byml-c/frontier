# 每日论文发现与筛选

## 流程

服务端大分类分页召回 → 排除库内/批内重复 → 通用概念粗筛并截取最多 120 篇 →
Agent 语义领域筛选 → 合格文章入库 → 兴趣初筛约 100 篇 →
质量和相关度评估 → 日报 20–50 篇 → 前 10 篇全文分析与预处理。

日报入选的每篇文章都由 Agent 重新核对第一作者、第一单位和通讯作者，并总结单位及核心课题组
完成的研究方向。卡片上的折叠箭头展示这段总结；服务端解析结果只作为待核对线索，用户手动修订的
作者信息优先级最高。

大领域默认是 **3D 视觉、具身智能、AI Agent**。AI Agent 包含工具使用、规划、
多 Agent 协作、记忆和计算机操作。VLA、WAM 等保留为细分兴趣，不能限制最初召回。
约 15% 的兴趣候选留给领域内有潜力或暂时不确定的工作。作者影响力只能适度影响相关度。

## 配置与迁移

| 设置 | 默认值 | 用途 |
| --- | --- | --- |
| `discovery_domains` | 三个大领域 | Agent 判断是否允许入库 |
| `discovery_arxiv_categories` | cs.CV、cs.RO、cs.AI、cs.LG、cs.CL、cs.MA、cs.HC、cs.SE | 广泛召回来源，不等同于领域通过 |
| `candidate_lookback_days` | 3 | 通常包含截止日的 UTC 提交日期窗口；周一至周三自动扩展到 7 天以覆盖公告延迟 |
| `discovery_scan_limit` | 300 | 该窗口内按分类分页扫描的最新文章数 |
| `discovery_prefilter_limit` | 120 | 宽松预筛后实际交给 Agent 的文章上限 |
| `initial_interest_domains` | 保留原有值 | 细分兴趣先验 |
| `abstract_limit` | 100 | 兴趣筛选后的候选目标，通常 80–120 |
| `brief_limit` | 50 | 最终自动推荐上限 |
| `fulltext_limit` / `featured_limit` | 10 / 10 | 全文与精选数量上限 |

部署后重启，启动迁移自动补充新设置，把旧 `abstract_limit=50` 升级到 100。
后续迁移将旧默认窗口 14 天改为 3 天、扫描上限 1,000 改为 300，并补充预筛输出上限 120。
其他自定义目标保留；迁移仅执行一次，不覆盖之后的修改。原有细分兴趣、偏好、论文、
评分和日报均保留。没有新增数据库表；候选池使用现有任务 JSON 和日报快照保存。

## 工具与核查

```bash
uv run frontier source arxiv-discover --date 2026-10-10 --offset 0 --page-size 100
# 一次调用内部完成分页、去重与粗筛，只把最多 120 条交给 Agent。
uv run frontier task candidate-pool TASK_ID --data @data/tasks/TASK_ID/candidate-pool.json
uv run frontier task get TASK_ID
```

候选文件格式：

```json
{"paper_ids": ["PAPER_ID"], "discovery_complete": false, "notes": "原始扫描达上限；筛选后合格文章不足目标"}
```

发现接口不入库。一次发现请求在服务端内部完成分页：先扫描最多 300 条元数据，用宽松的通用视觉、机器人、具身和 Agent
词族过滤掉明显无关的文章，再最多返回 120 条完整摘要和作者信息给 Agent。这个预筛不使用
VLA/WAM 等细分兴趣词，也不代替 Agent 的最终领域判断。
先排除库中全部匹配论文（包括 candidate），同时合并批内重复，依据为去版本的 arXiv ID、
去 DOI URL 前缀及统一大小写后的 DOI、规范化标题。已有文章不重复交给 Agent，不重复入库。
返回总数、原始扫描数、粗筛通过和排除数、库内重复数、批内重复数、最终返回数。
Agent 对领域通过且可推荐的论文入库，再做兴趣初筛；兴趣未入选的领域内文章仍可留库。
创建日报时传入对应 `task_id`。保存池时拒绝重复、缺失、非 candidate 论文和超过目标 120% 的请求；不足 80% 或
覆盖不完整须说明原因。日报关联该任务后，添加与发布都检查自动推荐是否来自保存的池。
旧任务没有候选池仍兼容。服务端分页进度写入操作日志，候选池位于任务的
`payload_json.candidate_pool`，发布后另存入日报快照。

最终领域归属、兴趣判断和探索比例依靠 Agent 执行；粗筛使用规则而非语义模型，仍可能漏检。
使用词边界，避免 supervision/reagent 等字符串误匹配；cs.RO/cs.MA 保守保留，单独的 memory
或 reasoning 需同时有语言模型语境。预筛得分只表示通用概念命中，不是质量或兴趣评分。
按粗筛分数排序、同分保持 arXiv 新近顺序；不足 120 篇直接返回，不从旧库或额外批次凑数。
`scan_complete` 表示本次有界扫描完成；`coverage_complete` 仅在未截断且从 0 开始时为真，
将其写入候选池的 discovery_complete。`scan_capped` 为原始扫描截断；`prefilter_capped` 为
粗筛命中过多而截取。API 失败不能当成无文章；请求报错后按原参数重试。
arXiv 分页、日期过滤及请求间隔遵循 [官方 API 手册](https://info.arxiv.org/help/api/user-manual.html)。
