# NewsClaw 用户可见控件闭环矩阵

> 初始审计基线：`22fe328`（2026-08-17）；2026-08-31 已按四端全功能审计重新核验并补齐失败、并发、焦点与作用域闭环。“持久化 / 状态”一列明确区分服务端落库、运行时状态和纯前端状态。同一处事件委托产生的同类动态卡片按一行记录。

本次复核新增的共同门禁：顶部筛选仅在“新材料”页可见并生效；所有抽屉、菜单和模态框
支持 Esc、焦点圈定、背景 inert 与关闭后焦点归还；发送、加载和反馈采用 single-flight，
只有后端确认成功才清空草稿或更新终态；通知加载失败显示错误与重试，不得渲染成空消息。

## 桌面 Web

| 可见控件 | 事件处理 | API | 持久化 / 状态 | 成功回显 | 失败回显 | 结论 |
|---|---|---|---|---|---|---|
| 品牌、主页、内容库、画像、对话、设置、侧栏 / 移动菜单 | `open*Page` / `toggleSideDrawer` / `openMobile*` | 按页面读取对应 API | 前端导航状态 | 页面、标签和焦点同步 | 单个资源在本页显示错误 / 重试 | 闭环 |
| 顶部“筛选当前已加载推荐”、重置 | `searchForm/input` / `resetFiltersBtn` | 无 | `state.query/filter` | 卡片立即过滤 / 恢复 | 无远端失败 | 闭环；不宣称全库搜索，不写画像 |
| 关于新闻爪 | `bindAboutButton` | 无 | 前端弹层 | 显示独立产品说明 | 缺 DOM 时进入 fatal 状态 | 闭环；无上游 GitHub 业务跳转 |
| 主题、色相、强调色、回到顶部 | `cycle/setTheme*` / `initBackToTop` | 无 | `localStorage` + DOM | 立即换肤 / 滚动 | 非法色相夹到 0–360 | 闭环 |
| 手机二维码、复制链接 | `openMobileQrDrawer` / clipboard handler | `GET /api/qr-info` | 不落库；只保存已看入口标记 | 仅非 loopback 且后端报告 LAN 可用时生成二维码并启用复制 | `127.0.0.1` 监听、状态读取失败或仍是 loopback 时禁用复制并明示说明 | 闭环；正式启动当前不宣称手机可直连 |
| 消息、待聊、后台动态、加载更多 | `renderMessages` / `respondNotification` / `refreshDesktopPendingConfirmations` / `loadActivityPage` | `GET /notifications/pending`、`POST /notifications/ack`、`/chat/pending-confirmations`、`/activity-feed` | 服务端消息 / 对话 / 活动记录；确认使用 `article_id + request_id` | 待通知新闻按 `exact/search/missing` 显示“阅读原文 / 检索来源 / 暂无原文”；精确原文才写点击并确认，检索只导航，手工标记可单独确认 | 确认失败保留当前卡片和抽屉快照并提示重试；通知读取失败显示错误 / 重试而不是“没有消息” | 闭环 |
| 换一批、继续加载、失败后重新加载 | `reshuffle` / `appendMore` / `restartDesktopFailedRecoveries` | `POST /recommendations/reshuffle`、`/append`；`GET /recommendations` | 推荐池 / 前端卡片 | 替换 / 追加；空结果保留现有卡片 | 空池、补货中和请求失败分别提示 | 闭环；NewsClaw 后端强制 `source_platform=news` |
| 推荐详情“阅读原文 / 检索来源 / 暂无原文” | `recordOriginalOpen` / `recordSourceSearch` | 精确原文才 `POST /recommendation-click`；检索无写接口 | 精确原文点击作为弱学习信号；检索不写点击、已读、画像或原文证据 | 来源有精确 URL 时直接打开；Tushare 未给 URL 时按“媒体 + 精简标题”打开外部检索并持续提示仍需核验 | 媒体或标题不足时禁用“暂无原文”；精确点击记录失败不阻断外链 | 闭环；`exact/search/missing` 三态不混淆 |
| 推荐卡喜欢 / 不感兴趣 / 忽略 / 撤销 | `stageRecommendationFeedback` | `POST /feedback` | 幂等 `request_id`，反馈落库并参与画像 | 10 秒可撤销，提交后明示结果 | 回滚按钮和文案，保留当前卡片 | 闭环 |
| 推荐卡“聊一聊”、发送 / 取消 | `handleCardAction` | `POST /feedback` (`comment`) | 评论线索落库并参与学习；同一输入 single-flight | 后端确认后状态行 + toast 并清空原输入 | 草稿回到发起动作的原输入框，状态行显示原因 | 闭环 |
| 推荐卡 / 惊喜卡收藏、稍后读、取消 | `desktopSavedMutations.toggle` | `POST /saved/favorite|watch_later`、`/remove` | 本机会员表；news 强制 `local_only_source`，清空旧 sync task 回显 | 按钮状态与“已保存 / 移除到本机” | 按钮回滚，状态行显示失败 | 闭环；不写画像，不同步媒体账号 |
| 惊喜上 / 下一条、展开摘要 | `setActiveDelight` / excerpt toggle | 无（已加载队列） | 前端索引 / DOM | 卡片和 `x/y` 更新 | 空队列不执行 | 闭环 |
| 今日首选刷新 | `refreshTodayPick` / 今日队列串行读取 | `GET /api/delight/pending-batch`、`POST /api/delight/trigger` | 北京时间今日 `news` 范围、持久评估预算和后端队列真值 | ready 时替换今日队列；processing/empty/degraded 分别回显 | 保留已加载内容，不用旧闻伪装成功；重叠点击共享最新结果 | 闭环 |
| 惊喜阅读、来源检索、喜欢、不感兴趣、看过 / 忽略 | `respondDelight` | 精确阅读才 `POST /delight/respond` 与 `/recommendation-click`；来源检索无写接口 | 精确阅读、惊喜响应和点击事件落库；检索不写点击或消费状态 | `exact/search/missing` 显示“阅读原文 / 检索来源 / 暂无原文”，已处理项出队 | 检索只导航且保留当前项；缺失入口禁用；写入未成功时保留并显示错误 | 闭环 |
| 惊喜“聊一聊” / 发送 | `respondDelight(send-comment)` | `POST /chat/turns`、`GET /chat/turns/{id}` | 对话 turn 落库 | pending → completed 回复 | 草稿恢复、failed / 超时文案 | 闭环 |
| 内容库稍后读 / 收藏 / 历史 tab，刷新、加载、重试 | `openContentLibraryPage`、`refresh*`、`loadContentHistoryPage` | `GET /saved/{kind}`、`GET /content-history` | 服务端本地会员 / 30 天历史 | 计数、列表、分页回显 | 保留已加载项并提供重试 | 闭环 |
| 内容库打开、移除、恢复、喜欢 / 不感兴趣 / 聊一聊、交叉收藏 | 卡片事件委托 | `/recommendation-click`、`/saved/*`、`/events` | 打开 / 反馈 / 本机会员分别落库 | 卡片就地更新 | 恢复按钮 / 状态行 / toast | 闭环；news 不显示原生平台同步动作 |
| 画像展开历史、编辑、添加 / 删除 / 保存 / 恢复 AI 建议 | `loadMoreProfileMemory` / `applyProfileEdit` | `GET /profile-summary`、`GET /profile-edit-state`、`POST /profile-edit` | 画像层落库；手工覆盖最高优先级 | 重读编辑态和画像 | 保留编辑态并显示失败 | 闭环 |
| 对话发送、失败重试、待聊打开、确认 / 否认 / 修正 / 稍后再聊 | `sendChat`、`handleDesktopPendingOpen`、`handleDesktopCardAction` | `/chat/turns`、`/chat/pending-confirmations`及卡片 action API | turn / 结算状态落库 | pending 到 terminal，卡片和待聊数同步 | 草稿恢复、可重试错误与超时文案 | 闭环 |
| AI 实例新建 / 编辑 / 删除 / 排序 / 路由、模型发现、链路探测、Embedding 探测 | 草稿管理 + `discover/run*Probe` | `POST /config/discover-models`、`/config/probe-service`；最终 `PUT /config` | 草稿在前端；保存后配置原子落盘 / 热应用 | 探测结果、已保存 / 应用中 / 已应用 | 字段级错误；热应用失败回滚最后可用配置 | 闭环 |
| Tushare Token 单独保存 | `saveTushareCredential` | `PUT /news/credentials/tushare` | 本机凭据文件，不回显原值 | 清空输入、移除该字段 dirty 标记、重读来源 | 保留输入并显示失败 | 闭环 |
| Tushare 三源和每个 RSS 的启停 | `persistNewsSourceSettings` | `PUT /news/sources/{source_id}` | 来源设置文件 | 只提交实际变更的 `{enabled}`，重读单媒体卡片 | 整体保存显示失败，保留未确认草稿 | 闭环；不再一张卡批量覆盖 3 个官方 RSS |
| 自定义 RSS 每行一个、保存时新增 / 删除 | URL 差集 | `POST /news/sources/rss`、`DELETE /news/sources/{id}` | 来源设置文件 | 重读后每媒体独立卡片 | 非 URL 立即拒绝；网络失败不伪装保存 | 闭环 |
| 验证全部新闻源、立即刷新 | `runNewsRefresh` | `POST /news/sources/probe` 或 `/news/refresh` | 来源健康 / 刷新任务终态 | 每源健康、最后成功、错误回显 | 单源降级不拖死页面；请求失败显示原因 | 闭环 |
| 调度、通用、前端、日志可见字段；保存 / 放弃 | form dirty + `buildConfigUpdate` | `PUT /news/settings`、`PUT /config`、`GET /config/apply-status`、`GET /news/sources` | 运行时设置 / 配置文件 / 本地前端设置 | dirty→saving→applying→applied；各配置域分别显示成功结果并重读真值 | 超时显示“无法确认是否写入”；部分成功逐域报告，失败域保留草稿；应用失败回滚 | 闭环；多端点保存不是跨 API 事务，见限制 |
| 访问密码保存、开机自启、代理探测 | `lanAuthControl`、`bootAutostartControl`、`runNetworkProxyConfigProbe` | `/auth/admin`、`/autostart/apply`、`/config/probe-service` | 鉴权状态 / Windows 自启 / 无持久探测 | 开关和状态文案回显 | 回滚开关或显示探测错误 | 闭环 |
| 导出、导入、取消待导入 | `export/stage/cancelMigration*` | `/migration/export`、`/import`、`/pending`、`/status` | 备份文件 / 待应用迁移状态 | `.newsclaw-backup` 下载；导入校验后要求重启 | 校验、环境、空间、忙状态错误明示 | 闭环；可读 `.obcbackup` 仅为兼容 |

