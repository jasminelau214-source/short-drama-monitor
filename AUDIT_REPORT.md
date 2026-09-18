# JSM 历史数据专项审计：2026-09-16 / 2026-09-17

> 分支：`audit/backfill-2026-09-16-17`  
> 基线：当前 `pilot/web-top10-8platform`  
> 状态：**AWAITING_USER_CONFIRMATION**  
> 本次未修改生产数据库、正式 Scheduler 或任务一分支；未直接覆盖 9/16、9/17 历史数据。

## 1. 先给结论

两日的异常来自两套不同链路。

- **9/16：Web 历史试验口径失控。** 14 次 collection job 共抓到 165 条原始行；按每个历史 target 只保留最新 job 后仍有 11 个 target、135 条；旧质量门槛判定 SUCCEEDED 的 8 个 target 共 115 条，并且这 115 条全部被建立成 research_tasks。当前基线要求 8 平台各一个选定 Web target、固定 Top10、禁止跨栏目补榜，因此旧的“115 条有效榜单”口径失效。
- **9/17：App 同日重复 run + 深研任务未随最新 run 收敛。** MoboReels/NetShort 各采两次，共 40 条原始观察；正确日快照应按同平台同 target 取最新完整 run，因此为 20 条。MoboReels 第一轮独有的 `His Neglected Wife Is The Top Scientist` 在第二轮已掉出 Top10，但其深研任务仍保留并完成，导致最终有 10 条 COMPLETE，而最新榜单实际只有 9 条 new title 应进入 App 深研候选。
- **9/17 同日 Web pilot 是另一证据层。** 当前 branch 留存的 Web pilot 共 53 条，其中 FlexTV/MoboReels/NetShort/ReelShort 各 10 条、共 40 条通过单次结构完整性；但四个平台当时均只有 1 次有效 run，`pathStable=false`，因此只能作为历史修正候选，不能视为已稳定生产数据。
- **深研结果还存在契约不一致。** 9/17 的 10 条 COMPLETE 中，只有 1 条 genre 符合当前 `research_pipeline.py` 的受控枚举；10/10 的 `research_json` 均缺少当前 schema 必需的 `canonicalTitle / newnessResolution / auditNotes / confidence / missingFields / sourceUrls / needsGPT`。来源证据多数为 MoboReels 官方页，可保留证据，但结果需重新过现行 schema gate。
- **未发现主要的日期偏移问题。** 9/17 App 的 `collectedAt` 与 UTC `created_at` 换算到 +08 后仍落在 9/17；9/16 research_tasks 虽在 9/17 才创建，但其 `collection_date=9/16` 是来源日期，不应把 processing time 当 collection date。

## 2. 数量账本

| 日期/证据层 | 原始采集 | 同目标最新/去重后 | 旧规则“有效” | 当前严格口径候选 | 实际深研 | 当前应进入深研 |
|---|---:|---:|---:|---:|---:|---:|
| 9/16 Official Web 历史链路 | 14 jobs / **165 rows** | 11 targets / **135 rows** | 8 targets / **115 rows** | **20 确认可用候选**；另 20 待语义复核 | **115 PENDING** | **0 自动触发** |
| 9/17 SHORT_DRAMA_APP | 4 runs / **40 rows** | 2 latest runs / **20 rows** | 20 | App层 **20**，不可替代 Web | **10 COMPLETE** | 最新 MoboReels **9** |
| 9/17 Official Web pilot | 8平台 / **53 rows** | 单轮无需 run 去重 | 40 结构完整 | **40 PASS_CANDIDATE**，但尚不稳定 | 0 | **0 自动触发** |

“去重”采用事实键/运行语义，不跨平台把同名剧合并：核心是同日同平台同 source/target 的重复 run 只保留最新完整快照。

## 3. 9/16 平台逐项重判

| 平台 | 历史实际采集 | 当前目标 | 当前判定 | 回填候选 |
|---|---|---|---|---|
| DramaBox | Trending 最终扩到 60 条 | Trending Top10 | 同一官方 channel 语义，可只截取历史 rank 1–10 | **10 条候选** |
| DramaWave | 无 job | Most Trending Top10 | 缺数据 | 0 |
| FlexTV | Top in FlexTV 仅 2 条 | Top in FlexTV Top10 | 不完整，禁止补 3–10 | 0 |
| GoodShort | Top in GoodShort 完整 10 条，en-US | 同目标 Top10 | 语义/URL/数量匹配 | **10 条候选** |
| MoboReels | Trending Series 8 条 | Popular Series Top10 | 栏目语义不一致且缺 2 条 | 0 |
| NetShort | Homepage Top10 10 条 | Trending Now Top10 | 有 10 条，但历史证据未证明两个栏目等价 | **10 条 HOLD/REVIEW** |
| ReelShort | TOP 10 条，但历史 job=NEEDS_REVIEW | 精确 TOP shelf Top10 | 历史证据来自 root 且明确要求复核 canonical shelf | **10 条 HOLD/REVIEW** |
| ShortMax | Most Popular 8 条 + 4 个分类货架共 27 条 | Most Popular Top10 | 8 条不足；分类货架不得拼入主榜 | 0 |

