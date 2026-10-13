# Docker 部署验收

2026-10-13，Windows Docker Desktop Linux/amd64，引擎 29.8.1、Compose 5.5.1。使用 `research-reader-deploy-test` 独立项目、8876 端口和新数据卷，没有迁移或改变用户原生文献库。

## 已验证

- 官方 Python 基础镜像与 GROBID 0.9.1-crf 镜像实际下载成功，摘要固定在 Dockerfile/Compose。
- 完整镜像成功构建；镜像内 RapidOCR 模型初始化、中文 TrueType 字体注册通过。
- 实际运行 `deploy.ps1` 返回成功；API、worker、GROBID 三服务健康。
- 自编 PDF 经真实后台解析，真实 GROBID 返回结构提示并匹配 2 个块；原件哈希、文本块和 PNG 预览通过。
- 容器内 DOCX 转换器真正生成 PDF，中文可以从 PDF 中提取；RapidOCR 真正识别自编扫描图片中的文字。
- API 跨站写入仍拒绝；当前未接入的 DOCX/图片导入任务明确报告 `unsupported_format`。
- 强制重建 API/worker 后，完整原件和阅读视图状态保持。
- `deploy.ps1 -Action Backup` 实际产出备份及 SHA-256，随后恢复三服务健康。
- 备份恢复到新的空 `research-reader-restore-test-20261013` 卷；数据库完整性通过，11 条自编测试记录恢复，应用 UID 可以正常写入并删除探测设置。
- Linux 镜像中的 Bash 语法检查通过；源码部署 ZIP 的必要文件、清单和私有数据排除检查通过。
- 后端全部 748 项、前端全部 31 项测试通过。

功能测量与原始结果见 [report.json](report.json)，部署/备份/恢复汇总见 [deployment-checks.json](deployment-checks.json)。测试未调用付费翻译或 AI 服务，未使用用户文献。

## 现有功能边界

正式导入 worker 目前只支持 PDF。OCR 和 DOCX 转换器作为镜像组件实际可调用，但未接入导入任务；本次部署没有扩大产品功能范围，也没有将“组件可用”报告为端到端导入成功。Zotero 桌面和 Word 不在容器中，远程服务器联动尚未实现。

当前验收覆盖本机 Docker Linux/amd64。GitHub Actions 对源代码提交 `278b095698f7494c3fbb7fa82ac4e84dcf7fd7e2` 的独立 Linux 部署验收也已成功，并产出未过期的 `research-reader-docker` ZIP（artifact 11785728271，795314 bytes）。[云端流程与下载产物](https://github.com/ccchaobo/transEXT/actions/runs/38340204166)。该结果独立于本机测试；本次文档补录不改变被验收的运行代码。

实际本机 Windows 双击脚本内部命令已验收，没有声称 macOS、ARM 或公网多人部署通过。

## 宿主机 WSL 故障

构建期间宿主机出现 `wslservice.exe 2.7.14`、`0xc00000fd` 崩溃，导致 Docker API 返回引擎错误。首次只重启 Docker 暂时恢复，随后再次发生。正常停止 Docker、停止 WSL 虚拟机再启动 Docker 后，最终功能、备份和恢复测试完成；没有删除分发、镜像、卷，也没有恢复出厂设置。具体崩溃根因未确认，不能把本次恢复称为系统问题已根治。事件摘要见 [host-engine-event.json](host-engine-event.json)。
