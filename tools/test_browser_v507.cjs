// Real Chromium layout/scroll tests. Network services are mocked, not iOS or live Drive.
const {chromium}=require('playwright'),fs=require('fs'),path=require('path'),assert=require('assert/strict'),{execFileSync}=require('child_process');
const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(root,'rit_tank/app.py'),'utf8').match(/APP_HTML = r'''([\s\S]*?)'''/)[1];
const summary=JSON.parse(execFileSync('python3',['-c',`
import sys, tempfile, json
from pathlib import Path
sys.path.insert(0,'rit_tank')
from test_v44 import app
with tempfile.TemporaryDirectory() as d:
 app.DATA_DIR=Path(d);app.DB_PATH=Path(d)/'test.db';app.OPTIONS_PATH=Path(d)/'options.json'
 app.init_db()
 print(json.dumps(app.summary()))
`],{cwd:root,encoding:'utf8'}));
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
 const page=await browser.newPage({viewport:{width:390,height:844}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 let scan={liters:32.45,price_per_liter:1.899,station:'Teststation'},saved,uploaded;
 summary.settings.location_fallback_entity='device_tracker.iphone';
 summary.app.backup={enabled:true,configured:true,retention_days:0,hour:3};
 await page.route('**/*',async route=>{
  const u=new URL(route.request().url()),p=u.pathname;
  if(p==='/')return route.fulfill({contentType:'text/html',body:html});
  if(p==='/api/summary')return route.fulfill({json:summary});
  if(p==='/api/receipt/scan')return route.fulfill({json:{receipt:scan}});
  if(p==='/api/location/entities')return route.fulfill({status:503,json:{error:'Unavailable'}});
  if(p==='/api/location/entity')return route.fulfill({json:{latitude:52,longitude:5,source:'ha'}});
  if(p==='/api/location/addresses')return route.fulfill({json:{street:'Teststraat',addresses:[{address:'Teststraat 7A, Teststad',latitude:52,longitude:5,distance_m:10}]}});
  if(p==='/api/settings'){saved=route.request().postDataJSON();return route.fulfill({json:{ok:true}})}
  if(p==='/api/business.pdf')return route.fulfill({contentType:'application/pdf',headers:{'Content-Disposition':'inline; filename="test.pdf"'},body:Buffer.from('%PDF-1.4\n% test fixture\n%%EOF')});
  if(p==='/api/backup/pdf'){uploaded=route.request().postDataJSON();return route.fulfill({json:{ok:true,name:'test.pdf'}})}
  if(p.startsWith('/api/'))return route.fulfill({json:{services:[],arrivals:[]}});
  const asset=path.join(root,'rit_tank',path.basename(p));
  if(fs.existsSync(asset)&&/\.(png|jpg)$/.test(p))return route.fulfill({path:asset});
  return route.fulfill({status:404,body:''});
 });
 await page.goto('https://rit-tank.test/');await page.waitForFunction(()=>typeof DATA!=='undefined'&&DATA!==null);
 await page.evaluate(()=>openFuel());
 const png=fs.readFileSync(path.join(root,'rit_tank/huisplan-icon-180.png'));
 await page.locator('#fuelReceipt').setInputFiles({name:'bon.png',mimeType:'image/png',buffer:png});
 await page.waitForFunction(()=>document.getElementById('receiptScanStatus').textContent.includes('32,45'));
 await page.waitForTimeout(250);
 assert.deepEqual(await page.evaluate(()=>fuelValues()),{liters:32.45,price:1.899});
 // Repeated scans must not leave an earlier wheel scroll callback behind.
 scan={liters:41.99,price_per_liter:2.009};
 await page.locator('#fuelReceipt').setInputFiles({name:'bon2.png',mimeType:'image/png',buffer:png});
 await page.waitForFunction(()=>document.getElementById('receiptScanStatus').textContent.includes('41,99'));
 await page.waitForTimeout(250);
 assert.deepEqual(await page.evaluate(()=>fuelValues()),{liters:41.99,price:2.009});
 scan={liters:28.75};
 await page.locator('#fuelReceipt').setInputFiles({name:'bon3.png',mimeType:'image/png',buffer:png});
 await page.waitForFunction(()=>document.getElementById('receiptScanStatus').textContent.includes('Niet herkend: literprijs'));
 await page.waitForTimeout(250);
 assert.deepEqual(await page.evaluate(()=>fuelValues()),{liters:28.75,price:2.009});
 await page.evaluate(()=>{closeModal('fuelModal');return openSettings()});
 assert.equal(await page.locator('#setLocationEntity').inputValue(),'device_tracker.iphone');
 await page.evaluate(()=>saveSettings());assert.equal(saved.location_fallback_entity,'device_tracker.iphone');
 await page.evaluate(()=>{browserLocation=async()=>{throw Error('GPS unavailable')};openTripPoint('start');return captureTripLocation()});
 await page.locator('#tripAddressChoices button').click();
 assert.equal(await page.evaluate(()=>TRIP_LOCATION.manual_label),'Teststraat 7A, Teststad');
 await page.locator('#tripManualAddress').fill('Teststraat 9, Teststad');
 assert.equal(await page.evaluate(()=>TRIP_LOCATION),null);
 await page.evaluate(()=>{confirmManualTripAddress();closeModal('tripModal');switchView('business')});
 assert.equal(await page.evaluate(()=>TRIP_LOCATION.manual_label),'Teststraat 9, Teststad');
 await page.locator('#bizPdfLink').click();await page.locator('#pdfOpen').waitFor({state:'visible'});
 assert.equal(await page.locator('#pdfDownload').getAttribute('download'),'test.pdf');
 await page.locator('#pdfDrive').click();await page.waitForFunction(()=>document.getElementById('pdfStatus').textContent.includes('Permanent'));
 assert.equal(Buffer.from(uploaded.pdf_base64,'base64').toString(),'%PDF-1.4\n% test fixture\n%%EOF');
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 assert.deepEqual(errors,[]);
 console.log('PASS: repeated and partial scans, exact wheel values, unavailable tracker persistence, confirmed address, PDF preparation and exact archive upload.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
