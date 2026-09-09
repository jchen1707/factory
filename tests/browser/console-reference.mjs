/** Read-only composition reference: adapt DOM fixture values, never prototype sources. */
import {createRequire} from 'node:module';
import path from 'node:path';
import fs from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
const require = createRequire(path.join(process.env.FACTORY_BROWSER_MODULES, '../package.json'));
const {chromium} = require('playwright');
const [root, output] = process.argv.slice(2);
const browser = await chromium.launch({channel: 'chrome', headless: true});
const report = {kind: 'hand-authored matched primary fields; remaining reference content is illustrative, not a pixel baseline', pages: []};
try {
  for (const [viewport, width, height] of [['desktop', 1440, 1000], ['narrow', 390, 844]]) {
    const context = await browser.newContext({viewport: {width, height}, reducedMotion: 'reduce'});
    await context.route('**/*', route => route.request().url().startsWith('file:') ? route.continue() : route.abort());
    const page = await context.newPage();
    for (const name of ['runs', 'projects', 'detail', 'timeline', 'settings', 'runtimes', 'config']) {
      await page.goto(pathToFileURL(path.join(root, 'docs/ui-alternatives/dark', `${name}.html`)).href);
      await page.evaluate(name => {
        const projects = ['python-harness', 'fixture-project-1-with-a-long-registry-identity', 'fixture-project-2-with-a-long-registry-identity'];
        const rows = [['BAC-4', 'python-harness', 'implementing', '46% · fresh', '≥ $0.47'], ...Array.from({length: 9}, (_, i) => [`FIXTURE-${i + 10}`, projects[i % 3], ['implementing', 'blocked', 'awaiting_human'][i % 3], 'Unavailable', 'Unknown'])];
        const replaceRows = (table, values) => {
          if (!table) return;
          const body = table.querySelector('tbody');
          body.replaceChildren(...values.map(values => {
            const row = document.createElement('tr');
            values.forEach(value => {const cell = document.createElement('td'); cell.textContent = value; row.append(cell);});
            return row;
          }));
        };
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        while (walker.nextNode()) walker.currentNode.textContent = walker.currentNode.textContent
          .replaceAll('BAC-42', 'BAC-4').replaceAll('182,430', '25,600').replaceAll('$4.82', '$0.47')
          .replaceAll('Preserve source on interrupted recovery', 'Application skeleton: Settings, structured logging, app factory')
          .replaceAll('Across 4 invocations', '56 invocations · incomplete observations')
          .replaceAll('92,000 / 200,000', '46,000 / 100,000').replaceAll('Attempt 2', 'Attempt 1');
        const stats = document.querySelectorAll('.stat');
        if (stats.length) {
          stats[0].innerHTML = name === 'detail' ? 'State / attempt<strong>implementing</strong><small>Attempt 1</small>' : 'Open runs<strong>10</strong><small>Displayed fixture runs</small>';
          stats[1].innerHTML = name === 'runs'
            ? 'Context availability<strong>1</strong><small>Live invocations with fresh observations</small>'
            : 'Context occupancy<strong>46%</strong><small>Current attempt · fresh fixture observation</small>';
        }
        if (name === 'runs') replaceRows(document.querySelector('table'), rows);
        if (name === 'projects') replaceRows(document.querySelector('table'), projects.map((p, i) => [p, 'Repository default', i ? '0 / 1' : '1 / 1', i ? '0 / inherited' : '1 / inherited', i ? 'None' : '12 certifications']));
        if (name === 'runtimes') replaceRows(document.querySelector('table'), [['factory-build-python-harness', 'Factory · running', 'Unavailable', 'Unverified', 'BAC-4']]);
        if (name === 'settings') replaceRows(document.querySelector('table'), [['Builder attempt 1', 'Active · identity unverified', '24,000 input / 1,600 output', '≥ $0.47'], ['54 retained children', 'Completed', 'Final reports unavailable', 'Unknown']]);
        const strip = document.querySelector('.ops-strip');
        if (strip) strip.innerHTML = '<strong>FIXTURE OPERATIONS</strong><span>10 open</span><span>3 blocked</span><span>3 await human merge</span>';
        const toolbar = document.querySelector('.toolbar');
        if (toolbar) toolbar.firstElementChild.textContent = 'MATCHED FIXTURE COMPOSITION REFERENCE';
        if (name === 'timeline') {
          const waterfall = document.querySelector('.waterfall');
          waterfall.innerHTML = '<p>Timing unavailable for synthetic retained invocations.</p><p>1 active builder · 54 retained child invocations</p>';
          replaceRows(document.querySelector('table'), []);
          replaceRows(document.querySelectorAll('table')[1], [['Builder', 'Active'], ['54 retained children', 'Completed'], ['Reviewer', 'Not admitted']]);
          const timeline = document.querySelector('.timeline');
          if (timeline) timeline.innerHTML = '<li><strong>Implementation active</strong><p>Fixture BAC-4 · attempt 1 · current context 46%</p></li>';
        }
        // Avoid presenting the prototype's invented certifications/review as matched evidence.
        document.querySelectorAll('details p').forEach(node => {node.textContent = 'Synthetic retained evidence. 54 child records and 12 pending certifications; no review disposition or certified identity is asserted.';});
        document.querySelectorAll('input').forEach(node => {
          if (node.value === 'diagnosis:1') node.value = 'No waiting invocation';
        });
      }, name);
      await page.screenshot({path: path.join(output, `reference-${name}-${viewport}.png`), fullPage: true});
      report.pages.push({name, viewport, width, height});
    }
    await context.close();
  }
} finally {
  await browser.close();
  await fs.writeFile(path.join(output, 'reference.json'), JSON.stringify(report, null, 2) + '\n');
}
