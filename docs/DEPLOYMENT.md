# 阿里云部署

首版推荐把应用只监听在服务器本机，通过 Tailscale Serve 暴露给 tailnet 中受信任的手机和电脑。这样设备换网段不会影响访问，也不会把无登录的应用直接开放到公网。

## 1. 安装与构建

以下示例假设代码位于 `/opt/frontier`，并使用已经配置好 Codex/DeepSeek provider 的同一个 Unix 用户运行服务。

```bash
cd /opt/frontier
uv sync --extra dev
cd web
pnpm install --frozen-lockfile
pnpm build
cd ..
cp .env.example .env
cp translation.env.example translation.env
chmod 600 translation.env
```

按服务器实际路径修改 `.env`。保持 `FRONTIER_CODEX_SANDBOX=workspace-write` 和
`FRONTIER_CODEX_NETWORK_ACCESS=true`，使受限 Agent 能访问本机 Server 与论文来源。
如果服务器通过本机代理访问外部网络，在 `.env` 中设置统一出站代理：

```dotenv
FRONTIER_OUTBOUND_PROXY=http://127.0.0.1:7890
```

该配置覆盖 Codex/OpenAI、翻译模型、arXiv API/HTML/PDF 和手动 URL 下载；
Frontier 本机 API 会通过 `NO_PROXY` 保持直连。修改后需要重启 `frontier.service`。
不要把 Codex API key 写进项目 `.env`；继续使用该用户已有的 Codex 配置。划词翻译的独立模型只写入 `translation.env`：

`FRONTIER_CODEX_PROFILE` 只用于显式选择 `$CODEX_HOME/<name>.config.toml`。
默认配置已经可用时应保持未设置；它不是 provider 名称，也不会自动创建
`[model_providers.<id>]`。

`FRONTIER_CODEX_APP_SERVER_MAX_WORKERS` 控制常驻 App Server 进程数上限，
2C2G 部署默认为 1。首个 Agent 请求会创建 worker 并持续复用，其余 worker 只在并发请求需要时创建；
已经加载的对话 thread 会优先回到同一 worker。启动日报等独立批任务前会驱逐空闲 worker，
把 Agent 汇总内存让给批任务；下一次交互会按需重建。修改该配置后需要重启 Frontier 服务。

2C2G 部署还应保留 `.env.example` 中的 Agent 限额：根 Agent 总并发为 1，单次 Codex
会话最多并发 1 个子代理、委派深度为 1、单个子代理最长 30 分钟，批任务总时限为 3
小时（包含有界执行队列和资源槽等待），交互 turn 最长 1 小时。所有 Codex 命令都使用严格
配置解析，旧版 CLI 不会静默忽略限额。日报全文下载和读取除 prompt 策略外，还由 Server
的全局闸门强制串行。任务 claim 依赖单个 Frontier 服务进程；不要配置多个 Uvicorn worker。

```dotenv
OPENAI_API_KEY=你的翻译模型密钥
BASE_URL=https://你的兼容接口/v1
MODEL=你的翻译模型名称
```

然后做一次前台验收：

```bash
uv run frontier-server
```

另开终端验证：

```bash
uv run frontier health
uv run frontier bootstrap bibtex '我的文库.bib'
```

## 2. systemd 常驻

