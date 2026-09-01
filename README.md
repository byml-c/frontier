# Frontier Research

面向个人研究者的 Agent 驱动论文简报、移动精读与研究方向图谱系统。Web 使用 Material UI 亮色主题，Agent 对话和阅读页翻译均为流式响应。

当前工程由四部分组成：

- `src/frontier`：FastAPI Server、定时任务和 Toolkit CLI；
- `web`：响应式 React Web；
- `.codex/skills/frontier-research`：Codex Agent 工作流；
- `data`：本地数据库、PDF、任务日志和 Git 备份（不提交到项目仓库）。

完整需求见 [REQUIREMENTS.md](REQUIREMENTS.md)，技术结构见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 本地启动

```bash
uv sync --extra dev
cp translation.env.example translation.env
cd web && pnpm install && pnpm build && cd ..
uv run frontier-server
```

Web 默认只公开 `/daily` 每日精选页。其他页面首次访问时需要输入
`.env` 中的 `FRONTIER_ACCESS_PASSWORD`（默认 `byml-frontier`），验证成功后会在该设备
写入长期 HttpOnly 信任 Cookie。公网部署应修改默认密码；启用 HTTPS 后同时设置
`FRONTIER_TRUSTED_COOKIE_SECURE=true`。

日报页面支持单人论文播客。在 `.env` 中填写
`FRONTIER_ALIYUN_TTS_API_KEY` 并重启服务后，新发布的日报会自动生成逐篇文字稿与阿里云
Qwen3-TTS 音频；已有日报可在页面中点击“生成播客”。

访问 `http://127.0.0.1:8000`。OpenAPI 文档位于 `/docs`。

文本模式默认只读取轻量的 arXiv HTML，不会启动 Docling。arXiv 未提供 HTML
或手动上传的 PDF 会继续使用 PDF 模式。如果部署机器资源充足，才建议安装并启用
PDF 结构化转换：

```bash
uv sync --extra docling
# 在 .env 中设置 FRONTIER_ENABLE_DOCLING=true，然后重启服务
```

首次初始化已有文献与研究图谱：

```bash
uv run frontier bootstrap bibtex '我的文库.bib'
uv run frontier graph list
```

前后端分离开发时，分别运行：

```bash
uv run frontier-server
cd web && pnpm dev
```

## Agent Toolkit

项目内置 `frontier` 非交互式 JSON CLI 和 `.codex/skills/frontier-research` Skill。Server 发起的 Codex 任务会要求 Agent 只通过 CLI 修改数据：

```bash
uv run frontier health
uv run frontier paper list --limit 10
uv run frontier paper context PAPER_ID
uv run frontier paper render-page PAPER_ID 3
uv run frontier graph subgraph
uv run frontier task run-daily
```

CLI 完整约定见 [.codex/skills/frontier-research/references/cli.md](.codex/skills/frontier-research/references/cli.md)。Codex 的 DeepSeek 兼容 API 地址、模型和密钥沿用运行用户已有配置。划词翻译使用独立的 `translation.env`，填写 `OPENAI_API_KEY`、`BASE_URL` 和 `MODEL`；两个模型配置互不覆盖，也不会在 Web 中暴露凭据。

## 验证

```bash
uv run ruff check src tests
uv run pytest -q
cd web && pnpm lint && pnpm build
```

阿里云常驻服务与受信设备访问见 [DEPLOYMENT.md](DEPLOYMENT.md)。公开 `/daily` 时应通过 HTTPS 反向代理暴露服务，不要直接将明文 HTTP 的 8000 端口开放到公网。
