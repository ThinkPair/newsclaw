# Research module (`openbiliclaw.research`)

## 定位

Research 模块为 NewsClaw 提供本机受控的外部研究能力。第一版只接入 Tavily
Search 与 Extract，并坚持“证据先行”：返回标题、公开 URL、证据片段、来源域名、
发布时间和相关度，而不是让第三方直接替 NewsClaw 生成最终结论。

Tavily 在这里是 **按需研究提供方**，不是 Tushare/RSS 一类定时新闻源。研究结果
不会自动写入用户画像、新闻候选池或推荐池，也不会自动触发后台搜索。Agent 只有在
用户明确提出研究问题、补充外部证据或抽取指定公开网页时才应调用它。

## 已实现功能

| 能力 | 状态 | 合同 |
| --- | --- | --- |
| Provider-neutral models | 已实现 | `SearchRequest`、`ExtractRequest`、`ResearchEvidence`、`ResearchResult` |
| Tavily Search | 已实现 | 固定 `basic` 与 `chunks_per_source=1`；默认 5 条、硬上限 8 条；关闭 answer、raw content、图片和自动参数 |
| Tavily Extract | 已实现 | 研究问题必填；每次 1–3 个公开 HTTP(S) URL；固定 `basic`；每个来源默认 2、上限 3 个片段 |
| 本地凭据 | 已实现 | 私有运行目录优先，`TAVILY_API_KEY` 仅作为环境变量回退；API 只返回脱敏状态 |
| SQLite 研究账本 | 已实现 | `newsclaw_research.db` 保存 run、证据快照与关联，不复用画像或推荐数据库表 |
| TTL 缓存 | 已实现 | general 24 小时、news 30 分钟、finance 15 分钟；`refresh=true` 显式绕过新鲜缓存 |
| REST | 已实现 | provider 状态/凭据/真实 probe、`/api/research/search`、`/api/research/extract` |
| Agent Bridge | 已实现 | `research-search` 与 `research-extract`，返回和 REST 一致的研究结果 |
| Desktop settings | 已实现 | 卡片名为“Tavily 按需研究（不是新闻源）”；支持脱敏状态、保存/删除 Key，以及通过官方 `/usage` 完成不消耗搜索额度的真实连接验证 |

## 数据流与模块边界

```text
Desktop settings ── credential write ──→ private NewsClaw credential file
                                              │
User / Agent question                         │ local key first
        │                                     │ env fallback
        ├─ REST /api/research/* ──────────────┤
        └─ Agent Bridge research-* ───────────┤
                                              v
                                      ResearchService
                                      │ local fresh cache?
                                      ├─ yes → replay run/evidence
                                      └─ no  → TavilyProvider
                                                   │
                                                   v
                                      bounded evidence + provenance
                                                   │
                                                   v
                                      newsclaw_research.db

No automatic edge to: profile / Soul / discovery_candidates / content_cache /
recommendation pool.
```

该模块与现有 `discovery.inspiration_provider` 的职责不同：inspiration provider 只为
发现关键词提供临时 grounding；Research 模块保存用户主动研究的可追溯 run 与证据。
以后如果需要让 Tavily 参与关键词灵感链，必须通过显式适配器把
`ResearchEvidence` 投影为 inspiration preview，不能把研究历史表当作发现缓存。

## Phase 1 客户端范围

- **研究交互入口**：DSH 的 `newsclaw_search_web` /
  `newsclaw_extract_evidence` 工具，以及后端 REST/Agent Bridge。
- **桌面 Web**：只管理 Tavily Key、显示脱敏状态，并通过本机 probe 调用 Tavily 官方
  `/usage` 验证连接和额度；本轮没有研究问题输入框、结果列表或研究历史 UI。
- **手机 Web/PWA 与浏览器扩展**：本轮不新增研究按钮、不接收或保存 Tavily Key，
  也不直接访问 Tavily。以后如展示研究结果，仍只能消费同一本机后端的安全投影。

因此“桌面设置里看见 Tavily 卡片”不代表四端都已经具备研究工作台。

## 请求合同

### Search

