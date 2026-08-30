# NewsClaw 后端动作闭环审计

本页记录 NewsClaw v0.1 的新闻原生动作合同。`article_id` 是公开身份；继承表中的
`bvid / up_name` 仅是兼容列，分别投影 `article_id / media`，不能被解释成视频或 UP
主。新闻事件统一保存 `source_platform=news`，并在证据可用时同时保存
`article_id / original_url / media / technical_provider`。

## 闭环矩阵

| 动作 | 原生接口 / Bridge | 幂等与冲突 | 持久化与回显 | 重启与失败语义 | 结论 |
| --- | --- | --- | --- | --- | --- |
| 首次设置 | `POST /api/news/bootstrap`，`GET /api/news/bootstrap/status` | 规范化兴趣与避雷词后生成输入摘要；同请求同输入回放，复用同一 `request_id` 改输入返回 409；校验错误 422 | `newsclaw_bootstrap.json` 保存请求、阶段、终态，手工偏好进入事件与画像 | 重启发现孤儿 `running` 会落成可重试的 `failed + recovered_after_restart`；已完成画像只允许原命令回放，不会覆盖 | 闭合 |
| 旧初始化入口 | `POST /api/init` | NewsClaw 模式固定返回 409 | 返回新闻原生 start/status 地址 | 不会误跑视频账号初始化或覆盖新闻画像 | 闭合 |
| 推荐 / 换一批 / 追加 | `/api/recommendations`、`/api/recommendations/reshuffle`、`/api/recommendations/append`；Bridge `recommend/reshuffle/append` | 排除集使用 `article_id/content_id/item_key`；读/出池操作没有独立命令回执 | 推荐池与 shown/seen 状态在 SQLite；新闻卡回显文章、媒体、原链和来源 | 单源失败由新闻 runtime 报 degraded；客户端需继续以新闻 scope 调用 | 有限闭合 |
| 今日首选 / 刷新 | `/api/delight/pending`、`/api/delight/pending-batch`、`/api/delight/trigger`、主动 WebSocket | 单条、批量、推送和触发共享北京时间今日 `news` scope；满池 claim 在事务中预留 lane/day 实际领取数 | scoped 计数与每日预算落 SQLite；copy-ready 后才做受保护池维护 | 预算不因失败/重启退款；跨日新 key；processing/empty/degraded 不以旧闻补位 | 闭合 |
| 喜欢 / 不喜欢 / 评论 / 忽略 | `POST /api/feedback`；Bridge `submit-feedback` | 事件入口按 `request_id` 去重；同 ID 改推荐、动作或评论返回 409 | 事件先落盘，再幂等修复推荐反馈投影；新闻证据字段随事件保存 | 重启后同一请求返回原 `event_id` 且 `duplicate=true`；画像异步处理失败不撤销事实 | 闭合 |
| 打开普通新闻 | `POST /api/recommendation-click` | `request_id` 对文章身份和来源做冲突检查 | durable `click` 事件进入历史，回显 `article_id/event_id/duplicate` | 重启重试不会重复学习 | 闭合 |
| 惊喜卡“看过” | `POST /api/spotlight/respond`，`response=view`；Bridge 同名动作 | 标记是天然幂等的集合更新，不写 click | 只把卡片标为已消费，不形成打开/兴趣证据 | 重载后卡片不会回来，也不会污染打开历史 | 闭合 |
| 惊喜卡“打开” | 同上，`response=open`；Bridge `respond-spotlight` | 必须有 `request_id`；REST 与 Bridge 都写可回放 click | 回显 `opened + event_id + duplicate`，并消费卡片 | 修复了原 Bridge 把 open 降成 view、导致打开动作丢失的问题 | 闭合 |
| 惊喜卡反应 | `like/dislike/dismiss/ignore` | durable 反馈事件；`ignore` 规范化为 `dismiss` | 反馈、出池/卡片状态和新闻证据落盘 | 重启可回放；dismiss 不再误当 dislike | 闭合 |
| 收藏 / 稍后读 | `/api/saved/{favorite|watch_later}`；兼容 `/api/favorites`、`/api/watch-later`；Bridge saved 工具 | `news:<article_id>` 唯一键 upsert，重复保存天然幂等 | `saved_memberships` 保存文章、原链、媒体和列表状态；新闻模式不写视频平台兼容表，也不触发站外收藏 | 保存、取消与列表重启后保持一致 | 闭合 |
| 内容历史 / 正文证据 | `GET /api/content-history`，`GET /api/news/articles/{article_id}` | 只读 | 历史回显 `article_id/original_url/media/technical_provider`；正文接口从缓存与 discovery 原始证据联表读取 | 不存在返回 404，坏 raw payload 降级为空对象 | 闭合 |
| 画像查看 / 手工编辑 | `/api/profile*`；Bridge `get-profile/profile-edit-state/edit-profile` | 手工覆盖操作是确定性写入，但无独立 `request_id` 回执 | override 与画像证据持久化，手工值优先于 AI 推断 | 重启恢复；调用方无法区分一次重试是否为首次应用 | 有限闭合 |
| 对话 | `POST /api/chat/turns` 与历史接口；Bridge `chat/chat-history` | `turn_id` 对 session/scope/subject/message 做冲突校验；惊喜卡 chat 使用 `delight:<request_id>` | durable turn 保存 pending/completed/failed 和回复 | 完成回放不重复调用 LLM；相同 turn 改消息返回冲突 | 闭合（旧 `/api/chat` 除外） |
| 兴趣 / 避雷探测 | probe 获取与 respond 接口；对应 Bridge 工具 | 反馈 `request_id` 进入 durable probe event，冲突检查 | 探测终态、画像更新和对话结果持久化 | 重启后回放不重复确认、拒绝或延期 | 闭合 |
| 消息 | `GET /api/notifications/pending`，`POST /api/notifications/ack` | ACK 以 `request_id + article_id` 持久去重；同 ID 换文章 409 | 消息行标记已发送，回显 `article_id/request_id/duplicate` | 重载不会再次出现；不存在文章 404；旧 `/sent` 仅兼容 | 闭合 |
| 来源开关 / RSS | `PUT /api/news/sources/{id}`，RSS 增删接口 | 自定义 RSS 以规范 URL 生成稳定 ID；完全相同添加回放 `duplicate=true`，同 URL 改参数 409 | 原子 JSON + last-good；健康与终态在 source task DB | 重启保留；一个来源坏只降级该来源。删除重试目前返回 404 | 有限闭合 |
| 刷新 / 探测 / runtime | `/api/news/refresh`、`/probe`、`/runtime` | refresh 使用稳定请求 ID、任务租约和持久终态 | 每源 attempt/health/last success/error 落盘，候选写 SQLite | 限流、超时、无权限、空结果分型；部分成功为 degraded，不伪装全成功 | 闭合 |
| 新闻设置 / 凭据 | `/api/news/settings`、`/api/news/credentials/tushare` | 参数严格校验；凭据写入是替换操作 | 设置原子写入并保留 last-good；Token 只存本机文件，接口仅返回脱敏状态 | 文件损坏自动回退 last-good；运行时应用回调失败后的自动反向回滚仍有限制 | 有限闭合 |
| 迁移 | migration 导出、暂存、重启应用 | manifest、SHA-256、SQLite integrity、pending journal | `newsclaw_credentials.json` 被标为 credential 并完整恢复；新闻 DB 走统一 schema 升级 | 测试覆盖导出→暂存→应用→凭据恢复；包未加密且含密钥 | 闭合但敏感 |

