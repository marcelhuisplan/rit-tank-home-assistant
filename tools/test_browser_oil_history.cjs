// PR #56: oil-maintenance user flows in actual Chromium/WebKit mobile DOM.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium, webkit} = require('playwright');
const root = path.resolve(__dirname, '..');
const fixture = spawnSync('python', ['-B', '-c', [
  'import app,json,tempfile', 'from pathlib import Path',
  'with tempfile.TemporaryDirectory() as td:',
  ' app.DATA_DIR=Path(td);app.DB_PATH=Path(td)/"test.db";app.OPTIONS_PATH=Path(td)/"options.json"',
  ' app.publish_sensors_async=lambda:None;app.init_db()',
  ' summary=app.summary("month");summary["current_odometer"]=64603',
  ' print(json.dumps(dict(html=app.APP_HTML,summary=summary)))'
].join('\n')], {cwd:path.join(root,'rit_tank'), encoding:'utf8', maxBuffer:4*1024*1024});
assert.equal(fixture.status, 0, fixture.stderr);
const {html, summary} = JSON.parse(fixture.stdout);
const oilJs = fs.readFileSync(path.join(root,'rit_tank/oil_history.js'),'utf8');

(async () => {
  for (const [engineName,engine] of Object.entries({chromium,webkit})) {
    const browser = await engine.launch({headless:true});
    try {
      for (const [device,viewport] of [['iPhone',{width:375,height:667}],['iPad',{width:768,height:1024}]]) {
        const ctx = await browser.newContext({viewport,isMobile:true,hasTouch:true,serviceWorkers:'block'});
        const page = await ctx.newPage(), errors = [];
        const entries = [];
        const saves = [];
        let nextId = 1;
        page.on('pageerror', e => errors.push(e.message));
        await page.route('**/*', route => {
          const pathname = new URL(route.request().url()).pathname;
          const method = route.request().method();
          if(pathname==='/') return route.fulfill({contentType:'text/html',body:html});
          if(pathname==='/oil-history.js') return route.fulfill({contentType:'text/javascript',body:oilJs});
          if(pathname==='/receipt-scanner.js' || pathname==='/receipt-archive.js')
            return route.fulfill({contentType:'text/javascript',body:''});
          if(pathname==='/api/summary') return route.fulfill({json:summary});
          if(pathname==='/api/maintenance/oil' && method==='GET') return route.fulfill({json:{
            entries:[...entries].sort((a,b)=>b.performed_on.localeCompare(a.performed_on)||b.id-a.id)}});
          if(pathname.startsWith('/api/maintenance/oil') && method==='POST') {
            const payload = route.request().postDataJSON();
            const id = Number(pathname.split('/').at(-1));
            if(Number(payload.odometer)>64603 && payload.confirm_odometer_anomaly!==true)
              return route.fulfill({status:409,json:{code:'OIL_ODOMETER_CONFIRMATION_REQUIRED',
                error:'De opgegeven stand ligt boven de laatst geregistreerde voertuigstand.'}});
            saves.push(payload);
            let found = entries.find(e=>e.id===id);
            if(!found) {found={id:nextId++};entries.push(found);}
            Object.assign(found,payload,{amount_ml:Number(payload.amount_ml),odometer:Number(payload.odometer)});
            return route.fulfill({status:200,json:{entry:found}});
          }
          if(pathname.startsWith('/api/maintenance/oil/') && method==='DELETE') {
            assert.equal(route.request().headers()['x-confirm-delete'],'yes');
            const id=Number(pathname.split('/').at(-1)),idx=entries.findIndex(e=>e.id===id);
            if(idx!==-1) entries.splice(idx,1);
            return route.fulfill({json:{ok:true}});
          }
          if(pathname.startsWith('/api/')) return route.fulfill({json:{}});
          return route.fulfill({status:204,body:''});
        });
        try {
          await page.goto('https://rit-tank.test/', {waitUntil:'domcontentloaded'});
          await page.locator('#oilHistoryList .empty').waitFor({state:'visible'});
          const title=page.locator('#oilHistoryCard h3');
          assert.match(await title.textContent(),/Onderhoud/);
          await page.locator('#oilHistoryCard .cardhead button').click();
          const modal=page.locator('#oilHistoryModal');
          await modal.waitFor({state:'visible'});
          const close = await page.locator('#oilHistoryModal .close').evaluate(el => {
            const rect=el.getBoundingClientRect(),top=document.elementFromPoint(rect.left+rect.width/2,rect.top+rect.height/2);
            return {width:rect.width,height:rect.height,top:rect.top,bottom:rect.bottom,touch:top===el||el.contains(top)};
          });
          assert.ok(close.width>=48 && close.height>=48 && close.top>=0 && close.bottom<=viewport.height && close.touch,
                    engineName+' '+device+' close must be usable');
          await page.locator('#oilHistoryKind').selectOption('top_up');
          await page.locator('#oilHistoryDate').fill('2026-10-29');
          await page.locator('#oilHistoryType').fill('0W-20');
          await page.locator('#oilHistoryAmount').fill('300');
          await page.locator('#oilHistoryOdometer').fill('64603');
          await page.locator('#oilHistorySave').click();
          await modal.waitFor({state:'hidden'});
          await page.locator('#oilHistoryList .event').first().waitFor({state:'visible'});
          assert.equal(entries.length,1);
          assert.equal(entries[0].amount_ml,300);
          assert.equal(entries[0].kind,'top_up');
          assert.match(await page.locator('#oilHistoryList').textContent(),/0W-20/);

          // Earlier, full change is listed second.
          await page.locator('#oilHistoryCard .cardhead button').click();
          await page.locator('#oilHistoryKind').selectOption('change');
          await page.locator('#oilHistoryDate').fill('2026-02-14');
          await page.locator('#oilHistoryType').fill('5W-30');
          await page.locator('#oilHistoryAmount').fill('4400');
          await page.locator('#oilHistoryOdometer').fill('64000');
          await page.locator('#oilHistorySave').click();
          await page.locator('#oilHistoryList .event').nth(1).waitFor({state:'visible'});
          assert.match(await page.locator('#oilHistoryList .event').first().textContent(),/bijgevuld/i);
          assert.match(await page.locator('#oilHistoryList .event').nth(1).textContent(),/ververst/i);

          // Edit just one entry.
          await page.locator('#oilHistoryList .event').first().locator('button.editbtn').click();
          assert.equal(await page.locator('#oilHistoryAmount').inputValue(),'300');
          await page.locator('#oilHistoryAmount').fill('350');
          await page.locator('#oilHistorySave').click();
          await modal.waitFor({state:'hidden'});
          assert.equal(entries.find(e=>e.id===1).amount_ml,350);
          assert.equal(entries.find(e=>e.id===2).amount_ml,4400);

          // Server rejects anomalous oil mileage until explicit opt-in.
          await page.locator('#oilHistoryList .event').first().locator('button.editbtn').click();
          await page.locator('#oilHistoryOdometer').fill('70000');
          page.once('dialog',dialog=>dialog.dismiss());
          await page.locator('#oilHistorySave').click();
          await page.locator('#oilHistoryError').waitFor({state:'visible'});
          assert.equal(entries.find(e=>e.id===1).odometer,64603);
          assert.equal(await modal.isVisible(),true);
          page.once('dialog',dialog=>dialog.accept());
          await page.locator('#oilHistorySave').click();
          await modal.waitFor({state:'hidden'});
          assert.equal(entries.find(e=>e.id===1).odometer,70000);
          assert.equal(saves.at(-1).confirm_odometer_anomaly,true);
          assert.equal(summary.current_odometer,64603,'vehicle mileage remains unchanged');

          page.once('dialog',dialog=>dialog.dismiss());
          await page.locator('#oilHistoryList .event').first().locator('button.trash').click();
          assert.equal(entries.length,2,'dismissed delete does nothing');
          page.once('dialog',dialog=>dialog.accept());
          await page.locator('#oilHistoryList .event').first().locator('button.trash').click();
          await page.waitForFunction(()=>document.querySelectorAll('#oilHistoryList .event').length===1);
          assert.equal(entries[0].id,2,'deleting first keeps second');
          assert.deepEqual(errors,[],engineName+' '+device+' page errors');
          console.log('Oil maintenance '+engineName+' '+device+' view/add/edit/warn/delete PASS');
        } finally {await ctx.close();}
      }
    } finally {await browser.close();}
  }
})().catch(error=>{console.error(error);process.exitCode=1});
