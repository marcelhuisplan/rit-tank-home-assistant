// Release 33.13: Dutch km/l presentation uses full-tank km and liters, not rounded L/100 km.
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/app.py','utf8');
for(const fragment of ['id="fullAvgVal"','id="fullAvgKmPerLiter"','id="kpiL100"','id="kpiKmPerLiter"']) {
 assert.ok(source.includes(fragment),'Missing dashboard element '+fragment);
}
assert.match(source,/\$\('kpiKmPerLiter'\)\.textContent=kmPerLiter\(f\)/);
assert.match(source,/\$\('fullAvgKmPerLiter'\)\.textContent=kmPerLiter\(f\)/);
const start=source.indexOf('function fmt(n,d=1)');
const end=source.indexOf('function localInputNow()',start);
assert.ok(start>0&&end>start,'Consumption formatting functions unavailable');
const ctx=vm.createContext({Number,Intl});
vm.runInContext(source.slice(start,end),ctx);
const show=f=>vm.runInContext('kmPerLiter('+JSON.stringify(f)+')',ctx);
assert.equal(show({km:1000,liters:59.4,l100:5.94}),'1 : 16,8');
assert.equal(show({km:1000,liters:31.55,l100:3.16}),'1 : 31,7',
 'Do not invert rounded 3.16, which gives 31.6');
assert.equal(show({km:600,liters:30,l100:5}),'1 : 20,0');
for(const input of [null,{}, {km:1000,liters:31.55,l100:null},
 {km:1000,liters:0,l100:0},{km:0,liters:30,l100:5},
 {km:'invalid',liters:30,l100:5},{km:100,liters:-5,l100:5}]) {
 assert.equal(show(input),'—','Invalid or absent reliable cycle must display a dash');
}
console.log('33.13 consumption unit/UI wiring OK');
