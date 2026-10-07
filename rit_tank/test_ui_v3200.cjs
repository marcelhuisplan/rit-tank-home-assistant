// Release 32 in-memory regressions for fixed Home/Beatrixschool/GPS switching.
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/app.py','utf8'),nodes=new Map(),calls=[];
function element(){return {value:'',textContent:'',innerHTML:'',hidden:false,disabled:false,style:{},children:[],classList:{add(){},remove(){},toggle(){}},appendChild(x){this.children.push(x)},addEventListener(){},focus(){}}}
const $=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)};
const home={address:'Verenlandweg 4, 7461 AP Rijssen',latitude:52.315,longitude:6.528,place_id:'home-id',source:'google_places'};
const school={address:'Van Broekhuizenstraat 4, 7461 VW Rijssen',latitude:52.307,longitude:6.519,place_id:'school-id',source:'google_places'};
const gps={address:'GPSstraat 7, 7461 AA Rijssen',latitude:52.31,longitude:6.52,distance_m:8};
let searchResolve=null,gpsResolve=null,deferSearch=false,deferGps=false;
const ctx=vm.createContext({$,console,Number,clearTimeout,setTimeout,TRIP_MODE:'start',TRIP_LOCATION:null,TRIP_SEGMENT_TYPE:'business',TRIP_ODO_MANUAL:false,
 document:{createElement:element},guideTo(){},fmt:n=>String(n),toast(m){throw Error(m)},initOdometerWheel:(p,n)=>$(p+'Odo').value=n,
 api:async(path,opts)=>{
   calls.push(path);
   if(path==='api/places/home')return home;
   if(path==='api/places/beatrixschool')return school;
   if(path==='api/business/route-preview')return {distance_m:10000,suggested_odometer:110,distance_source:'route'};
   if(path==='api/places/search-address'){
     if(deferSearch)return await new Promise(r=>searchResolve=r);
     return {places:[gps]};
   }
   if(path==='api/location/addresses')return {street:'GPSstraat',addresses:[gps]};
   throw Error('Unexpected request: '+path);
 },
 resolveLocation:async()=>deferGps?await new Promise(r=>gpsResolve=r):({latitude:52.31,longitude:6.52,accuracy:5})
});
const start=source.indexOf('let TRIP_SEARCH_TIMER='),end=source.indexOf('// Physical odometer flow:',start);
vm.runInContext(source.slice(start,end),ctx);
(async()=>{
 assert.match(source,/id="tripSchoolButton"[^>]*>🏫 Beatrixschool<\/button>/);
 assert.match(source,/id="tripCurrentLocationButton"[^>]*>📍 Gebruik huidige locatie<\/button>/);

 await ctx.selectTripSchool();
 assert.equal(ctx.TRIP_LOCATION.address,school.address);assert.equal($('tripManualAddress').value,school.address);
 assert.equal(ctx.TRIP_SEGMENT_TYPE,'business');

 await ctx.selectTripHome();assert.equal(ctx.TRIP_LOCATION.address,home.address);
 await ctx.selectTripSchool();assert.equal(ctx.TRIP_LOCATION.address,school.address);

 await ctx.captureTripLocation();ctx.chooseTripAddress(0);
 assert.equal(ctx.TRIP_LOCATION.address,gps.address);

 deferGps=true;const pendingGps=ctx.captureTripLocation();
 await ctx.selectTripSchool();gpsResolve({latitude:1,longitude:1,accuracy:5});await pendingGps;
 assert.equal(ctx.TRIP_LOCATION.address,school.address,'Late GPS must not replace school');
 deferGps=false;

 deferSearch=true;$('tripManualAddress').value='Oud adres';const pendingSearch=ctx.searchTripManualAddress();
 await ctx.selectTripSchool();searchResolve({places:[{address:'Vertraagd adres 1, 1234 AB Stad',latitude:1,longitude:1}]});await pendingSearch;
 assert.equal(ctx.TRIP_LOCATION.address,school.address,'Late Places result must not replace school');
 assert.equal($('tripManualAddress').value,school.address);
 deferSearch=false;

 for(const mode of ['stop','finish']){
   ctx.TRIP_MODE=mode;await ctx.selectTripSchool();
   assert.equal(ctx.TRIP_LOCATION.address,school.address);
   assert.equal(calls.at(-1),'api/business/route-preview');
 }
 assert.ok(calls.every(p=>!['api/business/start','api/business/stop','api/business/finish'].includes(p)));
 console.log('Release 32 UI: School visible, canonical, Home/GPS switching, stale GPS/Places blocked, shared stop/finish route and no auto-save PASS');
})().catch(e=>{console.error(e);process.exit(1)});
