# Factory UI alternatives

Open [the comparison page](index.html), then choose light, dark or compact. Each alternative has seven linked views. These drafts use representative fixtures only; no request reaches the factory, tracker, sandbox or forge. Production implementation awaits James's choice.

Light emphasizes spacious reading. Dark places failures and approval holds ahead of the queue, with a persistent operations strip. Compact uses horizontal navigation, full-width tables and expandable evidence. All offer the same capabilities, including project and run policy settings, invocation approval, lifecycle controls, runtime inventory, tool timing and estimated usage. Controls acknowledge local fixture actions; saved settings last until the view reloads or its fixture state changes. Acknowledgments do not simulate a real factory transition. PR identifiers and abbreviated hashes are illustrative.

Every page has populated, empty, blocked, approval-waiting and unavailable states. Unavailable usage is never represented as zero. Context occupancy, cumulative usage and estimated cost retain separate labels; incomplete usage produces a lower-bound estimate.

Screenshots: [light desktop](screenshots/light-1440.png), [light narrow](screenshots/light-390.png), [dark desktop](screenshots/dark-1440.png), [dark narrow](screenshots/dark-390.png), [compact desktop](screenshots/compact-1440.png), [compact narrow](screenshots/compact-390.png), [comparison](screenshots/comparison.png).

To repeat the browser checks, install Playwright and `@axe-core/playwright` in a temporary directory, install Chromium with Playwright or supply `CHROME_PATH`, and run:

```sh
NODE_PATH=/tmp/factory-ui-browser/node_modules CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" node docs/ui-alternatives/validation/browser.cjs
```

The checker opens all 21 routes at 1440px and 390px, switches all five states, checks document overflow, runs axe WCAG A/AA checks (including contrast), submits fixture settings, checks the keyboard skip link and generates screenshots. Horizontal table scrolling is intentional on narrow screens. Browser checks cover these prototypes; the Python suite covers the factory and does not establish prototype behavior.

The views retain console capabilities as follows:

| View | Representative capabilities |
| --- | --- |
| Runs | Queue, state, occupancy, cost, expandable branch/attempt/rung/elapsed/heartbeat/token/PR signals |
| Projects | Capacity, waiting work, policy, model/workflow defaults, delegation, certification and sandbox isolation |
| Run details | Suspend/resume/resume from planning/cancel/retry, evidence, gate/review status, artifacts and live tail |
| Run timeline | Host/human/sandbox transitions, runtime waterfall, child activity and tool durations |
| Run settings | Effective settings, frozen policy replacement, invocation admission and usage |
| Runtimes | Ownership, layout, certification freshness and compatibility evidence |
| Configuration | Role routing, effort, warning/ceiling estimates and project registry links |