## 手机 Web / PWA

| 可见控件 | 事件处理 | API | 持久化 / 状态 | 成功回显 | 失败回显 | 结论 |
|---|---|---|---|---|---|---|
| 底栏推荐 / 内容库 / 画像 / 对话，内容库三 tab | hash router / `navigateToTab` | 每 view 读对应 API | hash + view state | 选中态、焦点、返回历史同步 | 子 view 显示重试 | 闭环 |
| 消息铃、待通知新闻、探测回答、打开待聊 | `toggleMessages` / `handleNewsNotificationAction` 及委托 | `/notifications/*`、`/interest-probes/respond`、`/avoidance-probes/respond`、`/chat/*` | 精确原文阅读或手工标记后确认通知；检索不写点击或已读 | 待通知新闻按 `exact/search/missing` 显示“阅读原文 / 检索来源 / 暂无原文”，徽标、卡片、对话同步 | 确认失败保留卡片；通知刷新失败保留旧项；缺失入口禁用 | 闭环 |
| 新闻源设置、关闭、验证、立即刷新 | `openMobileSettings` | `GET /news/sources`、`POST /news/sources/probe|refresh` | 健康 / 刷新任务 | 每媒体健康、最后成功、错误 | 离线 / 降级不清空已有内容 | 闭环；手机端为只读来源卡 + 真实任务 |
| 换一批、追加、下拉 / 失败重试、惊喜上下条 | recommend handlers | `/recommendations*`、`/delight/*` | 推荐池 / 前端队列 | 卡片替换 / 追加 | 保留已加载内容、明示重试 | 闭环 |
| 阅读原文、检索来源、喜欢 / 不感兴趣 / 忽略 / 评论 | recommendation / feedback handlers | 精确原文才 `/recommendation-click`；检索无写接口；反馈使用 `/feedback`、`/events` | 精确原文点击 / 反馈事件落库；检索不写点击、已读或画像 | `exact/search/missing` 三态与卡片状态、toast 一致 | 缺失入口禁用；反馈回滚、草稿保留、重试 | 闭环 |
| 收藏 / 稍后读 / 移除 / 交叉收藏 | saved handlers | `/saved/{kind}`、`/remove`、`/status` | 本机会员；强制 `unsupported/local_only_source` | “仅保存在本机新闻爪” | 保留卡片并显示失败 | 闭环；旧 sync task 字段被清空，不会恢复平台任务 |
| 历史刷新 / 重试 / 加载 / 打开 / 恢复 | history handlers | `/content-history`、`/saved/*` | 30 天历史 / 本机会员 | 分页、恢复状态 | 局部重试 | 闭环 |
| 画像编辑全部按钮、认知加载更多、探测回答 | profile handlers | `/profile-summary`、`/profile-edit-state`、`/profile-edit`、probe API | 画像落库 | 重读真值 | 保留编辑面并提供重试 | 闭环 |
| 对话发送、重试、清除草稿、确认卡动作 | chat handlers | `/chat/turns*`、确认卡 API | turn / 结算落库；草稿为前端状态 | pending → terminal | 草稿恢复、失败转可重试 | 闭环 |
| 访问密码登录、回到顶部 | login submit / `initBackToTop` | `POST /auth/login` / 无 | session cookie / DOM | 重载进入应用 / 滚动 | 表单原位错误 | 闭环 |

