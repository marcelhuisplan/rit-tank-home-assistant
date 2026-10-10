// Release 33.15: raw digit input, backspace, proposal replacement and exact money.
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/app.py','utf8');
const begin=source.indexOf('let LAST_FUEL_PRICE=null;'),end=source.indexOf('function openFuel(){',begin);
assert.ok(begin>=0&&end>begin);
const modal=source.slice(source.indexOf('<div class="modal" id="fuelModal">'),source.indexOf('<div class="modal" id="kmModal">'));
assert.ok(!modal.includes('class="wheel"')&&!modal.includes('guideTo('));
const make=()=>({value:'',textContent:'',selectionStart:0,selectionEnd:0,
 setSelectionRange(a,b){this.selectionStart=a;this.selectionEnd=b},select(){this.selectionStart=0;this.selectionEnd=this.value.length},focus(){}});
const els={fuelOdo:make(),fuelLitersInput:make(),fuelPriceInput:make(),fuelInputError:make(),fuelTotal:make(),fuelOdoLast:make()};
const ctx=vm.createContext({Number,String,Math,localStorage:{getItem:()=>null,setItem(){}},
 document:{activeElement:null,getElementById:id=>els[id]},
 DATA:{settings:{currency:'€'},latest_fuel:{liters:43.75,price_per_liter:2.409},current_odometer:26199},
 fmt:(v,d)=>Number(v).toLocaleString('nl-NL',{minimumFractionDigits:d,maximumFractionDigits:d})});
vm.runInContext('const $=id=>document.getElementById(id);\n'+source.slice(begin,end),ctx);
const run=expr=>vm.runInContext(expr,ctx);
run('initFuelInputs()');
assert.equal(els.fuelOdo.value,'26199');
assert.equal(els.fuelLitersInput.value,'43,75');
assert.equal(els.fuelPriceInput.value,'2,409');
for(const [d,v] of [['4300','43,00'],['4375','43,75'],['2480','24,80'],['5','0,05'],['50','0,50']]){
 run("fuelSetDigits('liters','"+d+"')");assert.equal(els.fuelLitersInput.value,v);
}
for(const [d,v] of [['2400','2,400'],['2409','2,409'],['1899','1,899'],['5','0,005']]){
 run("fuelSetDigits('price','"+d+"')");assert.equal(els.fuelPriceInput.value,v);
}
run("fuelSetDigits('liters','4300');fuelSetDigits('price','2400')");
assert.equal(els.fuelTotal.textContent,'€ 103,20');
run("fuelSetDigits('liters','4301')");
assert.equal(els.fuelTotal.textContent,'€ 103,22');
const edit=(kind,type,data,selectAll=false)=>{
 let el=els[kind==='price'?'fuelPriceInput':'fuelLitersInput'];
 el.selectionStart=selectAll?0:el.value.length;el.selectionEnd=el.value.length;
 let prevented=false;ctx._event={cancelable:true,inputType:type,data,currentTarget:el,preventDefault(){prevented=true}};
 run("fuelBeforeInput(_event,'"+kind+"')");assert.equal(prevented,true);
};
run("fuelSetDigits('liters','')");
for(const c of '4375')edit('liters','insertText',c);
assert.equal(els.fuelLitersInput.value,'43,75');
edit('liters','deleteContentBackward',null);assert.equal(els.fuelLitersInput.value,'4,37');
edit('liters','deleteContentBackward',null);assert.equal(els.fuelLitersInput.value,'0,43');
edit('liters','insertText','0');assert.equal(els.fuelLitersInput.value,'4,30');
run("fuelSetDigits('price','2409')");
edit('price','insertText','5',true);assert.equal(els.fuelPriceInput.value,'0,005');
run("fuelSetDigits('price','2400')");edit('price','insertText','5');
assert.equal(els.fuelPriceInput.value,'2,400');
assert.match(els.fuelInputError.textContent,/maximaal/);
run("fuelSetDigits('odometer','26199')");assert.equal(els.fuelOdo.value,'26199');
assert.equal(run("fuelSetDigits('liters','25001')"),false);
assert.equal(els.fuelLitersInput.value,'4,30');
console.log('33.15 raw digits, rapid typing, Backspace, proposals, total and validation PASS');
