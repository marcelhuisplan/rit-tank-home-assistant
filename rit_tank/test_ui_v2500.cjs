// In-memory execution of actual location functions; no browser automation.
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/app.py','utf8'),elements=new Map(),calls=[];
function element(){return {value:'',textContent:'',innerHTML:'',hidden:false,disabled:false,style:{},children:[],classList:{add(){},toggle(){}},appendChild(x){this.children.push(x)},addEventListener(){}}}
const $=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)};
const home={address:'Verenlandweg 4, 7461 AP Rijssen',latitude:52.3,longitude:6.2,place_id:'home-id'};
let searchResolve;
const ctx=vm.createContext({$,console,Number,clearTimeout,setTimeout,TRIP_MODE:'start',TRIP_LOCATION:null,TRIP_SEGMENT_TYPE:'business',TRIP_ODO_MANUAL:false,document:{createElement:element},guideTo(){},fmt:n=>String(n),toast(m){throw Error(m)},initOdometerWheel:(p,n)=>$(p+'Odo').value=n,api:async(path,opts)=>{calls.push(path);if(path==='api/places/home')return home;if(path==='api/business/route-preview')return {distance_m:10000,suggested_odometer:110,distance_source:'route'};if(path==='api/places/search-address')return searchResolve?await new Promise(r=>searchResolve=r):{places:[home]};if(path==='api/location/addresses')return {addresses:[home]};throw Error('Unexpected request: '+path)},resolveLocation:async()=>({latitude:52.3,longitude:6.2})});
const start=source.indexOf('let TRIP_SEARCH_TIMER='),end=source.indexOf('async function saveTripPoint()',start);
vm.runInContext(source.slice(start,end),ctx);
(async()=>{
 $('tripOdo').value=100;
 await vm.runInContext('selectTripHome()',ctx);
 assert.equal(ctx.TRIP_LOCATION.address,home.address);assert.equal(ctx.TRIP_LOCATION.place_id,'home-id');assert.equal($('tripOdo').value,100);assert.deepEqual(calls,['api/places/home']);
 for(const mode of ['stop','finish']){ctx.TRIP_MODE=mode;await vm.runInContext('selectTripHome()',ctx);assert.equal($('tripOdo').value,110);assert.equal(calls.at(-1),'api/business/route-preview')}
 ctx.TRIP_MODE='start';$('tripManualAddress').value='Verenlandweg 4';$('tripAddressChoices').children=[];
 await vm.runInContext('searchTripManualAddress()',ctx);$('tripAddressChoices').children[0].onclick();
 assert.equal(ctx.TRIP_LOCATION.place_id,'home-id');assert.equal(ctx.TRIP_LOCATION.address,home.address);
 await vm.runInContext('captureTripLocation()',ctx);vm.runInContext('chooseTripAddress(0)',ctx);assert.equal(ctx.TRIP_LOCATION.address,home.address);
 searchResolve=true;$('tripManualAddress').value='Old query';const pending=vm.runInContext('searchTripManualAddress()',ctx);
 await vm.runInContext('selectTripHome()',ctx);searchResolve({places:[{address:'Stale address',latitude:1,longitude:1}]});await pending;
 assert.equal(ctx.TRIP_LOCATION.address,home.address);assert.equal($('tripManualAddress').value,home.address);
 assert.ok(calls.every(p=>!['api/business/start','api/business/stop','api/business/finish','api/events'].includes(p)));
 console.log('Location selection, GPS, Home, route and stale-search checks passed');
})().catch(e=>{console.error(e);process.exit(1)});
