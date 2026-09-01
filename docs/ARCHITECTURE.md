# 首版技术架构

## 目录

```text
frontier/
├── .codex/skills/frontier-research/  # Agent 工作流
├── src/frontier/                     # Server、CLI 与后台任务
├── web/                              # React 响应式 Web
├── data/                             # 运行数据（不提交）
├── tests/                            # Server 与 CLI 测试
├── REQUIREMENTS.md
└── pyproject.toml
```

## 运行结构

```text
Browser ──HTTP/NDJSON stream──> FastAPI Server ──SQLite / data files
                       ↑
                       │ JSON HTTP
                  frontier CLI
                       ↑
                       │ shell tools
                  Codex + Skill
```

- Server 是唯一的数据写入口。
- CLI 是 Server API 的无模型客户端。
- Codex 由 Server 按任务启动，也可由维护者手动启动。
- Web 与 Agent 共用同一套 Server 约束。

## 技术选择

- Python 3.12、FastAPI、SQLAlchemy、SQLite；
- APScheduler 执行北京时间 05:00 的日任务；
- PyMuPDF 提取 PDF 文本、页码与图片；
- arXiv Atom API 负责候选采集；
- React、TypeScript、Vite、Material UI（默认亮色主题）；
- PDF.js/React-PDF 实现阅读器；
- react-force-graph-2d 实现稳定的局部研究图谱；
- Codex `exec` 负责后台非交互式 Agent 运行；交互式请求通过 Codex app-server 的增量事件流转发。
- Codex 保持 `workspace-write` 文件沙箱，并仅开启网络访问，以调用本机 Toolkit Server
  和论文来源；uv 缓存固定在 `data/tasks/uv-cache`。

## 数据策略

- 关系数据保存在 SQLite；
- PDF 和任务产物保存在 `data/`；
- 数据库 ID 使用 UUID；
- Agent 修改写入轻量操作日志；
- 每日导出可读 JSON 后提交本地 Git；
- Codex Provider 配置不进入项目；独立翻译模型凭据只保存在已忽略的 `translation.env`。
- Zotero 通过 `integrations.ZoteroExporter` 预留单向导出边界，首版没有实际网络同步。
- 划词翻译以论文、页码、归一化文本和目标语言生成稳定缓存键，结果保存在 SQLite；选区坐标不参与键生成，缓存命中时不再调用 Agent。
- 词语上下文从浏览器已经渲染的 PDF 文本层截取并随翻译请求发送；Server 不在首个流事件前下载或抽取整篇 PDF。
- Web 手动摄入通过显式 `recommend=true` 与 Agent 的批量采集区分；Server 原子地创建当日 `manual` BriefItem 和 `manual_paper_analysis` 任务。`manual` 项不参与日报额度统计，并在简报中动态读取当前论文分析，避免处理完成后仍显示旧占位快照。
- 已发布日报可生成单人论文播客。Server 从已持久化的论文分析字段确定性地产生逐篇文字稿，保存 `PaperNarration`，再组合为 `PodcastEpisode`；`podcast_episode_render` 直接调用阿里云 Qwen3-TTS，不占用 Codex Agent 执行槽。音频全部成功后才原子发布，TTS 失败不会回滚日报。

## 实时交互

- 对话、划词翻译和完成精读统一使用 NDJSON 流；翻译通过独立的 OpenAI-compatible Chat Completions 流式客户端请求 `translation.env` 中配置的模型，不启动 Codex 会话。翻译配置在启动时预读，HTTP 连接复用；简单翻译显式关闭模型思考模式；
- 用户确认精读反馈后，Server 排队执行图谱维护任务；所有客户端走同一准入规则，不依赖 Web 额外触发；
- Server 将 Codex app-server 的最终消息增量转成 `delta`，并将公开推理摘要、commentary 与工具生命周期转成安全的 `activity` 事件；原始隐藏推理、命令参数和工具原始输出不发往浏览器；
- 完成事件持久化最终正文、引用、任务状态和可恢复的会话标识；
- Web 逐块更新界面，连接失败时显示明确错误，不用虚假的加载动画代替正文。

## Agent 任务

- `daily_brief`：采集、筛选、全文分析和日报发布，只记录潜在节点关联，不建立正式图谱连接；
- `manual_paper_analysis`：只处理用户刚添加的一篇论文，阅读全文、补全可靠 metadata 和推荐分析，不采集其他候选且不占日报额度；
- `paper_metadata_analysis`：由用户在论文信息窗口显式触发，重新阅读全文并更新分析字段和标签建议，不改变日报或图谱归属；
- `paper_chat`：围绕单篇论文回答并引用页码；
- `finish_reading`：汇总阅读材料并生成待确认反馈；
- `graph_maintenance`：在用户确认精读后关联论文、增量更新节点，也可按用户指令维护节点、边、进展和想法；
- `global_chat`：处理带当前页面上下文的研究与管理请求。
- `podcast_episode_render`：生成逐篇中文文字稿，组合单人日报播报并渲染音频；这是 Server 直连 provider 的任务，不启动 Codex。

所有任务都可以从 Server 状态恢复；Agent 记忆是显式检索的增强信息，不是正确运行的前置条件。

Server 仅允许 `completed` 论文写入 `PaperNode` 或带论文来源的节点进展。`library=true`
表示已完成精读的积累视图；不带该参数的论文列表包含全部状态。历史遗留的未完成论文连接不会删除，
但不会进入图谱统计或节点论文列表。

Agent 默认按页读取全文文本；遇到图表、公式布局或视觉证据不足时，可用
`frontier paper render-page PAPER_ID PAGE` 生成对应页面图片，再请求视觉读取。

论文 metadata 由统一的 `PATCH /api/papers/{paper_id}/metadata` 入口维护，Server 同步处理标题规范化、标签替换和操作日志，并保护分析任务状态及 Agent 标签建议不被过期表单覆盖。全局与论文对话共用基于 `react-markdown`、GFM、`remark-math` 和 KaTeX 的消息渲染组件，MUI 负责最终视觉样式和页码引用交互。
