// Release 33 mobile odometer/UI regressions.
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/app.py','utf8'),nodes=new Map(),toasts=[],guides=[];
function classList(){const s=new Set();return {add:x=>s.add(x),remove:x=>s.delete(x),toggle(x,v){if(v===undefined)v=!s.has(x);v?s.add(x):s.delete(x)},contains:x=>s.has(x)}}
function element(){return {value:'',textContent:'',innerHTML:'',checked:false,hidden:false,disabled:false,open:false,style:{},children:[],classList:classList(),focus(){},select(){this.selected=true},setSelectionRange(){this.selected=true},scrollIntoView(){},addEventListener(){}}}
const $=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)};
let view={height:420,offsetTop:12,addEventListener(){}};
const ctx=vm.createContext({
  $,console,Number,structuredClone,crypto:require('node:crypto').webcrypto,Uint8Array,
  TRIP_MODE:'start',TRIP_ODO_MANUAL:false,TRIP_LOCATION:null,TRIP_SEGMENT_TYPE:'business',
  DATA:{current_odometer:25230,business:{active_trip:null,odometer_suggestion:{suggested_odometer:25271,tracked_km:25,gps_total_km:40.2,gps_incomplete:true,suggestion_reliable:false}}},
  window:{visualViewport:view},document:{activeElement:{blur(){}},createElement:element},
  setTimeout:f=>{f();return 1},clearTimeout(){},
  fmt:(v,d=0)=>Number(v).toLocaleString('nl-NL',{minimumFractionDigits:d,maximumFractionDigits:d}),
  esc:String,toast:(m)=>toasts.push(m),guideTo:id=>guides.push(id),openModal:id=>{$(id).opened=true},closeModal:id=>{$(id).opened=false},
  switchView(){},reloadData:async()=>{},fetch:async()=>{throw Error('unexpected fetch')}
});
const a=source.indexOf('// Physical odometer flow:'),b=source.indexOf('\nfunction esc(s)',a);
vm.runInContext(source.slice(a,b),ctx);

assert.match(source,/id="physicalTripValue"[^>]*inputmode="numeric"/);
assert.match(source,/id="physicalTripConfirmed" type="checkbox" hidden/);
assert.doesNotMatch(source,/Ik heb deze fysieke tellerstand gecontroleerd\./);
assert.match(source,/GPS-informatie bekijken/);
assert.match(source,/Rit vastgelegd!/);
assert.match(source,/\.physical-primary[^}]*position:sticky/);
assert.match(source,/@media\(max-width:390px\)/);
assert.match(source,/#tripModal \.sheet[^}]*overflow-x:hidden/);

for(const value of ['25230','25.230'])assert.equal(ctx.physicalNumber(value),25230);
for(const value of ['','25,230','25230.5','25.23.0','-1','1e5'])assert.equal(ctx.physicalNumber(value),null);

ctx.initPhysicalTrip('start',null);
assert.equal($('physicalTripValue').value,'25.230');
assert.equal($('tripStepLocation').hidden,true);
assert.equal($('physicalTripConfirmed').checked,false);
ctx.confirmPhysicalTripStep();
assert.equal($('physicalTripConfirmed').checked,true);
assert.equal($('tripStepLocation').hidden,false);
assert.equal(guides.at(-1),'tripStepLocation');

$('physicalTripValue').value='25.231';ctx.physicalTripChanged();
assert.equal($('physicalTripConfirmed').checked,false,'editing invalidates physical confirmation');

ctx.TRIP_MODE='finish';
const active={id:8,start_odometer:25230,last_odometer:25246};
ctx.initPhysicalTrip('finish',active);
assert.equal($('physicalTripValue').value,'25.271');
assert.equal($('physicalStart').textContent,'25.230 km');
assert.match($('physicalTripDistance').innerHTML,/41 km/);
assert.match($('tripSaveButton').textContent,/41 km/);

$('physicalTripValue').value='25.245';ctx.confirmPhysicalTripStep();
assert.equal($('physicalTripConfirmed').checked,false);
assert.match(toasts.at(-1),/niet lager/);

$('physicalTripValue').value='25.271';ctx.renderPhysicalDistance();
const before=$('physicalTripValue').value;
ctx.fitPhysicalViewport();view.height=310;view.offsetTop=36;ctx.fitPhysicalViewport();
assert.equal($('physicalTripValue').value,before,'viewport/orientation changes must not erase input');
assert.equal($('tripModal').style.height,'310px');assert.equal($('tripModal').style.top,'36px');

ctx.showTripSuccess({trip:{km:41,stops:[
  {odometer:25230,location_address:'Verenlandweg 4, 7461 AP Rijssen',date_label:'7 oktober 2026',time_label:'08:00'},
  {odometer:25246,location_address:'Van Broekhuizenstraat 4, 7461 VW Rijssen',date_label:'7 oktober 2026',time_label:'09:00'},
  {odometer:25271,location_address:'Verenlandweg 4, 7461 AP Rijssen',date_label:'7 oktober 2026',time_label:'10:00'}
]}});
assert.equal($('tripSuccessDistance').textContent,'41 km');
assert.equal($('tripSuccessStartOdo').textContent,'25.230 km');
assert.equal($('tripSuccessEndOdo').textContent,'25.271 km');
assert.equal($('tripSuccessModal').opened,true);

console.log('Release 33 UI: large physical input, Dutch notation, explicit confirm, 41 km finish, hidden GPS, viewport/rotation persistence and success screen PASS');
