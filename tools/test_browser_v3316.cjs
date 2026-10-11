// Release 33.16: iPhone/iPad Safari/PWA fuel entry, scrollability and saving.
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
  console.error('33.16 '+scenario+' fuel geometry '+JSON.stringify(measurements));
  const directory=path.join(root,'test-results');fs.mkdirSync(directory,{recursive:true});
  await page.screenshot({path:path.join(directory,'fuel-3316-'+scenario.toLowerCase().replace(/[^a-z0-9]+/g,'-')+'.png'),animations:'disabled'});
 }catch(diagnosticError){console.error('33.16 fuel diagnostics unavailable:',diagnosticError)}
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
     // Real keyboard Return via Playwright, never a direct call to the focus handler.
     await page.evaluate(()=>{
       window.__fuelReturnKeys=[];
       document.querySelector('#fuelModal').addEventListener('keydown',e=>{
         if(e.key==='Enter')window.__fuelReturnKeys.push({id:e.target.id,trusted:e.isTrusted});
       },true);
       window.__keyboard(340,0);
       window.guideTo=()=>{throw Error('Unexpected guideTo() while filling fuel')};
     });
     await page.waitForFunction(()=>document.querySelector('#fuelModal').getBoundingClientRect().height<=341);
     const visibleFocused=async id=>{
       await page.waitForFunction(id=>document.activeElement?.id===id,id);
       await page.evaluate(()=>new Promise(ok=>requestAnimationFrame(()=>requestAnimationFrame(ok))));
       const g=await page.evaluate(id=>{
         const el=document.getElementById(id),sheet=document.getElementById('fuelSheet'),head=sheet.querySelector('.sheethead');
         const field=el.getBoundingClientRect(),pane=sheet.getBoundingClientRect();
         const top=Math.max(pane.top,head.getBoundingClientRect().bottom,visualViewport.offsetTop)+8;
         const bottom=Math.min(pane.bottom,visualViewport.offsetTop+visualViewport.height)-8;
         return{id,fieldTop:field.top,fieldBottom:field.bottom,top,bottom,scrollTop:sheet.scrollTop,scrollHeight:sheet.scrollHeight,clientHeight:sheet.clientHeight,visible:field.top>=top-2&&field.bottom<=bottom+2};
       },id);
       if(!g.visible){console.error('33.16 focus geometry '+scenario+' '+JSON.stringify(g));await fuelFailureDiagnostics(page,scenario)}
       assert.equal(g.visible,true,id+' focused above the keyboard in '+scenario);
     };
     for(const [id,hint] of [['fuelOdo','next'],['fuelLitersInput','next'],['fuelPriceInput','next'],['fuelStation','next'],['fuelNote','done']])
       assert.equal(await page.locator('#'+id).getAttribute('enterkeyhint'),hint,id+' keyboard hint');
     await odo.click();
     await odo.pressSequentially('26200',{delay:3});
     assert.equal(await odo.inputValue(),'26200');
     await odo.press('Enter');
     await visibleFocused('fuelDate');
     await page.locator('#fuelDate').fill('2026-10-10T15:30');
     const dateNext=page.locator('#fuelDateNext');
     assert.equal(await dateNext.textContent(),'Volgende → Liters');
     await dateNext.click();
     await visibleFocused('fuelLitersInput');
     await liters.pressSequentially('4300',{delay:3});
     assert.equal(await liters.inputValue(),'43,00');
     await liters.press('Enter');
     await visibleFocused('fuelPriceInput');
     assert.equal(await liters.inputValue(),'43,00','liters digits survived focus transfer');
     await price.pressSequentially('2400',{delay:3});
     assert.equal(await price.inputValue(),'2,400');
     await price.press('Enter');
     await visibleFocused('fuelStation');
     assert.equal(await page.locator('#fuelTotal').textContent(),'€ 103,20');
     assert.equal(await odo.inputValue(),'26200');
     assert.equal(await liters.inputValue(),'43,00');
     assert.equal(await price.inputValue(),'2,400');
     const station=page.locator('#fuelStation'),note=page.locator('#fuelNote');
     await station.fill('Testpomp');
     await station.press('Enter');
     await visibleFocused('fuelNote');
     await note.fill('Notitie test');
     await note.press('Enter');
     assert.notEqual(await page.evaluate(()=>document.activeElement?.id),'fuelNote','Done must blur last field');
     const pressed=await page.evaluate(()=>window.__fuelReturnKeys);
     assert.deepEqual(pressed.map(x=>x.id),['fuelOdo','fuelLitersInput','fuelPriceInput','fuelStation','fuelNote']);
     assert.equal(pressed.every(x=>x.trusted),true,'the actual keyboard Return events must be trusted');
     assert.equal(await page.locator('#fuelDate').inputValue(),'2026-10-10T15:30');
     assert.equal(await station.inputValue(),'Testpomp');assert.equal(await note.inputValue(),'Notitie test');
     await page.evaluate(()=>window.__keyboard(window.innerHeight,0));
     await page.locator('#fuelSaveButton').click();
     await page.waitForFunction(()=>!document.querySelector('#fuelModal').classList.contains('show'));
     assert.equal(sent.length,1);assert.equal(sent[0].odometer,26200);
     assert.equal(sent[0].liters,43);assert.equal(sent[0].price_per_liter,2.4);
     assert.equal(sent[0].station,'Testpomp');assert.equal(sent[0].note,'Notitie test');
     assert.deepEqual(errors,[],engineName+' '+device.name+' JS errors');
     console.log('33.16 '+engineName+' '+device.name+' '+(pwa?'PWA':'Safari')+' PASS');
    }finally{await ctx.close()}
   }
  }finally{await browser.close()}
 }
})().catch(e=>{console.error(e);process.exitCode=1});