复制 `deploy/frontier.service.example` 到 `/etc/systemd/system/frontier.service`，把 `YOUR_USER`、`YOUR_GROUP` 和路径替换成实际值，再执行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now frontier
sudo systemctl status frontier
```

示例 unit 面向 cgroup v2 的 2C2G 主机设置了服务总预算，并把 `cpu/io/memory/pids`
控制器委派给 Frontier。服务启动后，Frontier 会把 Web 主进程与所有 Codex 进程分到
`frontier-server` 和 `frontier-agents` 子树，再为每个 Codex 进程创建独立 cgroup。
unit 必须保持 `OOMPolicy=continue`，否则单个 Agent 子组发生 OOM 时 systemd 会连同 Web
一起停止；Web 主进程自身退出仍由 `Restart=on-failure` 拉起。
Frontier 会从 `FRONTIER_DATA_DIR` 自动解析块设备号，并对 Web、Agent 汇总组和每个 Agent
写入 `io.max`：默认读取 20 MiB/s、写入 10 MiB/s；`IOWeight` 仅保留为竞争优先级，
不是硬上限。PDF 下载采用临时文件和原子替换，默认最多 64 MiB、总时限 90 秒；PDF 下载、
提取、渲染与结构化转换全局并发为 1。PyMuPDF 正文提取在可终止子进程中逐页写缓存，默认
75 秒硬时限、最多 2000 万字符；转换请求进入单 worker 的 32 项有界去重队列。
`FRONTIER_AGENT_CGROUP_MODE=required` 时，如果 unit 未启用 `Delegate` 或控制器不可用，
或数据盘无法写入 `io.max`，Web 仍会启动，但 Agent 任务会明确失败，不会退回无隔离运行。

升级已有安装时，不能只拉取代码；还要合并新版 `.env.example` 中的
`FRONTIER_AGENT_*`、`FRONTIER_CODEX_MAX_CONCURRENT_SUBAGENTS` 与子代理超时配置，并把
新版 unit 的资源控制项同步到 `/etc/systemd/system/frontier.service`。随后执行：

```bash
sudo systemd-analyze verify /etc/systemd/system/frontier.service
sudo systemctl daemon-reload
sudo systemctl restart frontier.service
systemctl show frontier.service -p Delegate -p MemoryHigh -p MemoryMax -p CPUQuotaPerSecUSec
```

正常运行后可用以下命令确认 Web 与 Agent 分账；首个 Agent 请求前，单任务子组尚不存在
是正常现象：

```bash
systemd-cgls /system.slice/frontier.service
cat /sys/fs/cgroup/system.slice/frontier.service/frontier-agents/memory.max
cat /sys/fs/cgroup/system.slice/frontier.service/frontier-agents/io.max
```

发布后应先触发一个非关键 Agent 请求，再确认它进入独立叶子且 Web 健康；只有通过这项
服务器 smoke test，才能证明实际内核委派权限和控制器都已生效：

```bash
systemd-cgls /system.slice/frontier.service
frontier health
```

日志通过 `journalctl -u frontier` 查看。Web 的“任务与日志”页面另行展示每次 Agent 任务的状态和失败原因。

## 3. 受信设备访问

按 [Tailscale Linux 安装文档](https://tailscale.com/docs/install/linux) 把服务器加入自己的 tailnet，并在手机、电脑上登录同一个 tailnet。应用保持 `FRONTIER_HOST=127.0.0.1`，不要在阿里云安全组开放 8000 端口。

根据当前的 [Tailscale Serve 文档](https://tailscale.com/docs/features/tailscale-serve)，将本机服务私有转发到 tailnet：

```bash
sudo tailscale serve --bg http://127.0.0.1:8000
tailscale serve status
```

使用命令返回的 tailnet HTTPS 地址访问。只使用 Serve，不启用 Funnel；Funnel 会把应用公开到互联网，不符合本系统的信任设备边界。

## 4. 运行检查

- 北京时间 05:00 启动日报 Agent，23:50 创建结构化数据本地 Git 备份；
- 服务启动时会把旧实例遗留的排队中或运行中任务结算为失败，保留部分输出，并解除当日日报防重阻塞；
- 修改 Web 中的调度时间后，重启 Server 以重载任务；
- 备份仓库位于 `data/backups/git`，PDF 和 SQLite 不进入该仓库；
- 每次升级先执行 `pnpm build`，再重启 `frontier` 服务；
- Codex/provider 状态、额度和凭据由维护者负责，失败原因会留在任务页。

## 5. 从开发机一键更新

项目提供了固定部署到 SSH 别名 `SelfALI` 的脚本。它会在服务器的
`/home/byml/agent/frontier` 中执行仅快进拉取，随后从 `.env.example` 向服务器 `.env`
补充 2C2G 资源管理所需的新配置，再同步前端依赖、构建 Web、重启并检查
`frontier.service`：

```bash
./deploy/deploy-self-ali.sh
```

脚本只部署已经提交并推送到 Git 远端的代码，不会自动提交或推送本机修改。服务器
工作区若有未提交的受版本控制文件修改，部署会停止，避免覆盖服务器上的改动。
环境同步只处理 `deploy/frontier-2c2g-env.keys` 白名单中的键，并且只补充缺失项；服务器
已有值始终保留，密码、代理、端口和数据路径不会被复制或覆盖。该步骤使用同目录临时文件
原子替换 `.env`，可重复运行。如果环境要求 `FRONTIER_AGENT_CGROUP_MODE=required`，脚本
还会在重启前验证 systemd unit 已设置 `Delegate=yes`；未满足时停止部署，避免发布后所有
Agent 因隔离不可用而失败。

若 `Self_ALI` 使用非 root 用户，需要允许其无交互地重启和检查该服务。使用
`sudo visudo -f /etc/sudoers.d/frontier-deploy` 添加以下规则，并把 `byml` 替换为实际
SSH 用户；可用 `command -v systemctl` 确认 systemctl 的绝对路径：

```sudoers
byml ALL=(root) NOPASSWD: /usr/bin/systemctl restart frontier.service, /usr/bin/systemctl is-active --quiet frontier.service, /usr/bin/systemctl --no-pager --full status frontier.service
```

部署脚本使用 `BatchMode`，因此 SSH 别名应提前配置密钥登录；它不会等待 SSH 密码或
sudo 密码，避免无人值守部署永久卡住。脚本会通过一个短暂的交互 Bash 读取远端
`~/.bashrc` 配置的 PATH，以支持安装在用户目录中的 Node.js 和 pnpm；实际拉取、构建
和重启仍在非交互、遇错即停的 Shell 中执行。健康检查会读取远端 `.env` 中实际的
`FRONTIER_HOST` 和 `FRONTIER_PORT`，不要求服务使用默认的 `127.0.0.1:8000`。

首版没有应用层登录。若不用 Tailscale，必须在应用前增加等价的设备身份验证；不能直接把 8000 暴露到公网。
