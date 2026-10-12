// Regression: GPS-street house number search never uses nationwide Google fallback or auto-save.
'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(__dirname+'/app.py','utf8');
const start=html.indexOf('let TRIP_SEARCH_TIMER='),end=html.indexOf('// Physical odometer flow:',start);
assert.ok(start>=0&&end>start);
assert.ok(html.includes("if path == '/api/location/house-number'"));
assert.ok(html.includes("if(id==='tripModal'){clearTimeout(TRIP_HOUSE_TIMER);++TRIP_HOUSE_REQUEST"));
assert.ok(html.includes('id="tripHouseNumber"'));
assert.ok(html.includes('Andere straat of plaats zoeken'));
const nodes=new Map(),calls=[],deferred=[];
function element(){
 return {value:'',innerHTML:'',textContent:'',hidden:false,style:{},children:[],
  classList:{toggle(){},add(){},remove(){}},events:{},addEventListener(type,cb){this.events[type]=cb},
  appendChild(item){this.children.push(item)},focus(){}};
}
const $=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)};
const gps={address:'Voorste Land 1, Gieten',latitude:53.0,longitude:6.75,distance_m:10};
let accuracy=8;
const ctx=vm.createContext({$,console,setTimeout,clearTimeout,Number,
 TRIP_MODE:'start',TRIP_LOCATION:null,TRIP_ODO_MANUAL:false,TRIP_SEGMENT_TYPE:'business',
 document:{createElement:element},guideTo(){},fmt:n=>String(n),toast(){},
 initOdometerWheel(){},resolveLocation:async()=>({latitude:53,longitude:6.75,accuracy}),
 api:async(path,options)=>{
  calls.push({path,payload:options&&options.body?JSON.parse(options.body):null});
  if(path==='api/location/addresses')return {street:'Voorste Land',town:'Gieten',street_context:'opaque-client-context-with-entropy-123',addresses:[gps]};
  if(path==='api/location/house-number')return new Promise(resolve=>deferred.push(resolve));
  if(path==='api/business/route-preview')return {};
  if(path==='api/places/search-address')return {places:[]};
  throw new Error('Unexpected path '+path);
 }
});
vm.runInContext(html.slice(start,end),ctx);
(async()=>{
 await ctx.captureTripLocation();
 assert.equal($('tripHouseSearchBox').hidden,false);
 assert.equal($('tripHouseLabel').textContent,'Huisnummer zoeken in Voorste Land, Gieten');
 assert.equal($('tripAddressChoices').children.length,1,'nearby suggestions preserved');
 assert.equal($('tripManualBox').hidden,true);
 $('tripHouseNumber').value='7';
 const pending=ctx.searchTripHouseNumber();
 assert.equal(calls.at(-1).path,'api/location/house-number');
 assert.deepEqual(Object.keys(calls.at(-1).payload).sort(),['house_number','street_context']);
 deferred.shift()({addresses:[{address:'Voorste Land 7, Gieten',latitude:53.01,longitude:6.76}]});
 await pending;
 assert.equal($('tripHouseResults').children.length,1);
 assert.equal($('tripAddressChoices').children.length,1);
 assert.equal(ctx.TRIP_LOCATION,null,'search alone never confirms the location');
 $('tripHouseResults').children[0].onclick();
 assert.equal(ctx.TRIP_LOCATION.address,'Voorste Land 7, Gieten');
 assert.equal(ctx.TRIP_LOCATION.latitude,53.01);
 assert.equal(ctx.TRIP_LOCATION.longitude,6.76);
 assert.ok(!calls.some(v=>v.path.includes('/start')||v.path.includes('/stop')||v.path.includes('/finish')),'no auto-save');

 await ctx.captureTripLocation();
 $('tripHouseNumber').value='9';
 const stale=ctx.searchTripHouseNumber();
 await ctx.captureTripLocation();
 deferred.shift()({addresses:[{address:'Stale 9',latitude:1,longitude:1}]});
 await stale;
 assert.equal($('tripHouseResults').children.length,0,'new GPS rejects old response');
 $('tripHouseNumber').value='10';
 const staleTerm=ctx.searchTripHouseNumber();
 $('tripHouseNumber').events.input();
 deferred.shift()({addresses:[{address:'Stale 10',latitude:1,longitude:1}]});
 await staleTerm;
 assert.equal($('tripHouseResults').children.length,0,'changed term rejects old response');
 $('tripHouseNumber').value='11';
 const staleManual=ctx.searchTripHouseNumber();
 ctx.enableTripManualSearch();
 deferred.shift()({addresses:[{address:'Stale 11',latitude:1,longitude:1}]});
 await staleManual;
 assert.equal($('tripManualBox').hidden,false);
 assert.equal($('tripHouseResults').children.length,0);
 assert.equal($('tripHouseSearchBox').hidden,true);
 assert.equal(calls.filter(x=>x.path==='api/places/search-address').length,0,'manual fallback requires explicit typing');
 accuracy=400;await ctx.captureTripLocation();
 assert.equal($('tripHouseSearchBox').hidden,true,'uncertain GPS has no scoped search');
 assert.match($('tripLocationDetail').textContent,/onnauwkeurig/);
 console.log('Scoped house number UI: full BAG label, actual coordinates, no auto-save, async cancellation and manual fallback PASS');
})().catch(e=>{console.error(e);process.exit(1)});
