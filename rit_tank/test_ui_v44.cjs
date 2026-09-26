// Offline UI-logic checks with a minimal DOM; not a browser rendering test.
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const source = fs.readFileSync(__dirname + '/app.py', 'utf8');
const html = source.match(/APP_HTML = r'''([\s\S]*?)'''/)[1];
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(x=>x[1]).join('\n');
new vm.Script(script);
const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]);
assert.equal(ids.length, new Set(ids).size, 'Duplicate HTML IDs');
const nodes = new Map(ids.map(id=>[id, {value:'',textContent:'',style:{},checked:false,hidden:false,disabled:false,
  classList:{add(){},remove(){},toggle(){},contains(){return true}},scrollIntoView(){}}]));
let sent = [], message = '', arrival;
const context = vm.createContext({
  $:id=>{assert(nodes.has(id), 'Missing ID '+id);return nodes.get(id)},
  DATA:{business:{active_trip:null},current_odometer:63700}, ASSISTANT_ITEM:null,ASSISTANT_TYPE:'',
  api:async(path,opts)=>{if(opts){sent.push(JSON.parse(opts.body));return {ok:true}}return {arrivals:[arrival]}},
  toast:m=>{message=m},reloadData:async()=>{},updateAssistantRouteUI(){},setAssistantType:t=>{},
  initOdometerWheel:(prefix,n)=>{nodes.get(prefix+'Odo').value=n},
  openModal(){},closeModal(){},fmt:(v,d)=>Number(v).toFixed(d),setTimeout:f=>f(),guideTo(){},
});
let start = script.indexOf('async function openAssistantComplete('), end = script.indexOf('async function saveAssistantArrival(',start);
vm.runInContext(script.slice(start,end),context);
start = end; end = script.indexOf('\nfunction ',start);
vm.runInContext(script.slice(start,end),context);
start=script.indexOf('function showTripOdoProposal(');end=script.indexOf('\nfunction acceptTripOdoSuggestion',start);
vm.runInContext(script.slice(start,end),context);
(async()=>{
  arrival={id:1,is_next_to_review:true,origin_name:'Thuis',destination_name:'Klant',date_label:'18-09 16:00',suggested_type:'business',suggestion_confidence:.98,
    proposal:{start_odometer:63700,suggested_odometer:63719,gps_km:18.7,route_complete:true,calibration:{ready:false}}};
  await context.openAssistantComplete(1);
  assert.equal(nodes.get('arrivalEditor').hidden,true);
  assert.equal(nodes.get('assistantOdo').value,63719);
  assert.equal(nodes.get('arrivalOdoChecked').checked,false);
  assert.equal(nodes.get('arrivalSave').textContent,'✓ Alles akkoord');
  await context.saveAssistantArrival();
  assert.equal(sent[0].odometer,63719);
  assert.equal(sent[0].odometer_checked,false,'Accepting a proposal must not train');
  await context.openAssistantComplete(1);context.editArrivalProposal();
  assert.equal(nodes.get('arrivalEditor').hidden,false);
  nodes.get('assistantOdo').value=63720;nodes.get('arrivalOdoChecked').checked=true;
  await context.saveAssistantArrival();assert.equal(sent[1].odometer,63720);assert.equal(sent[1].odometer_checked,true);
  arrival.proposal.suggested_odometer=null;arrival.proposal.route_complete=false;
  await context.openAssistantComplete(1);
  assert.equal(nodes.get('arrivalEditor').hidden,false);
  assert.notEqual(nodes.get('arrivalSave').textContent,'✓ Alles akkoord');
  assert.equal(context.showTripOdoProposal({active:true,suggested_odometer:null,tracked_km:18}),false);
  assert.equal(context.showTripOdoProposal({active:true,suggested_odometer:63719,tracked_km:18.7,sample_count:12}),true);
  assert.equal(nodes.get('tripOdo').value,63719);
  let popups=0,finishOpened=false;
  context.document={hidden:false,querySelector:()=>null};
  context.openModal=()=>{popups++};context.openTripPoint=()=>{finishOpened=true};
  const prompt={id:'test-stop',trip_id:42,province:'Overijssel',address:'Zwolle',created_at:new Date().toISOString()};
  context.api=async()=>({active:true,trip_id:42,stop_prompt:prompt});
  const stopStart=script.indexOf('let STOP_PROMPT_BUSY='),stopEnd=script.indexOf('setInterval(checkStopPrompt',stopStart);
  vm.runInContext(script.slice(stopStart,stopEnd),context);
  await context.checkStopPrompt();await context.checkStopPrompt();assert.equal(popups,1);
  context.api=async()=>({active:true,trip_id:43});
  await context.finishFromStopPrompt();assert.equal(finishOpened,false,'Old prompt must not finish a different trip');
  console.log('UI logic OK: accept, edit, training consent, missing GPS, HTML IDs and JavaScript syntax.');
})().catch(e=>{console.error(e);process.exitCode=1});
