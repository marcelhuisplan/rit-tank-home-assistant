const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium, webkit} = require('playwright');

const script = fs.readFileSync('rit_tank/receipt_archive.js', 'utf8');

async function setup(engine) {
  const browser = await engine.launch({headless:true});
  const context = await browser.newContext({
    viewport:{width:390,height:844},
    isMobile:true,
    hasTouch:true
  });
  const page = await context.newPage();
  await page.setContent('<!doctype html><html><head></head><body></body></html>');
  await page.addScriptTag({content:script});
  await page.evaluate(async()=>{
    window.__prepareCalls = 0;
    window.__saveCalls = 0;
    window.__prepareResolvers = [];
    window.__deferPrepare = false;
    window.openModal = () => {};
    window.closeModal = () => {};
    window.toast = () => {};
    window.api = async path => {
      if (path === 'api/receipt-archive') {
        return {receipts:[{
          id:'legacy-1',
          filename:'synthetische-historische-bon.pdf',
          created_at:'2026-10-10T12:00:00+02:00',
          legacy:true
        }]};
      }
      if (path === 'api/receipt-archive/prepare') {
        window.__prepareCalls += 1;
        if (window.__deferPrepare) {
          return new Promise(resolve => {
            window.__prepareResolvers.push(resolve);
          });
        }
        return {
          pdf_data_url:'data:application/pdf;base64,JVBERi0xLjQK',
          suggested_filename:'synthetische-'+window.__prepareCalls+'.pdf',
          recognized:{},
          recognized_details:{},
          duplicates:[]
        };
      }
      if (path === 'api/receipt-archive/save') {
        window.__saveCalls += 1;
        return {filename:'synthetische-opgeslagen.pdf'};
      }
      throw new Error('Onverwacht API-pad: '+path);
    };
    window.RitReceiptScanner = {
      createCornerEditor(canvas, preview) {
        preview.append(canvas);
        return {getCorners:()=>[
          {x:0,y:0},{x:canvas.width,y:0},
          {x:canvas.width,y:canvas.height},{x:0,y:canvas.height}
        ]};
      },
      straightenReceipt() {
        const canvas = document.createElement('canvas');
        canvas.width = 120;
        canvas.height = 160;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = 'white';
        ctx.fillRect(0,0,120,160);
        return canvas;
      }
    };
    mountReceiptArchive();
    await openReceiptArchive();

    const source = document.createElement('canvas');
    source.width = 120;
    source.height = 160;
    source.getContext('2d').fillRect(0,0,120,160);
    const bytes = await (await fetch(source.toDataURL('image/jpeg'))).blob();
    window.__cameraFile = new File([bytes], 'synthetic-receipt.jpg', {
      type:'image/jpeg'
    });
  });
  return {browser,context,page};
}

async function assertHistory(page) {
  await page.getByText('synthetische-historische-bon.pdf').waitFor();
  assert.equal(
    await page.locator('#archiveList .archive-entry').count(), 1
  );
}

async function startCamera(page) {
  await page.evaluate(async()=>{
    await startArchiveCameraReview([window.__cameraFile]);
    straightenArchiveCamera();
  });
  await page.locator('#archiveCameraReview').waitFor({state:'visible'});
  await page.locator('#archiveUseStraightened').waitFor({state:'visible'});
}

