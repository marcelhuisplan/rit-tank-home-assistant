// Release 33.09 UI regression: exactly the shared trip close design for fuel/archive.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=__dirname;
const app=fs.readFileSync(path.join(root,'app.py'),'utf8');
const receipt=fs.readFileSync(path.join(root,'receipt_archive.js'),'utf8');
const config=fs.readFileSync(path.join(root,'config.yaml'),'utf8');
// Match the complete shared CSS region, regardless of line breaks or spacing.
const start=app.search(/#tripModal\s*,\s*#fuelModal\s*,\s*#receiptArchiveModal\s*\{/);
assert.ok(start>=0,'all three screens must use the same shared safe-area rules');
const end=app.indexOf('.trip-success-card',start);
assert.ok(end>start,'the close-control style block must remain in the app');
const css=app.slice(start,end).replace(/\/\*[\s\S]*?\*\//g,'').replace(/\s+/g,'');
for(const id of ['tripModal','fuelModal','receiptArchiveModal']){
  for(const selector of ['.sheet','.sheethead','.sheethead h2','.close','.close:focus-visible']){
    assert.ok(css.includes('#'+id+' '+selector),id+' '+selector+' missing');
  }
}
assert.match(css,/--trip-safe-top:env\(safe-area-inset-top\)/);
assert.match(css,/--trip-safe-bottom:env\(safe-area-inset-bottom\)/);
assert.match(css,/position:sticky;top:0;z-index:30/);
assert.match(css,/flex:0 0 56px;width:56px;height:56px;min-width:56px;min-height:56px/);
assert.match(css,/background:#50eec7;color:#05251d/);
assert.match(css,/touch-action:manipulation/);
assert.match(css,/scroll-padding-bottom:calc\(24px \+ var\(--trip-safe-bottom\)\)/);
assert.match(css,/#receiptArchiveModal\{z-index:31\}/);
assert.match(app,/id="fuelModal"[\s\S]*?aria-label="Sluiten" onclick="closeModal\('fuelModal'\)"/);
assert.match(receipt,/aria-label="Sluiten" onclick="closeModal\('receiptArchiveModal'\)"/);
assert.match(app,/onclick="openReceiptArchive\(\)"/);
assert.match(app,/if path == '\/api\/receipt-archive'/);
assert.doesNotMatch(app,/day_planning|openDayPlanning\(|\/api\/day-planning|google_calendar_client/);
assert.doesNotMatch(config,/google_calendar_|day_planning/);
assert.match(config,/google_drive_oauth_json/);
assert.match(receipt,/PDF opslaan/);
console.log('33.09: mint 56px shared close, safe-area, sticky, archive and calendar rollback PASS');
