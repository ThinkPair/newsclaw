# NewsClaw visual contract

Reference: the locally running OpenBiliClaw v0.3.207 surfaces captured on
2026-08-16. The upstream source baseline is preserved under the MIT license.

## Non-negotiable shell

- Desktop keeps the original top bar, collapsible left navigation, runtime
  card, activity card, delight hero, filters, and card grid.
- Mobile keeps the original single-column feed, compact status header,
  bottom navigation, pull-to-refresh, and message/settings overlays.
- The DSH client is a real trailing grid column, not a floating overlay. Its
  width is 380 px by default, constrained to 280–560 px while leaving at least
  400 px for the DSH conversation area.
- Spacing, typography, radii, shadows, loading skeletons, empty states, and
  online/degraded/offline presentation stay aligned with the upstream UI.

## News-domain substitutions

| Upstream concept | NewsClaw concept |
| --- | --- |
| OpenBiliClaw / 阿B | NewsClaw / 新闻爪 |
| video | news article |
| BVID/content id | article id |
| UP / creator | publisher / reporter |
| platform | media source |
| watch later | read later |
| open video | read original |
| delight | spotlight news |

The source visuals are design evidence, not sample user data. Dynamic article
text is masked during screenshot comparisons; the shell must remain within the
planned 1% pixel difference and 2 px critical-layout tolerance.
