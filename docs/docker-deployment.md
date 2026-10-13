# Docker 一键部署

## 部署内容与选择

包含网页/API、后台任务、CPU GROBID 0.9.1、RapidOCR 及其 ONNX 模型、中文字体和持久化文献库。前端随 API 镜像发布，不需要额外 Node 构建或本机 Python。默认使用固定版本和摘要的官方 Python、GROBID 镜像，Python 运行依赖由 `deploy/requirements.lock` 锁定。

GROBID 官方轻量镜像比完整深度学习镜像资源要求低，适合本项目结构辅助用途；仍有准确性权衡，不能称为完整深度学习模型部署。[官方说明](https://grobid.readthedocs.io/en/latest/Grobid-docker/)。既有源码构建配方保留，默认部署不依赖本机曾经构建的私有镜像。

推荐 Linux x86_64 或 Windows Docker Desktop 的 Linux 容器模式，Docker Compose v2.20 以上，至少 4 CPU、8 GB 可用内存和 10 GB 空闲磁盘。仅验收 amd64。首次部署需要联网下载镜像和 Python 依赖；本包不是离线镜像包。

## 一键启动

下载 GitHub 项目 ZIP 并解压，或克隆仓库：

```sh
git clone https://github.com/ccchaobo/transEXT.git
cd transEXT
bash deploy.sh
```

Windows 双击 `deploy.cmd`。也可以在 PowerShell 执行：

```powershell
.\deploy.ps1
```

入口：`http://127.0.0.1:8765`。脚本检查引擎、生成私有 `.env.docker`、构建镜像、等待三个服务健康，失败时返回非零退出码，不把“容器已创建”当成部署成功。

已有本机阅读器占用 8765 时：

```powershell
.\deploy.ps1 -Port 8876
```

长期修改端口或 Compose 项目名请编辑 `.env.docker` 的 `RR_HTTP_PORT`、`RR_COMPOSE_PROJECT`。命令行 `-Port`/`-Project` 只影响本次执行；后续操作使用同样参数或持久配置。

首次进入“翻译设置”配置服务、模型和密钥。翻译与 AI 仍需要你自己的外部服务账户；镜像不包含密钥，测试不自动产生付费请求。网页可以无密钥导入、解析和阅读。访问宿主机上的 Ollama 时，Docker 内的 `localhost` 表示容器自身，应按 Docker 平台使用宿主机地址配置。

## 构建缓存

固定运行依赖与项目代码分层构建。普通一键启动不需要缓存镜像；首次安装依赖后，修改代码或 README 只需重新打包项目，不反复下载 OCR、PDF 等大型依赖。pip 使用独立构建缓存，运行镜像仍为原来的官方 Python 基础及非 root 用户。

已有可信的本项目镜像时，可显式指定 `docker compose --env-file .env.docker build --build-arg DEPENDENCY_CACHE_IMAGE=research-reader:local api`。构建会核对 Python 可执行文件与目标基础镜像一致，并逐项核对 pyproject 和锁文件固定版本；缺包或版本不一致时重新安装依赖，Python 基础不一致时拒绝使用该缓存。随后仍按原 Compose 方案部署。不通过改版本或忽略下载哈希错误来绕过失败。

单用户入口只发布本机端口。远程 Linux 服务器可通过 SSH 转发访问：`ssh -L 8765:127.0.0.1:8765 user@server`。公网或多人共享部署还需独立身份验证和 HTTPS，不属于当前单用户部署包。

## 数据与组件边界

`reader-data` 命名卷保存 `/data`：SQLite、原件、解析修订、译文、标注、目录、文献管理信息和服务配置。API/worker 共享该卷；启动顺序避免初次建库竞争。正常 `Stop` 不删除卷；保持项目名即可重用旧数据。不要使用 `docker compose down -v` 作为日常停止命令。

API/worker 以 UID 10001 运行，根文件系统只读，可写临时文件在 `/tmp`。GROBID 不发布宿主机端口，只在内部网络访问，且无外网通道；PDF 的外部信息补全关闭。应用通过部署者明确设置的 `RR_GROBID_ALLOWED_HOSTS=grobid` 放行内部服务名，默认原生运行仍只允许回环地址。

默认把 GROBID 健康作为首次启动依赖；运行期间故障仍按原有规则告警并回退。API 健康检查验证 HTTP 和数据库，worker 检查进程存活和数据库可读；真正完成任务由功能验收检查，健康不等于解析质量保证。[Compose 启动依赖说明](https://docs.docker.com/compose/how-tos/startup-order/)。

正式后台导入任务当前只接入 PDF。OCR 模型和 DOCX 内置转换器已随镜像安装并分别做真实组件调用，但仍未接入导入任务；上传 DOCX/图片会明确记录 `unsupported_format`，扫描 PDF 仍需后续接入 OCR 流程。该既有缺口见 `docs/limitations.md` 第 26 项，不能把模型可加载说成端到端导入可用。DOCX 组件保持内置转换和保真告警，未新增 LibreOffice 排版保证。

Zotero 桌面及连接组件在用户电脑上单独安装；此服务器包包含联动代码，但不会把桌面 Zotero 或 Word 装进容器。当前本机回环联动不能直接跨服务器使用，不应把服务器部署说成 Zotero 桌面联动已经验收。

## 日常操作与备份

Linux：`bash deploy.sh status`、`bash deploy.sh logs`、`bash deploy.sh stop`、`bash deploy.sh backup`。Windows 使用 `.\deploy.ps1 -Action Status/Logs/Stop/Backup` 中对应动作。

备份会正常停止 API/worker、用 SQLite backup API 生成一致性数据库、打包原件/派生资源/导出，然后重新启动服务。输出在 `backups/`，附 SHA-256 文件。备份包含私有文献和配置，目录已从 Git 和 Docker 构建排除。升级代码后运行 `bash deploy.sh update` 或 `.\deploy.ps1 -Action Update`，先备份再构建启动；不要在未备份时切换项目名或删除卷。

恢复只接受空数据卷，不覆盖已有库。将备份及 `.sha256` 放到 `backups/` 后，使用新的项目名，先执行恢复，再启动服务：

```sh
docker compose --env-file .env.docker -p reader-restored run --rm --no-deps -T --user 0:0 --cap-add CHOWN --cap-add DAC_OVERRIDE -v "$(pwd)/backups:/backup:ro" api python deploy/archive.py restore /backup/reader-TIMESTAMP.tar.gz /data
RR_COMPOSE_PROJECT=reader-restored bash deploy.sh
```

恢复校验摘要、路径、文件类型与数据库完整性，设置应用所有权。备份/恢复的临时 helper 使用 root、DAC_OVERRIDE（读取或写入应用 UID 的文件）和 CHOWN（处理归属）。日常 API/worker 权限保持不变。保留原项目数据卷直到新库核对完成。Windows 用 PowerShell 的 `${PWD}` 绝对路径替换备份路径，并用 `-Project reader-restored` 启动。

原生 `var/` 不会自动搬入容器。迁移前应停止原生 API/worker，用同一备份程序对原生工作区生成快照，再恢复到空卷；不能直接复制运行中的 SQLite 单个文件。

## 故障与发布包

端口占用修改 `RR_HTTP_PORT`；GROBID 不健康查看 `docker compose --env-file .env.docker logs --tail=100 grobid`；网络受限可显式配置可信镜像源/`RR_APT_MIRROR`，不自动关闭 TLS 验证。

Windows 报 `DockerDesktop/Wsl/ExecError` 表示宿主机引擎尚未可用。先诊断 Docker/WSL，而非清空本项目文献库；WSL 全局停止与 Docker 重启会影响本机其他容器。实际本机故障证据和恢复/验收结果分别记录，不把暂时恢复称为根因已修复。[Docker 官方诊断](https://docs.docker.com/desktop/troubleshoot-and-support/troubleshoot/)。

`python scripts/package_docker_release.py` 生成 `release/research-reader-docker.zip` 和 SHA-256，包含项目源码、部署脚本与文档，排除运行数据、密钥和历史截图。GitHub Actions 会构建、功能验收并上传部署 ZIP；失败的流程不发布成功产物。下载源代码 ZIP 后也可直接运行一键脚本。

验收记录见 [Docker 部署验收](acceptance/2026-10-13/docker-deployment/README.md)。
