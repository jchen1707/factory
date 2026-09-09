# Production dark console browser evidence

Measured 2026-09-08 against the production FastAPI console, with isolated integration
fixtures. This directory contains 22 screenshots and five JSON reports: 70 page checks
across seven routes, two viewports, and five fixture scenarios. All returned HTTP 200,
passed axe WCAG A/AA checks (including contrast), had no document overflow or JavaScript
page errors, and exposed keyboard focus. Browser suite: **5 passed in 26.43s**.

## Reproduce

From the `factory-dark-console` checkout, install browser-only tools outside the repository
if needed, then run:

```sh
npm install --prefix /tmp/factory-ui-browser playwright@1.63.0 @axe-core/playwright@4.13.0
FACTORY_BROWSER_MODULES=/tmp/factory-ui-browser/node_modules FACTORY_BROWSER_OUTPUT="$PWD/docs/ui-alternatives/production-validation" uv run pytest tests/integration/test_console_browser.py
```

The measured environment was Python 3.12.14, Node v22.23.2, Playwright 1.63.0,
axe-core/playwright 4.13.0, and locally installed Google Chrome 152.0.7977.83. The script
uses Playwright's `chrome` channel; install Chrome locally before running it. The opt-in
browser test is skipped by the ordinary Python suite when `FACTORY_BROWSER_MODULES` is
unset. No frontend or browser dependency was added to production.

## What was exercised

The real uvicorn application serves routes and SSE to headless Chrome on an ephemeral
loopback port. Each scenario uses the existing integration Context, a temporary database,
project Git repository, vault and factory home, FakeSandbox and FakeLinear. Runtime
listing is explicitly replaced with fixture data; it never invokes `sbx`. Browser requests
to origins other than the fixture server are aborted. Settings writes affect only the
temporary database. This does not measure live sandbox execution or a deployed service.

| Fixture | Meaning | Report |
| --- | --- | --- |
| Populated | A live implementing run with an unfinished attempt | [JSON](populated.json) |
| Empty | A cancelled run leaves the board empty; no runtimes reported | [JSON](empty.json) |
| Blocked | The run carries an explicit operator-decision reason | [JSON](blocked.json) |
| Approval | An actual waiting invocation, separate from selecting approval mode | [JSON](approval.json) |
| Unavailable | No execution evidence, no runtimes, and deliberately aborted SSE transport | [JSON](unavailable.json) |

Every scenario visits all seven routes at 1440 × 1000 and 390 × 844. Empty is a board and
runtime condition: the terminal run's detail, timeline, settings, project, and configuration
remain available. Unavailable context and incomplete estimated costs retain explicit labels; incomplete known
estimates are lower bounds.
These fixtures test layouts and presentation; they do not claim every view has every
possible production state or that runtime collection succeeds against real adapters.

The browser checks actual board/timeline SSE connection status, and confirms explicit
unavailable status after blocking their SSE requests. It records shell navigation links,
checks keyboard focus, and saves/reloads run approval mode at both widths. Ten additional
integration regressions verify blocked attention ordering and reasons, observed invocation
approval counts (approval mode alone is insufficient), and sibling run navigation with a
single current-page marker, explicit missing frozen policy, and incomplete versus complete
runtime cost labels. Automatic prepared invocations and already-approved invocations
appear as pending launches, without inferring capacity or a human approval hold; failed
runs remain visible in attention. The approval browser fixture explicitly uses approval mode.

Axe found a real narrow-screen waterfall keyboard failure on the first pass. The production
scroll container gained a keyboard focus target and accessible region name, then all 70
checks passed. Automated axe checks and an initial keyboard focus check are bounded evidence,
not a complete manual assistive-technology audit. Keyboard focus is recorded in JSON;
screenshots blur the skip link so it does not cover the brand.

## Screenshots

| View | Desktop | Narrow |
| --- | --- | --- |
| Runs | [PNG](populated-runs-desktop.png) | [PNG](populated-runs-narrow.png) |
| Projects | [PNG](populated-projects-desktop.png) | [PNG](populated-projects-narrow.png) |
| Run details | [PNG](populated-detail-desktop.png) | [PNG](populated-detail-narrow.png) |
| Run timeline | [PNG](populated-timeline-desktop.png) | [PNG](populated-timeline-narrow.png) |
| Run settings | [PNG](populated-settings-desktop.png) | [PNG](populated-settings-narrow.png) |
| Runtimes | [PNG](populated-runtimes-desktop.png) | [PNG](populated-runtimes-narrow.png) |
| Configuration | [PNG](populated-config-desktop.png) | [PNG](populated-config-narrow.png) |
| Empty board | [PNG](empty-runs-desktop.png) | [PNG](empty-runs-narrow.png) |
| Blocked board | [PNG](blocked-runs-desktop.png) | [PNG](blocked-runs-narrow.png) |
| Invocation approval | [PNG](approval-runs-desktop.png) | [PNG](approval-runs-narrow.png) |
| Unavailable updates | [PNG](unavailable-runs-desktop.png) | [PNG](unavailable-runs-narrow.png) |

The repository Definition of Done is separate from this browser measurement. Its gate
report is recorded alongside this evidence by the implementation owner.
