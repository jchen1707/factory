/** Real browser checks; all writes target the temporary pytest fixture server. */
import {createRequire} from 'node:module';
import fs from 'node:fs/promises';
import path from 'node:path';
const require = createRequire(path.join(process.env.FACTORY_BROWSER_MODULES, '../package.json'));
const {chromium} = require('playwright');
const {default: AxeBuilder} = require('@axe-core/playwright');
const [base, scenario, output] = process.argv.slice(2);
const browser = await chromium.launch({channel: 'chrome', headless: true});
const report = {scenario, pages: [], failures: [], interactions: []};
const routes = [['runs', '/'], ['projects', '/projects'], ['detail', '/runs/BAC-4'],
  ['timeline', '/runs/BAC-4/timeline'], ['settings', '/settings/runs/BAC-4'],
  ['runtimes', '/runtimes'], ['config', '/config']];
try {
  for (const [viewport, width, height] of [['desktop', 1440, 1000], ['narrow', 390, 844]]) {
    const context = await browser.newContext({viewport: {width, height}, reducedMotion: 'reduce'});
    await context.route('**/*', route => {
      if (scenario === 'unavailable' && new URL(route.request().url()).pathname.startsWith('/sse/')) return route.abort();
      if (new URL(route.request().url()).origin === base) return route.continue();
      return route.abort();
    });
    const page = await context.newPage();
    for (const [name, route] of routes) {
      const errors = [];
      const listener = error => errors.push(error.message);
      page.on('pageerror', listener);
      const response = await page.goto(base + route, {waitUntil: 'domcontentloaded'});
      await page.waitForTimeout(100);
      if (name === 'runs' || name === 'timeline') {
        const expected = scenario === 'unavailable' ? 'Live updates unavailable' : 'Connected to live updates';
        await page.locator('[role="status"]').filter({hasText: expected}).waitFor();
        report.interactions.push({viewport, name, stream: expected});
      }
      const violations = (await new AxeBuilder({page}).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
      if (name === 'runs' && scenario !== 'empty') {
        const primary = await page.locator('table').first().evaluate(table => ({
          headings: [...table.querySelectorAll('thead th')].map(node => node.textContent.trim()),
          clipped: table.scrollWidth > table.parentElement.clientWidth + 1,
          rows: table.querySelectorAll('tbody tr').length,
        }));
        report.interactions.push({viewport, primary});
        if (primary.headings.length !== 5 || (viewport === 'desktop' && primary.clipped)) report.failures.push({viewport, primary});
        if (scenario === 'populated' && primary.rows < 10) report.failures.push({viewport, insufficientRows: primary.rows});
      }
      if (name === 'runtimes' && viewport === 'desktop') {
        const clipped = await page.locator('table').first().evaluate(table => table.scrollWidth > table.parentElement.clientWidth + 1).catch(() => false);
        if (clipped) report.failures.push({viewport, name, primaryInventoryClipped: true});
      }
      if (['projects', 'runtimes'].includes(name) && viewport === 'narrow' && await page.locator('table').count()) {
        const unreadableCells = await page.locator('table').first().locator('tbody td').evaluateAll(nodes => nodes.filter(node =>
          node.getBoundingClientRect().width < 160 || !node.dataset.label || getComputedStyle(node, '::before').content === 'none'
        ).map(node => ({width: node.getBoundingClientRect().width, label: node.dataset.label || null, text: node.textContent.slice(0, 60)})));
        report.interactions.push({viewport, name, unreadableCells});
        if (unreadableCells.length) report.failures.push({viewport, name, unreadableCells});
      }
      const sections = await page.evaluate(() => {
        const before = (a, b) => {
          const first = document.getElementById(a), second = document.getElementById(b);
          return Boolean(first && second && (first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING));
        };
        return {projects: before('project-inventory', 'project-defaults'),
          detail: before('current-attempt', 'run-history'),
          runtimes: Boolean(document.getElementById('runtime-compatibility')),
          config: Boolean(document.getElementById('configuration-sources'))};
      });
      if (['projects', 'detail', 'runtimes', 'config'].includes(name) && !sections[name]) report.failures.push({viewport, name, sections});
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
      await page.keyboard.press('Tab');
      const focus = await page.evaluate(() => ({tag: document.activeElement.tagName,
        text: document.activeElement.textContent.trim().slice(0, 80)}));
      const entry = {name, route, viewport, status: response.status(), overflow, focus,
        violations: violations.map(v => ({id: v.id, impact: v.impact, nodes: v.nodes.map(n => n.target)})), errors};
      report.pages.push(entry);
      if (entry.status !== 200 || overflow || violations.length || errors.length || focus.tag === 'BODY') report.failures.push(entry);
      {
        await page.evaluate(() => document.activeElement.blur());
        await page.screenshot({path: path.join(output, `${scenario}-${name}-${viewport}.png`), fullPage: true});
      }
      page.off('pageerror', listener);
    }
    // Navigate the actual shell, rather than assuming route reachability implies working links.
    await page.goto(base, {waitUntil: 'domcontentloaded'});
    const links = await page.locator('nav a').evaluateAll(nodes => nodes.map(n => n.getAttribute('href')));
    report.interactions.push({viewport, navigation: links});
    for (const target of ['/projects', '/runtimes', '/config']) {
      if (!links.includes(target)) report.failures.push({viewport, missingNavigation: target});
    }
    if (scenario === 'populated') {
      await page.goto(base + '/projects', {waitUntil: 'domcontentloaded'});
      const projectLink = page.locator('#project-inventory a').first();
      const projectTarget = await projectLink.getAttribute('href');
      await projectLink.focus();
      await page.keyboard.press('Enter');
      await page.waitForFunction(target => document.querySelector(target)?.open, projectTarget);
      report.interactions.push({viewport, projectAnchor: projectTarget, opened: true});
      await page.goto(base, {waitUntil: 'domcontentloaded'});
      const sequence = viewport === 'desktop' ? 0 : 10;
      const mutate = n => page.evaluate(n => fetch(`/__fixture/update/${n}`, {method: 'POST'}), sequence + n);
      const row = page.locator('details[data-key]').filter({has: page.locator('a[href="/settings/runs/BAC-4"]')}).first();
      await row.locator('summary').focus();
      await page.keyboard.press('Enter');
      const settingsLink = row.locator('a[href="/settings/runs/BAC-4"]');
      await settingsLink.focus();
      await mutate(1);
      await page.getByText(`fix/fixture-stream-${sequence + 1}`, {exact: true}).waitFor();
      const continuity = await row.evaluate(node => ({open: node.open, focused: node.contains(document.activeElement) && document.activeElement.getAttribute('href') === '/settings/runs/BAC-4'}));
      report.interactions.push({viewport, continuity});
      if (!continuity.open || !continuity.focused) report.failures.push({viewport, continuity});
      await row.locator('summary').click();
      await mutate(2);
      await page.waitForFunction(n => document.body.textContent.includes(`fix/fixture-stream-${n}`), sequence + 2);
      if (await row.getAttribute('open') !== null) report.failures.push({viewport, collapsedStateLost: true});
      const attention = page.locator('.attention a').first();
      await attention.focus();
      const attentionHref = await attention.getAttribute('href');
      await mutate(5);
      await page.waitForFunction(n => document.body.textContent.includes(`fix/fixture-stream-${n}`), sequence + 5);
      const attentionFocused = await page.evaluate(href => document.activeElement.getAttribute('href') === href, attentionHref);
      if (!attentionFocused) report.failures.push({viewport, attentionFocused});
      await page.goto(base + '/runs/BAC-4/timeline', {waitUntil: 'domcontentloaded'});
      const waterfall = page.locator('.waterfall');
      await waterfall.focus();
      const scrollBefore = await waterfall.evaluate(node => {node.scrollLeft = 120; return node.scrollLeft;});
      const previousWaterfall = await waterfall.elementHandle();
      const nextTimelineEvent = page.waitForResponse(response => response.url().endsWith('/__fixture/update/' + (sequence + 6)));
      await mutate(6);
      await nextTimelineEvent;
      await page.waitForFunction(node => !node.isConnected, previousWaterfall);
      const timelineContinuity = await waterfall.evaluate(node => ({focused: document.activeElement === node, scrollLeft: node.scrollLeft}));
      if (!timelineContinuity.focused || timelineContinuity.scrollLeft !== scrollBefore) report.failures.push({viewport, scrollBefore, timelineContinuity});
      report.interactions.push({viewport, attentionFocused, scrollBefore, timelineContinuity});
      await page.goto(base + '/runs/BAC-4', {waitUntil: 'domcontentloaded'});
      const tail = page.locator('#tail');
      await tail.filter({hasText: 'fixture retained event'}).waitFor();
      const tailBounds = await tail.evaluate(node => ({height: node.clientHeight, scrollHeight: node.scrollHeight}));
      if (tailBounds.height > 450 || tailBounds.scrollHeight <= tailBounds.height) report.failures.push({viewport, tailBounds});
      await tail.evaluate(node => {node.scrollTop = 40;});
      await mutate(3);
      await tail.filter({hasText: `fixture appended event ${sequence + 3}`}).waitFor();
      const retainedScroll = await tail.evaluate(node => node.scrollTop);
      if (Math.abs(retainedScroll - 40) > 1) report.failures.push({viewport, retainedScroll});
      await page.locator('#tail-follow').check();
      await mutate(4);
      await tail.filter({hasText: `fixture appended event ${sequence + 4}`}).waitFor();
      const following = await tail.evaluate(node => Math.abs(node.scrollHeight - node.clientHeight - node.scrollTop) <= 1);
      if (!following) report.failures.push({viewport, following});
      report.interactions.push({viewport, tailBounds, retainedScroll, following});
      await page.goto(base + '/settings/runs/BAC-4', {waitUntil: 'domcontentloaded'});
      const mode = page.locator('select[name="mode"]');
      await mode.selectOption('approval');
      const form = mode.locator('xpath=ancestor::form');
      await Promise.all([page.waitForResponse(response => response.request().method() === 'POST'), form.locator('button[type="submit"],button:not([type])').first().click()]);
      await page.goto(base + '/settings/runs/BAC-4', {waitUntil: 'domcontentloaded'});
      const saved = await page.locator('select[name="mode"]').inputValue();
      report.interactions.push({viewport, savedMode: saved});
      if (saved !== 'approval') report.failures.push({viewport, savedMode: saved});
    }
    if (scenario === 'populated') {
      await page.setViewportSize({width: 320, height: 844});
      for (const stressRoute of ['/projects', '/settings/runs/BAC-4', '/']) {
        await page.goto(base + stressRoute, {waitUntil: 'domcontentloaded'});
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
        report.interactions.push({stressRoute, width: 320, overflow});
        if (overflow) report.failures.push({stressRoute, width: 320, overflow});
      }
    }
    await context.close();
  }
} catch (error) {
  report.failures.push({exception: error.stack});
} finally {
  await browser.close();
  await fs.writeFile(path.join(output, `${scenario}.json`), JSON.stringify(report, null, 2) + '\n');
}
console.log(JSON.stringify({scenario, pages: report.pages.length, failures: report.failures}));
if (report.failures.length) process.exitCode = 1;
