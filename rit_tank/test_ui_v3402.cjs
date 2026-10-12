// 34.02: bidirectional focus order and fallback UI regression.
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/app.py','utf8');
const modal=source.slice(source.indexOf('<div class="modal" id="fuelModal">'),source.indexOf('<div class="modal" id="kmModal">'));
const fields=['fuelOdo','fuelLitersInput','fuelPriceInput','fuelStation','fuelNote','fuelDate'];
const order=source.match(/const FUEL_FOCUS_ORDER=\[([^\]]+)\];/);
assert.ok(order,'explicit focus order');
assert.deepEqual(Array.from(order[1].matchAll(/'([^']+)'/g),m=>m[1]),fields);
assert.ok(modal.indexOf('id="fuelKeyboardNav"')<modal.indexOf('id="fuelStepOdo"'),'navigation remains in sticky header');
for(const [id,direction,caption] of [['fuelKeyboardPrev',-1,'↑ Vorig'],['fuelKeyboardNext',1,'↓ Volgend']])
 assert.ok(modal.includes('id="'+id+'" onpointerdown="event.preventDefault()" onclick="fuelFocusRelative('+direction+')">'+caption+'</button>'));
assert.ok(modal.includes('id="fuelKeyboardNav"')&&modal.includes('hidden'),'navigation initially hidden');
assert.ok(source.includes('fuelFocusNext(FUEL_FOCUS_ORDER[target])'));
assert.ok(source.includes('next.focus({preventScroll:true})'));
assert.ok(source.includes('function fuelRevealFocused()'));
for(let i=0;i<fields.length-1;i++)assert.ok(modal.includes("onkeydown=\"fuelAdvance(event,'"+fields[i+1]+"')\""));
assert.ok(modal.includes('id="fuelFull" type="checkbox" checked'));
assert.ok(modal.includes('id="fuelSaveButton" onclick="saveFuel()"'));
console.log('34.02 fuel focus navigation markup PASS');
