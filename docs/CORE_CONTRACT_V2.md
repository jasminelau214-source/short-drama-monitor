# JSM Core Contract V2

**状态：LOCKED_NOT_IMPLEMENTED**  
**锁定日期：2026-10-08**  
**基线 main：`a206bc9c21138a9559aecf5a7a34069167268cfc`**

本文件是 V2 Integration 的业务合同，不表示实现已完成，更不表示可以晋升生产。V2 与旧 Core Contract v1 的关键冲突是 **Source Semantics / Newness / Research Eligibility**：旧 v1 仅允许 App 权威榜自动触发深研；V2 改为 **任一已验证来源第一次发现 canonical drama 即可触发一次全局深研**。因此不得把 v1 分支整分支合入 V2。

两份项目 Current 基线（均更新于 2026-09-20）继续约束生产；本次用户批准仅授权 Step A 候选契约，不构成替换生产业务规则的授权。基线文件 SHA-256 记录于 `promotion_manifest_v2.json`，项目 `sources/` 保持只读。V1 集成分支仅保留为证据来源。

## 1. Source Semantics

- 正式生产采集主来源：`OFFICIAL_WEB`。
- App 降为验证/补充证据，不再是唯一自动深研来源。
- 当前正式 Official Web 范围：DramaBox、FlexTV、GoodShort、MoboReels、NetShort、ReelShort。
- DramaWave 暂停：主榜稳定性未验证。
- ShortMax 暂停：主榜语义尚未锁定。
- 统一采集语言 English；locale `en-US`；有地区筛选时使用 US。
- 每个平台正式批次恰好 Top10；实际 source/target/ranking 证据必须一致，不能把页面前十项直接宣称为真实榜单。
- 未注册、未验证来源和范围外平台 fail-closed。未来多来源并行须经版本化注册、验证及 Integration Gate，不能自动扩容。
- 计划运行时间：Asia/Shanghai 每天 16:00 采集、16:25 检查，仅异常通知。本阶段仅定义合同，不安装、启用或修改 Scheduler。

## 2. Drama Identity / Newness

内容身份与发布身份必须分离：

- **Content identity**：跨来源、跨平台判断是不是同一部剧，使用唯一 `canonical_drama_identity`。
- **Publication identity**：写回必须绑定具体平台记录，不得因为内容相同跨平台写回。
- V2 深研触发字段为 `first_seen_any_verified_source`。
- 同一 canonical drama 在多个来源并发首次出现时，只允许创建一个全局 Research subject；后续来源只新增 observation。
- 保留每个平台/来源自己的首次出现和榜位历史，用于趋势分析。
- 标题相同本身不足以证明内容相同；身份未解决进入 REVIEW_REQUIRED。Dub/Dubbed/English Dub 仅在有证据且位于标题首尾的发行标签位置移除，不删除标题正文的真实词。
- 并发首次发现须以原子唯一性保证一个全局 subject；具体数据库实现留待后续独立隔离验证，不在 Step A 迁移。

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

版本刷新必须有显式、可审计的授权，不得由新平台 observation 隐式触发。免费额度恢复也须重新检查资格和权威 run；若旧 observation 已失效，不得机械继续旧任务。115 条历史 PENDING 继续冻结，V2 规则不得把这些旧任务自动合法化。

## 5. Research COMPLETE

只有同时满足以下条件才能 COMPLETE：

- deterministic schema validation 通过；
- CORE_FIELDS 有可靠内容；
- confidence >= medium；
- 至少一个允许的 evidence/source URL；
- 无 unresolved identity/source conflict；
- optional opening/paywall 字段可因证据不足为空，但必须声明 missing。

证据不足与身份冲突不能被模型“强行 COMPLETE”。

CORE_FIELDS 明确为 synopsis、genre、lane、audience、storyCore、storySkin、conflict、payoff、localizationLevel、localizationJudgment、mismatch（与基线 main 的字段名对齐，不代表 main 校验已达标）。占位文字不算可靠内容，evidence 必须支持已解析身份。证据不足进入 NEEDS_GPT；身份、source 或 schema 冲突进入 REVIEW_REQUIRED。NEEDS_GPT 是证据不足状态，不授权调用任何付费模型。

## 6. Writeback

