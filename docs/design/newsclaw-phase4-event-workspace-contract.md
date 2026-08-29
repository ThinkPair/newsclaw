# NewsClaw Phase 4 事件脉冲台设计合同

## Design target

- Product: NewsClaw（新闻爪），本地运行、持续记录真实变化的信息助手。
- Screen: 桌面端首页的客观变化工作台，同时覆盖事件收件箱、单事件变化线与证据抽屉。
- Surface and viewport: 桌面 Web，主参考视口 1440 × 900 CSS px；1280 × 800 可完整完成主流程，窄屏降级为抽屉。
- Intended user: 政策、金融等对事实变化敏感、需要持续跟进同一事件的专业从业者。
- 30-second outcome: 用户在 30 秒内看清“哪件事从什么状态变成什么状态、何时生效、证据是否充分或冲突”。
- Primary action: 选择一条客观变化并核验证据。
- Supporting actions: 标为已读、进入完整事件档案、持续跟踪、切换主题与注意状态。

## Visual anchors

- Primary style anchor: `docs/design/assets/phase4-selected-event-pulse.png`，用户明确选择的“事件脉冲台”方向。
- Primary anchor role: 决定左侧事件收件箱、中间时间脉冲、右侧按需证据链的整体构图、阅读节奏与温暖编辑部气质。
- Secondary anchor 1: NewsSentry `03-real-data-detail.png`。
- Secondary anchor 1 role: 仅借浅色编辑式详情层级、克制留白和正文可读性。
- Secondary anchor 2: changedetection.io `screenshot-diff.png` 与 OpenCTI `case-timeline.png`。
- Secondary anchor 2 role: 前者只借 A→B 差异语义，后者只借可追溯的事件时间线交互；不借各自整体视觉。
- Negative reference: `docs/design/assets/phase4-negative-current-home.png`。
- Rejected traits: 新闻卡片瀑布、标题摘要推荐理由同时铺满、永久三栏文字墙、深色赛博风、KPI 图表墙、关系网络、标签云、市场情绪、预测、匹配度、AI 影响评分、把检查运行记录冒充客观变化。

## Information hierarchy

- Visual hero: 中央“变化脉冲”时间线；选中变化用完整 A→B 事实句和证据状态形成唯一视觉焦点。
- First eye-line: 今日发生或新发现的已核验客观 A→B 变化。
- Second eye-line: 左侧同一事件的最新变化、注意状态和生效时间。
- Third eye-line: 右侧来源、发布时间、证据深度、冲突与历史状态。
- Deliberately de-emphasized: 推荐、新材料、系统检查日志、模型推断、预测、情绪与可能影响；它们不进入客观主流。
- Off-screen or later routes: 完整事件档案、历史审计线、检查回执、分析与围绕事件的对话。

## Visual tokens

- Grid and margins: 顶栏 64px；主导航 176–196px；事件收件箱 292–324px；中间弹性列最小 440px；证据列 340–376px；区域以 1px 分隔，不用多层卡片套娃。
- Spacing scale: 4 / 8 / 12 / 16 / 24 / 32px；主区域内边距 24px，行内间距 8–12px。
- Chinese type family and weights: 复用产品现有 `HarmonyOS Sans SC, PingFang SC, Microsoft YaHei UI, Noto Sans CJK SC, Source Han Sans SC, system-ui`；正文 400/500，标题 600/700。
- Numeric type family and weights: 复用正文无衬线字体，时间和编号使用 500/600，避免另加等宽字体破坏温度。
- Type scale: 12px 元数据、13–14px 控件与正文、16px 行标题、20px 分区标题、24–28px 选中变化主事实。
- Canvas color: `#f5f4ed` 暖象牙底。
- Surface colors: `#faf9f5` 主表面、`#ffffff` 证据表面、`#eeeae1` 选中与弱分区表面。
- Primary accent: `#b4583a` 锈橙，只用于当前位置、主动作和选中脉冲。
- Data category colors: 主题不另设彩虹色；主题只用中性色文字，避免类别颜色抢占状态语义。
- State colors: 已确认 `#168a63` + 对勾文字；冲突 `#b87411` + 警示文字；错误 `#b53333`；未读以锈橙圆点与文本共同表达。
- Radius: 控件 8px；选中变化与证据条目 10–12px；大区块不使用悬浮圆角卡片。
- Border: `#e5e1d8` 1px；选中项左侧 3px 锈橙并保留明确焦点环。
- Shadow, blur, and texture: 默认无模糊、无渐变；只允许证据抽屉 `0 12px 32px rgba(20,20,19,.08)` 的轻阴影。
- Icon family and stroke: Phosphor Icons Regular，统一 1.5–2px 视觉线重；品牌图标复用现有 NewsClaw PNG 资产。
- Memorable brand move: “脉冲节点 + A→B 事实句 + 可反查证据链”三件套，任何一条变化均可从主句沿变化 ID 回到本地证据。

## Chart system