## 首次设置

| 可见控件 | 事件处理 | API | 持久化 / 状态 | 成功回显 | 失败回显 | 结论 |
|---|---|---|---|---|---|---|
| AI 服务商 / 模型 / Base URL / Key，“验证 AI 并继续” | `probeAndSaveLlm` | `POST /config/probe-service` 后 `PUT /config`，必要时 `GET /config/apply-status` | 探测先不落盘；成功后配置落盘 / 热应用 | 进入第 2 步并清空 Key | 留在原步、显示真实错误 | 闭环；不再用 ping 伪装 AI 验证 |
| Tushare Token、验证新闻源 | `saveSourceCredential` / `validateSources` | `PUT /news/credentials/tushare`，`POST /news/sources/probe` | 本机凭据 / 来源健康 | Tushare 三接口和每个 RSS 独立回显 | 单源失败标降级；整体请求失败留在原步 | 闭环 |
| 关注标签、避开标签、补充避雷边界 | tag handlers | 无，第 4 步随 bootstrap 提交 | 当页草稿 | 按压状态；少于 3 个关注方向不放行 | 原地显示规则 | 闭环；自由文本明确是“需要避开”，不错写到兴趣 |
| 建立首批推荐、失败重试、页面恢复进度 | `startBootstrap` / `pollBootstrap` | `POST /news/bootstrap`、`GET /news/bootstrap/status` | 任务和终态持久化；相同草稿重试复用同一 request ID | 阶段进度、completed / degraded 真实回显后进入 Web | failed 留在本步可重试；短断线最多 5 次后报错 | 闭环；已删除 `/api/init` 回退 |
| 上一步 / 下一步 | `showStep` | 无 | 当页步骤 | tab / 按钮状态更新 | 该步 API 失败时不前进 | 闭环 |