- 同平台记录 + canonical identity 匹配 + COMPLETE + 最终 deterministic validation 才能写回。
- 禁止任何 cross-platform fallback。
- 内容级 Research 可以被不同平台 observation 复用，但发布/写回仍必须绑定当前平台记录与当前权威 run。
- 真正落笔时重验权威 run，并采用幂等条件写入，防止研究期间 run 被替代以及重放重复写入。

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
- 技术错误为 **每个外部 API 调用首次 + 2 次重试 = 最多3次尝试**；这是唯一重试权威，不是整个 SEARCH 阶段只允许3次请求；每剧最多3轮逻辑搜索，每轮的技术重试归属于原调用；
- 禁止“外层任务重跑 × 内层Provider重试”造成重试放大；
- 每剧最多3轮外部搜索；
- 单任务最长5分钟；
- 免费额度耗尽进入 `DEFERRED_FREE_QUOTA`，不是 FAILED；
- Provider 不写死为某个品牌，只有运行时明确验证为免费且不会自动产生费用的 Provider 才能启用。

429/临时错误须退避并降并发，业务错误不机械重试。SEARCH → ANALYZE → VALIDATE → WRITEBACK 保留 checkpoint，从失败调用/阶段恢复，不重新执行已成功消耗的搜索或模型调用。300 秒指任务累计活跃执行时间，跨 checkpoint 恢复不得重置；额度等待不占 Worker 槽位、不消耗活跃时间。达到时间上限时不能为了凑足重试继续超时运行。额度不明或免费性无法核验时停止外部调用，保留任务，禁止付费 fallback。

## 9. Stability / Promotion

稳定性观察不属于业务等待链路。

- Production 前：连续3个正式时间窗口在隔离 Staging/Shadow 验证；
- Production 启用后：继续观察7个自然日；
- 已成功平台的数据仍当天采集、当天分析、当天触发深研；
- 平台抓取失败与“系统是否正确隔离失败”分别计分。

Production Ready 必须同时通过 execution、structure、truth、semantic、fault、identity、stability、path_optimization、e2e、integration。CI green 不能单独等同业务验收成功。

任一 FAIL/BLOCKED/NOT_RUN、缺证或证据不属于候选 exact HEAD，均 BLOCK_PROMOTION。Manifest 中所有 runtime gate、事故重放、Staging 和 rollback 证据目前是 NOT_RUN；Step A 测试不能改绿这些条目。每份后续证据必须绑定 commitSha、环境、时间、数据范围、artifactSha256 和结果。生产前需3个连续正式时间窗口，生产后7个自然日是旁路观察，不延迟当天业务。

Collection、Analysis、Research、Publication 各自独立；采集成功不代表研究完成，研究完成也不代表发布成功。候选 Research 转换及条件在 JSON 的 `stateSemantics.researchTransitions` 中明确列出，未列转换默认禁止。COMPLETE/NEEDS_GPT/REVIEW_REQUIRED/FAILED 不自动重入；人工决定或版本刷新需另外记录。这里只定义新语义，不修改当前数据库状态约束。

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

## 11. 隔离验收与红蓝检查

运行 `python -I -S -B test_core_contract_v2.py -v`，仅依赖标准库，不导入应用、读取 secrets 或连接任何服务。检查分为：合同不变量、配置变异反例、Manifest 证据缺口、实际 Git 改动面，以及临时 Git 仓库中对运行文件越界（已提交/暂存/未跟踪）的拒绝测试。

测试 PASS 仅证明 Step A 契约与改动边界通过静态验收，**不证明** Collector、Identity、Worker、数据库、Writeback 或 Golden E2E 已实现。事故案例在 Manifest 中是未来必须执行的重放清单，不是真实115条生产数据快照，也不是已通过的业务测试。

蓝方证据：最新 main 为基点，候选仅四个新增文件，生产权限全部为 false。红方重点反证：只改清单不能掩盖实际运行文件变化；空 Gate 集合不能放行；缺证、假 PASS、跨平台写回、旧队列解冻、付费 fallback、重试放大和非法状态转换均须被测试拒绝。回滚 Step A 只需弃用候选，无生产数据回滚动作。后续运行层依赖、跨层一致性、恢复与人工维护成本仍是 Integration Gate 的待验项目。
