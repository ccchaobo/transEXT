# 文献管理与 Zotero 联动验收

日期：2026-10-12。用户最新成功标准为导入 Zotero 与联动，不要求 Word 插入。

| 检查 | 结果 | 证据范围 |
| --- | --- | --- |
| 整体后端回归 | 719 PASS | 真实隔离数据库、PDF、服务与任务逻辑；不代表真实 Zotero 写入 |
| 阅读器前端逻辑 | 19 PASS | 请求竞态、路由、页面定位和排版边界 |
| 连接组件逻辑 | 6 PASS | 实际 bootstrap.js 在模拟原生 API 中执行；鉴权、重放、DOI 复用、冲突和卸载 |
| 本地文献管理 Chrome | 25 PASS | 多文件、分类、标签、元数据、中文搜索、已读、收藏、回收站、恢复、原件、阅读和分页；1440/1024/768/390px |
| 真实 Zotero 浏览器 | 8 PASS | 58 书目、搜索、RIS 下载、富文本标题显示、写回预览；1440/768/390px |
| 真实 Zotero 连接 | PASS | 0.1.1 已加载于 Zotero 9.0.6；配对授权通过；126 记录、58 书目、23 个可见 PDF 附件 |
| 抓取预览 | PASS | DOI 与 BrLP arXiv 网页各识别 1 条；未保存原生条目 |
| 原生导出 | PASS | BibTeX、RIS、APA 文本返回实际内容 |
| 用户 BrLP 21 页排版回归 | PASS | 页框一致，段落/图表碰撞为 0，原件指纹一致，全文翻译 succeeded |
| 真实 Zotero 新增、写回、附件复制、合并 | 未执行真实库修改 | 已做隔离数据库及组件边界测试，不能称为真实库写入验收 |
| Word 插入与云端一致性 | 不作为本次完成条件 | 最新用户取消 Word 插入；云同步由原生 Zotero 核对 |

未在用户 Zotero 库添加测试文献或执行合并/删除。本地原有文献数仍为 7。

报告：[live-report.json](live-report.json)、[browser-report.json](browser-report.json)、[文献管理报告](../library-management/report.json)、[21 页布局报告](../layout-alignment/report.json)。

界面截图：[桌面 Zotero](catalog-1440.png)、[窄屏 Zotero](catalog-390.png)、[写回预览](write-preview.png)。

脚本：`scripts/verify_library_browser.py`、`scripts/verify_zotero_browser.py`、`scripts/verify_real_layout_alignment.py`；测试：`tests/integration/test_zotero.py`、`tests/frontend/zotero-bridge.test.cjs`。

已知维护限制：当前组件采用手动升级，自动升级已设为 Off；HTTPS 更新渠道未部署。参见 [集成规范](../../../zotero-integration.md)。
