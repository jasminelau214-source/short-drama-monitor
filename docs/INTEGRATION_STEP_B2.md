# V2 Integration Step B2 — 已审核证据的内容身份绑定

状态：ISOLATED_REVIEWED_BINDINGS_ONLY / BLOCK_PROMOTION。
基点：B1 `8225a47359d07d6bd1b57d1950c623a76402c23b`。
日期：2026-10-09；继续使用唯一 V2 Integration 分支。

开始前重新读取两份 Current 基线。基线与 V2 的来源/新鲜度/入队规则冲突继续限制在候选层，本单元不改变生产规则。

## 解决的边界

B1 的标题键和 evidence_ref 都不能证明内容身份。B2 只解析独立审核台账已经批准的 source entity → canonical drama 绑定：

1. observation 绑定 platform、sourceType、sourceEntityId 三元组，标题不进入解析 API。
2. 来源必须在独立提供的已验证 source registry 中；当前候选范围只允许六平台的 OFFICIAL_WEB / SHORT_DRAMA_APP。不扩展平台或来源，App 的验证角色不变。
3. claim 包含版本、准确 subject、内容 ID、evidenceRef、artifactSha256；闭合 schema 禁止额外字段及重复 JSON key。
4. 实际证据 snapshot bytes 必须匹配 artifactSha256；claim 原始字节哈希必须匹配已有审核记录。
5. 审核记录须具有 decision_id、有效期且未撤销。未批准、过期、未来生效、篡改、subject 不匹配或相互矛盾的内容 ID 均 IdentityReviewRequired。
6. 同一实体的多份一致证据可复用；排序/重复重放结果确定。同内容在另一平台需要该平台实体的独立绑定，保留各自 observation。

本模块不会自行创建全局 ID。没有已审核绑定时进入复核，不能从标题猜测“首次发现”或自动创建研究。上游须解决新内容的 ID 创建与原子唯一性。

## 信任前提与未完成事项

审核台账和 source registry 必须由受保护的验证边界提供，不能取自模型回答、用户证据请求中的 `verified=true` 或与 claim 同源的可改字段。这里的纯函数检查台账中的绑定和字节一致性，**不证明源网页真实，也不能防止调用方伪造整份台账**。

合成测试模拟这个前提；真实在线取证、证据审核、台账保护/刷新/撤销加载尚未实现，因而不能接正式入口。后续还需 live entity adapters、原子 first-seen、Python/数据库一致性、Batch/Authoritative Run 集成，以及 Collector/Worker/Publication 接线。解析结果不授权研究调用或写回，也不校验榜单 run 是否仍权威。

## 验证与红蓝质检

使用标准库隔离运行 `test_identity_evidence_v2.py`、`test_drama_identity_v2.py` 和 `test_core_contract_v2.py`，测试禁止网络，不读取凭证，不接生产数据。

蓝方：有效的审核绑定具有准确 subject、匹配的 claim/snapshot 哈希、审核 decision 和有效时间；一致证据与重复重放结果确定。

红方：缺审核、自报 verified、claim/snapshot 篡改、相同标题不同内容、跨实体/平台/来源借用、身份冲突、未知来源/暂停平台、过期/撤销、非法 schema 均拒绝。原 B1 five-file 边界固定在已验提交；B2 增量与累计改动面分别核验，不能改清单掩盖真实文件越界。

通过仅表示离线解析单元回归通过。Identity/Truth/Fault/Integration Gate、115条真实事故重放和 Staging E2E 均 NOT_RUN。main、Supabase、Render、Scheduler、正式 Research Queue、Secrets 无修改；弃用本候选即可回滚本单元。维护成本集中在受保护台账的建立/续期与证据审查，不能靠人工为未知标题逐条永久审批来宣称自动化已经完成。

下一单元为 Batch Validity / Authoritative Run 的隔离裁决，再与本解析结果组合验证调用前与写回前的资格；全局内容 ID 创建和并发唯一性仍须独立设计、验证。
