// 33.11 real iPhone Safari/WebKit and Chromium receipt-link flows.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const {chromium,webkit}=require('playwright');
const root=path.resolve(__dirname,'..');
const fixture=spawnSync('python',['-B','-c',`
import app,json,tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as td:
 app.DATA_DIR=Path(td);app.DB_PATH=Path(td)/'test.db';app.OPTIONS_PATH=Path(td)/'options.json'
 app.RECEIPT_DIR=Path(td)/'receipts';app.publish_sensors_async=lambda:None
 app.init_db()
 with app.db() as con:
  con.execute("INSERT INTO events(created_at,type,odometer,liters,price_per_liter,station) VALUES('2026-10-08T14:00:00+02:00','fuel',26500,40,1.889,'Shell')")
 print(json.dumps(dict(html=app.APP_HTML,summary=app.summary('month'))))
`],{cwd:path.join(root,'rit_tank'),encoding:'utf8',maxBuffer:4*1024*1024});
assert.equal(fixture.status,0,fixture.stderr);
const {html,summary}=JSON.parse(fixture.stdout);
const js=fs.readFileSync(path.join(root,'rit_tank/receipt_archive.js'),'utf8');
const pdf='data:application/pdf;base64,'+Buffer.from('%PDF-1.4\n%%EOF').toString('base64');
(async()=>{
 for(const [name,engine] of Object.entries({chromium,webkit})){
  const browser=await engine.launch({headless:true});
  try{
   const context=await browser.newContext({viewport:{width:375,height:667},isMobile:true,hasTouch:true,deviceScaleFactor:2,serviceWorkers:'block'});
   const page=await context.newPage(),sent=[],errors=[],current=structuredClone(summary);
   page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',async route=>{
     const u=new URL(route.request().url()),p=u.pathname;
     if(p==='/')return route.fulfill({contentType:'text/html',body:html});
     if(p==='/receipt-archive.js')return route.fulfill({contentType:'text/javascript',body:js});
     if(p==='/api/summary')return route.fulfill({json:current});
     if(p==='/api/receipt-archive')return route.fulfill({json:{receipts:[
       {id:1,filename:'Eerste.pdf',created_at:'2026-10-08',legacy:false},
       {id:2,filename:'Tweede.pdf',created_at:'2026-10-08',legacy:false},
       {id:'legacy-1',filename:'Oude.jpg',created_at:'2026-10-08',legacy:true}]}});
     if(p==='/api/receipt-archive/prepare')return route.fulfill({json:{pdf_data_url:pdf,suggested_filename:'Nieuwe.pdf',duplicates:[]}});
     if(p==='/api/fuel/1/receipt'){
       const body=route.request().postDataJSON();sent.push(body);
       assert.equal(body.odometer,undefined);
       assert.equal(body.liters,undefined);
       assert.equal(body.price_per_liter,undefined);
       current.recent.find(e=>e.id===1).receipt_archive_id=body.archive_id||3;
       return route.fulfill({status:201,json:{ok:true,archive_id:body.archive_id||3}});
     }
     if(p.startsWith('/api/'))return route.fulfill({json:{}});
     return route.fulfill({status:204,body:''});
   });
   await page.goto('https://rit-tank.test/');
   const action=page.locator('#history .fuel-receipt-action');
   await action.waitFor({state:'visible'});
   assert.match(await action.textContent(),/Bon toevoegen/);
   await action.tap();
   const modal=page.locator('#fuelReceiptModal');
   await modal.waitFor({state:'visible'});
   const close=modal.locator('.sheethead .close');
   const metrics=await close.evaluate(el=>{
     const b=el.getBoundingClientRect(),h=el.closest('.sheethead');
     const point=document.elementFromPoint(b.left+b.width/2,b.top+b.height/2);
     return {left:b.left,right:b.right,top:b.top,bottom:b.bottom,
       wide:b.width>=56&&b.height>=56,sticky:getComputedStyle(h).position==='sticky',
       touch:point===el||el.contains(point)};
   });
   assert.ok(metrics.left>=0&&metrics.right<=375&&metrics.top>=0&&metrics.bottom<=667,name+' close safe area');
   assert.ok(metrics.wide&&metrics.sticky&&metrics.touch,name+' close touchable');
   assert.equal(await page.locator('#fuelReceiptExistingSelect option').count(),3,'legacy image cannot be selected');
   await page.locator('#fuelReceiptExistingSelect').selectOption('1');
   await page.locator('#fuelReceiptSave').tap();
   await page.waitForFunction(()=>document.querySelector('#history a[href="api/receipt-archive/1"]'));
   assert.equal(sent.length,1);
   assert.deepEqual(sent[0],{archive_id:1});
   assert.equal(await page.locator('#history a[href="api/receipt-archive/1?download=1"]').count(),1);
   await action.tap();
   await modal.waitFor({state:'visible'});
   assert.equal(await page.locator('#fuelReceiptExisting').isVisible(),true);
   await page.locator('#fuelReceiptExistingSelect').selectOption('2');
   page.once('dialog',dialog=>dialog.dismiss());
   await page.locator('#fuelReceiptSave').tap();
   assert.equal(sent.length,1,name+' cancellation must not replace');
   page.once('dialog',dialog=>dialog.accept());
   await page.locator('#fuelReceiptSave').tap();
   await page.waitForFunction(()=>document.querySelector('#history a[href="api/receipt-archive/2"]'));
   assert.equal(sent[1].archive_id,2);
   assert.equal(sent[1].confirm_replace,true);
   await action.tap();
   await page.locator('#fuelReceiptFile').setInputFiles({
      name:'Nieuwe.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4\n%%EOF')
   });
   await page.locator('#fuelReceiptNew').waitFor({state:'visible'});
   page.once('dialog',dialog=>dialog.accept());
   await page.locator('#fuelReceiptSave').tap();
   await page.waitForFunction(()=>document.querySelector('#history a[href="api/receipt-archive/3"]'));
   assert.equal(sent[2].pdf_data_url,pdf);
   assert.equal(sent[2].filename,'Nieuwe.pdf');
   assert.equal(sent[2].confirm_replace,true);
   assert.deepEqual(errors,[],name+' page errors');
   await context.close();
   console.log('33.11 '+name+' iPhone SE archive selection, confirmation, PDF upload, download and close OK');
  }finally{await browser.close()}
 }
})().catch(e=>{console.error(e);process.exitCode=1});