因此，9/16 现在可以形成的**严格修正候选是 20 条**（DramaBox 10 + GoodShort 10）；NetShort/ReelShort 共 20 条只放 REVIEW 区，不纳入可回填候选。

## 4. 9/16 深度分析为什么错

这里需要区分“深研”和“市场分析”。

1. **115 个 research_tasks 实际仍是 PENDING，并未完成正式深研。** 任务 context 明确写有 `sourceType=OFFICIAL_WEB`，reason 为“9/16 Official Web采集入库 / 待内容分析”。这批任务来自旧的临时/人工入队路径；当前数据库 trigger 已有 `sourceType <> SHORT_DRAMA_APP => return` 的保护，因此按当前逻辑不会再自动生成。
2. **当日已经写入的 `marketAnalysis` 输入范围错误。** DramaBox 用 Top60 做趋势总结；ShortMax 的每个分类 run 又重复写入包含 Most Popular、末日、战神、龙族等多个货架的综合结论，形成跨栏目分析污染。
3. **部分内容判断依赖标题/货架名推断。** 例如没有剧情字段的 DramaBox rows 仍生成“强女主/女Boss、龙族/狼人”等内容信号。这类结论不能等同于经过剧情证据核验的内容分析。
4. 当前修正口径：Web pilot 只产出榜单事实和来源证据；内容深研必须走独立研究链路，且只在明确满足研究触发条件时进入，不能从推荐顺序、标题或分类货架补剧情。

## 5. 9/17 为什么数量和深研又错了

### App 数量

MoboReels 与 NetShort 上午/下午各有一轮完整 Top10。原始观察是 40，但日榜事实应选同日同 target 的最新完整 run，因此：

- MoboReels：20 原始观察 → 最新 10
- NetShort：20 原始观察 → 最新 10
- 当日 App 有效事实：**20**

当前 `live_observations_v2.py` 已按 `(date, platform, source_type, target_key)` 选择 latest complete run，所以事实展示层的最新逻辑是正确方向。

### 深研任务

当前数据库 enqueue function 的 conflict key 是 `(collection_date, platform, normalized_title)`，逻辑只会“新增/更新当前出现的 title”，**不会把后来 run 中消失的 title 作废**。因此：

- 第一轮 MoboReels 中 `His Neglected Wife Is The Top Scientist` 是 new；
- 第二轮该剧已掉出 Top10；
- 第二轮出现 `Weak Yesterday, Unstoppable Today`；
- 旧任务没有被 supersede，于是最终形成 10 条 COMPLETE，而最新榜单只有 9 条 new title。

正确口径应是：**每日内容结论只绑定最新有效 run；旧 run 独有 title 可保留为原始观察，但从当日榜单深研集合中退出。**

### 深研结果契约

现有 10 条 COMPLETE 结果没有通过当前 schema：

- 受控 genre 只有 **1/10** 合规，另外 9 条使用了“现代都市爱情 / 西幻狼人 / 西幻冒险 / 现代豪门·重生”等旧式自由文本；
- **10/10** 缺少当前 research_json 要求的 canonicalTitle、newnessResolution、auditNotes、confidence、missingFields、sourceUrls、needsGPT；
- 证据来源多数仍是官方 MoboReels 页面，因此无需把证据全部废弃；建议保留 source evidence，重新按当前研究 schema 归一化并复核分析字段。
- `The Trophy Wife's War` 的 App collector 把长篇 synopsis-like 文本塞入 tags，属于字段抽取污染；修正候选只清空 tags，不把这段文本当作标签或新剧情证据。

## 6. App / Web 不能互相补榜

9/17 同日可直接看到差异：

- MoboReels App `Trending Series` Top10 与 Web `Popular Series` Top10 只重合 **2/10**；
- NetShort App `Top Trending` 与 Web `Trending Now` 重合 **7/10**，且排序不同。