- Hero chart and purpose: 无传统统计图；以纵向事件脉冲时间线回答“现实具体发生了什么变化”。
- Supporting chart 1 and purpose: 无；左侧事件收件箱承担导航而非统计。
- Supporting chart 2 and purpose: 无；右侧证据链承担核验而非统计。
- Stroke and marker language: 时间线 1px 中性线；确认节点为青绿实心环，冲突节点为琥珀警示环，未确认或历史资料不足为中性虚线环。
- Grid and axis language: 时间轴仅显示现实生效时间与 NewsClaw 发现时间，不显示无意义数值坐标。
- Label and annotation language: 主标签只允许“已确认 / 有冲突 / 历史资料不完整 / 已被后续证据替代”；每条都带文字，不只靠颜色。
- Hover and selection: 行悬停只改变浅表面；选中后展示完整事实与证据；键盘焦点清晰可见。
- Empty and missing states: 分清未建立跟踪、正在跟踪但无核验变化、有材料但无确认变化、未读清空、证据冲突、筛选为空、加载失败与事件合并异常。

## Data semantics

- Event units: 一项 `news_event` 是同一现实事件的长期本地档案；事件不是单篇新闻，也不是一次系统检查。
- Person units: 本屏不统计人物；人物只可作为事件实体或证据出处。
- Order units: 客观收件箱按后端稳定游标的 `detected_at DESC`；同事件时间线明确显示 `effective_at` 与 `detected_at`，不在客户端伪造全局排序。
- Revenue units: 不适用；本屏不展示营收或市场价格预测。
- Conversion denominators: 不适用；本屏不展示转化率。
- Cross-source deduplication rule: 同一 `event_id + change_id` 只显示一次；证据按 `evidence_item_key` 聚合同源修订，事件别名解析到 canonical event ID。
- Evidence states: `full_text` 可显示已核验正文；`title_only` 只能标“标题级”；`partial_legacy` 标“历史资料不完整”；`related_no_change` 不得冒充支持变化；冲突与客观流隔离。
- Demo-data label: 独立测试数据截图必须显著写“演示数据”；正式生产库空态不注入假新闻。

## Interaction

- Default time range: 最近发现优先，后端稳定游标分页；不宣称自然日完整统计。
- Filters: 第一阶段提供服务端支持的注意状态（全部 / 未读 / 冲突）与主题；优先级仅在存在可靠持久字段后启用，不能用前端临时值伪装。
- Primary interaction flow: 打开客观变化 → 选择事件 → 选择变化 → 按需加载证据 → 标为已读或进入完整事件档案。
- Drill-down target: 同一事件的完整当前线、审计历史、证据与冲突；跟踪控制仍回到持续跟踪页面。
- Keyboard and focus expectations: Tab 顺序覆盖导航、筛选、事件、变化、证据和动作；Enter/Space 激活；方向键可在事件列表与时间线内移动；焦点环不被移除。
- Responsive fallback: 小于 1180px 时证据列变右侧抽屉；小于 820px 时事件列表与时间线单屏切换，主导航使用现有移动抽屉；核心 A→B 句不横向滚动。

## Working UI versus sales image

- Working UI priorities: 真实空态、加载/错误重试、稳定游标、证据分页、已读同步、争议隔离、事件别名、窄屏抽屉与键盘闭环。
- Sales image priorities: 只可使用独立测试数据库的明确“演示数据”展示视觉密度和交互状态。
- Elements allowed only in sales image: 演示数据标签和预置演示事件；生产界面不得显示虚构政策、行情或来源。

## Implementation

- Existing runtime or template: 复用现有 FastAPI + 原生 HTML/CSS/JavaScript 桌面运行时，不新建前端框架或产品路由。
- Chart implementation: 原生语义 HTML/CSS 时间线，无第三方图表库。
- Token implementation: 在现有 `app.css` 设计令牌之上新增 `--event-*` 语义令牌，经典主题同步覆盖；不改用户主题偏好合同。
- Fonts and asset sources: 现有本地字体回退；现有 NewsClaw PNG 品牌资产；新增图标使用本地 vendored Phosphor Icons Webfont 和许可证文件。
- Realistic data source: `/api/news/events`、`/api/news/changes`、`/api/news/events/{id}`、`/changes`、`/evidence`、`/conflicts` 与逐条已读接口；演示截图使用隔离测试数据库。

## QA acceptance

- Primary reference viewport: 1440 × 900 CSS px，浅色主题、事件脉冲台首页、证据面板展开。
- Thumbnail test: 25% 缩略图中仍一眼看到中间时间脉冲为英雄区，左侧是事件收件箱，右侧是证据，不退化成文字墙。
- Long-label test: 60 个中文字符的事件标题、80 个中文字符的 A→B 事实和长来源名不碰撞，按合同截断或展开。
- Zero, missing, and extreme-value test: 0 事件、0 变化、0 证据、仅争议、500+ 事件分页、证据字段缺失、事件合并 409、网络错误均有诚实状态。
- Reference-comparison passes: 至少一次同视口宏观对比、一次英雄区与证据区微观对比；修复所有 P0/P1/P2 后重新截图。
- Accessibility checks possible from implementation: 键盘主流程、可见焦点、状态非纯颜色、语义按钮/列表、`aria-live` 加载与错误、200% 缩放和窄屏回归。
