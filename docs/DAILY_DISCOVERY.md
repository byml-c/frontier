# 每日论文发现与筛选

## 流程

持久时间游标 → `lastUpdatedDate` 区间全量分页 → staging 与去重 → 具身智能领域硬筛 →
每日训练的元数据相关度模型（连续分数与置信度）→ 低置信度文章交给 Agent 复核 →
发布日报 20–50 篇并排队摘要翻译；
document 只在用户打开时实时生成。课题组元信息整理走独立 lane，不阻塞日报发布。

每日任务在发布后结束，不自动下载全文、预处理或生成详细分析。需要时在“论文信息”中点击
“让 Agent 生成”，由独立的手动分析任务阅读全文并补充详细字段。已有详细分析保留。

日报只评估个人相关度。每日本地模型根据人工五档标签训练，输出连续
`model_relevance_score`、`model_relevance_confidence` 和是否需要 Agent 复核。高置信度结果
直接作为 `relevance_score` 初值；低置信度、训练支持不足或接近决策边界的文章由 Agent
阅读摘要后写入连续 `relevance_score`（两位小数）。人工仍使用五档：不相关、较不相关、一般、
较相关、高度相关。不混入作者影响力或质量分；摘要级候选的 `research_score` 留空，质量
留待读后评价。已有全文分析及用户确认的质量反馈保留。

每篇最终推荐可保存一个论文级 `model_short_name` 和最多四个 `search_aliases`。这些字段用于
在论文库中按模型缩写、缩写展开或中文简记检索，不是主题标签；用户在“我的简记”中写的偏好
说明保存在 `screening_feedback.comment`，同样参与论文搜索。

当日任务失败后，恢复任务复用同日任务保存的候选池，避免重新发现时被库内去重排除。
若旧草稿已经包含其他兴趣上下文的排名，使用 `frontier brief recover-failed BRIEF_ID`
重建草稿并启动恢复任务。该操作保留论文分析、用户反馈和手动添加项，清空自动排名与旧
兴趣快照，并将原草稿记入审计日志；已发布且包含分析或用户反馈的日报仍受保护。

论文入库后立即触发独立的元信息 Agent：它搜索已有课题组（中英文名、机构、缩写、PI、别名），
核对第一作者、第一单位和通讯作者，并绑定唯一课题组。课题组以标签形式出现在论文上，
`Paper.research_group_id` 是唯一关联来源。`ResearchGroupPaper` 仅作为论文库视图。
不确定时只读取 arXiv HTML/PDF 的前几百字，不建立长期全文缓存；用户手动修订的作者信息优先级最高。

领域硬筛围绕具身智能：机器人、操作、VLA/WAM、导航、locomotion、触觉、遥操作、
sim-to-real 和具身评测。三维感知只有在明确服务机器人或具身行动时通过；一般 AI、LLM 或
Agent 方法必须有具身应用或机器人在环评测证据。默认类别为 cs.RO、cs.CV、cs.AI、cs.LG、
cs.CL、cs.MA，不再单独查询 cs.SE 和 cs.HC。

## 配置与迁移

| 设置 | 默认值 | 用途 |
| --- | --- | --- |
| `discovery_arxiv_categories` | cs.RO、cs.CV、cs.AI、cs.LG、cs.CL、cs.MA | 游标召回来源，不等同于领域通过 |
| 游标 | `lastUpdatedDate` | 上次完整成功截止点减去 2 小时重叠，直至今次安全截止 |
| 模型置信度 | 动态 | 低于阈值、训练支持不足或接近边界时交给 Agent |
| `initial_interest_domains` | 保留原有值 | 细分兴趣先验 |
| `brief_limit` | 50 | 最终自动推荐上限 |
| `featured_limit` | 10 | 重点推荐数量上限，不触发全文分析 |

旧 `fulltext_limit` 设置保留兼容，已不参与每日任务，设置页面不再展示。

旧日期窗口设置保留给旧接口；新的每日游标流程不使用 300 条扫描或 120 条预筛截断。
部署重启会创建游标、staging、模型版本和论文模型字段。原有人工评分、偏好、论文和日报
全部保留。

## 工具与核查

```bash
uv run frontier source arxiv-sync
# 训练当日模型，按 lastUpdatedDate 游标全量分页，领域筛选并返回需 Agent 复核的论文。
uv run frontier task candidate-pool TASK_ID --data @data/tasks/TASK_ID/candidate-pool.json
uv run frontier task get TASK_ID
```

候选文件格式：

```json
{"paper_ids": ["PAPER_ID"], "discovery_complete": true, "notes": "模型高置信相关及 Agent 复核后的候选"}
```

游标同步按 `lastUpdatedDate` 完整分页，不设 300 或 120 条上限。每条原始记录先进入
`discovery_items` staging；领域外记录保留筛除原因但不进入论文库，重复记录按去版本 arXiv ID、
DOI 和规范化标题合并。领域内新论文入库并立即计算连续模型分数、置信度、训练支持度和模型版本。
置信度足够且分数高于阈值的记录直接作为候选；低置信度或接近边界的记录加入
`agent_review_paper_ids`；高置信度低相关记录保留在论文库但不进入当日候选池。
先排除库中全部匹配论文（包括 candidate），同时合并批内重复，依据为去版本的 arXiv ID、
去 DOI URL 前缀及统一大小写后的 DOI、规范化标题。已有文章不重复交给 Agent，不重复入库。
返回游标区间、上游总数、实际抓取数、库内/批内去重、领域排除、模型过滤、直接候选和
Agent 复核数。只有全部分页成功且抓取数等于上游总数时才推进游标。候选池保留任意唯一
`candidate` 论文；不足目标或覆盖不完整时必须说明原因，但不再按 120% 上限拒绝。
服务端还必须保存 `interest_model_runs` 版本、标签 digest、验证 MAE、残差尺度、阈值与 artifact 路径。

每日本地模型使用标题、摘要、类别、主分类、comment、journal reference 和作者姓名训练。
人工五档是强标签；当前 Agent 连续分不作为训练标签，避免过滤器和 Agent 自我强化。
置信度综合 bootstrap ensemble 方差、最近训练样本支持度、时间切分残差尺度以及预测值
相对决策边界的位置；低于置信度阈值或训练支持不足时由 Agent 阅读摘要复核。

游标区间从上次完整成功的 `lastUpdatedDate` 截止点向前重叠 2 小时，到本次运行前 5 分钟，
内部时间一律用带时区的绝对时间。失败时不推进游标，下次从原位置重试，已抓取记录按 identity
去重。这样周末、假期和延迟可见记录不会因为日历窗口切换而丢失。

API 失败不能当成无文章。
服务端对 HTTP 429、502、503、504 及网络传输异常自动按原参数重试，退避间隔从 60 秒开始，
逐次加倍至最多 300 秒，并遵守更长的 `Retry-After`。一次发现调用的全部分页共享 600 秒预算，
包含排队、请求间隔和重试等待，不会给每一页重新分配 10 分钟；预算耗尽后明确报错。
同一服务进程中的 arXiv 查询串行执行，启动间隔至少 3 秒，后续调用共享上次失败的冷却时间。
CLI 的发现与搜索请求超时为 660 秒，预留服务端等待预算和返回时间。
arXiv 分页、日期过滤及请求间隔遵循 [官方 API 手册](https://info.arxiv.org/help/api/user-manual.html)。
