// Real Chromium/WebKit layout checks for the release-32 location shortcuts.
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const {chromium,webkit}=require('playwright');
const root=path.resolve(__dirname,'..'),output=path.join(root,'test-results/release32');
const fixture=spawnSync('python',['-B','-c',`
import app,json,tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as td:
 app.DATA_DIR=Path(td);app.DB_PATH=Path(td)/'test.db';app.OPTIONS_PATH=Path(td)/'options.json'
 app.publish_sensors_async=lambda:None;app.google_reverse_geocode=lambda *a:{}
 app.init_db()
 print(json.dumps(dict(html=app.APP_HTML,summary=app.summary('month'))))
`],{cwd:path.join(root,'rit_tank'),encoding:'utf8'});
assert.equal(fixture.status,0,fixture.stderr);
const {html,summary}=JSON.parse(fixture.stdout);
const HOME='Verenlandweg 4, 7461 AP Rijssen',SCHOOL='Van Broekhuizenstraat 4, 7461 VW Rijssen';
const home={address:HOME,latitude:52.315,longitude:6.528,place_id:'home-id',source:'google_places'};
const school={address:SCHOOL,latitude:52.307,longitude:6.519,place_id:'school-id',source:'google_places'};
const gps={address:'GPSstraat 7, 7461 AA Rijssen',latitude:52.31,longitude:6.52,distance_m:8};
(async()=>{
 fs.mkdirSync(output,{recursive:true});
 for(const [engine,type] of Object.entries({chromium,webkit})){
  const browser=await type.launch({headless:true});
  try{
   for(const [device,width,height] of [['iphone-small',320,568],['iphone',390,844],['ipad',820,1180],['ipad-pro-13',1024,1366]]){
    const context=await browser.newContext({viewport:{width,height},isMobile:true,hasTouch:true,deviceScaleFactor:2,serviceWorkers:'block'});
    const page=await context.newPage(),errors=[];let delayedPlaces=null;
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/*',async route=>{
      const u=new URL(route.request().url());let data={};
      if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
      if(u.pathname==='/api/summary')data=summary;
      else if(u.pathname==='/api/assistant/arrivals')data={arrivals:[]};
      else if(u.pathname==='/api/places/home')data=home;
      else if(u.pathname==='/api/places/beatrixschool')data=school;
      else if(u.pathname==='/api/location/addresses')data={street:'GPSstraat',addresses:[gps]};
      else if(u.pathname==='/api/places/search-address'){delayedPlaces=route;return}
      else if(u.pathname==='/api/business/route-preview')data={distance_m:9000,distance_source:'route',suggested_odometer:10009};
      else if(!u.pathname.startsWith('/api/'))return route.fulfill({status:204,body:''});
      await route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
    });
    await page.goto('https://rit-tank.test/');
    await page.waitForFunction(()=>typeof DATA!=='undefined');
    await page.evaluate(()=>openTripPoint('start'));
    await page.locator('#physicalConfirmButton').click();
    const homeBtn=page.locator('#tripHomeButton'),schoolBtn=page.locator('#tripSchoolButton'),gpsBtn=page.locator('#tripCurrentLocationButton');
    await schoolBtn.scrollIntoViewIfNeeded();
    for(const locator of [homeBtn,schoolBtn,gpsBtn])assert.equal(await locator.isVisible(),true);
    const [hb,sb,gb]=await Promise.all([homeBtn.boundingBox(),schoolBtn.boundingBox(),gpsBtn.boundingBox()]);
    assert.ok(Math.abs(hb.width-sb.width)<=1,engine+' '+device+' equal fixed button widths');
    assert.ok(Math.abs(hb.y-sb.y)<=1,engine+' '+device+' fixed buttons share first row');
    assert.ok(gb.y>=hb.y+hb.height-1,engine+' '+device+' GPS is on second row');
    assert.ok(gb.width>=hb.width+sb.width-2,engine+' '+device+' GPS spans full row');
    for(const locator of [homeBtn,schoolBtn,gpsBtn]){
      assert.equal(await locator.evaluate(el=>el.scrollWidth>el.clientWidth+1),false,engine+' '+device+' button text overflow');
      const box=await locator.boundingBox();assert.ok(box.height>=44,engine+' '+device+' touch target');
    }
    assert.equal(await page.locator('#tripModal .sheet').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);

    await schoolBtn.click();await page.waitForFunction(a=>document.getElementById('tripManualAddress').value===a,SCHOOL);
    assert.match(await page.locator('#tripLocationTitle').textContent(),/Adres bevestigd/);
    await homeBtn.click();await page.waitForFunction(a=>document.getElementById('tripManualAddress').value===a,HOME);
    await schoolBtn.click();await page.waitForFunction(a=>document.getElementById('tripManualAddress').value===a,SCHOOL);

    await page.evaluate(()=>{window.__gpsPromise=new Promise(r=>window.__resolveGps=r);resolveLocation=()=>window.__gpsPromise;void captureTripLocation()});
    await schoolBtn.click();await page.waitForFunction(a=>document.getElementById('tripManualAddress').value===a,SCHOOL);
    await page.evaluate(()=>window.__resolveGps({latitude:1,longitude:1,accuracy:5}));
    await page.waitForTimeout(30);assert.equal(await page.locator('#tripManualAddress').inputValue(),SCHOOL);

    await page.evaluate(()=>{resolveLocation=async()=>({latitude:52.31,longitude:6.52,accuracy:5});void captureTripLocation()});
    await page.locator('#tripAddressChoices button').waitFor();await page.locator('#tripAddressChoices button').click();
    assert.equal(await page.locator('#tripManualAddress').inputValue(),gps.address);

    await page.locator('#tripManualAddress').fill('Oud adres');
    for(let i=0;i<60&&!delayedPlaces;i++)await page.waitForTimeout(20);
    assert.ok(delayedPlaces,engine+' '+device+' delayed Places request');
    await schoolBtn.click();await page.waitForFunction(a=>document.getElementById('tripManualAddress').value===a,SCHOOL);
    await delayedPlaces.fulfill({contentType:'application/json',body:JSON.stringify({places:[{address:'Vertraagd adres 1, 1234 AB Stad',latitude:1,longitude:1}]})});
    await page.waitForTimeout(30);assert.equal(await page.locator('#tripManualAddress').inputValue(),SCHOOL);

    await page.screenshot({path:path.join(output,engine+'-'+device+'-locations.png'),fullPage:false});
    await page.setViewportSize({width,height:360});await page.locator('#tripManualAddress').focus();
    await page.waitForFunction(()=>window.visualViewport.height<=361&&document.getElementById('tripModal').getBoundingClientRect().height<=361);
    assert.equal(await page.locator('#tripModal .sheet').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);
    assert.equal(await page.locator('.trip-location-actions').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);
    await page.setViewportSize({width,height});
    assert.deepEqual(errors,[]);
    console.log(engine,device,'Beatrixschool layout, switching, stale GPS/Places, keyboard viewport and screenshot PASS');
    await context.close();
   }
  }finally{await browser.close()}
 }
})().catch(e=>{console.error(e);process.exitCode=1});
