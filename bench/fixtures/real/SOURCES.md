# Real-page fixtures

Fetched once, saved as inert static HTML (scripts stripped). The benchmarks load these from the
local fixture server with all non-local requests aborted, so no live site is contacted in a loop.

| name | source URL | fetched (UTC) | bytes | elements at fetch | title |
|---|---|---|---:|---:|---|
| wikipedia | https://en.wikipedia.org/wiki/Web_browser | 2026-09-02T19:43:22+00:00 | 613396 | 4186 | Web browser - Wikipedia |
| mdn | https://developer.mozilla.org/en-US/docs/Web/API/HTMLElement | 2026-09-02T19:43:25+00:00 | 102051 | 1535 | HTMLElement - Web APIs | MDN |
| github | https://github.com/microsoft/playwright | 2026-09-02T19:43:28+00:00 | 410658 | 2909 | GitHub - microsoft/playwright: Playwright is a framework for Web Testing and Automation. It allows testing Chromium, Firefox and WebKit with a single API. · GitHub |
