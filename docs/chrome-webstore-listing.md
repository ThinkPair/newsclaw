# NewsClaw Chrome Web Store 商店页文案

> 当前状态：仅供本机开发与验收，禁止提交到旧产品的商店条目。NewsClaw 尚无自己的公开主页、支持地址与隐私政策网址；这些地址建立前，不执行商店 metadata apply/publish。

## 提交边界

- 不复用其他产品的 Chrome Web Store item、主页、Issue、Release 或星标。
- 不填写虚构的 NewsClaw GitHub 地址。
- 当前扩展只连接 `127.0.0.1` / `localhost`，不注入新闻网站、不读取 Cookie、不检查活动标签页。
- 对外发布前，必须先建立 NewsClaw 自有主页、支持渠道与在线隐私政策，并重新完成商店审核材料。

## Short Description

```text
连接本机新闻爪的个性化真实新闻推荐、画像与对话侧栏
```

## Detailed Description

```text
NewsClaw / 新闻爪是一款独立的本地个性化新闻 Agent。这个浏览器扩展只是它的本机侧栏客户端：连接你在同一台电脑上运行的 NewsClaw 服务，查看真实新闻推荐、内容库、画像与对话。

它不会向新闻网站注入脚本，不读取 Cookie，不查看当前标签页，也不申请任意网站权限。扩展只允许连接 127.0.0.1 或 localhost。新闻来源由本机 NewsClaw 设置，可包括已授权新闻 API、官方 RSS/Atom 与你添加的 RSS。

你的数据库、画像、配置、收藏和行为记录保存在本机。启用 AI 分析后，只有完成当前任务所需的相关新闻内容会交给你自己配置的模型服务。扩展不使用项目方运营的云端服务，也不发送遥测。

NewsClaw 用于新闻整理、宏观理解、投资研究辅助和内容创作，不提供买卖指令，不连接券商，也不自动交易。
```

## 本机验收

```powershell
cd extension
npm ci
npm run typecheck
npm test
npm run build
npm run build:firefox
npm run build:safari
```

验收通过只代表本机包可用，不等于已获准公开发布。公开商店资料建立前，metadata workflow 只允许本地解析检查，不允许 apply 或 publish。
