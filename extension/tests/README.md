# Extension test contract

`npm test` validates the NewsClaw browser extension as a loopback-only sidebar
client. The active contract covers Chrome, Firefox, and Safari manifests, local
endpoint validation, the popup iframe, the minimal service worker, and removal
of stale non-news delight state.

The other test files are retained as upstream OpenBiliClaw history. They describe
the former page collectors, Cookie sync, platform task runners, and full popup;
those capabilities are intentionally absent from NewsClaw and are not part of
its default test suite.
