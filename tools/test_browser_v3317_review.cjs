const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium, webkit} = require('playwright');

const script = fs.readFileSync('rit_tank/receipt_archive.js', 'utf8');

async function test(name, engine) {
  const browser = await engine.launch({headless:true});
  try {
    const page = await browser.newPage({
      viewport:{width:390,height:844},
      isMobile:true,
      hasTouch:true
    });
    await page.setContent('<!doctype html><html><body></body></html>');
    await page.addScriptTag({content:script});

    await page.evaluate(() => {
      window.__saves = [];
      window.__duplicate = false;
      window.toast = () => {};
      window.api = async (path, options) => {
        if (path.endsWith('/prepare')) {
          return {
            pdf_data_url:'data:application/pdf;base64,JVBERi0xLjQK',
            suggested_filename:'ARGOS.pdf',
            recognized:{station:'Argos'},
            recognized_details:{
              station:'Argos',
              place:'Rijssen',
              liters:'24,70',
              price_per_liter:'2,429',
              total:'60,00',
              status:{}
            },
            duplicates:window.__duplicate ? [{id:1}] : []
          };
        }
        if (path.endsWith('/save')) {
          const data = JSON.parse(options.body);
          window.__saves.push(data);
          return {filename:data.filename};
        }
        if (path === 'api/receipt-archive') {
          return {receipts:[]};
        }
        throw new Error('Onverwacht API-pad: '+path);
      };
      mountReceiptArchive();
    });

    async function prepare(duplicate=false) {
      await page.evaluate(async duplicate => {
        window.__duplicate = duplicate;
        const file = new File(['%PDF-1.4'], 'bon.pdf', {
          type:'application/pdf'
        });
        await prepareArchiveReceipt([file]);
      }, duplicate);
      assert.equal(await page.locator('#archiveDraft').isHidden(), false);
    }

    await prepare();
    const price = page.locator('#review-price_per_liter');
    assert.equal(await price.inputValue(), '2,429');
    await price.fill('2,500');

    // Zonder bevestiging: uitsluitend PDF, geen OCR-gegevens.
    await page.locator('#archiveSave').click();
    let saves = await page.evaluate(() => window.__saves);
    assert.equal(saves.length, 1);
    assert.equal('reviewed_fields' in saves[0], false);

    // Met bevestiging: gecorrigeerde literprijs bewaren.
    await prepare();
    await page.locator('#review-price_per_liter').fill('2,500');
    await page.locator('#archiveReviewConfirm').check();
    await page.locator('#archiveSave').click();
    saves = await page.evaluate(() => window.__saves);
    assert.equal(saves.length, 2);
    assert.equal(saves[1].review_confirmed, true);
    assert.equal(saves[1].reviewed_fields.price_per_liter, '2,500');
    assert.equal(saves[1].reviewed_fields.liters, '24,70');

    // Dubbele bon vereist eigen, afzonderlijke bevestiging.
    await prepare(true);
    await page.locator('#archiveReviewConfirm').check();
    await page.locator('#archiveSave').click();
    assert.equal(
      await page.evaluate(() => window.__saves.length), 2
    );
    await page.locator('#archiveConfirm').check();
    await page.locator('#archiveSave').click();
    saves = await page.evaluate(() => window.__saves);
    assert.equal(saves.length, 3);
    assert.equal(saves[2].confirm_duplicate, true);
    assert.equal(saves[2].review_confirmed, true);

    await page.close();
    console.log(name+': OCR-formulier GESLAAGD');
  } finally {
    await browser.close();
  }
}

(async () => {
  await test('Chromium', chromium);
  await test('WebKit', webkit);
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
