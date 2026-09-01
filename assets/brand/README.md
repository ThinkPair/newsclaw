# NewsClaw 品牌图形来源与派生说明

本目录保存 NewsClaw 当前使用的可爱蟹钳新闻助手图形，以及由它确定性生成的各端图标。

## 权威文件

- `newsclaw-icon-source.png`：2026-09-01 在 Codex 内使用 OpenAI 图像生成能力产出的原始高清 PNG，1254 × 1254、RGBA。该文件只作来源留档，不直接用于运行时。
- `newsclaw-icon.png`：统一的 1024 × 1024 透明标准图标。
- `newsclaw-logo-application.png`：报名表、项目资料等可直接上传的 1024 × 1024 不透明方图。
- `brand-assets.json`：源图、派生脚本、品牌修订号和每个输出文件的 SHA-256 清单。
- `scripts/build_brand_icons.py`：网页、PWA、扩展、Windows、macOS 与文档图标的唯一派生入口。

重新生成或核验：

```bash
python scripts/build_brand_icons.py --write
python scripts/build_brand_icons.py --check
```

## 生成提示词留档

> Use case: logo-brand. Create a cute, approachable original logo for NewsClaw. The mascot is a tiny rounded news-card helper being gently hugged by two simple crab-like pincers—the “claw” idea—while the card shows three small evidence dots connected by one short line, suggesting remembered changes. Give the helper two tiny friendly eyes and a subtle smile. It should feel like a personal information companion people enjoy opening every day, not a formal enterprise or finance logo. Polished flat 2D kawaii app icon; simple rounded vector-like shapes; centered compact mascot inside a soft rounded-square app-icon base; generous transparent outer margin; coral pink `#F06F91`, warm peach `#FFB28A`, creamy ivory `#FFF7EC`, near-black `#27242A`; no text, watermark, cat, animal paw, paw pad, pink circular ring, OpenBiliClaw silhouette, brain, globe, magnifying glass, stock chart, shield or hexagon.

原始生成文件 SHA-256：

```text
a639f641a808221a3da8547c4bcfe814bc98707dc03a4f5e916c1fbd970126db
```

## 权利与使用边界

该图形是为 NewsClaw 单独生成并由项目方确认选用的 AI 辅助品牌资产，不是从 OpenBiliClaw 图标修改而来。仓库保留原图、提示词、生成日期、哈希和机械派生记录，便于后续申报与权属核验。

本说明不等同于商标注册、著作权登记或对全球近似图形的排他性保证。正式商业发布或商标申请前，仍应进行文字与图形近似检索，并根据申报主体补充资产授权或归属文件。
