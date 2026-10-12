const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium, webkit} = require('playwright');

const script = fs.readFileSync(
  'rit_tank/receipt_scanner.js', 'utf8'
);

async function test(name, engine) {
  const browser = await engine.launch({headless:true});
  try {
    const page = await browser.newPage();
    await page.setContent('<!doctype html><html><body></body></html>');
    await page.addScriptTag({content:script});

    const result = await page.evaluate(() => {
      const source = document.createElement('canvas');
      source.width = 400;
      source.height = 600;
      const ctx = source.getContext('2d');

      // Bruine tafel met een scheef gefotografeerde bon.
      ctx.fillStyle = '#63412b';
      ctx.fillRect(0,0,400,600);

      const corners = [
        {x:80,y:100},
        {x:320,y:80},
        {x:300,y:500},
        {x:90,y:520}
      ];

      ctx.fillStyle = '#ffffff';
      ctx.beginPath();
      corners.forEach((p,i) => {
        if (!i) ctx.moveTo(p.x,p.y);
        else ctx.lineTo(p.x,p.y);
      });
      ctx.closePath();
      ctx.fill();

      // Zwarte markering midden op het bonnetje.
      ctx.fillStyle = '#000000';
      ctx.fillRect(170,230,60,120);

      const original = JSON.stringify(corners);
      const corrected = RitReceiptScanner.straightenReceipt(
        source,corners
      );
      const out = corrected.getContext('2d');
      const pixel = (fractionX,fractionY) =>
        Array.from(out.getImageData(
          Math.floor(corrected.width*fractionX),
          Math.floor(corrected.height*fractionY),
          1,1
        ).data);

      let invalidRejected = false;
      try {
        RitReceiptScanner.straightenReceipt(source,[
          {x:80,y:100},{x:300,y:500},
          {x:320,y:80},{x:90,y:520}
        ]);
      } catch (_) {
        invalidRejected = true;
      }

      return {
        width:corrected.width,
        height:corrected.height,
        white:pixel(0.2,0.2),
        black:pixel(0.5,0.5),
        originalUnchanged:JSON.stringify(corners)===original,
        invalidRejected
      };
    });

    assert.ok(result.width > 200 && result.height > 350);
    assert.ok(result.white[0] > 220,
      'Het witte bonpapier ontbreekt in de uitsnede');
    assert.ok(result.black[0] < 60,
      'De zwarte bonmarkering ontbreekt');
    assert.equal(result.originalUnchanged,true);
    assert.equal(result.invalidRejected,true);

    console.log(name+': PERSPECTIEFCORRECTIE GESLAAGD');
  } finally {
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
