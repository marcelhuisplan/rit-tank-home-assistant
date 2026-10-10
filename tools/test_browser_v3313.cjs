// 33.13: iPhone/iPad Chromium and WebKit period-filter consumption rendering.
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawnSync}=require('node:child_process');
const {chromium,webkit}=require('playwright');
const root=path.resolve(__dirname,'..');
const python=[
 'import app,json,tempfile',
 'from pathlib import Path',
 'with tempfile.TemporaryDirectory() as td:',
 ' app.DATA_DIR=Path(td);app.DB_PATH=Path(td)/"test.db";app.OPTIONS_PATH=Path(td)/"options.json"',
 ' app.RECEIPT_DIR=Path(td)/"receipts";app.publish_sensors_async=lambda:None',
 ' app.init_db()',
 ' print(json.dumps(dict(html=app.APP_HTML,summary=app.summary("month"))))'
].join('\n');
const fixture=spawnSync('python',['-B','-c',python],{
 cwd:path.join(root,'rit_tank'),encoding:'utf8',maxBuffer:4*1024*1024
});
assert.equal(fixture.status,0,fixture.stderr);
const {html,summary}=JSON.parse(fixture.stdout);
const receiptJs=fs.readFileSync(path.join(root,'rit_tank/receipt_archive.js'),'utf8');
const values={
 month:{cycles:1,km:1000,liters:59.4,l100:5.94,cost100:11.2},
 year:{cycles:2,km:1000,liters:31.55,l100:3.16,cost100:6.2},
 day:null,week:null
};
(async()=>{
 for(const [engineName,engine] of Object.entries({chromium,webkit})){
  const browser=await engine.launch({headless:true});
  try{
   for(const device of [{name:'iPhone',width:375,height:812},{name:'iPad',width:820,height:1180}]){
    const context=await browser.newContext({
     viewport:{width:device.width,height:device.height},
     isMobile:true,hasTouch:true,deviceScaleFactor:2,serviceWorkers:'block'
    });
    try{
     const page=await context.newPage(),errors=[];
     page.on('pageerror',error=>errors.push(error.message));
     await page.route('**/*',route=>{
      const url=new URL(route.request().url());
      if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
      if(url.pathname==='/receipt-archive.js')return route.fulfill({contentType:'text/javascript',body:receiptJs});
      if(url.pathname==='/api/summary'){
       const period=url.searchParams.get('period')||'month';
       return route.fulfill({json:{...summary,period:{...summary.period,label:period},
        period_full_tank:values[period]||null}});
      }
      if(url.pathname.startsWith('/api/'))return route.fulfill({json:{}});
      return route.fulfill({status:200,body:''});
     });
     await page.goto('http://127.0.0.1:8099/',{waitUntil:'domcontentloaded'});
     await page.waitForFunction(()=>document.querySelector('#fullAvgKmPerLiter')?.textContent==='1 : 16,8');
     assert.equal(await page.locator('#fullAvgVal').textContent(),'5,94 L/100 km');
     assert.equal(await page.locator('#kpiL100').textContent(),'5,94');
     assert.equal(await page.locator('#kpiKmPerLiter').textContent(),'1 : 16,8');
     const fits=await page.evaluate(()=>{
      const el=document.querySelector('#fullAvgKmPerLiter'),tile=el.closest('.fullavg');
      const kpi=document.querySelector('#kpiKmPerLiter');
      return el.getBoundingClientRect().right<=tile.getBoundingClientRect().right+1 &&
       tile.getBoundingClientRect().right<=window.innerWidth+1 &&
       kpi.getBoundingClientRect().right<=window.innerWidth+1;
     });
     assert.equal(fits,true,engineName+' '+device.name+': consumption must fit the viewport');
     await page.locator('.tab[data-period="year"]').tap();
     await page.waitForFunction(()=>document.querySelector('#fullAvgKmPerLiter')?.textContent==='1 : 31,7');
     assert.equal(await page.locator('#fullAvgVal').textContent(),'3,16 L/100 km');
     assert.equal(await page.locator('#kpiKmPerLiter').textContent(),'1 : 31,7');
     await page.locator('.tab[data-period="day"]').tap();
     await page.waitForFunction(()=>document.querySelector('#fullAvgKmPerLiter')?.textContent==='—');
     assert.equal(await page.locator('#fullAvgVal').textContent(),'—');
     assert.equal(await page.locator('#kpiL100').textContent(),'—');
     assert.equal(await page.locator('#kpiKmPerLiter').textContent(),'—');
     assert.deepEqual(errors,[],engineName+' '+device.name+': no JavaScript errors');
     console.log('33.13 '+engineName+' '+device.name+' average, KPI, filter and missing-cycle OK');
    }finally{await context.close();}
   }
  }finally{await browser.close();}
 }
})().catch(error=>{console.error(error);process.exitCode=1});
