// 33.14 regression: mobile keyboard shrinks/pans visualViewport without resizing layout viewport.
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawnSync}=require('node:child_process'),{chromium,webkit}=require('playwright');
const root=path.resolve(__dirname,'..');
const fixture=spawnSync('python',['-B','-c',[
 'import app,json,tempfile',
 'from pathlib import Path',
 'with tempfile.TemporaryDirectory() as td:',
 ' app.DATA_DIR=Path(td);app.DB_PATH=Path(td)/"test.db";app.OPTIONS_PATH=Path(td)/"options.json"',
 ' app.publish_sensors_async=lambda:None;app.google_reverse_geocode=lambda *args:{}',
 ' app.init_db()',
 ' start=app.summary("month");start["current_odometer"]=25230',
 ' app.start_business_trip(dict(odometer=25230,created_at="2026-10-07T08:00:00+02:00",latitude=52.315,longitude=6.528,manual_label="Thuis",physical_confirmed=True))',
 ' finish=app.summary("month")',
 ' print(json.dumps(dict(html=app.APP_HTML,start=start,finish=finish)))'
].join('\n')],{cwd:path.join(root,'rit_tank'),encoding:'utf8',maxBuffer:4*1024*1024});
assert.equal(fixture.status,0,fixture.stderr);
const {html,start,finish}=JSON.parse(fixture.stdout),receiptJs=fs.readFileSync(path.join(root,'rit_tank/receipt_archive.js'),'utf8');
(async()=>{
 for(const [engineName,engine] of Object.entries({chromium,webkit})){
  const browser=await engine.launch({headless:true});
  try{
   for(const pwa of [false,true])for(const mode of ['start','finish']){
    const context=await browser.newContext({viewport:{width:375,height:667},isMobile:true,hasTouch:true,deviceScaleFactor:2,serviceWorkers:'block'});
    try{
     const page=await context.newPage(),errors=[],data=mode==='start'?start:finish;
     page.on('pageerror',e=>errors.push(e.message));
     await page.addInitScript(({pwa})=>{
      // Mimic iOS Safari and standalone PWA: only visualViewport changes.
      const box={height:window.innerHeight,top:0},view=new EventTarget();
      Object.defineProperty(view,'height',{get:()=>box.height});
      Object.defineProperty(view,'offsetTop',{get:()=>box.top});
      Object.defineProperty(window,'visualViewport',{configurable:true,value:view});
      if(pwa)Object.defineProperty(navigator,'standalone',{configurable:true,value:true});
      window.__keyboard=(height,top)=>{
       box.height=height;box.top=top;
       view.dispatchEvent(new Event('resize'));view.dispatchEvent(new Event('scroll'));
      };
     },{pwa});
     await page.route('**/*',route=>{
      const url=new URL(route.request().url());
      if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
      if(url.pathname==='/receipt-archive.js')return route.fulfill({contentType:'text/javascript',body:receiptJs});
      if(url.pathname==='/api/summary')return route.fulfill({json:data});
      if(url.pathname==='/api/business/odometer-suggestion')return route.fulfill({json:data.business?.odometer_suggestion||{}});
      if(url.pathname.startsWith('/api/'))return route.fulfill({json:{}});
      return route.fulfill({status:200,body:''});
     });
     await page.goto('http://127.0.0.1:8099/',{waitUntil:'domcontentloaded'});
     await page.waitForFunction(()=>typeof DATA!=='undefined');
     await page.evaluate(m=>openTripPoint(m),mode);
     await page.locator('#tripModal').waitFor({state:'visible'});
     const input=page.locator('#physicalTripValue'),button=page.locator('#physicalConfirmButton');
     assert.match(await button.textContent(),mode==='start'?/Startstand bevestigen/:/Eindstand bevestigen/);
     await input.focus();await input.fill('');
     await input.pressSequentially('25234',{delay:12});
     assert.equal(await input.inputValue(),'25234');
     for(const [height,top] of [[320,0],[280,28]]){
      await page.evaluate(({height,top})=>{
       document.querySelector('#tripModal .sheet').scrollTop=0;
       window.__keyboard(height,top);
      },{height,top});
      await page.waitForFunction(()=>{
       const v=window.visualViewport,modal=document.querySelector('#tripModal'),sheet=modal.querySelector('.sheet'),
        input=document.querySelector('#physicalTripValue'),header=sheet.querySelector('.sheethead'),
        m=modal.getBoundingClientRect(),s=sheet.getBoundingClientRect(),r=input.getBoundingClientRect(),
        visibleTop=Math.max(v.offsetTop,s.top,header.getBoundingClientRect().bottom)+8,
        visibleBottom=Math.min(v.offsetTop+v.height,s.bottom)-8,
        hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
       return m.top>=v.offsetTop-1&&m.bottom<=v.offsetTop+v.height+1&&
        s.top>=v.offsetTop-1&&s.bottom<=v.offsetTop+v.height+1&&
        r.top>=visibleTop&&r.bottom<=visibleBottom&&(hit===input||input.contains(hit));
      },null,{timeout:5000});
      if(height===320){await input.press('Backspace');await input.pressSequentially('5',{delay:12})}
      assert.equal(await input.inputValue(),'25235','keyboard must not replace typed digits');
      // The button remains reachable by scrolling inside the modal.
      await button.evaluate(el=>{
       const v=window.visualViewport,screenBottom=v.offsetTop+v.height-10,box=el.getBoundingClientRect();
       if(box.bottom>screenBottom)el.closest('.sheet').scrollTop+=box.bottom-screenBottom;
      });
      const reachable=await button.evaluate(el=>{
       const box=el.getBoundingClientRect(),v=window.visualViewport;
       return box.top>=v.offsetTop-1&&box.bottom<=v.offsetTop+v.height+1;
      });
      assert.equal(reachable,true,'confirmation remains reachable above keyboard');
      assert.equal(await input.inputValue(),'25235');
      // A later keyboard pan must restore focus visibility even after manual button scroll.
      await page.evaluate(()=>window.__keyboard(window.visualViewport.height,window.visualViewport.offsetTop));
      await page.waitForFunction(()=>{
       const e=document.querySelector('#physicalTripValue'),r=e.getBoundingClientRect(),
        v=window.visualViewport,s=e.closest('.sheet').getBoundingClientRect();
       return r.top>=Math.max(v.offsetTop,s.top)-1&&r.bottom<=Math.min(v.offsetTop+v.height,s.bottom)+1;
      },null,{timeout:5000});
     }
     await page.evaluate(()=>window.__keyboard(667,0));
     assert.equal(await input.inputValue(),'25235','keyboard close must preserve input');
     assert.deepEqual(errors,[],engineName+' '+mode+' JS errors');
     console.log('33.14 '+engineName+' iPhone '+(pwa?'PWA':'Safari')+' '+mode+' visualViewport keyboard PASS');
    }finally{await context.close()}
   }
  }finally{await browser.close()}
 }
})().catch(e=>{console.error(e);process.exitCode=1});
