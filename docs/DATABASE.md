# 当前数据库约定

应用使用 SQLite，现有 32 张业务表，另有 `schema_migrations` 记录迁移版本。

| 范围 | 表 | 保留原因 |
| --- | --- | --- |
| 论文与书目信息 | papers、paper_versions、tags、paper_tags、research_groups、research_group_papers | 论文当前信息、PDF 版本、标签、课题组及代表论文关系 |
| 阅读与对话 | annotations、reading_progress、translation_cache、conversations、messages、paper_agent_context_syncs | 笔记、进度、翻译缓存、对话，以及仅保存同步哈希的 Agent 上下文游标 |
| 论文架构 | architecture_documents、architecture_assets | 独立架构资产及其图片 |
| 研究图谱 | research_nodes、research_edges、paper_nodes、node_progress、research_ideas | 跨论文问题、关系、进展和想法 |
| 日报和讨论 | briefs、brief_items、daily_selection_items、daily_reviews、daily_replies | 推荐顺序、精选关系、匿名评论及回复 |
| 任务和运行 | memories、task_runs、interest_model_runs、discovery_cursors、discovery_runs、discovery_items、operation_logs、app_settings | 用户偏好、任务状态、模型产物、可重试采集批次、操作日志及配置 |

## 论文内容和评价

`papers` 是当前论文信息的唯一所有者。日报项只引用论文 ID 和推荐顺序，不保存内容快照。
阅读器、论文列表和日报都读取同一条当前记录。

| 字段 | 含义 |
| --- | --- |
| pre_read_summary | 读前简介，Markdown，可包含概要、亮点和推荐理由 |
| post_read_summary | 读后总结，Markdown，可包含方法、证据、实验、局限和开放问题 |
| user_note | 我的简记，快捷入口和论文信息共用，不在公共精选页面输出 |
| user_score | 用户明确填写的单一评分 |
| agent_score | Agent 独立给出的同一维度评分 |
| model_score | 预测模型给出的同一维度评分 |

三个分数都是 0–1 实数，保留实际精度；null 表示未评分，0 是有效评分。
人工界面提供很低、较低、一般、较高、很高五级，分别写入 0、0.25、0.5、0.75、1。
不再区分兴趣度、相关性、质量等评价维度。Agent 建议不自动变成人工评分。
模型置信度、版本和待复核标志是预测运行信息，不是额外评价维度。

作者信息保存在论文的 authors 中；论文通过 research_group_id 关联课题组。
metadata_json 仅保留出处和工作流信息，不复制论文总结或当前评分。

## 一次性迁移 v6

服务启动时自动执行事务迁移，不保留运行时旧字段兼容路径：

- 合并历史概要、分析和阅读总结到两个 Markdown 字段，私人态度移入 user_note。
- 人工多维布尔值和等级无法唯一换算为整体分数，清空为未评分；原有 Agent 和模型连续分数分别迁入对应字段。
- 公共评论原 1–6 分转换为 `(旧分数 - 1) / 5`，保留评论、回复，删除置信度和修改历史表。
- 删除音频功能对应的两张表、任务以及 data/audio 文件，不另行归档。
- 删除日报和日报项的 snapshot_json；旧单项精选关系并入 daily_selection_items。
- 删除旧评价列、旧结构化总结以及废弃设置；拆分的每日时、分只迁移一次到 daily_time。
- 提交前检查外键；失败回滚数据库变更。音频文件清理在提交后执行，失败会报告并在下次启动重试。

历史 PDF 版本和采集 staging 用于来源追踪、续传及去重，不参与当前论文内容展示。
