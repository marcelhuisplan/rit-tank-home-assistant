const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium, webkit} = require('playwright');

const script = fs.readFileSync(
  'rit_tank/receipt_scanner.js', 'utf8'
);

async function test(name, engine) {
  const browser = await engine.launch({headless:true});
  try {
    const page = await browser.newPage({
      viewport:{width:390,height:844},
      isMobile:true,
      hasTouch:true
    });

    await page.setContent(
      '<!doctype html><html><head>' +
      '<meta name="viewport" content="width=device-width,initial-scale=1">' +
      '</head><body><div id="scan" style="width:300px"></div></body></html>'
    );
    await page.addScriptTag({content:script});

    const before = await page.evaluate(() => {
      const canvas = document.createElement('canvas');
      canvas.width = 400;
      canvas.height = 600;
      const ctx = canvas.getContext('2d');

      ctx.fillStyle = '#6b4936';
      ctx.fillRect(0, 0, 400, 600);
      ctx.fillStyle = '#fff';
      ctx.fillRect(80, 90, 240, 420);

      window.editor = RitReceiptScanner.createCornerEditor(
        canvas, document.getElementById('scan')
      );

      return {
        automatic: editor.automatic,
        corners: editor.getCorners()
      };
    });

    assert.equal(before.automatic, true);
    assert.equal(before.corners.length, 4);
    assert.ok(Math.abs(before.corners[0].x - 80) < 12);
    assert.ok(Math.abs(before.corners[0].y - 90) < 12);

    const box = await page.locator('#scan canvas')
      .boundingBox();
    assert.ok(box, 'Voorvertoning ontbreekt');

    const first = before.corners[0];
    const startX = box.x + first.x * box.width / 400;
    const startY = box.y + first.y * box.height / 600;

    await page.mouse.move(startX, startY);
    await page.mouse.down();
    await page.mouse.move(startX + 23, startY + 26,
      {steps:8});
    await page.mouse.up();

    const after = await page.evaluate(
      () => editor.getCorners()
    );

    assert.ok(after[0].x > first.x + 20,
      'Hoek is niet horizontaal verschoven');
    assert.ok(after[0].y > first.y + 25,
      'Hoek is niet verticaal verschoven');

    console.log(name + ': BONHOEKEN GESLAAGD');
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
