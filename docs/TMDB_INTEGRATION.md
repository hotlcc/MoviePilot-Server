# 多媒体数据源与 TMDB 辅助信息

## 数据边界

MoviePilot-Server 不再把订阅统计、订阅分享或共享识别结果转换为 TMDB
数据，也不会因缺少 `tmdbid`、`genre_ids` 或 TMDB 图片而调用 TMDB API。
服务端按客户端上报的原识别源存储和展示数据。

媒体主身份由以下两个字段组成：

- `media_source`：固定枚举值，包括 `themoviedb`、`douban`、`bangumi`、
  `anilist`、`imdb`、`tvdb`、`musicbrainz`、`theaudiodb`、`doubanmusic`、
  `bilibili`、`mangguodiscover`、`migu`、`tencentvideodiscover`。
- `media_id`：对应数据源的原生 ID，统一按字符串存储。

新版本客户端只应发送统一字段。考虑到中心服务无法要求所有已部署客户端同时
升级，请求边界仍兼容 `tmdbid`、`doubanid`、`bangumiid`、`anilistid`、
`imdbid`、`tvdbid` 以及旧复合 `mediaid`；完整的新字段始终优先，旧字段只用于
转换。响应同时返回统一字段和按来源回填的旧字段，保证旧客户端可以继续读取。

兼容字段不会写入数据库或 Redis。服务内部在请求校验后只保留
`media_source + media_id`，存量升级完成后也会删除旧列和旧缓存字段。

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

`/recognize/share` 的新身份只接受固定枚举中的来源，并返回原始
`media_source + media_id`，同时在 API 响应补充旧字段。缓存键保留电视剧第 0
季，避免特别季与未指定季的记录冲突；命中旧 Redis 记录时会原位改写为统一
身份结构。

## 数据库升级

服务启动时会幂等补齐统计和分享表的统一字段及索引，根据存量专用 ID 回填，
然后删除专用 ID 字段。未知来源会优先用可识别的旧 ID 回填；仍无法识别的身份
会被清空。SQLite 和 PostgreSQL 使用相同的升级语义。
