# Zotero 9/10 本机连接组件权限说明

该组件是可审查的本地插件，不是 Zotero 官方插件。当前版本 0.2.0 支持 Zotero 9.0 至 10.0.*；已在 Zotero 10.0.6 验证加载、配对、目录读取和跨系统 PDF 下载。0.1.1 的上限为 9.0.*，升级 Zotero 10 后会被自动禁用，需安装新版 XPI；不会自动恢复。

安装会将组件长期保存到 Zotero 配置目录，并在阅读器本机数据库与 Zotero 设置中保存配对密钥。密钥不进入网页、公开源码或导出文件。Zotero 本身为插件提供完整内部 API 权限；该组件的实际代码只注册 `/research-reader/` 下的接口，不开放新公网端口。

组件能力包括读取书目信息、读取用户选择的 PDF 路径、在用户确认后新增或更新条目、复制选中的 PDF 附件到 Zotero、预览网页/DOI 抓取结果、合并用户选择的重复条目，以及启动 Zotero 原生 Word 引用和云同步流程。

书目更新和合并会核对原信息指纹，冲突时拒绝覆盖。捕获预览不保存条目；保存到哪一个文献库由用户明确选择。组件不注册永久删除 Zotero 文献接口，阅读器里的回收站和删除不会自动删除 Zotero 中的记录。

安装程序会备份设置文件和已有同名组件，不导入、修改或删除文献条目。只有本地 UI 中明确确认的写操作才会改变 Zotero 文献。云同步和 Word 原生引用窗口各自保留原有的账户登录、文档选择与确认流程；请求被接受不等于已经同步或插入引用。

可以在 Zotero 插件管理器中禁用或移除此组件。禁用/移除会注销全部组件接口；关闭连接后，阅读器自己的原件、译文和标注仍保留。修改配对授权时可同时清除阅读器的 `zotero.bridge_token` 和 Zotero 的 `extensions.zotero.researchReader.token` 设置。

源码：[bootstrap.js](bootstrap.js)，清单：[manifest.json](manifest.json)。安装包由上述文件生成，不包含配对密钥。

安装脚本仅准备版本化 XPI 和配对文件；需在正式插件管理器安装，状态接口 `authorized=true` 才说明加载与配对成功。当前手动发行，自动升级设为 Off。清单的本机 HTTPS 地址用于满足 Zotero 清单要求，当前并无 HTTPS 更新服务。TODO：发布受验证的 HTTPS 升级渠道后再启用自动更新，不降低全局安全设置。

已有配对升级时执行 `python scripts/install_zotero_bridge.py --package-only`，在插件管理器安装生成的 `var/zotero-bridge/research-reader-bridge-0.2.0.xpi`。此命令不修改 profile、密钥或文献，不需要退出 Zotero。Docker 数据卷与原本机数据库彼此独立；可执行 `python scripts/pair_zotero_docker.py --profile <实际 profile>`，复用已经授权的配对。脚本核对同一 Zotero 实例后写入阅读器的秘密设置，不把密钥放进参数、文件或输出。

0.2.0 新增受配对密钥保护的 `/file-chunk` 与 `/attachment-upload`，按原生库 ID 和附件 key 读取 PDF，或向明确选择的可编辑父条目传入 PDF。每块最多 256 KiB、每份最多 200 MiB，上传最多同时两份，校验位置、长度、PDF 文件头和完整文件 MD5。MD5 仅作内容完整性及重复附件判断，授权仍依赖配对密钥。下载不接受任意文件路径；上传暂存到 Zotero 临时目录，完成/取消/关闭组件时清理，过期会话在后续上传请求时清理。强制终止程序后的临时文件清理由 Zotero 临时目录生命周期负责。既有本机路径接口为旧阅读器保留。

Windows/macOS Docker Desktop 通过 `host.docker.internal` 访问同机 Zotero，Zotero 的端口仍只在本机监听。浏览器不直接访问组件，Docker 也不挂载 Zotero 数据库或储存目录。远程服务器和 Linux Docker 的桌面回环转发不在本次支持范围内。

新增操作保存导入操作关系用于防止请求重放重复建条目；相同 DOI 提示复用，已有相同 PDF 附件复用。完整日期、未改的作者结构和富文本标题不会被本地摘要字段覆写。详细数据语义和验收边界见 [集成规范](../../docs/zotero-integration.md)。
