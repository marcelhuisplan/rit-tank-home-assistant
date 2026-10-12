// Chromium/WebKit live DOM regression for server-scoped BAG house-number search.
'use strict';
const assert=require('node:assert/strict'),path=require('node:path'),{spawnSync}=require('node:child_process'),{chromium,webkit}=require('playwright');
const root=path.resolve(__dirname,'..');
const fixture=spawnSync('python',['-B','-c',[
 'import app,json,tempfile','from pathlib import Path',
 'with tempfile.TemporaryDirectory() as td:',
 ' app.DATA_DIR=Path(td);app.DB_PATH=Path(td)/"test.db";app.OPTIONS_PATH=Path(td)/"options.json"',
 ' app.publish_sensors_async=lambda:None;app.init_db()',
 ' print(json.dumps(dict(html=app.APP_HTML,summary=app.summary("month"))))'
].join('\n')],{cwd:path.join(root,'rit_tank'),encoding:'utf8',maxBuffer:4*1024*1024});
assert.equal(fixture.status,0,fixture.stderr);
const {html,summary}=JSON.parse(fixture.stdout);
const nearby={address:'Voorste Land 1, Gieten',latitude:53,longitude:6.75,distance_m:12};
const exact={address:'Voorste Land 7, Gieten',latitude:53.0007,longitude:6.7505};
(async()=>{
 for(const [name,engine] of Object.entries({chromium,webkit})){
  const browser=await engine.launch({headless:true});
  try{
   for(const viewport of [{name:'iPhone',width:375,height:667},{name:'iPad',width:768,height:1024}]){
    const ctx=await browser.newContext({viewport,isMobile:true,hasTouch:true,serviceWorkers:'block'});
    try{
     const page=await ctx.newPage(),errors=[],houseRequests=[],generalSearches=[];
     page.on('pageerror',e=>errors.push(e.message));
     await page.route('**/*',route=>{
      const url=new URL(route.request().url());
      if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
      if(url.pathname==='/receipt-archive.js')return route.fulfill({contentType:'text/javascript',body:''});
      if(url.pathname==='/api/summary')return route.fulfill({json:summary});
      if(url.pathname==='/api/location/addresses')
       return route.fulfill({json:{street:'Voorste Land',town:'Gieten',street_context:'synthetic-opaque-context-123456789',addresses:[nearby]}});
      if(url.pathname==='/api/location/house-number'){
       const payload=JSON.parse(route.request().postData());
       houseRequests.push(payload);
       return route.fulfill({json:{addresses:payload.house_number==='7'?[exact]:[],street:'Voorste Land',town:'Gieten'}});
      }
      if(url.pathname==='/api/places/search-address'){generalSearches.push(route.request().postData());return route.fulfill({json:{places:[]}})}
      if(url.pathname.startsWith('/api/'))return route.fulfill({json:{}});
      return route.fulfill({status:200,body:''});
     });
     await page.goto('http://127.0.0.1:8099/',{waitUntil:'domcontentloaded'});
     await page.waitForFunction(()=>typeof DATA!=='undefined'&&DATA!==null);
     await page.evaluate(()=>{resolveLocation=async()=>({latitude:53,longitude:6.75,accuracy:8});return openTripPoint('start')});
     await page.locator('#tripModal').waitFor({state:'visible'});
     await page.locator('#tripCurrentLocationButton').click();
     await page.locator('#tripHouseSearchBox').waitFor({state:'visible'});
     assert.equal(await page.locator('#tripHouseLabel').textContent(),'Huisnummer zoeken in Voorste Land, Gieten');
     assert.equal(await page.locator('#tripManualBox').isHidden(),true,'general search gated by explicit action');
     assert.equal(await page.locator('#tripAddressChoices button').count(),1);
     await page.locator('#tripHouseNumber').fill('7');
     await page.locator('#tripHouseResults button').waitFor();
     assert.equal(await page.locator('#tripHouseResults button').textContent(),exact.address);
     assert.equal(await page.locator('#tripAddressChoices button').count(),1,'nearby proposal still available');
     assert.equal(await page.evaluate(()=>TRIP_LOCATION),null,'search alone must not select');
     assert.deepEqual(houseRequests[0],{street_context:'synthetic-opaque-context-123456789',house_number:'7'});
     assert.equal(generalSearches.length,0,'no automatic Google search');
     await page.locator('#tripHouseResults button').click();
     assert.equal(await page.evaluate(()=>TRIP_LOCATION.address),exact.address);
     assert.equal(await page.evaluate(()=>TRIP_LOCATION.latitude),exact.latitude);
     assert.equal(await page.evaluate(()=>TRIP_LOCATION.longitude),exact.longitude);
     assert.equal(await page.locator('#tripSaveButton').count(),1,'existing explicit save retained');
     await page.locator('#tripCurrentLocationButton').click();
     await page.locator('#tripHouseSearchBox').waitFor({state:'visible'});
     await page.locator('#tripHouseNumber').fill('999');
     await page.waitForFunction(()=>document.querySelector('#tripHouseStatus').textContent.includes('bestaat niet'));
     assert.equal(await page.locator('#tripHouseResults button').count(),0);
     assert.equal(generalSearches.length,0);
     await page.locator('#tripOtherAddressButton').click();
     assert.equal(await page.locator('#tripManualBox').isVisible(),true);
     await page.locator('#tripModal .close').click();
     assert.equal(await page.locator('#tripHouseResults button').count(),0);
     assert.deepEqual(errors,[],name+' '+viewport.name+' page errors');
     console.log('BAG house number '+name+' '+viewport.name+' PASS');
    }finally{await ctx.close()}
   }
  }finally{await browser.close()}
 }
})().catch(e=>{console.error(e);process.exitCode=1});