## 浏览器扩展

| 可见控件 | 事件处理 | API | 持久化 / 状态 | 成功回显 | 失败回显 | 结论 |
|---|---|---|---|---|---|---|
| 重试连接 | `#retryButton` click | 本机 health / capabilities | 扩展连接状态 | 进入应用态 | 保留错误态 | 静态合同通过；待真浏览器 E2E |
| 打开设置 / 错误态设置 / 取消 | `settingsButton`、`stateSettingsButton`、`cancelSettingsButton` | 无 | 弹层状态 | 打开 / 关闭设置 | 无远端失败 | 静态合同通过；待真浏览器 E2E |
| 本机地址表单 | `settingsForm` submit + `normalizeEndpoint` | 保存后重连 | `chrome.storage` | 只接受 `127.0.0.1` / `localhost`，保存后重连 | 非本机地址表单内拒绝 | 闭环；扩展不接受 LAN / 公网后端 |
| 打开完整版 | `openFullButton` click | 无（打开本机 Web URL） | 新 tab / 窗口 | 打开 NewsClaw Web | 异常进入扩展错误态 | 静态合同通过；待真浏览器 E2E |

## 已隐藏 / 移除的假功能

| 入口 | 处理 |
|---|---|
| 桌面“重新初始化 / 重建画像” | 移除按钮，改为只读说明。`/api/news/bootstrap` 尚无破坏性 force 合同，不能用旧 `/api/init` 伪装。 |
| 高级视频 / 弹幕 / 关键帧 / 封面多模态 | 从 NewsClaw DOM 与保存逻辑中移除；保存其他设置只原样保留已读取的 legacy discovery / embedding 兼容字段，不将缺失控件解释为 `false`。 |
| 搜索词生成模式 | 从 NewsClaw DOM 与保存逻辑中移除；新闻 API / RSS 刷新不运行旧视频 discovery 关键词轴链路。 |
| 单来源权重与独立刷新频率 | 控件隐藏且不再经 `/config` 假保存；当前只保存真实生效的启停。 |
| 桌面 / 手机“同步到原平台” | news 项强制仅本机；桌面批量同步按钮隐藏，手机不产生可操作的 sync 视图。 |
| 数据库路径 | 只读；真实数据库由数据目录决定。 |
| 浏览器扩展网页注入 / Cookie / 活动标签页 | NewsClaw manifest 不声明这些权限，构建不包含 content scripts。 |

## 仍需主线验收的限制

1. 设置保存横跨新闻源、新闻运行时和通用配置三个 API，不是数据库级分布式事务。前一步已写入而后一步失败时，页面会报失败并保留草稿，再次保存会按服务端真值补齐；尚未提供跨端点原子回滚。
2. 正式运行准备脚本目前把 API 监听地址固定为 `127.0.0.1`，所以二维码界面会如实显示“手机入口尚未启用”。真正改为 `0.0.0.0` 还需主线同时验收 Windows 防火墙、访问密码与局域网安全。
3. 迁移包对外文件名与 MIME 已改为 NewsClaw；包内 manifest 仍保留上游格式版本，用于读取旧备份。这是文件格式兼容，不是产品链接或运行依赖。
4. 扩展按钮已通过 DOM → handler 静态合同，但权限、窗口 / 侧栏打开和 `chrome.storage` 仍需在真实 Chrome / Edge 中跑 E2E；本轮没有将静态检查写成“浏览器已验收”。
5. 原文链接由新闻 API / RSS 提供，打开后是否可读受媒体网站、网络和登录要求影响；NewsClaw 不用爬虫或 Cookie 绕过媒体限制。
