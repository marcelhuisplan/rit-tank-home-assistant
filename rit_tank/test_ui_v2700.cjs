// Execute actual declaration UI logic in memory; no browser automation.
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/app.py','utf8'),elements=new Map(),calls=[],opened=[],downloads=[];
function element(){return {value:'',textContent:'',innerHTML:'',disabled:false,children:[],classList:{contains:()=>true},appendChild(x){this.children.push(x)},remove(){},click(){downloads.push(this.href)}}}
const $=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)};
let answer={status:'ok',period:{label:'September 2026'},summary:{row_count:10,business_km:100,reimbursement:'35.00',errors:0,warnings:0,clean_rows:10},issues:[]},deferred;
const ctx=vm.createContext({$,console,setTimeout,clearTimeout,document:{createElement:element,body:element()},URL:{createObjectURL:()=> 'blob:csv',revokeObjectURL(){}},api:async path=>{calls.push(path);if(deferred)return new Promise(r=>deferred=r);return structuredClone(answer)},fetch:async url=>{calls.push(url);return {ok:true,blob:async()=>({})}},fmt:n=>String(n),esc:String,escAttr:String,openModal:id=>opened.push(id),closeModal(){},openPdfExport:url=>calls.push(url),openTripEdit:(id,stop)=>calls.push([id,stop]),toast:m=>{throw Error(m)}});
const start=source.indexOf('let REPORT_CHECK='),end=source.indexOf('function closePdfPreview',start);
assert.ok(end>start);
vm.runInContext(source.slice(start,end),ctx);
(async()=>{
 $('pdfYear').value=2026;$('pdfMonth').value=9;$('pdfRange').value='month';
 await vm.runInContext('runReportValidation()',ctx);assert.equal($('reportExportButton').disabled,false);assert.match($('reportResults').innerHTML,/Alles in orde/);
 vm.runInContext('confirmPdfPeriod()',ctx);assert.equal(calls.at(-1),'api/business.pdf?period=month&year=2026&month=9');
 answer.status='warning';answer.summary.warnings=2;answer.issues=[{severity:'warning',row_number:14,title:'Afstand is 0,0 km',km:0,start_address:'Adres',end_address:'Adres',explanation:'Controleer',fixable:true,trip_id:42,destination_stop_id:84}];
 await vm.runInContext('runReportValidation()',ctx);vm.runInContext('confirmPdfPeriod()',ctx);assert.equal(opened.at(-1),'reportWarningModal');assert.match($('reportWarningText').textContent,/2 aandachtspunten/);
 $('reportResults').children.at(-1).children[0].onclick();assert.deepEqual(calls.at(-1),[42,84]);
 await vm.runInContext('generateCheckedReport(true)',ctx);assert.match(calls.at(-1),/allow_warnings=true$/);
 vm.runInContext("REPORT_FORMAT='csv'",ctx);await vm.runInContext('runReportValidation()',ctx);vm.runInContext('confirmPdfPeriod()',ctx);assert.match($('reportWarningText').textContent,/CSV/);await vm.runInContext('generateCheckedReport(true)',ctx);assert.match(calls.at(-1),/business.csv.*allow_warnings=true/);assert.equal(downloads.length,1);
 answer.status='error';answer.summary.errors=1;await vm.runInContext('runReportValidation()',ctx);assert.equal($('reportExportButton').disabled,true);const count=calls.length;vm.runInContext('confirmPdfPeriod()',ctx);assert.equal(calls.length,count);
 // A stale validation result must not enable export for a different period.
 deferred=true;const pending=vm.runInContext('runReportValidation()',ctx);const resolve=deferred;deferred=null;$('pdfYear').value=0;await vm.runInContext('runReportValidation()',ctx);resolve(answer);await pending;assert.equal($('reportExportButton').disabled,true);assert.match($('pdfPeriodError').textContent,/geldig jaar/);
 // The existing edit modal saves only changed stop fields and refreshes the selected report.
 const editStart=source.indexOf('let EDIT_STOPS='),editEnd=source.indexOf('\nfunction ',source.indexOf('async function saveTripEdit',editStart));
 vm.runInContext(source.slice(editStart,editEnd),ctx);let saved;
 ctx.reloadData=()=>{};ctx.toast=()=>{};
 const stop={id:84,odometer:1000,created_at:'2026-09-01T08:00:00+02:00',report_address:'Verenlandweg 4, 7461 AP Rijssen'};
 ctx.api=async(path,opts)=>{calls.push(path);if(opts){saved=JSON.parse(opts.body);return {ok:true}}if(path.endsWith('/edit'))return {trip:{id:42},stops:[stop]};return answer};
 await vm.runInContext('openTripEdit(42)',ctx);
 $('editAddress84').value=stop.report_address;$('editOdo84').value='1010';$('editTime84').value=stop.created_at.slice(0,16);$('pdfYear').value=2026;
 await vm.runInContext('saveTripEdit()',ctx);assert.deepEqual(saved.stops,[{id:84,odometer:'1010'}]);assert.match(calls.at(-1),/business\/validate/);
 console.log('Declaration UI: clean, errors, warnings, PDF/CSV opt-in, correction target and stale response checks passed');
})().catch(e=>{console.error(e);process.exit(1)});