```json
{
  "query": "人工智能基础设施最近一周有哪些关键变化？",
  "topic": "news",
  "time_range": "week",
  "max_results": 5,
  "refresh": false
}
```

- `query` 必填；在发往外部服务前会规范化，并拒绝明显的邮箱、手机号、本机路径、
  密钥和令牌。
- `topic` 为 `general | news | finance`，默认 `general`。
- `time_range` 可省略；可选 `day | week | month | year`。
- `max_results` 默认 5，范围为 1–8。
- Tavily 参数固定为 `search_depth=basic`、`chunks_per_source=1`、`include_answer=false`、
  `include_raw_content=false`、`include_images=false`、`auto_parameters=false`。

### Extract

```json
{
  "urls": ["https://example.com/report"],
  "query": "这份报告对算力供给的核心判断是什么？",
  "chunks_per_source": 2,
  "refresh": false
}
```

- `query` 必填，使用与 Search 相同的隐私检查。
- `urls` 必须包含 1–3 个公开 `http`/`https` URL。
- 拒绝带账号密码的 URL、localhost、单标签主机、私网/环回 IP，以及
  `.local`、`.localhost`、`.internal` 主机；URL fragment 不发送。
- `chunks_per_source` 默认 2，范围为 1–3；固定 `extract_depth=basic`，不返回图片。
- Extract 只处理调用者明确给出的 URL，不是通用网页爬虫，也不会读取浏览器 Cookie。

### 统一结果

Search 与 Extract 返回同一结构：

```json
{
  "run_id": "...",
  "operation": "search",
  "query": "...",
  "provider": "tavily",
  "provider_request_id": "...",
  "cache_hit": false,
  "fresh_until": "...",
  "topic": "news",
  "time_range": "week",
  "result_count": 1,
  "credits_used": 1,
  "evidence": [
    {
      "url": "https://example.com/article",
      "title": "...",
      "content": "...",
      "source_domain": "example.com",
      "published_at": "...",
      "score": 0.9
    }
  ]
}
```

Search 的每条 content 最多保留 600 字符、总计最多 3,000 字符；Extract 每条最多
1,500 字符、总计最多 4,500 字符。`provider_request_id` 用于和 Tavily 侧请求对账；`credits_used` 只有 provider 返回
usage 时才有值。`cache_hit=true` 表示本次没有再次消耗 Tavily 请求。证据 URL 是
引用入口，不代表 NewsClaw 已验证来源中的每个陈述。

## REST API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/research/providers/tavily` | 查看是否已配置以及密钥来自本地还是环境变量；只返回掩码 |
| `PUT` | `/api/research/providers/tavily/credential` | 把 Tavily API Key 写入 NewsClaw 私有运行目录 |
| `DELETE` | `/api/research/providers/tavily/credential` | 删除本地副本；环境变量若存在会继续作为回退生效 |
| `POST` | `/api/research/providers/tavily/probe` | 调用 Tavily 官方 `GET /usage` 验证连接、权限与额度；不发送 Search、不消耗搜索 credit |
| `POST` | `/api/research/search` | 执行或回放一轮受控 Search |
| `POST` | `/api/research/extract` | 从 1–3 个显式公开 URL 提取问题相关证据 |

Provider 状态固定为：

```json
{
  "provider": "tavily",
  "configured": true,
  "stored_locally": true,
  "source": "local",
  "masked": "••••••••"
}
```

`source` 为 `local | environment | none`。密钥明文不会从 GET、错误、日志、研究
run 或 Agent 输出中返回。

Key 写入要求 trim 后长度 8–512 且不含空白字符。共享
`newsclaw_credentials.json` 使用按字段合并更新，因此 Tavily Key 与 Tushare Token
不会互相覆盖；删除 Tavily 只删除 `tavily_api_key` 字段。

桌面设置中的“验证连接（不消耗搜索额度）”调用本机 provider probe 端点；后端只用
Key 鉴权访问 Tavily 官方 `GET /usage`。它验证的不只是“Key 长得像”，而是真实网络、
权限和额度，但不会发送研究 query 或 URL、不会创建本地研究 run，也不会消耗搜索
credit。成功可返回 `{ok, provider, key_usage, key_limit, account_plan, plan_usage,
plan_limit}`；这些只是脱敏的用量/套餐投影，Key 仍不会展示或回显。