## DSH 必须遵守的精确合同

### 首次设置

`POST /api/news/bootstrap` 接收：

```json
{
  "interests": ["宏观", "投资", "创作"],
  "avoid_terms": ["标题党"],
  "request_id": "stable-client-request-id"
}
```

成功启动返回 HTTP 202，至少包含 `request_id`、`input_digest`、`status`、
`running`、`profile_ready`、`duplicate`、`replayed`、`started_at` 和 `stages`。
随后轮询 `GET /api/news/bootstrap/status`，只在 `completed / degraded / failed`
且 `running=false` 时收口。同命令超时重试会返回 `duplicate=true, replayed=true`；
不同命令并发或复用 ID 返回 409；输入不合规返回 422。`POST /api/init` 在新闻模式
永远不是回退入口。

### 消息确认

```http
POST /api/notifications/ack
Content-Type: application/json

{"article_id":"article-001","request_id":"stable-client-request-id"}
```

首次成功返回 `ok=true, duplicate=false`；完全相同的重试返回同一文章并带
`duplicate=true`；同一个 `request_id` 换文章返回 409；缺字段返回 422；不存在的文章
返回 404。`POST /api/notifications/sent` 只为旧客户端保留，新的 DSH 不应使用。

## 已修复的 P0 / P1

- 禁止 NewsClaw 误入视频专属 `/api/init`，补齐新闻 bootstrap 的并发、回放与重启恢复。
- 收藏与稍后读由裸 `BVID` 改为 `news:<article_id>`，并验证取消与重启恢复。
- 普通卡和惊喜卡的打开统一写 durable click；`view` 不再冒充打开。
- 修复 Agent Bridge `open -> view` 的错误降级，以及 dismiss 未落盘、惊喜 chat 无稳定 turn。
- 消息新增文章原生、持久、可冲突检测的 ACK，解决“本地移除、重载又回来”。
- 补齐新闻缓存解析，让反馈、打开、历史和正文证据能读回媒体与技术提供方。
- 自定义 RSS 添加可安全重试；迁移清单正确标记并恢复 NewsClaw 凭据。

## 剩余限制

1. 旧 `POST /api/chat` 和 REST 惊喜卡 chat 仍是兼容直连路径，不具备 durable
   `turn_id`；四端应使用 chat-turn 或 Agent Bridge 对话合同。
2. 画像编辑、来源删除和部分设置写入是确定性/原子操作，但没有统一 command receipt；
   它们能恢复最终状态，不能完整回放首次响应。
3. `PUT /api/news/settings` 会先持久化再调用 runtime apply；若 apply 回调失败，磁盘上有
   last-good 副本但当前路由不会自动反向恢复。发布前应补事务式 apply/rollback。
4. 通用 saved membership 快照不单独保存 `technical_provider`；需要时从文章证据缓存联表。
5. 继承数据库、Python 模型和兼容 API 中仍保留 `bvid/up_name/openbiliclaw.db` 名称。
   它们是内部 schema 兼容债，不得再次暴露成视频文案或视频平台行为。
6. 迁移包明确未加密且包含本机凭据，只能在可信设备间离线传递。
