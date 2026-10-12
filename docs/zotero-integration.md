# Zotero 本机联动规范

2026-10-12。用户选择对接现有 Zotero，并已批准自定义组件的长期安装、配对密钥存储及内部 API 权限。组件已在本机 Zotero 9.0.6 加载，版本 0.1.1；系统应用标识仍显示旧的 9.0.5，不据此判断运行版本。最新用户指示不需要 Word 插入验收。

## 工程选择

方案一是在阅读器重建网页识别、CSL 引用与云同步，开发与维护成本高，并容易使两套书目互相覆盖。方案二是以 Zotero 为书目管理端，阅读器负责不可变原件副本、翻译与阅读状态。按用户选择采用方案二。

Zotero 9 的标准本机 API 用于只读查询和格式导出；写入使用可审查的本机组件及 Zotero 原生对象 API。没有直接读取或修改 Zotero SQLite 文件。阅读器只绑定回环地址，同源规则仍生效。

## 权限与安装

组件只注册 Zotero 自带 HTTP 服务下的 `/research-reader/*` 接口。非公开状态接口校验配对密钥；状态接口仅返回连接标志，不返回密钥。配对密钥存储于阅读器秘密设置和 Zotero `extensions.zotero.researchReader.token`，不发送给浏览器。

初次配对需先保存并正常退出 Zotero。执行 `python scripts/install_zotero_bridge.py --profile <已确认的 profile>` 后，在 Zotero 插件管理器选择版本化 XPI。复制文件和配对成功仅表示准备完成，必须检查组件状态接口授权成功才能宣布连接可用。正式安装后的组件可通过插件管理器禁用或卸载。

当前为本地手动发行，组件自动升级已设为 Off。Zotero 9 要求声明 HTTPS 更新地址，清单保留本机 HTTPS 地址，但本项目没有运行该更新服务。不要声称有自动更新功能，也不要降低 Zotero 的全局更新安全检查。

TODO：只有部署可验证的 HTTPS 更新渠道和版本化安装包后，才发布对应清单并启用组件自动更新。保留手动升级的原因是本组件尚无已发布的更新渠道；不借用第三方域名或降低全局安全设置来绕过要求。

源码与具体权限：[组件说明](../plugins/zotero-reader-bridge/README.md)、[bootstrap.js](../plugins/zotero-reader-bridge/bootstrap.js)、[manifest.json](../plugins/zotero-reader-bridge/manifest.json)。

## 身份、关联与数据所有权

缓存条目的身份为 `(Zotero 来源实例, library_id, item_key)`，禁止只凭 item key 或标题跨库关联。

- `ZoteroRecord` 保存原始书目 JSON、信息指纹、标题、搜索文本、类型、父附件及目录缓存时间。
- `ZoteroBinding` 保存本地 document_id 对应的原生条目、附件、已同步指纹及共享字段基线。
- 共享字段为标题、作者、年份、出版来源、DOI、链接、类型与摘要。本地笔记、阅读状态、收藏、分类、译文、标注不自动写回 Zotero。
- Zotero 富文本标题在阅读器列表显示为可读文本，完整原始 JSON 仍保留；未改动标题时不写回，从而保留原生斜体和上下标。

目录同步更新本机缓存，不等于 Zotero 云端同步。原生来源删除不会删除本地原件副本。

## 双向流程与冲突

从 Zotero 导入：先同步目录，再选 PDF 附件；阅读器复制文件并按文件指纹复用，建立书目关联。Zotero 原件不变，没有本机副本时明确提示先下载。

从阅读器导入：详情中选择“Zotero 关联与写回”。优先关联已有书目，明确确认是同一文献后采用其书目信息；否则新增到选择的可编辑文献库，可勾选同时复制 PDF。已有同 DOI 条目优先复用，提示先关联，避免新增或覆写。相同 PDF 附件通过内容校验复用。

写回仅提交相对基线改过的字段，保留原生完整日期、未改动的姓名结构以及类型专属字段；书籍映射 publisher、学位论文映射 university、报告映射 institution。已关联条目的类型需在 Zotero 修改后同步。

每次写回或合并核对当前指纹。双方均改动时不自动覆盖；核对窗口显示本地与 Zotero 的差异，并提供采用原生版本或确认本地写回。核对后原生条目再次变化，操作仍被拒绝。

跨 Zotero 写入与阅读器数据库无法构成单一事务。新增操作在原生条目保存导入操作关系，使同一本地文献的请求重放复用条目；附件通过内容检查复用。若书目保存成功而附件复制失败，提示错误并保留已有书目关联，重试不会重复新增书目。

## 网页、导出和重复项

网页或 DOI 抓取使用 Zotero 原生 translator，先预览再确认保存，不在预览阶段写入 Zotero。需登录或反爬的网页可能无法识别，可用浏览器 Zotero Connector 保存后刷新目录。

BibTeX、RIS 与 APA 参考文献由 Zotero 生成。APA HTML 转为可读文本后导出；个人库与群组库使用各自原生地址，不能导出错误的库。

重复项按相同 DOI，或相同标题、年份、首位作者列候选。候选不自动合并，用户确认主条目后调用 Zotero 原生合并并迁移本地关联。不同类型不合并；附件和引用关联由 Zotero 原生流程负责。

## 原生流程与当前验收边界

Word 与 Zotero 云同步入口仍可使用原生流程，但本次用户明确取消 Word 实际插入验收。后台任务的 running、finished、failed、unknown 只描述流程，不证明已经插入动态引用或云端状态已一致；重启后任务状态可能 unknown。

本次真实验收：授权组件加载；126 个本机记录、58 篇书目、23 个可见 PDF 附件；实际 BibTeX/RIS/APA 导出；DOI 和 arXiv 网页抓取预览；真实 Chrome 的目录搜索、下载和 1440/768/390px 窗口显示。未创建、更新、合并或删除用户的真实 Zotero 条目。

写回、复制、关联、冲突、类型映射和权限边界由隔离数据库/HTTP 边界测试验证，组件鉴权、重放、DOI 复用和冲突保护由 JavaScript 组件测试验证。它们不冒充真实 Zotero 写入、群组协作、云端同步或 Word 文档验收。用户第一次写入时仍需核对实际条目和附件结果。

证据：[真实连接报告](acceptance/2026-10-12/zotero-integration/live-report.json)、[浏览器报告](acceptance/2026-10-12/zotero-integration/browser-report.json)。

## 官方依据

- [Zotero 本机 API](https://www.zotero.org/support/dev/web_api/v3/local_api)
- [Zotero 插件清单与生命周期](https://www.zotero.org/support/dev/zotero_7_for_developers)
- [Zotero JavaScript API](https://www.zotero.org/support/dev/client_coding/javascript_api)
- [分类与标签](https://www.zotero.org/support/collections_and_tags)
- [重复项合并](https://www.zotero.org/support/duplicate_detection)
- [原生同步](https://www.zotero.org/support/sync)
