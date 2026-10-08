# JSM Core Contract V2

**状态：LOCKED_NOT_IMPLEMENTED**  
**锁定日期：2026-10-08**  
**基线 main：`a206bc9c21138a9559aecf5a7a34069167268cfc`**

本文件是 V2 Integration 的业务合同，不表示实现已完成，更不表示可以晋升生产。V2 与旧 Core Contract v1 的关键冲突是 **Source Semantics / Newness / Research Eligibility**：旧 v1 仅允许 App 权威榜自动触发深研；V2 改为 **任一已验证来源第一次发现 canonical drama 即可触发一次全局深研**。因此不得把 v1 分支整分支合入 V2。

## 1. Source Semantics

- 正式生产采集主来源：`OFFICIAL_WEB`。
- App 降为验证/补充证据，不再是唯一自动深研来源。
- 当前正式 Official Web 范围：DramaBox、FlexTV、GoodShort、MoboReels、NetShort、ReelShort。
- DramaWave 暂停：主榜稳定性未验证。
- ShortMax 暂停：主榜语义尚未锁定。
- 统一采集语言 English；locale `en-US`；有地区筛选时使用 US。

## 2. Drama Identity / Newness

内容身份与发布身份必须分离：

- **Content identity**：跨来源、跨平台判断是不是同一部剧，使用唯一 `canonical_drama_identity`。
- **Publication identity**：写回必须绑定具体平台记录，不得因为内容相同跨平台写回。
- V2 深研触发字段为 `first_seen_any_verified_source`。
- 同一 canonical drama 在多个来源并发首次出现时，只允许创建一个全局 Research subject；后续来源只新增 observation。
- 保留每个平台/来源自己的首次出现和榜位历史，用于趋势分析。

## 3. Platform Failure Isolation

六个平台按平台独立验收、独立提交。

- 某一平台失败时，其余成功平台立即进入分析与首次发现判断。
- 失败平台当天明确为 `MISSING/FAILED`，不得复制昨天数据冒充今天。
- 上一权威 run 可以继续作为历史/陈旧展示，但必须标记 stale，并展示真实 sourceDate。
- 跨平台统计若只覆盖 5/6，必须显式展示 coverage，不得伪装成完整六平台结果。

## 4. Research Eligibility

数据库 enqueue 只能代表候选，不能代表最终执行授权。

Worker 在任何外部搜索/模型消耗前必须重新验证：

1. observation 来自允许的 verified source；
2. origin run 仍是对应平台/来源/target 的有效权威 run；
3. canonical identity 已解析；
4. 该 canonical drama 没有可复用的有效 COMPLETE 研究，或明确需要研究版本刷新。

## 5. Research COMPLETE

只有同时满足以下条件才能 COMPLETE：

- deterministic schema validation 通过；
- CORE_FIELDS 有可靠内容；
- confidence >= medium；
- 至少一个允许的 evidence/source URL；
- 无 unresolved identity/source conflict；
- optional opening/paywall 字段可因证据不足为空，但必须声明 missing。

证据不足与身份冲突不能被模型“强行 COMPLETE”。

## 6. Writeback

- 同平台记录 + canonical identity 匹配 + COMPLETE + 最终 deterministic validation 才能写回。
- 禁止任何 cross-platform fallback。
- 内容级 Research 可以被不同平台 observation 复用，但发布/写回仍必须绑定当前平台记录与当前权威 run。

## 7. Authoritative Run / Batch Validity

同一 `collection_date + platform + source_type + target_key`：

- 只从完整、结构合法的 run 中选择权威版本；
- latest valid complete run 为权威；
- later incomplete/failed 不得覆盖 earlier complete；
- 被 later authoritative run 移除的 title 不得继续执行旧 Research Task。

合法批次至少要求：`batchComplete=true`、rows=TopN、rank恰好1..N、无重复rank/title、identity可解析、source/target/ranking语义真实一致。Partial 必须 fail-closed。

## 8. Zero-Paid-Cost Research

前期深研采用 `FREE_ONLY`：

- 付费预算：USD 0；
- 禁止自动 paid fallback；
- Research Queue 数量不限；
- 默认并发 2，遇到 provider throttle/429 可降为 1；
- 技术错误为 **首次 + 2 次重试 = 每阶段最多3次尝试**；
- 禁止“外层任务重跑 × 内层Provider重试”造成重试放大；
- 每剧最多3轮外部搜索；
- 单任务最长5分钟；
- 免费额度耗尽进入 `DEFERRED_FREE_QUOTA`，不是 FAILED；
- Provider 不写死为某个品牌，只有运行时明确验证为免费且不会自动产生费用的 Provider 才能启用。

## 9. Stability / Promotion

稳定性观察不属于业务等待链路。

- Production 前：连续3个正式时间窗口在隔离 Staging/Shadow 验证；
- Production 启用后：继续观察7个自然日；
- 已成功平台的数据仍当天采集、当天分析、当天触发深研；
- 平台抓取失败与“系统是否正确隔离失败”分别计分。

Production Ready 必须同时通过 execution、structure、truth、semantic、fault、identity、stability、path_optimization、e2e、integration。CI green 不能单独等同业务验收成功。

## 10. Step A 边界

本阶段只建立 V2 合同、合同测试和 Promotion Manifest 骨架。

**明确禁止：**

- 修改 main；
- 修改 Production Supabase schema/data/RLS/trigger/Edge Function；
- 修改 Render Production；
- 修改正式 Windows Scheduler；
- 启动正式 Research Queue；
- 整分支合并任何旧 Pilot/Audit/Shadow/V1 Integration。

后续 runtime 修改必须按最小 promotion unit 引入，并继续经过 Integration Gate。
