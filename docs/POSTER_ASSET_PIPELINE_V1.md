# JSM 海报 / 封面资源采集方案 V1

## 目标
为前端剧目卡、今日重点、单剧详情、生命周期、题材研究提供稳定的剧目海报/封面资源。

## 名称约定
- poster：剧目竖版海报，优先使用。
- cover：来源页提供的封面图，可作为 poster 的同义输入。
- thumbnail：列表缩略图，可能分辨率较低，仅作为候选。
- still：剧照，不等同于海报，后续放入 drama_assets。

## 架构原则
排名采集是核心链路，海报采集不能阻塞榜单事实入库。

采用“主采集顺手拿 + 独立补全”的混合模式：

1. **采集器顺手拿**
   - 当官方页面/API 本身已经返回 img/cover/poster URL 时，采集器同时输出 poster_url。
   - 这是零/低额外成本，不需要额外搜索。

2. **独立 poster_enricher 补缺**
   - 仅处理 posterUrl 为空的剧目。
   - 优先访问 JSM 已保存的 sourceUrl、episodeUrl、researchSources。
   - 优先读取 og:image / twitter:image / 页面内 poster/cover 图。
   - 保存来源页面、抓取时间、证据类型。

3. **搜索补缺（后续）**
   - 只有官方来源页无法获得海报时，才进入联网标题搜索。
   - 必须校验平台 + 剧名，避免同名剧错配。
   - 搜索失败不得影响排名、分析或深研任务。

## 数据字段（V2 兼容）
当前 JSON 记录先支持：
- posterUrl
- posterSourceUrl
- posterUpdatedAt
- posterEvidence

V3+ 迁移到 drama_assets：
- drama_id
- asset_type: poster | still | trailer | video | ad | landing_page
- url
- source_url
- platform
- fetched_at
- confidence
- width / height
- checksum（可选）

## 前端规则
- 有 posterUrl：显示真实海报。
- URL 失效/被防盗链：自动回退设计占位图，不影响页面。
- 所有海报与剧名点击：进入同一 drama_id 单剧详情。
- 海报不是排名事实字段，不应参与榜单去重或剧目身份判断。

## 当前实现
- poster_enricher.py：独立补全脚本。
- official_web_collectors.py / goodshort_collector.py：页面已有图片时顺手输出 poster_url。
- collector_import.py：接受 poster_url / posterUrl / cover_url / coverUrl。
- live_observations_v2.py：将 posterUrl 带入动态剧目记录。
- index.html：海报有值时自动显示，失败时回退。
- Staging /api/data：排名与深研数据只读来自生产公开数据，海报字段可由 Staging 本地补充覆盖空值。
