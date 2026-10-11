// Offline interaction tests; no layout engine or live Google services.
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(__dirname+'/app.py','utf8');
const html=source.match(/APP_HTML = r'''([\s\S]*?)'''/)[1];
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
const frames=[],timers=new Map();let nextTimer=0;
function node(){return {children:[],value:'',textContent:'',style:{},dataset:{},classList:{toggle(){}},appendChild(x){this.children.push(x)},set innerHTML(x){this.children=[]},get innerHTML(){return ''}}}
const nodes=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],node()]));
const get=id=>{assert(nodes.has(id),id);return nodes.get(id)};
const ctx=vm.createContext({$:get,document:{createElement:node,activeElement:null},DATA:{settings:{currency:'€'},latest_fuel:{liters:40,price_per_liter:1.899}},wheels:{},requestAnimationFrame:f=>frames.push(f),setTimeout:f=>{let n=++nextTimer;timers.set(n,f);return n},clearTimeout:n=>timers.delete(n),fmt:(v,n)=>Number(v).toFixed(n),money:v=>Number(v).toFixed(2),toast(){},receiptScanImage:async()=>'',FUEL_PLACE:{place_id:'old'},RECEIPT_REQUEST:0,RECEIPT_SCANNING:false,localStorage:{getItem:()=> 'device_tracker.old'},openModal(){},closeModal(){}});
function section(start,end){vm.runInContext(script.slice(script.indexOf(start),script.indexOf(end,script.indexOf(start))),ctx)}
function single(name){const line=script.split('\n').find(x=>x.startsWith('function '+name+'(')||x.startsWith('async function '+name+'('));assert(line,name);vm.runInContext(line,ctx)}
const flush=()=>{while(frames.length)frames.shift()();for(const f of timers.values())f();timers.clear()};
section('let LAST_FUEL_PRICE=null;', 'function openFuel(){');
assert(!script.includes('scanSelectedReceipt('));
single('savedLocationFallback');single('migrateLocationFallback');single('loadLocationEntities');
(async()=>{
 ctx.initFuelInputs();
 assert.equal(get('fuelLitersInput').value,'40,00');
 assert.equal(get('fuelPriceInput').value,'1,899');
 ctx.fuelSetDigits('liters','3245');ctx.fuelSetDigits('price','2000');
 assert.equal(ctx.fuelValues().liters,32.45);assert.equal(ctx.fuelValues().price,2);
 ctx.fuelSetDigits('liters','4199');ctx.fuelSetDigits('price','2009');
 assert.equal(ctx.fuelValues().liters,41.99);
 assert(html.includes('onclick="openReceiptArchive()"'));
 assert(!script.includes('receipt_data_url=await readReceiptFile()'));
 assert.equal(ctx.fuelValues().liters,41.99);
 assert.equal(ctx.fuelValues().price,2.009);
 let saved;ctx.api=async(p,o)=>{saved=JSON.parse(o.body);return {ok:true}};
 await ctx.migrateLocationFallback();assert.equal(saved.location_fallback_entity,'device_tracker.old');
 ctx.DATA.settings.location_fallback_entity='device_tracker.persisted';ctx.api=async()=>{throw Error('HA unavailable')};
 await ctx.loadLocationEntities();assert.equal(get('setLocationEntity').value,'device_tracker.persisted');assert.equal(get('setLocationEntity').children[0].value,'device_tracker.persisted');
 ctx.DATA.settings.location_fallback_entity='';assert.equal(ctx.savedLocationFallback(),'');
 // Native sharing gets a prepared File in the same click, and cancellation is harmless.
 ctx.PDF_EXPORT={file:{name:'test.pdf'}};let shared;ctx.navigator={share:async x=>{shared=x}};
 single('sharePdf');await ctx.sharePdf();assert.equal(shared.files[0].name,'test.pdf');
 assert(html.indexOf('id="fuelStepScan"')>html.indexOf('id="fuelStepFinish"'));
 assert(!html.slice(html.indexOf('id="fuelModal"'),html.indexOf('id="kmModal"')).includes('class="wheel"'));
 assert(!html.slice(html.indexOf('id="fuelModal"'),html.indexOf('id="kmModal"')).includes('guideTo('));
 // Remember physical selections without external OCR changes.
 let remembered={};ctx.localStorage={getItem:k=>remembered[k]??null,setItem:(k,v)=>remembered[k]=v};
 ctx.fuelSetDigits('price','2019');assert.equal(remembered.rit_tank_last_fuel_price,undefined);
 let chosen=ctx.fuelValues().price;ctx.rememberFuelPrice(chosen);
 assert.equal(remembered.rit_tank_last_fuel_price,'2.019');
 ctx.fuelSetDigits('price','2400');assert.equal(ctx.fuelValues().price,2.4);
 vm.runInContext('LAST_FUEL_PRICE=null',ctx);ctx.initFuelInputs();flush();
 assert.equal(ctx.fuelValues().price,chosen);
 assert.equal(get('fuelPriceInput').value,'2,019');
 assert(!html.slice(0,html.indexOf('id="fuelModal"')).includes('class="scan-card"'));
 assert(!html.includes('class="wheelbox liters"'));
 assert(html.includes('id="fuelLitersInput"'));
 assert(html.includes('id="fuelPriceInput"'));
 assert(html.includes('inputmode="numeric"'));
 assert(html.includes('class="fuel-clear"'));
 assert(html.includes('grid-template-columns:minmax(0,1fr) auto'));
 assert(script.includes("$('tripPurpose').value='klantbezoek'"));
 // Address confirmation scrolls immediately, even while a suggestion is pending.
 let target;ctx.guideTo=id=>target=id;ctx.TRIP_MODE='start';ctx.TRIP_SEGMENT_TYPE='';
 ctx.TRIP_SEARCH_TIMER=null;ctx.TRIP_ADDRESS_REQUEST=0;ctx.TRIP_ROUTE_PREVIEW_REQUEST=0;
 single('advanceTripAfterLocation');section('async function confirmTripAddress(', 'async function selectTripHome(');
 await ctx.confirmTripAddress('Voorbeeldstraat 4',{latitude:52,longitude:5});
 assert.equal(target,'tripSaveButton');
 let finishSuggestion;ctx.TRIP_MODE='stop';ctx.showTripSuggestion=()=>{};ctx.api=()=>new Promise(r=>finishSuggestion=r);
 let confirmation=ctx.confirmTripAddress('Voorbeeldstraat 6',{latitude:52,longitude:5});
 assert.equal(target,'tripSaveButton');ctx.TRIP_SEGMENT_TYPE='business';ctx.advanceTripAfterLocation();assert.equal(target,'tripSaveButton');
 finishSuggestion({suggested_type:'private'});await confirmation;assert.equal(ctx.TRIP_SEGMENT_TYPE,'business');
 // Scrolling targets the open sheet, not the underlying welcome screen.
 const sheet={scrollTop:100,getBoundingClientRect:()=>({top:100}),scrollTo:o=>{sheet.result=o.top}};
 const modal={classList:{contains:()=>true},querySelectorAll:()=>[]};
 get('tripSaveButton').closest=selector=>selector==='.modal'?modal:sheet;
 get('tripSaveButton').getBoundingClientRect=()=>({top:500});ctx.GUIDE_TIMER=null;
 single('guideTo');ctx.guideTo('tripSaveButton',0);flush();assert.equal(sheet.result,476);
 console.log('PASS: digit precision, PDF-only archive, no automatic fuel fields, fallback migration/outage/clear.');
 console.log('PASS: saved price memory, scanner placement, direct liters input and sheet scrolling.');
})().catch(e=>{console.error(e);process.exitCode=1});
