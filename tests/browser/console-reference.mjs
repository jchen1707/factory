/** Two distinct tracks: untouched illustrative design, and explicit fixture composition. */
import {createRequire} from 'node:module';
import path from 'node:path';
import fs from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {renderFixtureReference} from './console-reference-fixture.mjs';
const require = createRequire(path.join(process.env.FACTORY_BROWSER_MODULES, '../package.json'));
const {chromium} = require('playwright');
const [root, output, fixtureDirectory = output] = process.argv.slice(2);
await fs.mkdir(output, {recursive: true});
const browser = await chromium.launch({channel: 'chrome', headless: true});
const report = {browser: browser.version(), tracks: {
  original: 'Unmodified original prototype DOM, illustrative data, not a matching-data baseline',
  matched: 'Original composition reconstructed with manifest evidence; production-only navigation/actions remain intentional differences. Human review required.',
}, pages: []};
try {
  for (const [viewport, width, height] of [['desktop', 1440, 1000], ['narrow', 390, 844]]) {
    const context = await browser.newContext({viewport: {width, height}, reducedMotion: 'reduce'});
    await context.route('**/*', route => route.request().url().startsWith('file:') ? route.continue() : route.abort());
    const page = await context.newPage();
    for (const name of ['runs', 'projects', 'detail', 'timeline', 'settings', 'runtimes', 'config']) {
      await page.goto(pathToFileURL(path.join(root, 'docs/ui-alternatives/dark', `${name}.html`)).href);
      await page.screenshot({path: path.join(output, `original-${name}-${viewport}.png`), fullPage: true});
      await page.screenshot({path: path.join(output, `original-${name}-${viewport}-first.png`)});
      report.pages.push({track: 'original', name, viewport, width, height});
      for (const scenario of ['populated', 'stress']) {
        const manifest = `${scenario}-${viewport}-fixture.json`;
        const fixture = JSON.parse(await fs.readFile(path.join(fixtureDirectory, manifest), 'utf8'));
        await page.evaluate(renderFixtureReference, {name, fixture});
        await page.screenshot({path: path.join(output, `matched-${scenario}-${name}-${viewport}.png`), fullPage: true});
        await page.screenshot({path: path.join(output, `matched-${scenario}-${name}-${viewport}-first.png`)});
        report.pages.push({track: 'matched', scenario, manifest, name, viewport, width, height});
      }
    }
    await context.close();
  }
} finally {
  await browser.close();
  await fs.writeFile(path.join(output, 'reference.json'), JSON.stringify(report, null, 2) + '\n');
}
