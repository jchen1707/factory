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
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
      await page.keyboard.press('Tab');
      const focus = await page.evaluate(() => ({tag: document.activeElement.tagName,
        text: document.activeElement.textContent.trim().slice(0, 80)}));
      const entry = {name, route, viewport, status: response.status(), overflow, focus,
        violations: violations.map(v => ({id: v.id, impact: v.impact, nodes: v.nodes.map(n => n.target)})), errors};
      report.pages.push(entry);
      if (entry.status !== 200 || overflow || violations.length || errors.length || focus.tag === 'BODY') report.failures.push(entry);
      if (scenario === 'populated' || name === 'runs') {
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
