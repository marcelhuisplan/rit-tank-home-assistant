// Offline interaction tests; no layout engine or live Google services.
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(__dirname+'/app.py','utf8');
const html=source.match(/APP_HTML = r'''([\s\S]*?)'''/)[1];
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
const frames=[],timers=new Map();let nextTimer=0;
function node(){return {children:[],value:'',textContent:'',style:{},dataset:{},classList:{toggle(){}},appendChild(x){this.children.push(x)},set innerHTML(x){this.children=[]},get innerHTML(){return ''}}}
const nodes=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],node()]));
const get=id=>{assert(nodes.has(id),id);return nodes.get(id)};
const ctx=vm.createContext({$:get,document:{createElement:node},DATA:{settings:{currency:'€'},latest_fuel:{liters:40,price_per_liter:1.899}},wheels:{},requestAnimationFrame:f=>frames.push(f),setTimeout:f=>{let n=++nextTimer;timers.set(n,f);return n},clearTimeout:n=>timers.delete(n),fmt:(v,n)=>Number(v).toFixed(n),money:v=>Number(v).toFixed(2),toast(){},receiptScanImage:async()=>'',FUEL_PLACE:{place_id:'old'},RECEIPT_REQUEST:0,RECEIPT_SCANNING:false,localStorage:{getItem:()=> 'device_tracker.old'},openModal(){},closeModal(){}});
function section(start,end){vm.runInContext(script.slice(script.indexOf(start),script.indexOf(end,script.indexOf(start))),ctx)}
function single(name){const line=script.split('\n').find(x=>x.startsWith('function '+name+'(')||x.startsWith('async function '+name+'('));assert(line,name);vm.runInContext(line,ctx)}
const flush=()=>{while(frames.length)frames.shift()();for(const f of timers.values())f();timers.clear()};
section('function createWheel(', 'function initOdometerWheel(');
single('initFuelWheels');single('fuelValues');single('updateFuelTotal');
section('async function scanSelectedReceipt(', "$('fuelReceipt').addEventListener");
single('savedLocationFallback');single('migrateLocationFallback');single('loadLocationEntities');
(async()=>{
 ctx.initFuelWheels();flush();
 const oldWheel=ctx.wheels.literWhole;get('literWhole').scrollTop=100;get('literWhole').onscroll();
 ctx.initFuelWheels(32.45,1.9996);flush();oldWheel.select(2);
 assert.equal(ctx.fuelValues().liters,32.45);assert.equal(ctx.fuelValues().price,2);
 ctx.initFuelWheels(41.99,2.009);flush();assert.equal(ctx.fuelValues().liters,41.99);
 get('fuelReceipt').files=[{}];get('fuelDate').value='2026-09-19T12:00';
 ctx.api=async()=>({receipt:{liters:28.75,station:'New station'}});
 await ctx.scanSelectedReceipt();flush();assert.equal(ctx.fuelValues().liters,28.75);assert.equal(ctx.fuelValues().price,2.009);assert.equal(ctx.FUEL_PLACE,null);
 assert(get('receiptScanStatus').textContent.includes('Niet herkend: literprijs'));
 // A later scan wins even if an earlier response arrives last.
 let pending=[];ctx.api=()=>new Promise(resolve=>pending.push(resolve));
 let first=ctx.scanSelectedReceipt();await new Promise(setImmediate);let second=ctx.scanSelectedReceipt();await new Promise(setImmediate);
 pending[1]({receipt:{liters:55.25,price_per_liter:1.889}});await second;flush();
 pending[0]({receipt:{liters:10,price_per_liter:2.999}});await first;flush();
 assert.equal(ctx.fuelValues().liters,55.25);assert.equal(ctx.fuelValues().price,1.889);
 let saved;ctx.api=async(p,o)=>{saved=JSON.parse(o.body);return {ok:true}};
 await ctx.migrateLocationFallback();assert.equal(saved.location_fallback_entity,'device_tracker.old');
 ctx.DATA.settings.location_fallback_entity='device_tracker.persisted';ctx.api=async()=>{throw Error('HA unavailable')};
 await ctx.loadLocationEntities();assert.equal(get('setLocationEntity').value,'device_tracker.persisted');assert.equal(get('setLocationEntity').children[0].value,'device_tracker.persisted');
 ctx.DATA.settings.location_fallback_entity='';assert.equal(ctx.savedLocationFallback(),'');
 // Native sharing gets a prepared File in the same click, and cancellation is harmless.
 ctx.PDF_EXPORT={file:{name:'test.pdf'}};let shared;ctx.navigator={share:async x=>{shared=x}};
 single('sharePdf');await ctx.sharePdf();assert.equal(shared.files[0].name,'test.pdf');
 assert(html.indexOf('id="fuelStepScan"')<html.indexOf('id="fuelStepOdo"'));
 console.log('PASS: wheel races/rounding, repeated/partial/stale OCR responses, fallback migration/outage/clear, prepared PDF share.');
})().catch(e=>{console.error(e);process.exitCode=1});
