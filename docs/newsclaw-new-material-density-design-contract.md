# NewsClaw 新材料判断工作台设计合同

## Design target

- Product: NewsClaw（新闻爪）
- Screen: 新材料判断工作台的优先阅读队列与单条详情
- Surface and viewport: 桌面网页主目标 2048×1108；同时覆盖窄桌面与 390×844 手机视口
- Intended user: 需要快速核验变化的政策、金融及其他高信息敏感度从业者
- 30-second outcome: 在 30 秒内扫完当前队列主题，选中一条真正变化，判断为何值得看，并知道证据能否直接核验
- Primary action: 选择一条材料并通过“查看原文”或明确标注的“检索原始来源”继续核验
- Supporting actions: 加入跟踪、稍后读、收藏、反馈推荐质量、展开已采集正文

## Visual anchors

- Primary style anchor: 当前 NewsClaw 事件脉冲台与新材料页的暖灰画布、纸张式表面、陶土橙强调色和左右主从结构
- Primary anchor role: 保留已被认可的 NewsClaw 产品气质和导航框架，只重建内容密度、证据入口与主从职责
- Secondary anchor 1: 当前正式 8430 页面左侧优先阅读队列
- Secondary anchor 1 role: 保留快速切换材料的工作台结构，压缩为“优先级、主题、干净标题、来源时间”四类扫描信息
- Secondary anchor 2: 当前详情中的结构化判断区
- Secondary anchor 2 role: 保留“为什么值得看、可能影响链、后续观察点”，让判断与原始证据同屏但不互相重复
- Negative reference: 用户 2026-08-30 截图中标题夹带正文、左右重复长标题、匹配信息重复、原文入口埋在底部且无后备路径的内容区域
- Rejected traits: 满屏长段落、把正文当标题、同一诊断在标签和句子中重复、把搜索结果伪装成精确原文、缺失链接时只给死按钮

## Information hierarchy

- Visual hero: 当前选中材料的干净标题、来源与证据状态，以及三段结构化判断
- First eye-line: 左侧队列的优先级、主题和最多两行干净标题
- Second eye-line: 右侧详情的来源、发布时间、精确链接状态和完整但去重后的标题
- Third eye-line: 为什么值得看、可能影响链、后续观察点，以及按需展开的已采集正文
- Deliberately de-emphasized: 匹配分数、技术提供商、抓取范围、重复的优先级原因和全文内容
- Off-screen or later routes: 内容库历史、个人记忆、聊天、设置和跟踪详情保持现有路由，不塞入本屏首屏

## Visual tokens

- Grid and margins: 延续现有应用外壳；工作区为 40% 队列与 60% 详情，内容边距使用现有 16/24 像素节奏
- Spacing scale: 4、8、12、16、20、24、32、48 像素
- Chinese type family and weights: 继承现有系统中文无衬线字体；正文 400/500，标签 600，标题 700/800
- Numeric type family and weights: 继承现有系统字体，时间与序号使用 500/600，避免额外字体依赖
- Type scale: 12 像素辅助信息，13–14 像素正文与队列标题，16 像素区块标题，24–30 像素详情主标题
- Canvas color: 继承现有暖灰画布变量
- Surface colors: 继承现有纸白、浅暖灰与选中态浅陶土表面变量
- Primary accent: 继承现有陶土橙，用于当前选择、证据入口与高优先级，不扩大为大面积装饰
- Data category colors: 主题使用中性暖灰；时效使用沙金；证据使用低饱和绿色；故事使用低饱和蓝灰
- State colors: 精确原文使用正向绿色提示；仅可检索使用琥珀提示；完全缺失使用中性灰；错误使用现有红色状态
- Radius: 8、12、16 像素及胶囊标签
- Border: 1 像素暖灰边框；选中队列项用左侧 3 像素陶土橙强调
- Shadow, blur, and texture: 仅保留现有轻阴影，不新增玻璃模糊或高噪纹理
- Icon family and stroke: 复用现有线性 SVG/字符图标，统一约 1.5–2 像素视觉线宽
- Memorable brand move: “证据灯”——在来源旁用一个小状态点和准确文案区分“精确原文”“来源检索”“链接缺失”

## Chart system

