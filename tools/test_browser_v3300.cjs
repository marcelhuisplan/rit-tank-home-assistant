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
   for(const [device,width,height,safeTop,safeBottom] of [['iphone-se',375,667,20,0],['iphone-15-pro',393,852,59,34],['ipad-pro-13',1032,1376,24,20]]){
    const context=await browser.newContext({viewport:{width,height},isMobile:true,hasTouch:true,deviceScaleFactor:2,serviceWorkers:'block'});
    const page=await context.newPage(),errors=[],sent=[],odometerNetwork=[];let current=start;
    // Collect only synthetic API traffic to diagnose WebKit access-control failures.
    const isOdometerRequest=url=>new URL(url).pathname==='/api/business/odometer-suggestion';
    const trace=(event,details={})=>{
      odometerNetwork.push({event,...details});
      if(odometerNetwork.length>120)odometerNetwork.shift();
    };
    page.on('request',req=>{
      if(isOdometerRequest(req.url()))trace('request',{url:req.url(),method:req.method(),document:page.url()});
    });
    page.on('response',res=>{
      if(isOdometerRequest(res.url()))trace('response',{url:res.url(),status:res.status()});
    });
    page.on('requestfinished',req=>{
      if(isOdometerRequest(req.url()))trace('requestfinished',{url:req.url()});
    });
    page.on('requestfailed',req=>{
      if(isOdometerRequest(req.url()))trace('requestfailed',{url:req.url(),reason:req.failure(),document:page.url()});
    });
    page.on('framenavigated',frame=>{
      if(frame===page.mainFrame())trace('navigation',{url:frame.url()});
    });
    await page.addInitScript(({top,bottom})=>{
      addEventListener('DOMContentLoaded',()=>{
        const modal=document.getElementById('tripModal');
        if(modal){modal.style.setProperty('--trip-safe-top',top+'px');modal.style.setProperty('--trip-safe-bottom',bottom+'px')}
      },{once:true});
    },{top:safeTop,bottom:safeBottom});
    page.on('pageerror',e=>{
      errors.push(e.message);
      trace('pageerror',{message:e.message,document:page.url()});
    });
    await page.route('**/*',async route=>{
      const u=new URL(route.request().url());let data={};
      if(u.pathname==='/'){current=u.searchParams.get('case')==='finish'?finish:start;return route.fulfill({contentType:'text/html',body:html})}
      if(u.pathname==='/api/summary')data=current;
      else if(u.pathname==='/api/assistant/arrivals')data={arrivals:[]};
      else if(u.pathname==='/api/business/odometer-suggestion'){
        data=current.business.odometer_suggestion;
        trace('intercept',{url:u.href,fixture:current===start?'start':'finish',
          valueType:data===undefined?'undefined':data===null?'null':typeof data});
      }
      else if(u.pathname==='/api/places/home')data=home;
      else if(u.pathname==='/api/places/beatrixschool')data=school;
      else if(u.pathname==='/api/business/route-preview')data={distance_m:9000,distance_source:'route',suggested_odometer:25255};
      else if(u.pathname==='/api/business/finish'){
        const payload=route.request().postDataJSON();sent.push(payload);
        if(!payload.confirmation_token)return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({confirmation_required:true,confirmation_token:'release33-browser-token',check:discrepancy})});
        return route.fulfill({status:201,contentType:'application/json',body:JSON.stringify(completed)});
      }else if(!u.pathname.startsWith('/api/'))return route.fulfill({status:204,body:''});
      await route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
      if(u.pathname==='/api/business/odometer-suggestion')trace('fulfilled',{url:u.href});
    });

    // Release 33.09: iPhone/iPad tankbon close-controls at both scroll extremes.
    await page.goto('https://rit-tank.test/?case=start');
    await page.waitForFunction(()=>typeof DATA!=='undefined');
    await page.addScriptTag({content:fs.readFileSync(path.join(root,'rit_tank/receipt_archive.js'),'utf8')});
    for(const atBottom of [false,true]){
      await page.evaluate(()=>openReceiptArchive());
      await page.evaluate(({top,bottom})=>{
        const modal=document.getElementById('receiptArchiveModal');
        modal.style.setProperty('--trip-safe-top',top+'px');
        modal.style.setProperty('--trip-safe-bottom',bottom+'px');
        const list=document.getElementById('archiveList');
        for(let n=0;n<32;n++){
          const row=document.createElement('div');row.className='known-row';
          row.textContent='Bestaande bon '+n;list.appendChild(row);
        }
      },{top:safeTop,bottom:safeBottom});
      const archive=page.locator('#receiptArchiveModal'),close=archive.locator('.sheethead .close');
      await archive.waitFor({state:'visible'});
      await archive.locator('.sheet').evaluate((el,bottom)=>{el.scrollTop=bottom?el.scrollHeight:0},atBottom);
      await page.waitForTimeout(60);
      const metrics=await close.evaluate(el=>{
        const r=el.getBoundingClientRect(),head=el.closest('.sheethead'),
          sheet=el.closest('.sheet'),style=getComputedStyle(el),h=head.querySelector('h2').getBoundingClientRect();
        const point=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
        return {width:r.width,height:r.height,left:r.left,right:r.right,top:r.top,bottom:r.bottom,
          background:style.backgroundColor,color:style.color,
          sticky:getComputedStyle(head).position,hit:point===el||el.contains(point),
          gap:r.left-h.right,withinSheet:r.left>=sheet.getBoundingClientRect().left-.5&&
          r.right<=sheet.getBoundingClientRect().right+.5,
          atBottom:sheet.scrollTop+sheet.clientHeight>=sheet.scrollHeight-1};
      });
      const tag=engine+' '+device+' receipt archive '+(atBottom?'bottom':'top');
      assert.ok(metrics.width>=56&&metrics.height>=56,tag+' large close button');
      assert.equal(metrics.background,'rgb(80, 238, 199)',tag+' mint background');
      assert.equal(metrics.color,'rgb(5, 37, 29)',tag+' dark cross');
      assert.equal(metrics.sticky,'sticky',tag+' sticky title bar');
      assert.equal(metrics.hit,true,tag+' center is touchable');
      assert.equal(metrics.withinSheet,true,tag+' button within sheet');
      assert.ok(metrics.left>=0&&metrics.right<=width+.5&&metrics.top>=safeTop-.5&&metrics.bottom<=height-safeBottom+.5,tag+' button fully inside safe area');
      assert.ok(metrics.gap>=10,tag+' heading does not overlap button');
      if(atBottom)assert.equal(metrics.atBottom,true,tag+' scrolled to end');
      await page.screenshot({path:path.join(output,engine+'-'+device+'-receipt-close-'+(atBottom?'bottom':'top')+'.png')});
      await close.tap();
      await archive.waitFor({state:'hidden'});
    }

    // Release 33.09: Tankbeurt has the identical sticky touch-safe control.
    for(const atBottom of [false,true]){
      await page.evaluate(()=>openFuel());
      const fuel=page.locator('#fuelModal'),close=fuel.locator('.sheethead .close');
      await fuel.waitFor({state:'visible'});
      await page.evaluate(({top,bottom})=>{
        const el=document.getElementById('fuelModal');
        el.style.setProperty('--trip-safe-top',top+'px');
        el.style.setProperty('--trip-safe-bottom',bottom+'px');
      },{top:safeTop,bottom:safeBottom});
      // openFuel schedules a guideTo() smooth scroll after 80 ms.
      // Wait until that scheduled navigation has started, then override it
      // with an instant scroll of the actual scroll container (#fuelSheet).
      await page.waitForTimeout(180);
      await fuel.locator('.sheet').evaluate((el,bottom)=>{
        el.scrollTo({top:bottom?el.scrollHeight:0,behavior:'instant'});
      },atBottom);
      await page.waitForFunction(bottom=>{
        const sheet=document.querySelector('#fuelModal .sheet');
        return bottom
          ? sheet.scrollHeight>sheet.clientHeight && sheet.scrollTop>0 &&
            sheet.scrollTop+sheet.clientHeight>=sheet.scrollHeight-1
          : sheet.scrollTop<=1;
      },atBottom,{timeout:5000});
      const m=await close.evaluate(el=>{
        const r=el.getBoundingClientRect(),sheet=el.closest('.sheet').getBoundingClientRect(),
          title=el.closest('.sheethead').querySelector('h2').getBoundingClientRect(),
          style=getComputedStyle(el),header=getComputedStyle(el.closest('.sheethead')),
          container=el.closest('.sheet');
        const hit=(x,y)=>{const p=document.elementFromPoint(x,y);return p===el||el.contains(p)};
        return {width:r.width,height:r.height,left:r.left,right:r.right,top:r.top,bottom:r.bottom,
          bg:style.backgroundColor,fg:style.color,sticky:header.position,
          hit:[[.25,.25],[.5,.5],[.75,.75]].every(([x,y])=>hit(r.left+r.width*x,r.top+r.height*y)),
          inSheet:r.left>=sheet.left-.5&&r.right<=sheet.right+.5,gap:r.left-title.right,
          atBottom:container.scrollTop+container.clientHeight>=container.scrollHeight-1};
      });
      const tag=engine+' '+device+' fuel '+(atBottom?'bottom':'top');
      assert.ok(m.width>=56&&m.height>=56,tag+' 56px close');
      assert.equal(m.bg,'rgb(80, 238, 199)',tag+' mint');
      assert.equal(m.fg,'rgb(5, 37, 29)',tag+' dark cross');
      assert.equal(m.sticky,'sticky',tag+' sticky');
      assert.equal(m.hit,true,tag+' tappable');
      assert.equal(m.inSheet,true,tag+' within sheet');
      assert.ok(m.gap>=10,tag+' no title overlap');
      assert.ok(m.left>=0&&m.right<=width+.5&&m.top>=safeTop-.5&&m.bottom<=height-safeBottom+.5,tag+' safe viewport');
      if(atBottom)assert.equal(m.atBottom,true,tag+' scroll complete');
      await page.screenshot({path:path.join(output,engine+'-'+device+'-fuel-close-'+(atBottom?'bottom':'top')+'.png')});
      await close.tap();
      await fuel.waitFor({state:'hidden'});
      assert.equal(sent.length,0,tag+' close did not store data');
    }

    // Release 33.04: the shared close button must stay inside the safe viewport in every trip screen at both scroll extremes.
    for(const mode of ['start','stop','finish']){
      for(const atBottom of [false,true]){
        await page.goto('https://rit-tank.test/?case='+(mode==='start'?'start':'finish'));
        await page.waitForFunction(()=>typeof DATA!=='undefined');
        await page.evaluate(mode=>openTripPoint(mode),mode);
        await page.locator('#tripModal').waitFor({state:'visible'});
        // Allow the existing delayed initial input scroll to finish before choosing an extreme.
        await page.waitForTimeout(150);
        await page.locator('#tripModal .sheet').evaluate((el,bottom)=>{el.scrollTop=bottom?el.scrollHeight:0},atBottom);
        await page.waitForFunction(bottom=>{
          const el=document.querySelector('#tripModal .sheet');
          return bottom?el.scrollTop+el.clientHeight>=el.scrollHeight-1:el.scrollTop<=1;
        },atBottom,{timeout:5000});
        const close=page.getByRole('button',{name:'Sluiten',exact:true});
        assert.equal(await close.isVisible(),true);
        const m=await close.evaluate(el=>{
          const r=el.getBoundingClientRect(),s=getComputedStyle(el),head=el.closest('.sheethead');
          const title=head.querySelector('h2').getBoundingClientRect(),sheet=el.closest('.sheet').getBoundingClientRect(),modal=el.closest('#tripModal'),ms=getComputedStyle(modal);
          const safeTop=parseFloat(ms.getPropertyValue('--trip-safe-top'))||0,safeBottom=parseFloat(ms.getPropertyValue('--trip-safe-bottom'))||0;
          const rgb=v=>v.match(/[\d.]+/g).slice(0,3).map(Number);
          const luminance=v=>rgb(v).map(x=>{x/=255;return x<=.04045?x/12.92:((x+.055)/1.055)**2.4}).reduce((sum,x,i)=>sum+x*[.2126,.7152,.0722][i],0);
          const fg=luminance(s.color),bg=luminance(s.backgroundColor);
          const hit=(x,y)=>{const target=document.elementFromPoint(x,y);return target===el||el.contains(target)};
          return {width:r.width,height:r.height,background:s.backgroundColor,color:s.color,
            contrast:(Math.max(fg,bg)+.05)/(Math.min(fg,bg)+.05),fontSize:parseFloat(s.fontSize),weight:Number(s.fontWeight),opacity:s.opacity,
            inside:r.left>=Math.max(0,sheet.left)&&r.right<=Math.min(innerWidth,sheet.right)+.5&&r.top>=Math.max(0,sheet.top)&&r.bottom<=Math.min(innerHeight,sheet.bottom)+.5,
            safeViewport:r.top>=safeTop-.5&&r.bottom<=innerHeight-safeBottom+.5,
            sheetSafeTop:sheet.top>=safeTop-.5,sheetHeight:sheet.height,maxSheetHeight:innerHeight-safeTop-12,
            gap:r.left-title.right,sticky:getComputedStyle(head).position,
            hit:[[.5,.5],[.25,.25],[.75,.25],[.25,.75],[.75,.75]].every(([x,y])=>hit(r.left+r.width*x,r.top+r.height*y))};
        });
        const label=engine+' '+device+' '+mode+' '+(atBottom?'bottom':'top');
        assert.ok(m.width>=56&&m.height>=56,label+' 56px touch target');
        assert.equal(m.background,'rgb(80, 238, 199)',label+' bright mint');
        assert.equal(m.color,'rgb(5, 37, 29)',label+' dark cross');
        assert.ok(m.contrast>=7,label+' high contrast');
        assert.ok(m.fontSize>=36&&m.weight>=900,label+' large bold cross');
        assert.equal(m.opacity,'1',label+' opaque');
        assert.equal(m.sticky,'sticky',label+' sticky header');
        assert.equal(m.inside,true,label+' visible within sheet and viewport');
        assert.equal(m.safeViewport,true,label+' clear of top/bottom safe area');
        assert.equal(m.sheetSafeTop,true,label+' trip sheet begins below top safe area');
        assert.ok(m.sheetHeight<=m.maxSheetHeight+1,label+' trip sheet height respects top safe area');
        assert.ok(m.gap>=12,label+' no title overlap');
        assert.equal(m.hit,true,label+' unobstructed touch target');
        await page.screenshot({path:path.join(output,engine+'-'+device+'-'+mode+'-close-'+(atBottom?'bottom':'top')+'.png')});
        await close.tap();
        await page.locator('#tripModal').waitFor({state:'hidden'});
        assert.equal(sent.length,0,label+' close does not save a trip');
      }
    }

    // Release 33.10: simulate the shrinking/moving iOS visual viewport while
    // typing. Verify the actual input rect, not just the sheet height.
    for(const mode of ['start','stop','finish']){
      await page.goto('https://rit-tank.test/?case='+(mode==='start'?'start':'finish'));
      await page.waitForFunction(m=>typeof DATA!=='undefined'&&(m==='start'||DATA?.business?.active_trip),mode);
      await page.evaluate(m=>openTripPoint(m),mode);
      await page.locator('#tripModal').waitFor({state:'visible'});
      await page.waitForTimeout(150); // Let the pre-existing opening scroll complete.
      const field=page.locator('#physicalTripValue');
      await field.click();
      for(const keyboardHeight of [360,320]){
        await page.setViewportSize({width,height:keyboardHeight});
        // Safari can reset the nested sheet scroll during a viewport pan.
        await page.evaluate(()=>{
          document.querySelector('#tripModal .sheet').scrollTop=0;
          window.visualViewport.dispatchEvent(new Event('scroll'));
        });
        await page.waitForFunction(()=>{
          const el=document.getElementById('physicalTripValue'),sheet=el.closest('.sheet'),
            head=sheet.querySelector('.sheethead'),view=window.visualViewport;
          const r=el.getBoundingClientRect(),s=sheet.getBoundingClientRect(),
            top=Math.max(s.top,head.getBoundingClientRect().bottom,view.offsetTop)+8,
            bottom=Math.min(s.bottom,view.offsetTop+view.height)-8,
            hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
          return document.activeElement===el&&r.top>=top-.5&&r.bottom<=bottom+.5&&
            (hit===el||el.contains(hit));
        },null,{timeout:5000});
        if(keyboardHeight===360){
          await field.fill('');
          await field.pressSequentially('25234',{delay:12});
        }else{
          await field.press('Backspace');
          await field.pressSequentially('5',{delay:12});
        }
        assert.equal(await field.inputValue(),keyboardHeight===360?'25234':'25235',
          engine+' '+device+' '+mode+' typed odometer is correct');
        const typingVisible=await field.evaluate(el=>{
          const r=el.getBoundingClientRect(),sheet=el.closest('.sheet'),
            head=sheet.querySelector('.sheethead'),s=sheet.getBoundingClientRect(),
            view=window.visualViewport,hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
          return r.top>=Math.max(s.top,head.getBoundingClientRect().bottom,view.offsetTop)+7&&
            r.bottom<=Math.min(s.bottom,view.offsetTop+view.height)-7&&
            (hit===el||el.contains(hit));
        });
        assert.equal(typingVisible,true,engine+' '+device+' '+mode+' input stays fully visible while typing '+keyboardHeight);
        assert.equal(await page.locator('#tripModal .sheet').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);
        await page.screenshot({path:path.join(output,engine+'-'+device+'-'+mode+'-typing-'+keyboardHeight+'.png')});
      }
      await page.setViewportSize({width,height});
      assert.equal(await field.inputValue(),'25235',engine+' '+device+' '+mode+' odometer survives keyboard dismissal');
      await page.locator('#tripModal .close').tap();
      await page.locator('#tripModal').waitFor({state:'hidden'});
      assert.equal(sent.length,0,engine+' '+device+' '+mode+' typing did not save trip');
    }

    // Tankbeurt enters kilometres by six wheels (no keyboard). The displayed
    // value must survive opening and closing a keyboard for another fuel field.
    await page.goto('https://rit-tank.test/?case=start');
    await page.waitForFunction(()=>typeof DATA!=='undefined');
    await page.evaluate(()=>openFuel());
    await page.locator('#fuelModal').waitFor({state:'visible'});
    await page.waitForTimeout(180);
    assert.equal(await page.locator('#fuelOdoDisplay').textContent(),'25.230');
    await page.locator('#fuelOdoD5').evaluate(el=>{el.scrollTop=50});
    await page.waitForFunction(()=>document.getElementById('fuelOdo').value==='25231');
    assert.equal(await page.locator('#fuelOdoDisplay').textContent(),'25.231');
    await page.locator('#fuelStation').focus();
    await page.setViewportSize({width,height:360});
    await page.waitForFunction(()=>document.getElementById('fuelModal').getBoundingClientRect().height<=361);
    assert.equal(await page.locator('#fuelOdoDisplay').textContent(),'25.231');
    await page.setViewportSize({width,height});
    await page.locator('#fuelModal .close').tap();
    await page.locator('#fuelModal').waitFor({state:'hidden'});
    assert.equal(sent.length,0,engine+' '+device+' fuel wheel keyboard check did not save data');

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

    // Release 33.02 regression: at the very bottom, the Tussenstop close bar stays sticky and clickable.
    await page.locator('#tripModal .sheet').evaluate(el=>{el.scrollTop=el.scrollHeight});
    await page.waitForFunction(()=>{
      const sheet=document.querySelector('#tripModal .sheet');
      return sheet.scrollTop+sheet.clientHeight>=sheet.scrollHeight-1;
    },null,{timeout:5000});
    const stickyClose=await page.locator('#tripModal .close').evaluate(el=>{
      const r=el.getBoundingClientRect(),sheet=el.closest('.sheet'),head=el.closest('.sheethead');
      const hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
      return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,viewportWidth:innerWidth,viewportHeight:innerHeight,
        atBottom:sheet.scrollTop+sheet.clientHeight>=sheet.scrollHeight-1,position:getComputedStyle(head).position,
        hit:hit===el||el.contains(hit)};
    });
    assert.equal(stickyClose.position,'sticky',engine+' '+device+' trip close header is sticky');
    assert.equal(stickyClose.atBottom,true,engine+' '+device+' trip sheet scrolled fully to bottom');
    assert.ok(stickyClose.left>=0&&stickyClose.right<=stickyClose.viewportWidth+.5&&stickyClose.top>=0&&stickyClose.bottom<=stickyClose.viewportHeight+.5,
      engine+' '+device+' close remains visible at scroll bottom');
    assert.equal(stickyClose.hit,true,engine+' '+device+' close remains topmost/clickable at scroll bottom');
    await page.screenshot({path:path.join(output,engine+'-'+device+'-sticky-close-bottom.png'),fullPage:false});
    await page.locator('#tripModal .close').click();
    await page.locator('#tripModal').waitFor({state:'hidden'});
    await page.evaluate(()=>openTripPoint('stop'));
    await page.waitForFunction(()=>document.getElementById('physicalTripValue').value.length>0);
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
    if(errors.length)console.error('33.00 WebKit/Chromium odometer API diagnostics',engine,device,JSON.stringify(odometerNetwork));
    assert.deepEqual(errors,[]);
    console.log(engine,device,'start input, selection, keyboard, rotation, 41 km finish, GPS warning and success screenshots PASS');
    await context.close();
   }
  }finally{await browser.close()}
 }
})().catch(e=>{console.error(e);process.exitCode=1});
