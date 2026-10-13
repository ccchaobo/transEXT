# GROBID 解析辅助

2026-10-13：完整 Docker 一键部署改为 API、worker、官方 GROBID 独立服务和共享数据卷，见 [部署说明](docker-deployment.md)。该部署显式允许内部服务名 `grobid`，默认原生配置仍只允许回环地址；下面的本机历史构建与启用记录保持原意。

## 工程选择

保留原 PDF 提取、阅读顺序、行坐标、原页画面及图表保护，GROBID 只提供可核对的结构证据。
替换整个解析器需要重建段落和坐标映射，会牵涉译文缓存、标注和视觉版式；
辅助模式开发成本较低，也允许单独升级或关闭 GROBID，因此本次采用辅助模式。

[官方接口](https://grobid.readthedocs.io/en/latest/Grobid-service/) 返回 TEI，
包括正文结构和参考文献；[坐标说明](https://grobid.readthedocs.io/en/latest/Coordinates-in-PDF/)
指出页码从 1 开始，坐标基于 PDF。辅助器把页码转换为内部 0 起始索引，仅用于消除文本匹配歧义，
没有把 GROBID 坐标直接替换成阅读器坐标，也不声称两种坐标系统未经校验就可互换。

## 行为

- 默认关闭，启用后在新文献解析的布局阶段调用本地服务。
- 原文规范化后完整匹配，必要时结合页码；重复文字、结构冲突和匹配失败保留原判断。
- 辅助校正章节、正文、摘要和图注类型，重新建立章节路径；原文字串、阅读顺序和行坐标保持原流程。
- GROBID 的 reference 标签本身不会取消正文翻译。仍须先出现显式 References／参考文献标题。
- 服务超时、非 TEI、损坏 XML、HTTP 错误等会给出告警并回退；没有自动重试。
- 修订 stats.grobid 记录 disabled、ok 或 fallback，以及匹配块数；块 metadata.grobid_type 保留匹配证据。
- 已有文献、译文和标注不会自动重解析。显式重解析仍建立新修订，旧缓存不能据此冒充新修订译文。

## 本机启用

选择官方 CPU 轻量镜像 0.9.1-crf，单独服务，不作为应用启动的强制依赖。
完整镜像更大、资源要求更高；本次轻量选择的资源与准确性权衡参考
[官方部署说明](https://grobid.readthedocs.io/en/latest/Grobid-docker/)。

```powershell
docker compose -f deploy/grobid.compose.yaml up -d
python scripts/verify_grobid.py
```

确认真实自编样例通过后，在项目 .env 配置：

```dotenv
RR_GROBID_URL=http://127.0.0.1:8070
RR_GROBID_TIMEOUT=45
```

正常启动时 API 和 worker 都读取 .env；配置后重启这两个进程。
若使用 RR_ENV_FILE 指定其他文件，应写入该文件；环境变量仍优先。

## 数据范围与限制

只接受本机 HTTP 回环地址，不支持第三方在线演示服务。请求发送整份待解析 PDF 到本机服务，
关闭 header、citation、funder 的外部信息补全，HTTP 客户端不使用系统代理、不跟随重定向。
应用没有新增云端文献上传路径；部署者修改 GROBID 自身配置后的网络行为须另行检查。
XML 有大小上限并拒绝实体声明。

此接入不会替代 OCR，也不会把 TEI 变成同版式译文 PDF。完全匹配策略有意偏保守，
复杂断行、跨栏合并、拆分标题及结构识别错误可能无法匹配；未确认内容不强行改分类。

## 此前部署阻塞记录（已解决）

客户端请求、TEI 读取、文本／页码匹配、原页与坐标保持、解析故障回退已通过本地测试。
这些测试使用明确标记的 TEI 样例，不能代替真实 GROBID 模型质量验收。
本机 Docker 已启动，但两次官方镜像拉取均返回 registry-1.docker.io 网络 EOF，
真实 GROBID 服务没有启动，当前用户服务中此辅助功能保持关闭。
真实服务验证入口已准备好；下载恢复后执行上面的两条命令，再启用配置。


2026-10-12 再次恢复 Docker Linux 引擎后，官方镜像仍下载失败；
Google 官方 Docker Hub 缓存 mirror.gcr.io 也返回 EOF。
系统代理可以访问 GitHub，但 Docker Hub 注册表及镜像标签接口的 TLS 连接失败。
镜像尚未下载，GROBID 辅助仍保持关闭；需要可访问镜像仓库的网络线路后再执行真实验收。


## 当前启用状态（2026-10-12）

本机 GROBID 0.9.1 已启动并通过真实解析验证，`.env` 已配置本机地址，API 与 worker 已重新载入。
后续新文献使用辅助识别，服务不可用仍回退；已有 6 份文献的译文、标注及活动修订没有被自动改写。
功能发布默认值仍为关闭，本机已显式启用。

当前使用官方源码标签 0.9.1，提交 `523bb8e97f357e93682dc4eaa6d22faa8b5b93de` 构建。
构建安装 JDK 21，运行镜像使用已缓存的 Linux Java 基础镜像；没有修改其他项目容器。
Gradle 9.6.1 下载包通过 SHA-256 校验；公共依赖通过
[华为云 Maven 镜像](https://www.huaweicloud.com/special/maven-jingxiang.html) 和
[阿里云仓库服务](https://maven.aliyun.com/mvn/guide) 获取，版本沿用源码。
启用的是 CRF 推理路径，构建上下文不包含训练语料，暂不提供语料训练部署能力。

源码编译 22 项任务完成；完整镜像提交曾出现 Docker EOF，随后从本次构建容器保留发行 ZIP，
用轻量运行镜像完成打包。修正了 Windows CRLF 导致 Linux pdfalto_server 无法执行的问题。
运行服务为非 root 用户、仅绑定 127.0.0.1:8070，限制内存与 CPU；PDF 子进程内存和超时也已设置。
完整源码构建与轻量运行配方均保存在 deploy；本机使用的是轻量运行配方。

### 日常启动与验证

```powershell
docker compose -p research-reader-grobid-src -f deploy/grobid-source.compose.yaml up -d
./scripts/start_reader.ps1
python scripts/verify_grobid.py
python scripts/verify_grobid_pipeline.py
python scripts/verify_active_stack.py --require-grobid
```

容器设为 unless-stopped；Docker 引擎启动后会自动恢复。阅读器启动脚本保留已有配置。
`--require-grobid` 用后台任务的真实结果确认辅助器已运行，不能用仅服务存活替代。

### 重新打包已有发行产物

运行目录由 `scripts/prepare_grobid_runtime.py` 管理。首次准备需为空目录；本机已有管理标记。

```powershell
python scripts/prepare_grobid_runtime.py
docker build -f deploy/Dockerfile.grobid-runtime -t research-reader-grobid:0.9.1-source var/grobid-runtime
```

默认基础镜像为 Temurin 21。镜像仓库不可访问时，可显式传入已确认含 Linux Java 的缓存基础镜像。
本机缓存来自 Neo4j 的公共基础镜像，仅用作 Java 运行环境，入口已替换，未启动或挂载数据库。
TODO：镜像仓库恢复后，改回 Temurin 基础镜像并重新验收，减少备用镜像体积和额外软件。
本次为使用离线缓存临时采用 legacy builder，未改 Docker 全局配置；正常构建应使用默认 BuildKit。

### 验收结论与边界

真实服务自编 PDF 解析通过；隔离阅读器流水线匹配 3 个块，原图及坐标可读取，临时数据库清理成功。
实际运行后台任务也确认 grobid.status=ok。
对一份实际论文在本机验证：识别 11 个章节、21 个正文、4 个图注、1 个摘要、34 个参考文献条目，
20 个结构提示能匹配当前原文；文字及坐标保持一致。仅在内存中比较，没有改写其已存储修订。
这不是全部论文质量通过的证明；未匹配或冲突仍保留原判断。
完整后端 702 项通过（3 条既有警告），浏览器 78 项通过。
[最终证据](acceptance/2026-10-12/grobid-enabled/report.json)。
