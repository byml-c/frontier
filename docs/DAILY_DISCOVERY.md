# 每日论文发现与筛选

## 采集标识与时间

论文的基础 arXiv ID 是身份，不是全站更新顺序。服务按 `arxiv_id + arxiv_version`
判定新增和版本更新；不能只保存最大 ID，也不能遇到第一篇已知论文就停止 RSS 抓取。

| 字段 | 含义 | 用途 |
| --- | --- | --- |
| API `published` | v1 投稿时间 | 首次投稿信息 |
| API `updated` | 当前所取版本的投稿时间 | API 增量扫描的时间边界 |
| RSS 条目 `pubDate` | 本次公告时间 | 接收迟到公告，独立保存在 `arxiv_announced_at` |
| 日报日期和 `daily_time` | 本期固定截止点 | 限定本期可使用的源数据，不是文章游标 |
| `last_success_at` | 服务成功运行时间 | 运维信息，不参与文章新旧判断 |

`discovery_cursors` 的 `arxiv-paper-version-time-v2.committed_through` 只保存已经入库的
真实文章版本时间最大值。没有新源记录时保持原值；旧 ID 的迟到公告照常接收，不使游标倒退。
从旧运行时间游标升级时，只用已成功批次 staging 的文章投稿/版本时间重建，排除窗口外记录，
不沿用旧的日报截止点。该转换不需要新增数据库列。

## RSS 主源与 API 补充

1. 获取配置分类的完整 RSS 公告批次。默认分类为 cs.RO、cs.CV、cs.AI、cs.LG、cs.CL、cs.MA。
   合并 feed 触及 2000 条时拆成分类请求；单分类仍可能截断或批次不一致则报错，不能当作完整。
2. 完整检查 RSS 全部条目，按 ID/版本去重；不以文章版本时间早于游标为由排除迟到公告。
   公告类型、分类、标题、摘要和作者直接来自 RSS。
3. 按每批最多 100 个明确带版本的 `id_list` 补取元数据。RSS 公告时间不能冒充版本时间；
   API 缺少文章、版本不符或缺少真实时间都视为元数据未补齐。以前未补齐时间的入库文章也重试。
4. API 搜索按 `lastUpdatedDate` 降序，每页 100 篇，补充 RSS 当前批次以外的文章更新。
   不使用 `submittedDate` 过滤，以免漏旧论文新版本。一直读取到严格早于源时间游标的记录；
   与游标相同时间戳的所有记录都必须读完，包括跨页同时间戳。遇到重复页、总数变化、顺序错误、
   缺失总数/页偏移或中途空页均不能报告完整。初次没有历史源记录时只为 API 设置 bootstrap 窗口，
   不用这个窗口排除 RSS 公告。
5. RSS 和 API 都失败时才尝试 OAI-PMH。其 datestamp 仅用于请求，不能替代文章版本时间；
   OAI 也无法补齐 RSS 公告历史，降级结果必须明确标记覆盖不完整。

RSS 和 API 使用不同时间轴，因此时间游标只是 API 扫描优化，ID/版本处理记录才决定论文是否新增。
RSS 没有历史分页接口，也不是历史公告档案：停机错过并已经过期的公告批次仍需历史公告补抓，
不能宣称单靠最大 ID、时间游标或固定天数回看就能证明全历史无遗漏。

所有来源统一串行限流，启动间隔至少 3 秒，遵守 `Retry-After`，一次同步共用 600 秒网络预算。
RSS（包括补元数据）与 Atom 扫描各最多 180 秒；RSS 单次请求 15 秒、最多两次尝试；
id_list 单次 15 秒、一次尝试；Atom 单次 15 秒、最多两次尝试。元数据 API 已失败时，
不再次等待同一 API 的搜索重试，直接保留有效 RSS 数据。

## 入库、筛选和事务

所有窗口内抓取记录先进入论文库和 `discovery_items`。领域筛选只控制日报候选，不再丢弃
领域外抓取记录。批内同 ID 取最高版本；相同版本合并 RSS 来源信息，避免 API 覆盖掉迟到公告证据。
已有 ID 的新版本更新源标题、摘要、链接、版本和元数据，保留人工评分、简记、作者单位确认信息，
以及 `pre_read_summary` 和 `post_read_summary`。已有论文不因版本更新而重复推荐。
新版本解除当前 PDF 缓存指针，后续按版本文件名下载，避免把旧 PDF 当作新版本或覆盖旧文件；
每日采集本身不下载全文。

本次论文、staging、候选计算和源游标在同一事务提交。任何论文写入、候选计算或审计失败时回滚，
不提交半批论文或游标推进。失败批次只可恢复同日期、同窗口中新入库候选的 staging，不能将
此前已有论文重新补进候选池。

RSS 正常但 API 不可用时，仍保存 RSS 论文；未知投稿/版本时间保持空值并标记
`arxiv_timestamp_pending`，源游标保持不变，返回 `coverage_complete=false` 和错误原因。
恢复后补齐时间并继续扫描。不能将这种不完整的空结果解释为“没有新论文”。

只有 RSS 批次和 API 版本扫描都完成、源时间补齐时，才推进源游标并将覆盖标为完整。
同日完整批次重试复用原结果，再排除已推荐和已反馈文章；不完整批次允许重新抓取和补齐。
`source` 返回实际来源、RSS 公告批次、元数据错误及覆盖状态；`cursor.basis` 为
`paper-version-time`，`window.end` 是本期截止点，两者不再混用。

## 每日 Agent

后台每日任务使用一个主 Agent，串行执行，不开子 Agent，也不排队逐篇元信息 Agent。
采集、分页、入库和模型训练由服务代码完成。Agent 只复核 `agent_review_paper_ids`，
核查最终推荐的一作和主要单位，写 `pre_read_summary`，选择并发布日报。

模型由明确的人工作为训练证据，输出单维度 0–1 的 `model_score` 与置信度。
Agent 独立保存 `agent_score`；`user_score` 只由用户明确输入设置。三种来源分开保存。
低置信度候选交给 Agent，领域外论文留在库内但不进入当期候选池。

只从本批新候选与同窗口失败 staging 中选择，排除已有推荐、明确评分和简记；不从旧库补位。
非空 RSS 候选即使存在 API 覆盖缺口，也可附明确说明发布；不完整且为空时不能发布“无新论文”的空日报。
日报只引用当前论文信息，不保存内容快照；读后总结仅在论文信息中展示。

```bash
uv run frontier source arxiv-sync --date 2026-11-10
uv run frontier task candidate-pool TASK_ID --data @data/tasks/TASK_ID/candidate-pool.json
```

候选池使用返回的 `run_id`，并原样传递 `coverage_complete` 到 `discovery_complete`：

```json
{"discovery_run_id":"RUN_ID", "paper_ids":["PAPER_ID"], "discovery_complete":false, "notes":"RSS 已入库，API 元数据或历史扫描尚未完成"}
```

协议参考：[RSS 规范](https://info.arxiv.org/help/rss_specifications.html)、
[API 手册](https://info.arxiv.org/help/api/user-manual.html)、
[ID 与版本规范](https://info.arxiv.org/help/arxiv_identifier.html)。