## Agent Bridge

Search payload：

```json
{
  "command": "research-search",
  "argv": [
    "--query", "本周 AI 基础设施有哪些重要变化？",
    "--topic", "news",
    "--time-range", "week",
    "--max-results", "5"
  ]
}
```

Extract payload：

```json
{
  "command": "research-extract",
  "argv": [
    "--query", "这两份材料有哪些相同判断与冲突？",
    "--url", "https://example.com/a",
    "--url", "https://example.com/b",
    "--chunks-per-source", "2"
  ]
}
```

上面的 payload 发往运行中 NewsClaw 的 `POST /api/agent-bridge`；DSH 工具会代为
完成这一步。两条命令均接受 `--refresh`。它们依赖服务进程注入的 `NewsRuntime`，
当前不支持把同名 parser 子命令作为 cold-start 独立模块 CLI 直接运行。Agent
Bridge 只调用本机 `ResearchService`；Agent 宿主和 DSH 插件不持有 Tavily Key，
也不直接访问 Tavily。NewsClaw runtime 会把
`newsclaw_search_web` / `newsclaw_extract_evidence` 加入 `capabilities`；宿主应
用能力协商刷新工具，不长期维护旧的静态命令清单。

## 本地持久化与缓存

研究数据放在活动 NewsClaw 数据目录下的 `newsclaw_research.db`：

- `research_runs`：请求参数摘要、operation、provider request ID、状态、请求与
  新鲜截止时间、结果数、credits 和安全错误信息；
- `research_evidence`：规范 URL、标题、证据片段、来源域名、发布时间、provider
  分数和内容指纹；
- `research_run_evidence`：保存一次 run 对证据快照的稳定顺序与 query-specific
  relevance score，避免同一页面被后续查询复用时改写旧 run 的历史分数。

缓存键绑定规范化 query 和会改变结果的有界参数。命中新鲜缓存时不调用 Tavily；
`refresh=true` 只绕过缓存，不删除旧 run 或证据。缓存“过期”表示不能继续当作新鲜
响应复用，不表示历史证据被改写或自动删除。

## 安全、成本与失败规则

- HTTP 客户端使用 `trust_env=False`，不继承系统代理；凭据通过 Bearer header
  发送且永不持久化到研究表。API Key 只用于请求鉴权，不会拼进 query、URL 或
  evidence 正文，也不会在页面、REST GET 或 Agent 输出中展示/回显。
- 搜索默认每轮最多 5 条、硬上限 8 条，持久化内容每条最多 600 字符、合计最多
  3,000 字符；Extract 每轮最多 3 个 URL、每个来源最多 3 个片段，落库/返回内容
  每条最多 1,500 字符、合计最多 4,500 字符。默认不请求 Tavily answer、Search
  raw content 或图片，避免把无界内容直接塞给模型。
- `401/403`、`429`、超时和 provider schema/HTTP 错误被翻译为可展示且不含密钥、
  响应正文或 Authorization header 的错误。
- 缓存不会把失败伪装成成功；Tavily 未配置时 Search/Extract 明确失败，不回退到
  画像、推荐池或旧新闻结果。
- Tavily 可以通过“先检索少量证据、再让模型分析”减少上下文 Token，但不是自动
  省 Token。调用者仍应只选择最相关证据，不应把全部研究历史重复塞进 prompt。

## 与未来本地知识库的关系

`research_runs` 和 `research_evidence` 是未来本地知识层的第一块“外部证据账本”，
但不是完整知识库，也不是用户记忆。下一阶段可以在不改变本模块外部合同的前提下：

1. 对已保存证据做本地全文检索和 embedding 重排；
2. 把事实主张、时间有效区间、来源证据和模型推断分表保存；
3. 查询时先检索本地证据，覆盖不足再调用 Tavily；
4. 把 Obsidian/Markdown 作为可读投影，而不是唯一事实数据库；
5. 只有用户明确确认的偏好或结论才进入画像，外部搜索结果永不自动“教会”Soul。
