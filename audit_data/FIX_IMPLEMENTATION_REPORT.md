# 9/16–9/17 错误修复实施说明

状态：**审计分支已实现修复，尚未写入生产数据库、未改正式 Scheduler、未部署 Render 生产服务。**

## 1. 程序为什么会错

| 环节 | 根因 | 历史表现 | 修复方式 |
|---|---|---|---|
| 日榜事实 | 同日可有多个完整 run，但研究任务没有“最新 run 权威性” | 9/17 早榜掉榜剧仍继续深研 | worker 在外部搜索/模型调用前校验最新完整 run |
| DB 自动入队 | trigger 只 upsert 当前出现 title，不处理后来消失 title | stale task 留在队列 | 已写 DB trigger 修复草案：later run supersede unfinished old tasks |
| 深研结果 | 过去 COMPLETE 没有确定性 schema write gate | 旧自由文本 genre、缺关键字段仍显示已研究 | 新增 deterministic validator；不合规统一 REVIEW_REQUIRED |
| Web/App 分层 | 历史 Web 任务曾进入研究队列 | 9/16 115 个 Web PENDING task | Web 前端不再 join research_tasks；当前 trigger 已限定 App |
| 前端 coverage | 直接使用历史所有 target 的 latest coverage | 旧分类 shelf、旧 target、Top60 都进入“采集数量” | 按 pilot_scope 的当前排名语义生成 current-scope coverage |
| 排名语义 | target enabled/历史 success 不等于当前口径可用 | ShortMax 分类货架、Mobo Trending Series 等混入 | 平台 + rankingType + category + TopN 共同校验 |
| UI 文案 | Web “采集通过”被写成“进入分析队列” | 给用户造成 Web 会自动深研的错误理解 | 改为“Web采集证据 / 证据采集通过 / 不自动进入内容深研” |

## 2. 已实现的代码保护

### A. latest-run supersession guard
文件：`research_guard.py`、`research_worker.py`

研究 worker 在 Tavily/Gemini 调用之前检查：
- 必须是 SHORT_DRAMA_APP；
- 原任务 run 必须可解析；
- 同日、同平台、同 sourceType、同 targetKey 只认最新完整 run；
- title 若已从最新 run 消失，任务转 `REVIEW_REQUIRED`，错误码 `SUPERSEDED_BY_LATER_RUN`；
- 不再浪费外部 API，也不会继续影响当日日榜分析。

9/17 的 `His Neglected Wife Is The Top Scientist` 已被固定为该测试的回归样本。

### B. research schema write gate
文件：`research_validation.py`、`research_pipeline.py`、`research_worker.py`

现在 COMPLETE 前必须再次确定性校验：
- required keys 完整；
- genre / audience / newnessResolution / confidence 使用受控枚举；
- needsGPT 类型正确；
- missingFields / auditNotes / sourceUrls 类型正确；
- sourceUrls 只能来自本轮实际证据；
- 空白核心字段必须在 missingFields 中声明。

任何模型输出即使声称 COMPLETE，只要不满足契约，就转 `REVIEW_REQUIRED`，不能写入前端“已研究”覆盖层。

### C. current-scope frontend coverage
文件：`live_observations.py`

前端 Web coverage 不再直接使用历史 `v_daily_monitoring_coverage` 作为当前口径，而是：
- 读取 `pilot_scope.json`；
- 只保留与当前平台排名语义相同的 Official Web target；
- 不接受不同 shelf/category；
- 历史 TopN 大于当前 TopN 时只截取当前 TopN，不把后面的行计入；
- 历史 TopN 小于当前 TopN 时不补齐；
- disabled target 不进入当前口径；
- raw coverage 仍保留为 `rawCoverage` 供审计，不删除证据。

因此 9/16 当前严格口径会落到：
- DramaBox Trending：Top60 只取 rank 1–10；
- GoodShort Top in GoodShort：10；
- 合计 20；
- ShortMax 分类 shelf、ShortMax Most Popular Top8、MoboReels Trending Series、NetShort Homepage Top10 等不进入当前口径。

### D. Official Web 不自动深研
文件：`live_observations.py`、`runtime_ui_patch.py`

Web collection 不再读取/绑定历史 research_tasks。
UI 状态改为：
- `证据层已采集（不自动深研）`
- `待补采/复核`
- `待质量复核`

同时去掉“本批次应显示 2026-09-16”这类硬编码日期文案。

## 3. 数据库 P0 修复草案

文件：`audit_data/proposed_db_supersession_fix.sql`

尚未执行。

草案补充：
1. 只有 latest complete App run 才能入队；
2. 旧 run 被再次更新/重放时，不得反向覆盖 newer run；
3. later run 出现后，已经掉榜的 PENDING / RESEARCHING / NEEDS_GPT / FAILED 任务转 REVIEW_REQUIRED；
4. COMPLETE 研究结果保留为研究资产，但不自动代表该剧仍属于当天最新 TopN。

P1 暂不实施：未来如果同一 App 同时有多个需要深研的 target，应给 research_tasks 增加 source_type + target_key，并升级唯一键；当前唯一键 `(date, platform, normalized_title)` 只适合“每平台一个研究型 App target”的现阶段结构。

## 4. 回归防错

新增：
- `test_research_guard.py`
- `test_research_validation.py`
- `test_web_current_scope.py`
- `test_runtime_ui_patch.py`
- `.github/workflows/audit-backfill-check.yml`

覆盖：
- 9/17 早榜剧掉出晚榜；
- title 仍在最新榜时继续研究；
- 不同 target 不相互 supersede；
- Official Web 任务拒绝自动研究；
- 旧自由文本 genre 不得 COMPLETE；
- required key 缺失不得 COMPLETE；
- 非本轮证据 URL 不得进入 research；
- 9/16 DramaBox Top60 只取前10；
- ShortMax 分类 shelf / Top8 不得补主榜；
- Web 不再自动绑定研究任务；
- UI 不再显示“进入分析队列”的错误文案。

GitHub Actions 的 audit regression workflow 已有成功运行记录；后续该分支每次代码变动都会再次执行检查。

## 5. 仍未对生产执行的事项

生产环境当前仍保持原状。下一阶段正式晋级时需要按顺序处理：
1. 先部署 audit 修复代码到 staging，人工看一次前端；
2. 再应用 DB supersession trigger；
3. 处理 9/16 的 115 个旧 Web PENDING tasks，使其退出活跃队列但保留审计记录；
4. 9/17 stale COMPLETE 研究保留资产，但从当日最新榜分析集合排除；
5. 9/17 当前 9 个 MoboReels new title 重新通过现行 schema；
6. 再将前端/程序修复晋级生产。

这样修正的是“产生错误的路径”，而不只是把两天的错误数字手工改掉。