因此“都是官网/都是 Top10/标题有重合”都不能证明同一排行榜。当前 source group、target_key、ranking_type 必须作为事实主键的一部分。

## 7. 八类问题归因

| 类型 | 9/16 | 9/17 | 当前状态 |
|---|---|---|---|
| 数据源错误 | ShortMax 分类货架被拿来参与主榜逻辑 | MoboReels tags 字段污染 | 需清洗/隔离 |
| 采集逻辑错误 | 多 target、多轮、DramaBox Top60 | 同日两轮被同时计入“采集量” | 当前 pilot 已固定一平台一 Web target；App需统一 latest-run 口径 |
| 排名语义错误 | Category Shelf/Homepage Top10/Trending 等混用 | App 与 Web 不能互替 | 必须 source+target 隔离 |
| 日期错误 | 未发现 off-by-one | 未发现 off-by-one | 保持 collection_date 与 processing_at 分开 |
| 去重错误 | 165 raw 与 135 latest-target 混为一个数 | 40 raw 与 20 effective 混为一个数 | 统一 latest-run / target 口径 |
| 任务生成错误 | 115 个 Official Web 任务错误入队 | 旧 run 独有 title 不失效 | **仍需补 supersession gate** |
| 深研错误 | marketAnalysis 跨栏目/超 Top10；正式深研未完成 | 1 stale task + schema 不合规 | 需重跑/重校验 |
| 前端展示错误 | “采集数量”未区分 raw/effective/current-valid | 历史 override 可显示旧分类/旧任务 | 统一指标字典 + 历史回填后再发布 |

## 8. 建议的系统修正（本分支只记录，不执行生产变更）

### P0 — latest-run supersession gate

保存新的完整 App run 后，以 `collection_date + platform + sourceType + targetKey` 找到上一有效 run：

- 仍在新 run 的 title：任务绑定更新到新 run；
- 已从新 run 消失的旧 title：不得继续进入当日研究集合；已有 PENDING/RESEARCHING 改为 REVIEW_REQUIRED，并标记 `SUPERSEDED_BY_LATER_RUN`；
- 已 COMPLETE 的旧 title：保留研究资产，但从该日最新榜单分析/统计中剥离，不计入“当日新增剧/题材”。

### P0 — research schema gate

在标记 COMPLETE 和写入 `drama_overrides` 之前，必须用当前 schema 校验：

- genre/audience 受控枚举；
- 当前 required keys 完整；
- sourceUrls 与 evidence 对齐；
- 低证据/冲突进入 NEEDS_GPT 或 REVIEW_REQUIRED；
- 校验失败不得写成“已研究”。

### P0 — 历史回填口径

历史 backfill 只写“修正候选”，不覆盖 raw evidence。事实层与研究层分两步确认：

1. 先确认榜单 source/target/rank；
2. 再确认哪些 title 需要重新深研；
3. 用户确认后才允许写生产。

### P1 — 前端指标字典

任何日报/仪表盘必须分别显示：

- Raw observations / job attempts
- Latest target rows
- Structurally valid TopN rows
- Current-scope eligible rows
- Research task count
- Research-complete count
- Review-required count

禁止再用一个“采集数量”覆盖所有含义。

## 9. 回填建议

### 2026-09-16

- **可进入回填候选：20 条**：DramaBox Top10 + GoodShort Top10。
- **待用户/证据复核：20 条**：NetShort 10 + ReelShort 10。
- FlexTV、MoboReels、ShortMax、DramaWave：现有历史证据不足以构成当前口径完整 Top10。
- 115 个 Official Web PENDING research_tasks：建议后续统一作废/归档，不执行深研；本次未修改。

### 2026-09-17

- Web pilot：40 条单次结构完整候选（FlexTV/MoboReels/NetShort/ReelShort），但尚未达到 3 次稳定验证，**不直接晋级生产**。
- App：最新有效事实 20 条（MoboReels 10 + NetShort 10），继续保持在 App source layer。
- 深研：以最新 MoboReels run 为准，9 条 new title 进入重新 schema 校验/必要时重研；`His Neglected Wife Is The Top Scientist` 保留为早一轮观察及独立研究资产，但不得计入 9/17 最新 Top10 分析。
- 当前 10 条历史 research override 在用户确认前不覆盖、不删除。

## 10. 当前停止点

**AWAITING_USER_CONFIRMATION**

本分支只新增 `audit_data/` 与本报告。没有修改：
- `pilot/web-top10-8platform`
- 生产数据库任何行
- 正式 Scheduler
- Render 部署/环境变量
- 生产前端代码
