/* Rit & Tank 33.17 — local receipt edge detection.
   No uploads, storage or changes to fuel data. */
'use strict';

window.RitReceiptScanner = (() => {
  function detectCorners(canvas) {
    const width = canvas.width;
    const height = canvas.height;
    if (width < 40 || height < 40) {
      throw new Error('Afbeelding is te klein.');
    }

    const ctx = canvas.getContext('2d', {
      willReadFrequently: true
    });
    const pixels = ctx.getImageData(
      0, 0, width, height
    ).data;

    function pixel(x, y) {
      const i = (y * width + x) * 4;
      return [
        pixels[i],
        pixels[i + 1],
        pixels[i + 2]
      ];
    }

    // Estimate the background using all four corners.
    const margin = Math.max(
      2, Math.round(Math.min(width, height) * 0.02)
    );
    const background = [
      pixel(margin, margin),
      pixel(width - margin - 1, margin),
      pixel(margin, height - margin - 1),
      pixel(width - margin - 1, height - margin - 1)
    ];

    const bg = [0, 1, 2].map(channel =>
      background
        .map(p => p[channel])
        .sort((a, b) => a - b)
        .slice(1, 3)
        .reduce((a, b) => a + b, 0) / 2
    );

    const step = Math.max(
      2, Math.floor(Math.min(width, height) / 250)
    );

    let left = width;
    let top = height;
    let right = 0;
    let bottom = 0;
    let count = 0;

    for (let y = margin; y < height - margin; y += step) {
      for (let x = margin; x < width - margin; x += step) {
        const p = pixel(x, y);
        const difference = Math.hypot(
          p[0] - bg[0],
          p[1] - bg[1],
          p[2] - bg[2]
        );

        if (difference > 65) {
          left = Math.min(left, x);
          top = Math.min(top, y);
          right = Math.max(right, x);
          bottom = Math.max(bottom, y);
          count++;
        }
      }
    }

    const coverage = count * step * step /
      (width * height);

    // Uncertain detection: provide adjustable default corners.
    const confident =
      count > 20 &&
      coverage > 0.05 &&
      coverage < 0.90 &&
      right - left > width * 0.15 &&
      bottom - top > height * 0.15;

    if (!confident) {
      left = width * 0.08;
      right = width * 0.92;
      top = height * 0.05;
      bottom = height * 0.95;
    }

    return {
      corners: [
        { x: left, y: top },
        { x: right, y: top },
        { x: right, y: bottom },
        { x: left, y: bottom }
      ],
      automatic: confident
    };
  }


  function createCornerEditor(sourceCanvas, container) {
    const detection = detectCorners(sourceCanvas);
    const corners = detection.corners.map(p => ({...p}));

    const preview = document.createElement('canvas');
    preview.width = sourceCanvas.width;
    preview.height = sourceCanvas.height;
    preview.style.width = '100%';
    preview.style.maxWidth = '100%';
    preview.style.height = 'auto';
    preview.style.display = 'block';
    preview.style.touchAction = 'none';
    preview.setAttribute(
      'aria-label', 'Bonhoeken aanpassen'
    );

    container.appendChild(preview);
    const ctx = preview.getContext('2d');
    let active = -1;

    function draw() {
      ctx.clearRect(0, 0, preview.width, preview.height);
      ctx.drawImage(sourceCanvas, 0, 0);

      const scale = Math.max(
        1, Math.min(preview.width, preview.height) / 300
      );

      ctx.beginPath();
      corners.forEach((p, i) => {
        if (i === 0) ctx.moveTo(p.x, p.y);
        else ctx.lineTo(p.x, p.y);
      });
      ctx.closePath();
      ctx.lineWidth = 3 * scale;
      ctx.strokeStyle = '#40e0bd';
      ctx.stroke();

      corners.forEach(p => {
        ctx.beginPath();
        ctx.arc(p.x, p.y, 9 * scale, 0, 2 * Math.PI);
        ctx.fillStyle = '#40e0bd';
        ctx.fill();
        ctx.lineWidth = 2 * scale;
        ctx.strokeStyle = '#10201d';
        ctx.stroke();
      });
    }

    function point(event) {
      const box = preview.getBoundingClientRect();
      return {
        x: (event.clientX - box.left) *
           preview.width / box.width,
        y: (event.clientY - box.top) *
           preview.height / box.height
      };
    }

    preview.addEventListener('pointerdown', event => {
      const p = point(event);
      const maxDistance = Math.max(
        30, Math.min(preview.width, preview.height) * 0.10
      );

      let nearest = -1;
      let distance = Infinity;
      corners.forEach((corner, i) => {
        const d = Math.hypot(
          corner.x - p.x, corner.y - p.y
        );
        if (d < distance) {
          distance = d;
          nearest = i;
        }
      });

      if (distance > maxDistance) return;
      active = nearest;
      preview.setPointerCapture(event.pointerId);
    });

    preview.addEventListener('pointermove', event => {
      if (active < 0) return;
      const p = point(event);
      corners[active] = {
        x: Math.max(0, Math.min(preview.width - 1, p.x)),
        y: Math.max(0, Math.min(preview.height - 1, p.y))
      };
      draw();
    });

    function stopDragging() {
      active = -1;
    }

    preview.addEventListener('pointerup', stopDragging);
    preview.addEventListener('pointercancel', stopDragging);
    preview.addEventListener('lostpointercapture', stopDragging);

    draw();

    return {
      canvas: preview,
      automatic: detection.automatic,
      getCorners: () => corners.map(p => ({...p}))
    };
  }


  function straightenReceipt(sourceCanvas, corners) {
    if (!Array.isArray(corners) || corners.length !== 4) {
      throw new Error('Selecteer vier bonhoeken.');
    }

    const pts = corners.map(p => ({x:Number(p.x), y:Number(p.y)}));
    for (const p of pts) {
      if (!Number.isFinite(p.x) || !Number.isFinite(p.y) ||
          p.x < 0 || p.y < 0 ||
          p.x >= sourceCanvas.width || p.y >= sourceCanvas.height) {
        throw new Error('Bonhoek valt buiten de afbeelding.');
      }
    }

    // Controleer of de vier hoeken een geldige vierhoek vormen.
    let orientation = 0;
    for (let i = 0; i < 4; i++) {
      const a = pts[i], b = pts[(i+1)%4], c = pts[(i+2)%4];
      const cross = (b.x-a.x)*(c.y-b.y) -
                    (b.y-a.y)*(c.x-b.x);
      if (Math.abs(cross) < 1 || (orientation && cross*orientation <= 0)) {
        throw new Error('Controleer de vier bonhoeken.');
      }
      orientation = Math.sign(cross);
    }
    if (orientation < 0) {
      throw new Error('Controleer de volgorde van de hoeken.');
    }

    const dist = (a,b) => Math.hypot(a.x-b.x,a.y-b.y);
    let w = Math.round(Math.max(dist(pts[0],pts[1]),
                                dist(pts[3],pts[2])));
    let h = Math.round(Math.max(dist(pts[0],pts[3]),
                                dist(pts[1],pts[2])));
    if (w < 30 || h < 30) {
      throw new Error('Uitsnede is te klein.');
    }

    const scale = Math.min(1,2200/Math.max(w,h),
                           Math.sqrt(3000000/(w*h)));
    w = Math.max(30,Math.round(w*scale));
    h = Math.max(30,Math.round(h*scale));

    // Projectieve transformatie van rechte bon naar vier fotohoeken.
    const [p0,p1,p2,p3] = pts;
    const dx1=p1.x-p2.x, dx2=p3.x-p2.x;
    const dy1=p1.y-p2.y, dy2=p3.y-p2.y;
    const dx3=p0.x-p1.x+p2.x-p3.x;
    const dy3=p0.y-p1.y+p2.y-p3.y;
    const det=dx1*dy2-dx2*dy1;
    let g=0, k=0;
    if (Math.abs(dx3)+Math.abs(dy3)>0.00001) {
      if (Math.abs(det)<0.00001) {
        throw new Error('Perspectief kan niet worden berekend.');
      }
      g=(dx3*dy2-dx2*dy3)/det;
      k=(dx1*dy3-dx3*dy1)/det;
    }
    const a=p1.x-p0.x+g*p1.x, b=p3.x-p0.x+k*p3.x;
    const d=p1.y-p0.y+g*p1.y, e=p3.y-p0.y+k*p3.y;

    const sw=sourceCanvas.width, sh=sourceCanvas.height;
    const src=sourceCanvas.getContext('2d')
      .getImageData(0,0,sw,sh).data;
    const out=document.createElement('canvas');
    out.width=w; out.height=h;
    const ctx=out.getContext('2d');
    const image=ctx.createImageData(w,h);

    for (let y=0;y<h;y++) {
      const v=y/Math.max(1,h-1);
      for (let x=0;x<w;x++) {
        const u=x/Math.max(1,w-1);
        const divisor=g*u+k*v+1;
        if (Math.abs(divisor)<1e-8) {
          throw new Error('Ongeldige perspectiefcorrectie.');
        }
        const sx=Math.max(0,Math.min(sw-1,
          Math.round((a*u+b*v+p0.x)/divisor)));
        const sy=Math.max(0,Math.min(sh-1,
          Math.round((d*u+e*v+p0.y)/divisor)));
        const si=(sy*sw+sx)*4, di=(y*w+x)*4;
        for(let c=0;c<3;c++) image.data[di+c]=src[si+c];
        image.data[di+3]=255;
      }
    }
    ctx.putImageData(image,0,0);
    return out;
  }

  return { detectCorners, createCornerEditor, straightenReceipt };
})();
