// Release 33.15: iPhone/iPad Safari/PWA fuel entry, scrollability and saving.
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{spawnSync}=require('node:child_process'),{chromium,webkit}=require('playwright');
const root=path.resolve(__dirname,'..');
const fixture=spawnSync('python',['-B','-c',[
 'import app,json,tempfile','from pathlib import Path',
 'with tempfile.TemporaryDirectory() as td:',
 ' app.DATA_DIR=Path(td);app.DB_PATH=Path(td)/"test.db";app.OPTIONS_PATH=Path(td)/"options.json"',
 ' app.publish_sensors_async=lambda:None;app.init_db()',
 ' s=app.summary("month");s["current_odometer"]=26199',
 ' s["latest_fuel"]={"liters":43.75,"price_per_liter":2.409,"station":"Vorige pomp"}',
 ' print(json.dumps(dict(html=app.APP_HTML,summary=s)))'
].join('\n')],{cwd:path.join(root,'rit_tank'),encoding:'utf8',maxBuffer:4*1024*1024});
assert.equal(fixture.status,0,fixture.stderr);
const {html,summary}=JSON.parse(fixture.stdout);
async function fuelFailureDiagnostics(page,scenario){
 try{
  const measurements=await page.evaluate(()=>{
   const measure=id=>{
    const el=document.getElementById(id),r=el.getBoundingClientRect(),css=getComputedStyle(el);
    return{top:r.top,bottom:r.bottom,height:r.height,scrollTop:el.scrollTop,scrollHeight:el.scrollHeight,clientHeight:el.clientHeight,overflowY:css.overflowY};
   };
   return{innerWidth,innerHeight,visualViewportHeight:visualViewport.height,visualViewportTop:visualViewport.offsetTop,
    modal:measure('fuelModal'),sheet:measure('fuelSheet'),liters:measure('fuelLitersInput'),
    price:measure('fuelPriceInput'),save:measure('fuelSaveButton')};
  });
  console.error('33.15 '+scenario+' fuel geometry '+JSON.stringify(measurements));
  const directory=path.join(root,'test-results');fs.mkdirSync(directory,{recursive:true});
  await page.screenshot({path:path.join(directory,'fuel-3315-'+scenario.toLowerCase().replace(/[^a-z0-9]+/g,'-')+'.png'),animations:'disabled'});
 }catch(diagnosticError){console.error('33.15 fuel diagnostics unavailable:',diagnosticError)}
}
(async()=>{
 for(const [engineName,engine] of Object.entries({chromium,webkit})){
  const browser=await engine.launch({headless:true});
  try{
   for(const pwa of [false,true])for(const device of [{name:'iPhone',width:375,height:667},{name:'iPad',width:768,height:1024}]){
    const ctx=await browser.newContext({viewport:{width:device.width,height:device.height},isMobile:true,hasTouch:true,serviceWorkers:'block'});
    try{
     const page=await ctx.newPage(),sent=[],errors=[];
     page.on('pageerror',err=>errors.push(err.message));
     await page.addInitScript(({pwa})=>{
       // At document-start innerHeight is still the pre-meta mobile viewport (e.g. 1744px on iPhone).
       // Follow the settled layout viewport until the simulated keyboard explicitly overrides it.
       const box={height:null,top:0},view=new EventTarget();
       Object.defineProperty(view,'height',{get:()=>box.height??window.innerHeight});
       Object.defineProperty(view,'offsetTop',{get:()=>box.top});
       Object.defineProperty(window,'visualViewport',{configurable:true,value:view});
       if(pwa)Object.defineProperty(navigator,'standalone',{configurable:true,value:true});
       window.__keyboard=(height,top=0)=>{box.height=height;box.top=top;view.dispatchEvent(new Event('resize'));view.dispatchEvent(new Event('scroll'))};
     },{pwa});
     await page.route('**/*',route=>{
      const u=new URL(route.request().url());
      if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
      if(u.pathname==='/receipt-archive.js')return route.fulfill({contentType:'text/javascript',body:''});
      if(u.pathname==='/api/summary')return route.fulfill({json:summary});
      if(u.pathname==='/api/fuel'&&route.request().method()==='POST'){sent.push(JSON.parse(route.request().postData()));return route.fulfill({json:{ok:true,cost:103.20}})}
      if(u.pathname.startsWith('/api/'))return route.fulfill({json:{}});
      return route.fulfill({status:200,body:''});
     });
     await page.goto('http://127.0.0.1:8099/',{waitUntil:'domcontentloaded'});
     await page.waitForFunction(()=>typeof DATA!=='undefined'&&DATA!==null);
     await page.evaluate(()=>openFuel());await page.locator('#fuelModal').waitFor({state:'visible'});
     const odo=page.locator('#fuelOdo'),liters=page.locator('#fuelLitersInput'),price=page.locator('#fuelPriceInput');
     assert.equal(await page.locator('#fuelModal .wheel').count(),0);
     assert.equal(await odo.inputValue(),'26199');assert.equal(await liters.inputValue(),'43,75');assert.equal(await price.inputValue(),'2,409');
     const scenario=engineName+' '+device.name+' '+(pwa?'PWA':'Safari');
     const baseline=await page.evaluate(()=>({layout:window.innerHeight,visual:window.visualViewport.height}));
     if(baseline.visual!==baseline.layout)await fuelFailureDiagnostics(page,scenario);
     assert.equal(baseline.visual,baseline.layout,'visualViewport fixture must use post-meta mobile height');
     // A new sequence replaces selected proposals, not appends to them.
     try{await liters.click({timeout:5000})}catch(error){await fuelFailureDiagnostics(page,scenario);throw error}
     await liters.pressSequentially('4300',{delay:3});
     assert.equal(await liters.inputValue(),'43,00');
     await liters.press('Backspace');assert.equal(await liters.inputValue(),'4,30');
     await liters.pressSequentially('0',{delay:3});assert.equal(await liters.inputValue(),'43,00');
     await price.click();await price.pressSequentially('2400',{delay:3});
     assert.equal(await price.inputValue(),'2,400');
     assert.equal(await page.locator('#fuelTotal').textContent(),'€ 103,20');
     // Simulate iOS visualViewport shrink, without changing the layout viewport.
     await page.evaluate(()=>{document.querySelector('#fuelSheet').scrollTop=0;window.__keyboard(300,0)});
     await page.waitForFunction(()=>document.querySelector('#fuelModal').getBoundingClientRect().height<=301);
     const button=page.locator('#fuelSaveButton'),close=page.locator('#fuelModal .close');
     async function reach(locator){
      for(let n=0;n<17;n++){
       const reached=await locator.evaluate(el=>{
        const sheet=el.closest('.sheet'),v=window.visualViewport,head=sheet.querySelector('.sheethead').getBoundingClientRect(),r=el.getBoundingClientRect(),s=sheet.getBoundingClientRect();
        const top=Math.max(v.offsetTop+5,s.top+5,head.bottom+5),bottom=Math.min(v.offsetTop+v.height-5,s.bottom-5);
        let visible=r.top>=top-1&&r.bottom<=bottom+1;
        if(!visible)sheet.scrollTop+=r.bottom>bottom?Math.min(140,r.bottom-bottom+7):-Math.min(140,top-r.top+7);
        return visible;
       });
       if(reached)return true;
       await page.evaluate(()=>new Promise(ok=>requestAnimationFrame(ok)));
      }
      return false;
     }
     const priceReachable=await reach(price);
     if(!priceReachable)await fuelFailureDiagnostics(page,scenario);
     assert.equal(priceReachable,true,'price must be manually reachable above keyboard');
     await price.press('Backspace');await price.pressSequentially('0',{delay:3});
     assert.equal(await price.inputValue(),'2,400','keyboard scrolling preserves typed digits');
     const saveReachable=await reach(button);
     if(!saveReachable)await fuelFailureDiagnostics(page,scenario);
     assert.equal(saveReachable,true,'save button reachable with keyboard open');
     assert.equal(await close.evaluate(el=>{const r=el.getBoundingClientRect(),v=window.visualViewport;return r.top>=v.offsetTop&&r.bottom<=v.offsetTop+v.height}),true,'mint close reachable');
     await page.locator('#fuelModal .fuel-done-head').click();
     await page.evaluate(()=>window.__keyboard(window.innerHeight,0));
     await odo.click();await odo.pressSequentially('26200',{delay:3});
     assert.equal(await odo.inputValue(),'26200');
     assert.equal(await liters.inputValue(),'43,00');assert.equal(await price.inputValue(),'2,400');
     await page.locator('#fuelDate').fill('2026-10-10T15:30');
     await button.click();
     await page.waitForFunction(()=>!document.querySelector('#fuelModal').classList.contains('show'));
     assert.equal(sent.length,1);assert.equal(sent[0].odometer,26200);assert.equal(sent[0].liters,43);assert.equal(sent[0].price_per_liter,2.4);
     assert.equal(sent[0].station,'Vorige pomp');assert.equal(sent[0].full_tank,true);
     assert.deepEqual(errors,[],engineName+' '+device.name+' JS errors');
     console.log('33.15 '+engineName+' '+device.name+' '+(pwa?'PWA':'Safari')+' PASS');
    }finally{await ctx.close()}
   }
  }finally{await browser.close()}
 }
})().catch(e=>{console.error(e);process.exitCode=1});
