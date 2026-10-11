// Release 33.16: fuel keyboard navigation markup regression.
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const html=fs.readFileSync(__dirname+'/app.py','utf8');
const modal=html.slice(html.indexOf('<div class="modal" id="fuelModal">'),html.indexOf('<div class="modal" id="kmModal">'));
for(const [id,hint] of [['fuelOdo','next'],['fuelLitersInput','next'],['fuelPriceInput','next'],['fuelStation','next'],['fuelNote','done']]){
 const el=modal.match(new RegExp('<input id="'+id+'"[^>]*>'));
 assert.ok(el,id+' is present');
 assert.ok(el[0].includes('enterkeyhint="'+hint+'"'),id+' keyboard hint');
 assert.ok(el[0].includes('onkeydown="fuelAdvance(event'),id+' Return handler');
}
assert.ok(modal.includes('id="fuelDateNext"'));
assert.ok(modal.includes('Volgende → Liters'));
assert.ok(!modal.includes('guideTo('));
assert.ok(html.includes('next.focus({preventScroll:true})'));
assert.ok(html.includes('function fuelRevealFocused()'));
console.log('33.16 keyboard hints, date fallback and focus logic PASS');
