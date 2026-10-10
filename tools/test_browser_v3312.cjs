// 33.14: an iPhone PWA must replace an old cached receipt JavaScript file.
'use strict';
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium, webkit} = require('playwright');
const root = path.resolve(__dirname, '..');
const python = [
  'import app,json,tempfile',
  'from pathlib import Path',
  'with tempfile.TemporaryDirectory() as td:',
  ' app.DATA_DIR=Path(td);app.DB_PATH=Path(td)/"test.db";app.OPTIONS_PATH=Path(td)/"options.json"',
  ' app.RECEIPT_DIR=Path(td)/"receipts";app.publish_sensors_async=lambda:None',
  ' app.init_db()',
  ' with app.db() as con:',
  '  con.execute("INSERT INTO events(created_at,type,odometer,liters,price_per_liter,station) VALUES(\'2026-10-08T14:00:00+02:00\',\'fuel\',26500,40,1.889,\'Shell\')")',
  ' print(json.dumps(dict(html=app.APP_HTML,summary=app.summary("month"),worker=app.SERVICE_WORKER.decode("utf-8"))))'
].join('\n');
const fixture = spawnSync('python', ['-B','-c',python], {
  cwd:path.join(root,'rit_tank'),encoding:'utf8',maxBuffer:4*1024*1024
});
assert.equal(fixture.status,0,fixture.stderr);
const {html,summary,worker} = JSON.parse(fixture.stdout);
assert.match(html,/receipt-archive\.js\?v=33\.13/);
const currentJs = fs.readFileSync(path.join(root,'rit_tank/receipt_archive.js'),'utf8');
assert.match(currentJs,/\b(?:async\s+)?function\s+openFuelReceiptLink\s*\(/,
 'updated JavaScript must define the receipt-link handler');
const marker = currentJs.indexOf('/* Release 33.11:');
assert.ok(marker > 0,'legacy fixture must omit the 33.11 handler');
const oldJs = currentJs.slice(0,marker);
assert.doesNotMatch(oldJs,/function openFuelReceiptLink/);
const oldHtml = html.replace('receipt-archive.js?v=33.14','receipt-archive.js');

// Match the previous service worker's cache-first strategy and shell cache name.
const oldWorker = [
 'const CACHE = "rit-tank-shell-33.11";',
 'const SHELL = ["./","manifest.webmanifest","huisplan-icon-180.png","huisplan-icon-192.png","huisplan-icon-512.png","captur-2014.png"];',
 'self.addEventListener("install",event=>{event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(SHELL)).then(()=>self.skipWaiting()))});',
 'self.addEventListener("activate",event=>{event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key.startsWith("rit-tank-shell-")&&key!==CACHE).map(key=>caches.delete(key)))).then(()=>self.clients.claim()))});',
 'self.addEventListener("fetch",event=>{const request=event.request;if(request.method!=="GET")return;const url=new URL(request.url);if(url.origin!==self.location.origin||url.pathname.includes("/api/")||url.pathname.endsWith("/health"))return;if(request.mode==="navigate"){event.respondWith(fetch(request).then(response=>{if(response.ok)caches.open(CACHE).then(cache=>cache.put("./",response.clone()));return response}).catch(()=>caches.match("./")));return;}event.respondWith(caches.match(request).then(cached=>cached||fetch(request).then(response=>{if(response.ok)caches.open(CACHE).then(cache=>cache.put(request,response.clone()));return response})))})'
].join('\n');

(async () => {
 for (const [name,engine] of Object.entries({chromium,webkit})) {
  let newHtml=false,newWorker=false;
  const requests=[];
  const server=http.createServer((req,res)=>{
   const url=new URL(req.url,'http://127.0.0.1');
   requests.push(url.pathname+url.search);
   function send(body,type='text/plain',policy='no-cache') {
    res.writeHead(200,{'Content-Type':type,'Cache-Control':policy});
    res.end(body);
   }
   if(url.pathname==='/')return send(newHtml?html:oldHtml,'text/html; charset=utf-8','no-store');
   if(url.pathname==='/service-worker.js')return send(newWorker?worker:oldWorker,'text/javascript');
   if(url.pathname==='/receipt-archive.js') {
    const body=url.searchParams.get('v')==='33.14'?currentJs:oldJs;
    return send(body,'text/javascript');
   }
   if(url.pathname==='/api/summary')return send(JSON.stringify(summary),'application/json','no-store');
   if(url.pathname==='/api/receipt-archive')
    return send(JSON.stringify({receipts:[{id:1,filename:'Bestaand.pdf',created_at:'2026-10-08',legacy:false}]}),'application/json');
   if(url.pathname.startsWith('/api/'))return send('{}','application/json','no-store');
   return send('','text/plain');
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  // Closing the origin is repeatable and simulates a real outage without WebKit's
  // service-worker-incompatible Playwright offline emulation.
  const stopServer=async()=>{
   if(!server.listening)return;
   await new Promise((resolve,reject)=>{
    server.close(error=>error?reject(error):resolve());
    server.closeAllConnections();
   });
  };
  const origin='http://127.0.0.1:'+server.address().port;
  const browser=await engine.launch({headless:true});
  try {
   const context=await browser.newContext({
    viewport:{width:375,height:667},isMobile:true,hasTouch:true,deviceScaleFactor:2,
    serviceWorkers:'allow'
   });
   try {
    const page=await context.newPage();
    await page.goto(origin+'/');
    await page.evaluate(()=>navigator.serviceWorker.ready);
    await page.waitForFunction(()=>Boolean(navigator.serviceWorker.controller));
    await page.reload();
    await page.locator('#history .fuel-receipt-action').waitFor({state:'visible'});
    assert.equal(await page.evaluate(()=>typeof window.openFuelReceiptLink),'undefined',
     name+': old JavaScript must reproduce the missing handler');
    // Seed the old 33.11 cache explicitly: its asynchronous writes are not under test.
    const legacyScriptUrl=origin+'/receipt-archive.js';
    const cachedOldJs=await page.evaluate(async({scriptUrl,scriptBody})=>{
     const cache=await caches.open('rit-tank-shell-33.11');
     await cache.put(scriptUrl,new Response(scriptBody,{
      status:200,headers:{'Content-Type':'text/javascript'}
     }));
     const entry=await cache.match(scriptUrl);
     const keys=await cache.keys();
     return {hasExactKey:keys.some(request=>request.url===scriptUrl),
      body:entry?await entry.text():null};
    },{scriptUrl:legacyScriptUrl,scriptBody:oldJs});
    assert.equal(cachedOldJs.hasExactKey,true,
     name+': previous cache must contain the exact unversioned script URL');
    assert.equal(cachedOldJs.body,oldJs,
     name+': seeded response must contain the complete old JavaScript');
    const sha256=body=>crypto.createHash('sha256').update(body,'utf8').digest('hex');
    assert.equal(sha256(cachedOldJs.body),sha256(oldJs),
     name+': cached script SHA-256 must match the old JavaScript');
    assert.doesNotMatch(cachedOldJs.body,/function openFuelReceiptLink/,
     name+': old cached JavaScript must not define the new handler');
    assert.equal(await page.evaluate(async()=>(await fetch('/receipt-archive.js')).text()),oldJs,
     name+': active old worker must serve the explicitly cached script');
    await page.evaluate(async()=>{
     localStorage.setItem('cache-update-preservation','kept');
     await caches.open('other-user-cache');
    });

    // Home Assistant now serves new HTML; the OLD cache-first worker remains active.
    newHtml=true;
    await page.reload();
    await page.locator('#history .fuel-receipt-action').waitFor({state:'visible'});
    assert.equal(await page.evaluate(()=>typeof window.openFuelReceiptLink),'function',
     name+': new JS must bypass old cache');
    assert.ok(requests.includes('/receipt-archive.js?v=33.14'),
     name+': 33.14 script URL was not fetched');
    assert.equal(await page.evaluate(async()=>
     Boolean(await (await caches.open('rit-tank-shell-33.11')).match('receipt-archive.js'))
    ),true,name+': old JS still present during upgrade');
    await page.locator('#history .fuel-receipt-action').tap();
    await page.locator('#fuelReceiptModal').waitFor({state:'visible'});
    await page.locator('#fuelReceiptModal .sheethead .close').tap();
    assert.equal(await page.locator('#fuelReceiptModal').isVisible(),false);

    // Activate new SW; do not delete non-shell caches or local browser data.
    newWorker=true;
    await page.evaluate(async()=>(await navigator.serviceWorker.ready).update());
    await page.waitForFunction(async()=>{
     const keys=await caches.keys();
     return keys.includes('rit-tank-shell-33.14')&&!keys.includes('rit-tank-shell-33.11');
    });
    assert.equal(await page.evaluate(async()=>
     Boolean(await (await caches.open('rit-tank-shell-33.14')).match('receipt-archive.js?v=33.14'))
    ),true,name+': fresh worker must precache new JS for offline use');
    assert.equal(await page.evaluate(()=>localStorage.getItem('cache-update-preservation')),'kept');
    assert.equal(await page.evaluate(async()=>(await caches.keys()).includes('other-user-cache')),true);
    // Playwright/WebKit can reject SW-served requests under setOffline(true).
    // Stopping the origin exercises the same real SW fallback on WebKit.
    const offlineScriptResponses=[];
    const onOfflineResponse=response=>{
     if(response.url()===origin+'/receipt-archive.js?v=33.14')
      offlineScriptResponses.push(response);
    };
    page.on('response',onOfflineResponse);
    if(name==='webkit')await stopServer();
    else await context.setOffline(true);
    const offlineNavigation=await page.reload({waitUntil:'domcontentloaded'});
    page.off('response',onOfflineResponse);
    assert.equal(offlineNavigation?.status(),200,
     name+': cached app shell must load without its origin');
    assert.equal(offlineNavigation.fromServiceWorker(),true,
     name+': offline navigation must be handled by the service worker');
    const offlineScript=offlineScriptResponses.at(-1);
    assert.ok(offlineScript,name+': offline reload must request versioned receipt JS');
    assert.equal(offlineScript.status(),200,name+': offline receipt JS request must succeed');
    assert.equal(offlineScript.fromServiceWorker(),true,
     name+': offline receipt JS must come from the service worker');
    assert.equal(await page.evaluate(()=>typeof window.openFuelReceiptLink),'function',
     name+': updated JS must work offline');
    if(name==='chromium')await context.setOffline(false);
    console.log('33.14 '+name+' iPhone old JS cache, modal, update, offline and data preservation OK');
   } finally {await context.close();}
  } finally {await browser.close();await stopServer();}
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
