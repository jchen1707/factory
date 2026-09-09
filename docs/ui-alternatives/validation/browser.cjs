const {chromium}=require('playwright');
const AxeBuilder=require('@axe-core/playwright').default;
const fs=require('fs');
const path=require('path');
const {pathToFileURL}=require('url');
const root=path.resolve(__dirname,'..');
(async()=>{
const browser=await chromium.launch({...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{}),headless:true});
const context=await browser.newContext();const page=await context.newPage(); const failures=[]; const errors=[]; page.on('pageerror',e=>errors.push(e.message));
const base=pathToFileURL(root+'/').href;
fs.mkdirSync(path.join(root,'screenshots'),{recursive:true});
let states=0;
for(const width of [1440,390]){await page.setViewportSize({width,height:1000});
for(const theme of ['light','dark','compact']) for(const view of ['runs','projects','detail','timeline','settings','runtimes','config']){
await page.goto(base+theme+'/'+view+'.html');await page.locator('h1').waitFor();
for(const state of ['populated','empty','blocked','approval','unavailable']){await page.locator('#state').selectOption(state);states++;
if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))failures.push(`${theme}/${view}/${state}/${width}: overflow`);
if(state==='empty'||state==='unavailable'){await page.locator('#load').click();if(await page.locator('#state').inputValue()!=='populated')failures.push('fixture retry failed');}
if(state==='approval'){await page.locator('[data-action]').first().click();if(!await page.locator('#status').innerText())failures.push('approval not observed');}
if(state==='populated'){const results=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();for(const v of results.violations)failures.push(`${theme}/${view}/${width}: ${v.id} ${v.nodes.map(n=>n.target).join(';')}`);}
}
await page.locator('#state').selectOption('populated');
if(['projects','settings','config'].includes(view)){await page.locator('form').last().locator('button').first().click();if(!await page.locator('#status').innerText())failures.push('save not observed');}
if(view==='detail'){for(const button of await page.locator('[data-action]').all()){await button.click();if(!await page.locator('#status').innerText())failures.push('control not observed');}const summary=page.locator('summary').first();await summary.focus();await page.keyboard.press('Enter');if(!await summary.evaluate(el=>el.parentElement.open))failures.push('keyboard expansion failed');}
if(view==='runs'){await page.screenshot({path:base.replace('file://','')+'screenshots/'+theme+'-'+width+'.png',fullPage:true});await page.goto(base+theme+'/'+view+'.html');await page.keyboard.press('Tab');if(await page.locator(':focus').innerText()!=='Skip to content')failures.push('skip link keyboard');await page.keyboard.press('Enter');}
}}
await page.setViewportSize({width:1440,height:1000});await page.goto(base+'index.html');const comparisonAudit=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa']).analyze();for(const v of comparisonAudit.violations)failures.push('comparison: '+v.id);await page.screenshot({path:base.replace('file://','')+'screenshots/comparison.png',fullPage:true});
console.log(JSON.stringify({routes:21,states,viewports:[1440,390],errors,failures},null,2));await browser.close();if(failures.length||errors.length)process.exitCode=1;
})();
