// Run the actual correction UI with mocked Google responses, including touch and races.
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/app.py','utf8'),elements=new Map(),calls=[],toasts=[];
function element(){return {value:'',textContent:'',disabled:false,children:[],style:{},classList:{contains:()=>false},blur(){this.blurred=true},scrollIntoView(){},appendChild(x){this.children.push(x)},set innerHTML(v){this.html=v;this.children=[]},get innerHTML(){return this.html||''}}}
const $=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)};
const stops=[1,2,3].map(id=>({id,edit_address:id===2?'Wierdens':'Oudstraat 1, 1234 AB Plaats',odometer:100+id,created_at:'2026-09-01T08:00:00+02:00'}));
let results=[{place_id:'first',address:'Verenlandweg 4, 7461 AP Rijssen'},{place_id:'second',address:'Stationsstraat 1, 7461 AA Rijssen'}],saved,fail=false,delayed;
const viewport={offsetTop:20,height:360,addEventListener(){}};
const ctx=vm.createContext({window:{visualViewport:viewport},$,console,setTimeout,clearTimeout,document:{createElement:element},escAttr:String,openModal(){},closeModal(){},reloadData(){},toast:m=>toasts.push(m),api:async(path,opts)=>{
 calls.push(path);if(path==='api/places/search-address'){if(fail)throw Error('offline');if(delayed)return new Promise(r=>delayed=r);return {places:results}}
 if(opts){saved=JSON.parse(opts.body);return {ok:true}}return {trip:{id:42},stops:structuredClone(stops)}
}});
const start=source.indexOf('let EDIT_STOPS='),end=source.indexOf('\nfunction showTripOdoProposal',start);
vm.runInContext(source.slice(start,end),ctx);
function fill(){for(const s of stops){$('editAddress'+s.id).value=s.edit_address;$('editOdo'+s.id).value=String(s.odometer);$('editTime'+s.id).value=s.created_at.slice(0,16)}}
async function run(code){return vm.runInContext(code,ctx)}
(async()=>{
 await run('openTripEdit(42)');fill();assert.equal($('tripEditModal').style.height,'360px');assert.equal($('tripEditModal').style.top,'20px');assert.match($('editTripStops').innerHTML,/Wierdens/);assert.match($('editTripStops').innerHTML,/autocomplete="off"/);assert.match($('editTripStops').innerHTML,/oninput="editAddressInput/);
 await run('saveTripEdit()');assert.deepEqual(saved.stops,[]);assert.equal(calls.filter(x=>x.includes('search-address')).length,0);
 // Typing does not select, even a complete-looking address; all stop positions use the same flow.
 for(const id of [1,2,3]){
   $('editAddress'+id).value='Wierdens';await run(`editAddressInput(${id})`);
   if(id===2)$('editAddress'+id).value='Wierden';
   saved=null;await run('saveTripEdit()');assert.equal(saved,null);
   await run(`searchEditAddress(${id})`);assert.equal($('editChoices'+id).children.length,2);
   const choice=$('editChoices'+id).children[1];let prevented=false;choice.onpointerdown({preventDefault(){prevented=true}});assert.equal(prevented,true);choice.onclick();
   assert.equal($('editAddress'+id).value,results[1].address);assert.equal($('editAddress'+id).blurred,true);
   await run('saveTripEdit()');assert.equal(saved.stops.find(s=>s.id===id).place_id,'second');assert.equal(saved.stops.find(s=>s.id===id).address,results[1].address);
 }
 // Editing after selection invalidates the choice immediately.
 $('editAddress1').value='Rijssen';await run('editAddressInput(1)');assert.equal($('editTripSave').disabled,true);saved=null;await run('saveTripEdit()');assert.equal(saved,null);
 fail=true;await run('searchEditAddress(1)');assert.match($('editStatus1').textContent,/oorspronkelijke adres blijft bewaard/);assert.equal($('editChoices1').children.length,0);fail=false;
 // Old responses and old clickable results must not win after more typing or another trip is opened.
 delayed=true;const pending=run('searchEditAddress(1)'),resolve=delayed;delayed=null;$('editAddress1').value='new query';await run('editAddressInput(1)');resolve({places:results});await pending;assert.equal($('editChoices1').children.length,0);
 await run('searchEditAddress(1)');const oldButton=$('editChoices1').children[0];await run('openTripEdit(42)');fill();oldButton.onclick();assert.equal($('editAddress1').value,stops[0].edit_address);
 // Refocusing an unchanged historical value only searches; it never silently chooses a result.
 await run('searchEditAddress(2)');await run('saveTripEdit()');assert.deepEqual(saved.stops,[]);
 console.log('Google correction UI: unchanged, typing, all stop selections, touch, errors, stale requests and modal lifecycle passed');
})().catch(e=>{console.error(e);process.exitCode=1});
