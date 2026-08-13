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
- 修改 Web 中的调度时间后，重启 Server 以重载任务；
- 备份仓库位于 `data/backups/git`，PDF 和 SQLite 不进入该仓库；
- 每次升级先执行 `pnpm build`，再重启 `frontier` 服务；
- Codex/provider 状态、额度和凭据由维护者负责，失败原因会留在任务页。

## 5. 从开发机一键更新

项目提供了固定部署到 SSH 别名 `SelfALI` 的脚本。它会在服务器的
`/home/byml/agent/frontier` 中执行仅快进拉取、同步前端依赖、构建 Web，随后重启并
检查 `frontier.service`：

```bash
./deploy/deploy-self-ali.sh
```

脚本只部署已经提交并推送到 Git 远端的代码，不会自动提交或推送本机修改。服务器
工作区若有未提交的受版本控制文件修改，部署会停止，避免覆盖服务器上的改动。

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
