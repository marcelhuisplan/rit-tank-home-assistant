// Release 33.16: fuel keyboard navigation markup regression.
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const html=fs.readFileSync(__dirname+'/app.py','utf8');
const modal=html.slice(html.indexOf('<div class="modal" id="fuelModal">'),html.indexOf('<div class="modal" id="kmModal">'));
for(const [id,hint] of [['fuelOdo','next'],['fuelLitersInput','next'],['fuelPriceInput','next'],['fuelStation','next'],['fuelAddress','next'],['fuelNote','next']]){
 const el=modal.match(new RegExp('<input id="'+id+'"[^>]*>'));
 assert.ok(el,id+' is present');
 assert.ok(el[0].includes('enterkeyhint="'+hint+'"'),id+' keyboard hint');
 assert.ok(el[0].includes('onkeydown="fuelAdvance(event'),id+' Return handler');
}
const order=['fuelStepOdo','fuelStepLiters','fuelStepPrice','fuelStepLocation','fuelStepFinish','fuelStepDate','fuelSaveButton'];
const positions=order.map(id=>modal.indexOf('id="'+id+'"'));
assert.ok(positions.every((position,i)=>position>=0&&(!i||position>positions[i-1])),'fuel DOM and step order');
assert.ok(!modal.includes('fuelDateNext')&&!modal.includes('Volgende → Liters'),'old date next button removed');
for(const [id,step] of [['fuelStepOdo','1'],['fuelStepLiters','2'],['fuelStepPrice','3'],['fuelStepLocation','4'],['fuelStepFinish','5'],['fuelStepDate','6']]){
 const section=modal.slice(modal.indexOf('id="'+id+'"'),modal.indexOf('id="'+id+'"')+130);
 assert.ok(section.includes('step-badge">'+step+'</span>'),id+' number');
}
for(const [id,next] of [['fuelOdo','fuelLitersInput'],['fuelLitersInput','fuelPriceInput'],['fuelPriceInput','fuelStation'],['fuelStation','fuelAddress'],['fuelAddress','fuelNote'],['fuelNote','fuelDate']]){
 assert.match(modal,new RegExp('id="'+id+'"[^>]*onkeydown="fuelAdvance\\(event,\\x27'+next+'\\x27\\)"'),id+' Enter transition');
}
assert.ok(modal.includes('id="fuelDate" type="datetime-local" enterkeyhint="done" onkeydown="fuelAdvance(event)"'));
assert.ok(modal.includes('type="button" id="fuelSaveButton"'));
assert.ok(!modal.includes('guideTo('));
assert.ok(html.includes('next.focus({preventScroll:true})'));
assert.ok(html.includes('function fuelRevealFocused()'));
console.log('33.16 keyboard hints, final date step and focus logic PASS');
