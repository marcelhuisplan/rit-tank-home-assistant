// Real mobile browser layout + actual application JS; API is isolated test data.
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const {chromium,webkit}=require('playwright');
const root=path.resolve(__dirname,'..'),output=path.join(root,'test-results/release31');
const fixture=spawnSync('python',['-B','-c',`
import app,json,tempfile
from pathlib import Path
from datetime import timedelta
with tempfile.TemporaryDirectory() as td:
 app.DATA_DIR=Path(td);app.DB_PATH=Path(td)/'test.db';app.OPTIONS_PATH=Path(td)/'options.json'
 app.publish_sensors_async=lambda:None
 app.google_reverse_geocode=lambda *a:{}
 app.init_db()
 t=app.start_business_trip(dict(odometer=64334,created_at=app.iso_local(app.now_local()-timedelta(hours=1)),latitude=52,longitude=6,manual_label='Startstraat 1, 1234 AB Teststad'))['trip']
 app.assistant_state_set('trip_distance_tracking',dict(trip_id=t['id'],stop_id=t['stops'][-1]['id'],segment_m=36400,sample_count=50,incomplete=True))
 print(json.dumps(dict(html=app.APP_HTML,summary=app.summary('month'))))
`],{cwd:path.join(root,'rit_tank'),encoding:'utf8'});
assert.equal(fixture.status,0,fixture.stderr);
const {html,summary}=JSON.parse(fixture.stdout);
const check={significant:true,gps_km:36.4,odometer_km:41,difference_km:4.6,relative_difference:4.6/41,start_odometer:64334,end_odometer:64375,gps_incomplete:true};
(async()=>{
 fs.mkdirSync(output,{recursive:true});
 for(const [engine,type] of Object.entries({chromium,webkit})){
  const browser=await type.launch({headless:true});
  try{for(const [device,width,height] of [['iphone-se',375,667],['iphone',390,844],['ipad',820,1180]]){
   const context=await browser.newContext({viewport:{width,height},isMobile:true,hasTouch:true,deviceScaleFactor:2,serviceWorkers:'block'});
   const page=await context.newPage(),errors=[],sent=[];let delayedRoute;
   page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',async route=>{
    const url=new URL(route.request().url());let data={};
    if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
    if(url.pathname==='/api/summary')data=summary;
    else if(url.pathname==='/api/business/odometer-suggestion')data=summary.business.odometer_suggestion;
    else if(url.pathname==='/api/business/route-preview'){delayedRoute=route;return}
    else if(url.pathname==='/api/business/finish'){
     const payload=route.request().postDataJSON();sent.push(payload);
     return route.fulfill({status:payload.confirmation_token?201:409,contentType:'application/json',body:JSON.stringify(payload.confirmation_token?{ok:true}:{confirmation_required:true,confirmation_token:'isolated-browser-token',check})});
    }else if(url.pathname==='/api/assistant/arrivals')data={arrivals:[]};
    else if(!url.pathname.startsWith('/api/'))return route.fulfill({status:204,body:''});
    await route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
   });
   await page.goto('https://rit-tank.test/');
   await page.waitForFunction(()=>typeof DATA!=='undefined'&&DATA?.business?.active_trip);
   await page.evaluate(()=>openTripPoint('finish'));
   await page.locator('#physicalTripValue').fill('64.375');
   await page.locator('#physicalTripConfirmed').check();
   await page.evaluate(()=>{void confirmTripAddress('Eindstraat 2, 1234 AC Teststad',{latitude:52.2,longitude:6})});
   for(let i=0;i<100&&!delayedRoute;i++)await page.waitForTimeout(20);
   assert.ok(delayedRoute,'Route lookup must be exercised');
   await delayedRoute.fulfill({contentType:'application/json',body:JSON.stringify({distance_m:30000,distance_source:'route',suggested_odometer:64364})});
   await page.waitForFunction(()=>document.getElementById('tripLocationDetail').textContent.includes('30'));
   assert.equal(await page.locator('#physicalTripValue').inputValue(),'64.375');
   assert.equal(await page.locator('#physicalTripConfirmed').isChecked(),true);
   assert.match(await page.locator('#physicalGpsWarning').textContent(),/onderbroken/);
   const overflow=await page.locator('#tripModal .sheet').evaluate(el=>el.scrollWidth>el.clientWidth+1);
   assert.equal(overflow,false,engine+' '+device+' no horizontal scroll');
   await page.locator('#physicalTripCard').scrollIntoViewIfNeeded();
   await page.screenshot({path:path.join(output,engine+'-'+device+'-input.png')});
   // Simulate reduced visual viewport with software keyboard. Real iOS hardware remains acceptance testing.
   await page.setViewportSize({width,height:360});await page.locator('#physicalTripValue').focus();
   const box=await page.locator('#tripModal').boundingBox();assert.ok(box.height<=361);
   assert.equal(await page.locator('#tripModal .sheet').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);
   await page.setViewportSize({width,height});
   await page.locator('#tripSaveButton').click();await page.locator('#distanceModal').waitFor({state:'visible'});
   assert.match(await page.locator('#distanceFacts').textContent(),/41 km/);
   assert.match(await page.locator('#distanceExplanation').textContent(),/onderbroken/);
   assert.equal(await page.locator('#distanceModal .sheet').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);
   await page.screenshot({path:path.join(output,engine+'-'+device+'-warning.png')});
   await page.locator('#distanceBack').click();assert.equal(sent.length,1);
   await page.locator('#tripSaveButton').click();await page.locator('#distanceConfirm').click();
   await page.waitForFunction(()=>!document.getElementById('tripModal').classList.contains('show'));
   assert.equal(sent.length,3);assert.equal(sent[2].odometer,'64.375');assert.equal(sent[2].confirmation_token,'isolated-browser-token');
   assert.deepEqual(errors,[]);console.log(engine,device,'input, delayed route, keyboard viewport, modal, cancel and confirmation PASS');
   await context.close();
  }}finally{await browser.close()}
 }
})().catch(e=>{console.error(e);process.exitCode=1});
