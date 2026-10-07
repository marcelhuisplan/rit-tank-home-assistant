// Release 33 real Chromium/WebKit validation for simplified odometer input and mobile completion.
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const {chromium,webkit}=require('playwright');
const root=path.resolve(__dirname,'..'),output=path.join(root,'test-results/release33');
const fixture=spawnSync('python',['-B','-c',`
import app,json,tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as td:
 app.DATA_DIR=Path(td);app.DB_PATH=Path(td)/'test.db';app.OPTIONS_PATH=Path(td)/'options.json'
 app.publish_sensors_async=lambda:None;app.google_reverse_geocode=lambda *a:{}
 app.init_db();app.set_settings({'km_reimbursement_rate':'0.25'})
 start=app.summary('month');start['current_odometer']=25230
 t=app.start_business_trip(dict(odometer=25230,created_at='2026-10-07T08:00:00+02:00',latitude=52.315,longitude=6.528,manual_label='Verenlandweg 4, 7461 AP Rijssen',physical_confirmed=True))['trip']
 m=app.add_business_stop(dict(trip_id=t['id'],odometer=25246,created_at='2026-10-07T09:00:00+02:00',latitude=52.307,longitude=6.519,manual_label='Van Broekhuizenstraat 4, 7461 VW Rijssen',physical_confirmed=True,segment_trip_type='business'))['trip']
 app.assistant_state_set('trip_distance_tracking',dict(trip_id=m['id'],stop_id=m['stops'][-1]['id'],segment_m=25000,sample_count=40,incomplete=False))
 finish=app.summary('month')
 print(json.dumps(dict(html=app.APP_HTML,start=start,finish=finish)))
`],{cwd:path.join(root,'rit_tank'),encoding:'utf8'});
assert.equal(fixture.status,0,fixture.stderr);
const {html,start,finish}=JSON.parse(fixture.stdout);
const HOME='Verenlandweg 4, 7461 AP Rijssen',SCHOOL='Van Broekhuizenstraat 4, 7461 VW Rijssen';
const home={address:HOME,latitude:52.315,longitude:6.528,place_id:'home-id',source:'google_places'};
const school={address:SCHOOL,latitude:52.307,longitude:6.519,place_id:'school-id',source:'google_places'};
const completed={ok:true,trip:{id:8,status:'completed',km:41,business_km:41,stops:[
 {odometer:25230,location_address:HOME,location_label:HOME,date_label:'7 oktober 2026',time_label:'08:00'},
 {odometer:25246,location_address:SCHOOL,location_label:SCHOOL,date_label:'7 oktober 2026',time_label:'09:00'},
 {odometer:25271,location_address:HOME,location_label:HOME,date_label:'7 oktober 2026',time_label:'10:00'}
]}};
const discrepancy={significant:true,gps_km:36.4,odometer_km:41,difference_km:4.6,relative_difference:4.6/41,start_odometer:25230,end_odometer:25271,gps_incomplete:false};
(async()=>{
 fs.mkdirSync(output,{recursive:true});
 for(const [engine,type] of Object.entries({chromium,webkit})){
  const browser=await type.launch({headless:true});
  try{
   for(const [device,width,height] of [['iphone-se',375,667],['iphone-large',430,932],['ipad-pro-13',1032,1376]]){
    const context=await browser.newContext({viewport:{width,height},isMobile:true,hasTouch:true,deviceScaleFactor:2,serviceWorkers:'block'});
    const page=await context.newPage(),errors=[],sent=[];let current=start;
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/*',async route=>{
      const u=new URL(route.request().url());let data={};
      if(u.pathname==='/'){current=u.searchParams.get('case')==='finish'?finish:start;return route.fulfill({contentType:'text/html',body:html})}
      if(u.pathname==='/api/summary')data=current;
      else if(u.pathname==='/api/assistant/arrivals')data={arrivals:[]};
      else if(u.pathname==='/api/business/odometer-suggestion')data=current.business.odometer_suggestion;
      else if(u.pathname==='/api/places/home')data=home;
      else if(u.pathname==='/api/places/beatrixschool')data=school;
      else if(u.pathname==='/api/business/route-preview')data={distance_m:9000,distance_source:'route',suggested_odometer:25255};
      else if(u.pathname==='/api/business/finish'){
        const payload=route.request().postDataJSON();sent.push(payload);
        if(!payload.confirmation_token)return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({confirmation_required:true,confirmation_token:'release33-browser-token',check:discrepancy})});
        return route.fulfill({status:201,contentType:'application/json',body:JSON.stringify(completed)});
      }else if(!u.pathname.startsWith('/api/'))return route.fulfill({status:204,body:''});
      await route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
    });

    // Start: proposal is not trusted until the large explicit confirmation button is pressed.
    await page.goto('https://rit-tank.test/?case=start');
    await page.waitForFunction(()=>typeof DATA!=='undefined');
    await page.evaluate(()=>openTripPoint('start'));
    const input=page.locator('#physicalTripValue'),confirm=page.locator('#physicalConfirmButton');
    assert.equal(await input.inputValue(),'25.230',engine+' '+device+' last physical odometer proposal');
    assert.equal(await page.locator('#tripStepLocation').isHidden(),true);
    const inputBox=await input.boundingBox(),confirmBox=await confirm.boundingBox();
    assert.ok(inputBox.height>=90,engine+' '+device+' large odometer field');
    assert.ok(confirmBox.height>=56,engine+' '+device+' large confirmation touch target');
    assert.equal(await page.locator('#physicalGpsDetails').getAttribute('open'),null);
    assert.equal(await page.locator('#tripModal .sheet').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);
    await input.click();await page.waitForTimeout(20);
    const selection=await input.evaluate(el=>[el.selectionStart,el.selectionEnd,el.value.length]);
    assert.deepEqual(selection,[0,selection[2],selection[2]],engine+' '+device+' proposal selected for easy overwrite');
    await input.fill('25.231');
    assert.equal(await page.locator('#physicalTripConfirmed').isChecked(),false);
    await confirm.click();
    assert.equal(await page.locator('#physicalTripConfirmed').isChecked(),true);
    assert.equal(await page.locator('#tripStepLocation').isVisible(),true);
    for(const id of ['#tripHomeButton','#tripSchoolButton','#tripCurrentLocationButton']){
      const target=page.locator(id);assert.equal(await target.isVisible(),true);assert.ok((await target.boundingBox()).height>=44);
    }
    await page.screenshot({path:path.join(output,engine+'-'+device+'-start.png'),fullPage:false});

    // Keyboard-sized viewport: input is preserved and the sticky confirmation action remains reachable.
    await page.setViewportSize({width,height:360});await input.focus();
    await page.waitForFunction(()=>window.visualViewport.height<=361&&document.getElementById('tripModal').getBoundingClientRect().height<=361);
    await confirm.scrollIntoViewIfNeeded();const keyButton=await confirm.boundingBox();
    assert.ok(keyButton.y>=0&&keyButton.y+keyButton.height<=361.5,engine+' '+device+' confirmation reachable above keyboard viewport');
    assert.equal(await input.inputValue(),'25.231');
    assert.equal(await page.locator('#tripModal .sheet').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);
    await page.screenshot({path:path.join(output,engine+'-'+device+'-keyboard.png'),fullPage:false});

    // Orientation/viewport changes must not erase physical input.
    await page.setViewportSize({width:Math.max(width,height),height:Math.min(width,height)});
    await page.waitForTimeout(30);assert.equal(await input.inputValue(),'25.231');
    await page.setViewportSize({width,height});await page.waitForTimeout(30);assert.equal(await input.inputValue(),'25.231');

    // Stop: long title keeps the close control safe and the save action clear of GPS details.
    await page.goto('https://rit-tank.test/?case=finish');
    await page.waitForFunction(()=>typeof DATA!=='undefined'&&DATA?.business?.active_trip);
    await page.evaluate(()=>openTripPoint('stop'));
    await page.waitForFunction(()=>document.getElementById('physicalTripValue').value.length>0);
    assert.equal(await page.locator('#tripSaveButton').textContent(),'Locatie opslaan');
    const closeMetrics=await page.locator('#tripModal .close').evaluate(el=>{
      const r=el.getBoundingClientRect(),s=el.closest('.sheet').getBoundingClientRect();
      return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height,
        sheetLeft:s.left,sheetRight:s.right,viewportWidth:innerWidth,viewportHeight:innerHeight};
    });
    assert.ok(closeMetrics.width>=44&&closeMetrics.height>=44,engine+' '+device+' close touch target');
    assert.ok(closeMetrics.left>=closeMetrics.sheetLeft&&closeMetrics.right<=closeMetrics.sheetRight+.5,
      engine+' '+device+' close inside sheet');
    assert.ok(closeMetrics.left>=0&&closeMetrics.right<=closeMetrics.viewportWidth+.5&&closeMetrics.top>=0&&closeMetrics.bottom<=closeMetrics.viewportHeight+.5,
      engine+' '+device+' close inside viewport');
    await page.locator('#physicalGpsDetails summary').click();
    await page.locator('#physicalGpsDetails').scrollIntoViewIfNeeded();
    const stopSpacing=await page.evaluate(()=>{
      const gps=document.getElementById('physicalGpsDetails').getBoundingClientRect();
      const save=document.getElementById('tripSaveButton').getBoundingClientRect();
      const sheet=document.querySelector('#tripModal .sheet');
      return {gap:save.top-gps.bottom,position:getComputedStyle(document.getElementById('tripSaveButton')).position,
        paddingBottom:parseFloat(getComputedStyle(sheet).paddingBottom)};
    });
    assert.equal(stopSpacing.position,'static',engine+' '+device+' save action does not float over content');
    assert.ok(stopSpacing.gap>=12,engine+' '+device+' GPS/save spacing');
    assert.ok(stopSpacing.paddingBottom>=18,engine+' '+device+' safe bottom breathing room');
    assert.equal(await page.locator('#tripModal .sheet').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);

    // Finish: server-oriented physical total is prominent; GPS stays folded until requested.
    await page.goto('https://rit-tank.test/?case=finish');
    await page.waitForFunction(()=>typeof DATA!=='undefined'&&DATA?.business?.active_trip);
    await page.evaluate(()=>openTripPoint('finish'));
    await page.waitForFunction(()=>document.getElementById('physicalTripValue').value.length>0);
    assert.equal(await page.locator('#physicalTripValue').inputValue(),'25.271');
    assert.equal(await page.locator('#physicalStart').textContent(),'25.230 km');
    assert.match(await page.locator('#physicalTripDistance').textContent(),/41 km/);
    assert.match(await page.locator('#tripSaveButton').textContent(),/41 km/);
    assert.equal(await page.locator('#physicalGpsDetails').evaluate(el=>el.open),false);
    await page.locator('#physicalConfirmButton').click();
    await page.locator('#tripHomeButton').click();
    await page.waitForFunction(a=>document.getElementById('tripManualAddress').value===a,HOME);
    await page.screenshot({path:path.join(output,engine+'-'+device+'-finish.png'),fullPage:false});
    assert.equal(await page.locator('#tripModal .sheet').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);

    await page.locator('#tripSaveButton').click();await page.locator('#distanceModal').waitFor({state:'visible'});
    assert.match(await page.locator('#distanceExplanation').textContent(),/fysieke kilometerstand wijkt af van de GPS-meting/i);
    assert.match(await page.locator('#distanceFacts').textContent(),/41 km/);
    await page.screenshot({path:path.join(output,engine+'-'+device+'-warning.png'),fullPage:false});
    await page.locator('#distanceConfirm').click();
    await page.locator('#tripSuccessModal').waitFor({state:'visible'});
    assert.equal(await page.locator('#tripSuccessDistance').textContent(),'41 km');
    assert.equal(await page.locator('#tripSuccessStartOdo').textContent(),'25.230 km');
    assert.equal(await page.locator('#tripSuccessEndOdo').textContent(),'25.271 km');
    assert.match(await page.locator('#tripSuccessStartLocation').textContent(),/Verenlandweg 4/);
    assert.match(await page.locator('#tripSuccessEndLocation').textContent(),/Verenlandweg 4/);
    assert.equal(sent.length,2,engine+' '+device+' exactly challenge + one confirmed finish POST');
    assert.equal(sent[1].confirmation_token,'release33-browser-token');
    await page.screenshot({path:path.join(output,engine+'-'+device+'-success.png'),fullPage:false});
    assert.equal(await page.locator('#tripSuccessModal .sheet').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);
    assert.deepEqual(errors,[]);
    console.log(engine,device,'start input, selection, keyboard, rotation, 41 km finish, GPS warning and success screenshots PASS');
    await context.close();
   }
  }finally{await browser.close()}
 }
})().catch(e=>{console.error(e);process.exitCode=1});
