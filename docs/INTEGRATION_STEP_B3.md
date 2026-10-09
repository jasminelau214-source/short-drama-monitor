# V2 Integration Step B3 — Top10 与权威 run 裁决

状态：ISOLATED_BATCH_AUTHORITY_ONLY / BLOCK_PROMOTION。
基点：B2 `df0e4166296e13a54e49325149ea14e580c9df85`。
日期：2026-10-09；继续使用唯一 V2 Integration 分支。

开始前重读两份 Current 基线。生产仍遵从 Source Semantics 等 Current 规则；V2 与旧规则的冲突只留在候选合同。115 条 OFFICIAL_WEB 历史 PENDING 继续冻结，本单元不读取、不处理。

## B3 规则

- 每个平台独立验证；批次必须有审核过的 immutable ranking snapshot，并绑定 scope、采集时间、排序定义、区域/语言与每行内容身份凭证。
- 候选榜必须明确 `SUCCEEDED`、`batch_complete is True`（拒绝 `1`）、TopN=10、恰好十行、整数 rank 1–10 无重复或缺失。标题键、source entity、record 和 resolved content ID 在单榜内均不得重复。
- 只有与受信任 target registry 完全匹配、verified source allowlist 允许且快照审核有效的批次才能参选。榜单自报 complete 不等于 witness 证明。
- Source rank `observed_at` 按 Asia/Shanghai 日期核对；未来、naive、错日采集拒绝。`updated_at` 不参与裁决。
- 对同一 collection date + platform + source type + target key，只在有效完整快照中选择最晚 `observed_at`。较新的失败、不完整、证据失效批次不得取代较早完整有效批次，也不能覆盖其他 scope。有效最新时间并列或相同 run ID 内容相异 → 复核。
- 研究/后续消费任务须指向当前权威 run ID，source entity、same-platform record、content ID 均要仍匹配。即使剧也存在于较新的 run，旧 run task 仍为 superseded，等待显式重建/重绑定流程。
- 每次裁决返回 snapshot SHA-256，供未来存储 CAS。当前函数不阻止裁决与实际写入之间的竞态；接线方必须在 API spend/DB write 前、事务内重验同一 snapshot。

## 隔离验证

运行 `python -I -S -B test_batch_authority_v2.py -v`、`python -I -S -B test_identity_evidence_v2.py -v`、`python -I -S -B test_drama_identity_v2.py -v` 与 `python -I -S -B test_core_contract_v2.py -v`。fixture 全为合成样本，禁网，不声称重放生产事故或115条数据。

覆盖 Top10 结构、假 bool、榜单 witness、scope 与 profile、identity 绑定、重复内容、后续 run 替代、partial/stale origin、时间并列、Mobo 单平台失败隔离和 5/6 coverage。历史 B1/B2 diff 边界固定到已验提交，B3 增量单独精确限制。

此模块只从调用方提交的 target registry、verified source allowlist、身份审核台账和榜单快照台账读取。它不核验台账的根信任、当前数据库 run 窗口完整性、线上抓取是否真实反映官网，也不完成 DB CAS 或外部任务消费；将这些调用方输入接错会污染结果。Identity、Truth、Fault、集成 Gate、真实历史事故、Staging E2E 都仍 NOT_RUN，不能据此判定系统或生产通过。

未改 Collector、Worker、DB/schema、Research Queue、API/Frontend、Scheduler、Render 或任何生产资源；回滚只需弃用离线候选模块。后续最小工作是只读/隔离的官方 Run 窗口读取适配器以及事务条件更新设计，再接调用前/写回前门禁；生产切换另需单独审批。
