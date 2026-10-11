// Historical receipt confirmation must be explicit, show the neighbor and keep the same payload.
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/app.py','utf8');
assert.match(source,/id="fuelConflictWarning" hidden role="alert"/);
assert.match(source,/onclick="saveFuelConfirmed\(\)"/);
assert.match(source,/Kilometerconflict — afstand\/verbruik uitgesloten/);
const a=source.indexOf('let FUEL_CONFLICT_PAYLOAD=null;'),b=source.indexOf('\nfunction openKm()',a);
assert.ok(a>0&&b>a);
const fuelStart=source.indexOf('let LAST_FUEL_PRICE=null;'),fuelEnd=source.indexOf('function openFuel(){',fuelStart);
assert.ok(fuelStart>0&&fuelEnd>fuelStart,'real 33.15 digit helpers must be loaded');
const nodes=new Map(),requests=[],toasts=[];
const $=id=>{if(!nodes.has(id))nodes.set(id,{value:'',hidden:true,checked:false,innerHTML:'',textContent:''});return nodes.get(id)};
$('fuelDate').value='2026-10-05T11:30';
$('fuelStation').value='Tankstation';$('fuelFull').checked=true;
const ctx=vm.createContext({
 $,console,JSON,Number,Date,Array,Promise,String,Math,document:{activeElement:null},
 DATA:{settings:{currency:'€'},latest_fuel:null},FUEL_LOCATION:null,FUEL_PLACE:null,RECEIPT_SCANNING:false,
 readReceiptFile:async()=>'',esc:s=>String(s),
 fmt:(v)=>Number(v).toLocaleString('nl-NL'),guideTo:()=>{},toast:(s)=>toasts.push(s),
 closeModal:id=>{ctx.closed=id},reloadData:()=>{ctx.reloaded=true},money:v=>'€ '+v,
 api:async(path,opts)=>{
  const payload=JSON.parse(opts.body);requests.push(payload);
  if(requests.length===1){
   const e=new Error('Kilometerconflict');e.code='FUEL_KILOMETER_CONFLICT';
   e.confirmation_key='preview-key';e.conflicts=[{
    direction:'eerdere',id:7,type:'odometer',created_at:'2026-10-05T11:00:00+02:00',
    odometer:24763,message:'Kilometerstand is lager dan de vorige registratie (24763 km).'
   }];throw e;
  }
  return {cost:48.58,kilometer_conflict:true,receipt:false};
 }
});
vm.runInContext(source.slice(fuelStart,fuelEnd),ctx);
vm.runInContext(source.slice(a,b),ctx);
vm.runInContext("fuelSetDigits('odometer','24553');fuelSetDigits('liters','2430');fuelSetDigits('price','1999')",ctx);
assert.equal($('fuelOdo').value,'24553');
assert.equal($('fuelLitersInput').value,'24,30');
assert.equal($('fuelPriceInput').value,'1,999');
(async()=>{
 await ctx.saveFuel();
 assert.equal(requests.length,1);
 assert.equal(requests[0].confirm_kilometer_conflict,undefined);
 assert.equal(requests[0].odometer,24553);
 assert.equal(requests[0].liters,24.3);
 assert.equal(requests[0].price_per_liter,1.999);
 assert.equal($('fuelConflictWarning').hidden,false);
 assert.match($('fuelConflictDetails').innerHTML,/Eerdere kilometerregistratie/);
 assert.match($('fuelConflictDetails').innerHTML,/24.763 km/);
 assert.match($('fuelConflictDetails').innerHTML,/2026/);
 assert.equal(ctx.closed,undefined);
 // Changing all three visible fields after the warning must not replace the reviewed payload.
 vm.runInContext("fuelSetDigits('odometer','99999');fuelSetDigits('liters','5000');fuelSetDigits('price','2400')",ctx);
 await ctx.saveFuelConfirmed();
 assert.equal(requests.length,2);
 assert.equal(requests[1].odometer,24553);
 assert.equal(requests[1].liters,24.3);
 assert.equal(requests[1].price_per_liter,1.999);
 assert.equal(requests[1].station,'Tankstation');
 assert.equal(requests[1].created_at,'2026-10-05T11:30');
 assert.equal(requests[1].confirm_kilometer_conflict,true);
 assert.equal(requests[1].conflict_confirmation_key,'preview-key');
 assert.deepEqual(Object.fromEntries(Object.entries(requests[1]).filter(([key])=>!['confirm_kilometer_conflict','conflict_confirmation_key'].includes(key))),requests[0]);
 assert.equal($('fuelConflictWarning').hidden,true);
 assert.equal(ctx.closed,'fuelModal');
 assert.equal(ctx.reloaded,true);
 assert.match(toasts.at(-1),/kilometerconflict/);
 console.log('Historical fuel conflict UI: explicit preview, labeled neighbor, stable confirmation PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
