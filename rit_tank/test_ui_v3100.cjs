// Actual release-31 UI functions, with delayed responses and a small offline DOM.
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/app.py','utf8'),nodes=new Map(),calls=[];
const $=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',innerHTML:'',checked:false,hidden:false,disabled:false,style:{},children:[],focus(){},scrollIntoView(){},classList:{add(){},remove(){},toggle(){}}});return nodes.get(id)};
let routeResolve,reply,done=0,toasts=[];
const ctx=vm.createContext({$,console,crypto:require('node:crypto').webcrypto,Uint8Array,structuredClone,
  window:{visualViewport:{height:380,offsetTop:15,addEventListener(){}}},document:{activeElement:{blur(){}}},
  DATA:{current_odometer:64334,business:{active_trip:{id:5,start_odometer:64334,last_odometer:64334},odometer_suggestion:{suggested_odometer:64370,tracked_km:36.4,gps_total_km:36.4,gps_incomplete:true,distance_warning:'GPS-route mogelijk onderbroken — controleer de tellerstand.'}}},
  TRIP_MODE:'finish',TRIP_ODO_MANUAL:false,TRIP_LOCATION:null,TRIP_SEARCH_TIMER:null,TRIP_ADDRESS_REQUEST:0,TRIP_ROUTE_PREVIEW_REQUEST:0,
  clearTimeout(){},setTimeout:f=>f(),fmt:(v,d)=>Number(v).toLocaleString('nl-NL',{maximumFractionDigits:d,minimumFractionDigits:d}),esc:String,
  toast:m=>toasts.push(m),guideTo(){},advanceTripAfterLocation(){},openModal(){},closeModal(){},
  initOdometerWheel:(p,v)=>{$(p+'Odo').value=v},switchView(){},reloadData(){},
  api:async()=>new Promise(resolve=>routeResolve=resolve),
  fetch:async(path,options)=>{calls.push({path,options,payload:JSON.parse(options.body)});return typeof reply==='function'?reply():reply}
});
vm.runInContext(source.slice(source.indexOf('// Physical odometer flow:'),source.indexOf('\nfunction esc(s)')),ctx);
vm.runInContext(source.slice(source.indexOf('async function confirmTripAddress('),source.indexOf('async function selectTripHome(')),ctx);
vm.runInContext(source.slice(source.indexOf('function showTripOdoProposal('),source.indexOf('let TRIP_PROPOSAL_REQUEST=0;')),ctx);
const run=s=>vm.runInContext(s,ctx);
(async()=>{
  for(const s of ['64375','64.375','64375,0','64375.0'])assert.equal(ctx.physicalNumber(s),64375);
  for(const s of ['','NaN','Infinity','-1','64.37.5','64,375','64375.5','1e5'])assert.equal(ctx.physicalNumber(s),null);
  run('initPhysicalTrip(TRIP_MODE,DATA.business.active_trip)');
  assert.equal($('physicalTripValue').value,'64.370');assert.equal($('physicalStart').textContent,'64.334 km');
  assert.match($('physicalGpsWarning').textContent,/onvolledig/);
  assert.equal($('tripModal').style.height,'380px');assert.equal($('tripModal').style.top,'15px');
  // Start route lookup before manual input. Resolve it only after physical confirmation.
  const pending=run("confirmTripAddress('Teststraat 1, 1234 AB Stad',{latitude:52,longitude:6})");
  $('physicalTripValue').value='64.375';ctx.physicalTripChanged();$('physicalTripConfirmed').checked=true;ctx.physicalTripConsent();
  routeResolve({distance_m:30000,distance_source:'route',suggested_odometer:64364});await pending;
  assert.equal($('physicalTripValue').value,'64.375');assert.equal($('physicalTripConfirmed').checked,true);
  assert.match($('physicalGpsWarning').textContent,/onvolledig/);
  ctx.showTripOdoProposal({active:true,tracked_km:37,suggested_odometer:64371});
  assert.equal($('physicalTripValue').value,'64.375','Background proposals must never own physical input');
  $('physicalTripConfirmed').checked=false;await ctx.saveTripPoint();assert.equal(calls.length,0);
  $('physicalTripConfirmed').checked=true;$('tripDate').value='2026-09-28T12:00';
  const check={significant:true,gps_km:36.4,odometer_km:41,difference_km:4.6,relative_difference:4.6/41,start_odometer:64334,end_odometer:64375,gps_incomplete:true};
  reply={status:409,ok:false,json:async()=>({confirmation_required:true,confirmation_token:'server-token',check})};
  await ctx.saveTripPoint();assert.equal(calls.length,1);assert.equal(calls[0].payload.odometer,'64.375');
  assert.equal(calls[0].payload.physical_confirmed,true);assert.equal(calls[0].payload.trip_id,5);
  assert.match($('distanceFacts').innerHTML,/41 km/);assert.match($('distanceFacts').innerHTML,/\+4,6 km/);
  assert.match($('distanceExplanation').textContent,/onvolledig/);
  // Back/cancel and editing invalidate the saved client token; they do not POST.
  ctx.cancelPhysicalCheck();await ctx.confirmPhysicalCheck();assert.equal(calls.length,1);
  await ctx.saveTripPoint();$('physicalTripValue').value='64.376';ctx.physicalTripChanged();await ctx.confirmPhysicalCheck();assert.equal(calls.length,2);
  // An unchanged second POST uses exactly the same data and server token.
  $('physicalTripValue').value='64.375';$('physicalTripConfirmed').checked=true;await ctx.saveTripPoint();
  let resolveSave;reply=()=>new Promise(resolve=>resolveSave=resolve);
  const saving=ctx.confirmPhysicalCheck();await ctx.confirmPhysicalCheck();assert.equal(calls.length,4,'Double tap must issue only one confirm POST');
  assert.equal(calls.at(-1).payload.confirmation_token,'server-token');assert.equal(calls.at(-1).payload.odometer,'64.375');
  resolveSave({status:201,ok:true,json:async()=>({ok:true})});await saving;
  await ctx.confirmPhysicalCheck();assert.equal(calls.length,4);
  ctx.showPhysicalCheck({...check,significant:false,gps_missing:true,gps_km:null,difference_km:null,relative_difference:null});
  assert.match($('distanceFacts').innerHTML,/Niet volledig beschikbaar/);assert.doesNotMatch($('distanceFacts').innerHTML,/NaN|Infinity/);
  console.log('Release 31 UI: Dutch input, physical consent, 41 km, delayed routes/GPS, persistent warning, token, cancel, edits, duplicate POST and keyboard viewport PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
