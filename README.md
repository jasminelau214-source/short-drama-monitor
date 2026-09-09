# 短剧热播榜研究工具｜V1.2 Beta

当前版本定位：**长期可审计的海外短剧榜单数据库 + 人工/AI分析工作流**。

## 当前已实现

- 4个平台：NetShort、DramaWave、MoboReels、ReelShort。
- 已纳入真实采集日：2026-09-04、2026-09-07、2026-09-08。
- 每日榜单按日期追加历史，不覆盖旧排名。
- 2026-09-08 当前榜单：4平台 × Top10 = 40部。
- 新旧剧识别后，当前历史独立剧目约57部。
- 题材、细分赛道、受众、故事核、故事皮、核心冲突、核心爽点可展示与编辑。
- 本土化板块已加入：本土化完成度 / 本土化判断 / 明显不适配点。
- 研究证据元数据已单独保存，可记录来源、置信度和待核事项。
- 管理后台 `/review` 使用管理员认证，未配置 `ADMIN_PASSWORD` 时不会自动放行。
- 采集入口 `/collect` 已可上传 JPG / PNG / WebP 榜单截图到待分析区。
- `/health` 提供基础运行健康信息。

## 当前仍未完全自动化

以下能力仍属于下一阶段，不应视为已经完成：

1. 上传截图后的自动 OCR 与平台专用解析器。
2. 自动完成 Rank / Title / Heat / Tag 质检后生成待确认草稿。
3. 草稿与正式库的完整隔离、字段锁、review history。
4. 新剧自动公开资料研究与模型 Adapter 路由。
5. 第一集逐镜、最后免费集、首个付费集的自动视频分析。
6. Render 上 SQLite / 上传截图的可靠持久化；正式长期运行建议迁移 Postgres 或持久磁盘。
7. 更完整的字段级 source/evidence/confidence 数据表。

## 数据架构

- `data.json`：基础历史数据。
- `updates/*.json`：按日期/平台追加的榜单观察与内容补丁。
- `research_meta.json`：新剧来源、置信度、待核事项。
- `short_drama.sqlite3`：管理员人工覆盖字段（本地/当前实例）。
- `uploads/`：采集入口上传的原始截图（运行时目录）。

## 运行

Windows 双击 `启动短剧研究工具.bat`，或：

```text
python app.py
```

默认本地地址：`http://127.0.0.1:4173/`

## 公网页面

- `/`：市场仪表盘
- `/collect`：榜单采集中心（管理员）
- `/review`：资料审核/补全后台（管理员）
- `/api/data`：公开结构化数据
- `/health`：健康检查

## Render 部署

`render.yaml` 当前配置为：

- Python Web Service
- `python -m compileall app.py` 构建检查
- 自动部署 `main`
- `/health` 健康检查
- `ADMIN_USER=admin`
- `ADMIN_PASSWORD` 由 Render Secret 配置

> 注意：Render 免费 Web Service 的本地文件系统不是可靠长期数据库。正式生产环境不要把人工审核结果和上传截图只依赖实例本地 SQLite/文件目录。

## 2026-09-09 发布状态

本轮发布用于把 9/8 四平台 Top10、9/7 待补分析、本土化字段、证据元数据和可用采集入口纳入同一版代码/数据基线。详见 `RELEASE_2026-09-09.md`。
