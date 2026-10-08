# V2 Integration Step B1 — 身份基础模块

状态：ISOLATED_PRIMITIVES_ONLY / BLOCK_PROMOTION。
基点：Step A `aca65184a360d5b765f06858ef3fea3cd7533fad`。
继续使用唯一 V2 Integration 分支；main 和生产权限保持不变。

开始前重读两份 2026-09-20 Current 基线。生产仍按 Current 约束；本单元不改变 Source Semantics、Newness 或 Research Eligibility。V2 与 Current 的冲突仍只记录在候选合同中。

## 选择与红队反证

参考 V1 `4558142808468fb3f64ec0b56e31a698a32bdc21` 的独立 identity 模块，以及 Shadow `f74798feb8941e663192e903c0b03b3b4543c093` 的标题规则，不整分支合并。

Shadow 的宽松前缀删除可能把 `Dubbed a Queen` 的真实词删掉；V1 的 `same_title` / ASCII 标题键也不能证明两个内容相同。例如 `A-B` 与 `AB` 都变成 `ab`。因此不把旧标题键直接升级为全局内容 ID，不将旧 normalizer 接到数据库唯一键。

## 本单元行为

- `canonical_title_key` 返回带版本的匹配提示，不是 global canonical drama identity。
- 无证据时保留 Dub 标签。调用方须先验证证据，再提供具体标签、prefix/suffix 和 evidence_ref；本模块检查格式、合法标签、位置和词边界。证据引用本身不证明来源可信，本模块不访问外部来源。
- `(Dubbed)`、`[DUBBED]`、English Dub、ENG DUB、DUBBED 和受支持配对括号可在证据匹配时移除。正文词、无证据标签不得自行移除；错位置、错括号和空键进入 IdentityReviewRequired。
- 暂保留 ASCII 字母/数字的候选键规则；非 ASCII 字母/数字明确进入复核，避免 `Café` 或混合文字被有损删除。Unicode 标点仍可处理。
- `publication_identity_key` 绑定上游已解析内容 ID、平台和具体记录 ID；不同平台/记录不会因同名折叠。该函数不验证内容证据，也不提供写回授权。

## 隔离验证与明确缺口

执行：

```
python -I -S -B test_drama_identity_v2.py -v
python -I -S -B test_core_contract_v2.py -v
```

使用合成 fixture，禁止网络；未使用115条生产数据。覆盖标签/正文、证据缺失与错误、空值与类型、Unicode 损失、幂等、标题碰撞和同内容跨平台记录隔离。合同回归继续检查所有生产权限为 false、Gate NOT_RUN、完整改动面，以及相对 Step A 的精确五文件变更边界。Step A 的四文件清单保留为历史阶段范围，B1 在独立 `stepB1` 字段声明增量，不能用扩大旧清单掩盖新增改动。

仍未实现：可信证据适配器、全局内容解析、原子首次发现去重、Python/数据库等价性，以及 Collector/Worker/Publication 接线。旧运行代码完全未修改；全局 Identity Gate、事故重放和 Staging E2E 均保持 NOT_RUN。单元通过不等于这些能力已实现。

下一最小单元应先确定可信证据到内容身份的解析边界，再结合 Batch/Authoritative Run 做隔离集成；不得提前开启新来源入队。回滚本单元只需弃用这批候选变更，无数据库或运行环境回滚操作。单一模块与显式证据接口减少后续重复 normalizer，但旧入口的实际统一尚待完成，不能宣称维护债已消除。