- Hero chart and purpose: 不适用；本屏的核心对象是单条变化与证据入口，不使用图表制造虚假权威感
- Supporting chart 1 and purpose: 不适用；队列用排序和标签表达优先级
- Supporting chart 2 and purpose: 不适用；后续观察点以可执行列表表达
- Stroke and marker language: 不适用图表；状态点采用实心圆和文字双重编码
- Grid and axis language: 不适用图表；左右分栏和 1 像素分隔线承担结构作用
- Label and annotation language: 所有状态直接使用白话中文，不仅依靠颜色或图标
- Hover and selection: 队列悬停只轻微抬高背景；选中态持久显示；外部链接悬停说明其是精确原文还是检索入口
- Empty and missing states: 精确链接为空时显示“来源未提供直链，可检索核验”；标题或来源也不足时显示“暂无可核验入口”，不得出现无解释的禁用按钮

## Data semantics

- Event units: 一条队列记录代表一份已采集新闻材料，不等同于一个已经去重合并的完整事件
- Person units: 不在本屏统计人员；作者或媒体只作为来源元数据
- Order units: 不适用
- Revenue units: 不适用
- Conversion denominators: 不适用；匹配分数只是推荐证据之一，不作为转化率展示
- Cross-source deduplication rule: 本次只做展示去重，不改文章身份；当标题以成对中文方括号包住一个合理长度的标题且后面紧跟正文时，展示层提取括号内标题，原始标题与正文继续保存在本地证据中
- Evidence states: exact 表示来源提供经过安全校验的精确 URL；search 表示来源未提供 URL，只能按媒体与标题检索；missing 表示标题或来源不足，不能生成可靠核验入口
- Demo-data label: 正式运行页不得混入演示数据；测试夹具必须显式标注为测试/演示

## Interaction

- Default time range: 当前后端已加载的推荐队列，继续遵守 NewsClaw 的当前日期与刷新合同
- Filters: 保留主题、媒体来源、推荐队列/今日首选和搜索筛选，不新增平行筛选层
- Primary interaction flow: 扫描左侧短标题 → 选择材料 → 阅读右侧结构化判断 → 查看精确原文或检索来源 → 需要时加入跟踪/保存/反馈
- Drill-down target: 精确 URL 在新标签页打开；检索后备入口在新标签页打开带媒体和精简标题的网页检索；已采集正文在原位展开
- Keyboard and focus expectations: 队列项保持按钮语义和可见焦点；选择后详情标题可聚焦；外链带可读标签、目标和安全 rel 属性
- Responsive fallback: 手机端保持现有详情抽屉；队列标题最多三行，证据状态与操作在标题后就近出现，避免横向挤压

## Working UI versus sales image

- Working UI priorities: 信息真实性、来源状态、扫描速度、键盘可达、长标题和缺失数据的稳定表现
- Sales image priorities: 仍以真实数据和真实状态展示，不为截图隐藏缺失链接或失败状态
- Elements allowed only in sales image: 无；本次不制作独立销售图，也不允许在正式 UI 中加入仅装饰的伪指标

## Implementation

- Existing runtime or template: 当前 NewsClaw 桌面 Web 前端与 FastAPI 推荐接口，继续使用现有 HTML/CSS/原生 JavaScript
- Chart implementation: 不新增图表库
- Token implementation: 复用 `app.css` 现有 CSS 变量和间距/圆角/颜色体系，仅增加本屏语义类
- Fonts and asset sources: 仅使用项目现有系统字体和内置图标，不引入外部字体或图片
- Realistic data source: 正式本地 SQLite 推荐记录；重点覆盖 Tushare `news/major_news` 不返回 URL 的真实合同，以及 RSS/其他来源返回精确 URL 的情况

## QA acceptance

- Primary reference viewport: 2048×1108，与用户截图相同级别视口；补测 1366×768 和 390×844
- Thumbnail test: 缩小到 25% 后仍能先看到左右职责、选中材料、标题和证据状态，不出现均匀文字墙
- Long-label test: 使用超过 200 字、以 `【标题】正文` 形式出现的真实 Tushare 样本；左侧不超过两行，右侧不重复正文
- Zero, missing, and extreme-value test: 覆盖精确 URL、空 URL 可检索、来源与 URL 均缺失、空正文、超长标题和 20 条满队列
- Reference-comparison passes: 至少完成宏观布局、微观排版和满数据压力三轮截图对比
- Accessibility checks possible from implementation: 键盘焦点、按钮/链接语义、状态非纯颜色编码、外链可识别、禁用状态有解释、长文本不遮挡操作
