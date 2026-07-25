# 多媒体数据源与 TMDB 辅助信息

## 数据边界

MoviePilot-Server 不再把订阅统计、订阅分享或共享识别结果转换为 TMDB
数据，也不会因缺少 `tmdbid`、`genre_ids` 或 TMDB 图片而调用 TMDB API。
服务端按客户端上报的原识别源存储和展示数据。

媒体主身份由以下两个字段组成：

- `media_source`：`themoviedb`、`douban`、`bangumi`、`anilist` 或插件自定义源。
- `media_id`：对应数据源的原生 ID，统一按字符串存储。

`tmdbid`、`doubanid`、`bangumiid`、`anilistid` 是兼容和辅助字段，不参与
跨数据源合并。旧客户端未上报统一身份时，服务端按上述顺序选择首个有效 ID
回填 `media_source + media_id`。

## 客户端处理

- 添加订阅时保留原识别源，不额外请求 TMDB。
- 下载需要按媒体类别选择目录或下载器分类，因此在目录解析前补充 TMDB 辅数据。
- 整理在目标目录分类和刮削前补充 TMDB 辅数据；启用自动类别目录但无法获得
  TMDB 分类时中断整理并返回明确错误。
- TMDB 补充只写入辅助 ID、`tmdb_info`、类型 ID 和缺失的外部 ID，不覆盖原
  识别源的标题、年份、季号、图片或主身份。

## 订阅统计

`/subscribe/add`、`/subscribe/done` 和 `/subscribe/report` 按
`media_source + media_id + season` 定位记录。不同数据源即使原生 ID 数值相同也
不会合并，第 0 季会作为有效季号单独统计。

`genre_ids`、海报、背景图、评分和简介均为可选上报字段。缺失时服务端原样保存
空值，不再尝试从 TMDB 补齐。

## 订阅分享

`/subscribe/share` 保存分享者上报的原识别源及其原生 ID，查询接口也原样返回。
复用分享的客户端负责按 `media_source + media_id` 重新识别，并在后续下载或整理
阶段按需补充 TMDB 辅数据。

## 共享识别

`/recognize/share` 支持 TMDB、豆瓣、Bangumi、AniList 和插件自定义源，并返回
原始 `media_source + media_id`。缓存键保留电视剧第 0 季，避免特别季与未指定季
的记录冲突。

## 数据库升级

服务启动时会幂等补齐统计和分享表的多数据源字段及索引，并根据存量兼容 ID
回填统一身份。SQLite 和 PostgreSQL 使用相同的升级语义。