async function test(name, engine) {
  const {browser,context,page} = await setup(engine);
  try {
    await assertHistory(page);

    // Cancel while canvas.toBlob is still encoding the corrected JPEG.
    await startCamera(page);
    await page.evaluate(()=>{
      window.__nativeToBlob = HTMLCanvasElement.prototype.toBlob;
      HTMLCanvasElement.prototype.toBlob = function(callback) {
        window.__pendingBlobCallback = callback;
      };
      window.__straightenedUse = useStraightenedArchiveCamera();
    });
    await page.waitForFunction(()=>typeof window.__pendingBlobCallback==='function');
    await page.locator('#archiveCancelCamera').click();
    await page.evaluate(async()=>{
      window.__pendingBlobCallback(new Blob(['synthetic jpeg']));
      await window.__straightenedUse;
      HTMLCanvasElement.prototype.toBlob = window.__nativeToBlob;
    });
    assert.equal(await page.locator('#archiveDraft').isVisible(), false);
    assert.equal(await page.locator('#archiveFilename').inputValue(), '');
    assert.equal(await page.evaluate(()=>window.__prepareCalls), 0);
    await assertHistory(page);

    // Cancel while prepareArchiveReceipt is converting an image to JPEG.
    await page.evaluate(()=>{
      const NativeImage = window.Image;
      window.__nativeImage = NativeImage;
      window.__nativeGetContext = HTMLCanvasElement.prototype.getContext;
      window.__nativeToDataURL = HTMLCanvasElement.prototype.toDataURL;
      window.Image = function() {
        const image = new NativeImage();
        Object.defineProperties(image, {
          naturalWidth:{value:120},
          naturalHeight:{value:160},
          src:{
            configurable:true,
            get:()=> '',
            set:value=>{window.__pendingConversionImage=image;}
          }
        });
        return image;
      };
      HTMLCanvasElement.prototype.getContext = () => ({
        fillRect(){},
        drawImage(){},
        set fillStyle(value){}
      });
      HTMLCanvasElement.prototype.toDataURL = () =>
        'data:image/jpeg;base64,AA==';
      window.__jpegPreparation = prepareArchiveReceipt([window.__cameraFile]);
    });
    await page.waitForFunction(()=>Boolean(window.__pendingConversionImage));
    await page.evaluate(()=>{
      cancelArchiveCamera();
      window.__pendingConversionImage.onload();
    });
    await page.evaluate(()=>window.__jpegPreparation);
    await page.evaluate(()=>{
      window.Image = window.__nativeImage;
      HTMLCanvasElement.prototype.getContext = window.__nativeGetContext;
      HTMLCanvasElement.prototype.toDataURL = window.__nativeToDataURL;
    });
    assert.equal(await page.locator('#archiveDraft').isVisible(), false);
    assert.equal(await page.locator('#archiveFilename').inputValue(), '');
    assert.equal(await page.evaluate(()=>window.__prepareCalls), 0);
    await assertHistory(page);

    // A late OCR/PDF response must not replace a newer, accepted proposal.
    await page.evaluate(async()=>{
      window.__deferPrepare = true;
      const oldPdf = new File(['%PDF-1.4 synthetic'], 'old.pdf', {
        type:'application/pdf'
      });
      window.__stalePreparation = prepareArchiveReceipt([oldPdf]);
    });
    await page.waitForFunction(()=>window.__prepareResolvers.length===1);
    await page.evaluate(()=>cancelArchiveCamera());
    await page.evaluate(async()=>{
      window.__deferPrepare = false;
      const currentPdf = new File(['%PDF-1.4 current'], 'current.pdf', {
        type:'application/pdf'
      });
      await prepareArchiveReceipt([currentPdf]);
    });
    assert.equal(await page.locator('#archiveDraft').isVisible(), true);
    assert.equal(await page.locator('#archiveFilename').inputValue(),
      'synthetische-2.pdf');
    await page.evaluate(async()=>{
      window.__prepareResolvers[0]({
        pdf_data_url:'data:application/pdf;base64,JVBERi0xLjQK',
        suggested_filename:'stale-ocr-proposal.pdf',
        recognized:{place:'Oude plaats'},
        recognized_details:{place:'Oude plaats'},
        duplicates:[]
      });
      await window.__stalePreparation;
    });
    assert.equal(await page.locator('#archiveDraft').isVisible(), true);
    assert.equal(await page.locator('#archiveFilename').inputValue(),
      'synthetische-2.pdf');
    assert.equal(await page.evaluate(()=>window.__saveCalls), 0);
    await assertHistory(page);

    // Accepting the original photo still starts a normal proposal.
    await page.evaluate(()=>cancelArchiveCamera());
    await page.evaluate(async()=>{
      await startArchiveCameraReview([window.__cameraFile]);
      await useOriginalArchiveCamera();
    });
    await page.locator('#archiveDraft').waitFor({state:'visible'});
    assert.equal(await page.locator('#archiveFilename').inputValue(),
      'synthetische-3.pdf');
    await assertHistory(page);

    // Accepting the corrected photo still converts, prepares, and displays it.
    await page.evaluate(()=>cancelArchiveCamera());
    await page.evaluate(async()=>{
      await startArchiveCameraReview([window.__cameraFile]);
      straightenArchiveCamera();
      await useStraightenedArchiveCamera();
    });
    await page.locator('#archiveDraft').waitFor({state:'visible'});
    assert.equal(await page.locator('#archiveFilename').inputValue(),
      'synthetische-4.pdf');
    assert.equal(await page.evaluate(()=>window.__prepareCalls), 4);
    assert.equal(await page.evaluate(()=>window.__saveCalls), 0);
    await assertHistory(page);
    console.log(name+': cancel races and original/corrected acceptance passed');
  } finally {
    await context.close();
    await browser.close();
  }
}

(async()=>{
  await test('Chromium',chromium);
  await test('WebKit',webkit);
})().catch(error=>{
  console.error(error);
  process.exitCode=1;
});
