/* Standalone PDF receipt archive UI; never touches fuel or trip values. */
'use strict';
let ARCHIVE_PDF = null;
let ARCHIVE_ROWS = [];
let ARCHIVE_BUSY = false;
let ARCHIVE_CAMERA_FILE = null;
let ARCHIVE_CAMERA_SOURCE = null;
let ARCHIVE_CAMERA_EDITOR = null;
let ARCHIVE_CAMERA_STRAIGHTENED = null;

function mountReceiptArchive() {
  const style = document.createElement('style');
  style.textContent = `
    #receiptArchiveModal .archive-upload { display:grid; gap:10px; margin:15px 0; }
    #receiptArchiveModal .scan-card { cursor:pointer; text-align:center; min-height:60px; }
    #receiptArchiveModal .archive-files { position:absolute; width:1px; height:1px; opacity:0; overflow:hidden; }
    #receiptArchiveModal .archive-entry { display:flex; align-items:center; justify-content:space-between; gap:12px; min-width:0; }
    #receiptArchiveModal .archive-name { min-width:0; overflow-wrap:anywhere; }
    #receiptArchiveModal .archive-name small { display:block; }
    #receiptArchiveModal .archive-links { display:flex; flex-wrap:wrap; gap:6px; }
    #receiptArchiveModal .archive-links a { font-size:12px; padding:9px; min-height:40px; }
    #receiptArchiveModal .camera-review { display:grid; gap:10px; margin:15px 0; }
    #receiptArchiveModal .camera-review-actions { display:flex; flex-wrap:wrap; gap:8px; }
    #archiveDuplicate { color:#ffd38d; }
  `;
  document.head.appendChild(style);
  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'receiptArchiveModal';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.innerHTML = `
    <div class="sheet">
      <div class="sheethead"><h2>🗂️ Tankbonnenarchief</h2><button type="button" class="close" aria-label="Sluiten" onclick="closeModal('receiptArchiveModal')"><span aria-hidden="true">✕</span></button></div>
      <p>Bewaar bonnen als PDF. Herkenning wordt alleen voor de bestandsnaam gebruikt; tankgegevens blijven onaangeroerd.</p>
      <div class="archive-upload">
        <label class="scan-card" for="archiveCamera">📷 <b>Foto maken met iPhone</b></label>
        <input class="archive-files" id="archiveCamera" type="file" accept="image/*" capture="environment">
        <label class="scan-card" for="archiveFiles">📁 <b>Foto’s of PDF uploaden</b></label>
        <input class="archive-files" id="archiveFiles" type="file" accept="image/*,.heic,.heif,application/pdf" multiple>
      </div>
      <section id="archiveCameraReview" class="camera-review" hidden>
        <h3>Controleer de bonhoeken</h3>
        <div id="archiveCameraPreview"></div>
        <div class="camera-review-actions">
          <button id="archiveStraighten" type="button">Bon rechtzetten</button>
          <button id="archiveUseStraightened" class="save" type="button" disabled>Deze bon gebruiken</button>
          <button id="archiveUseOriginal" type="button">Originele foto gebruiken</button>
          <button id="archiveCancelCamera" type="button">Annuleren</button>
        </div>
      </section>
      <p id="archiveStatus" role="status" aria-live="polite"></p>
      <section id="archiveDraft" hidden>
        <h3>Controleer de voorgestelde bestandsnaam</h3>
        <p id="archiveRecognized" class="assistant-note"></p>
        <div id="archiveDetails" class="assistant-note"
             style="white-space:pre-line;line-height:1.8"></div>
        <div class="field"><label for="archiveFilename">PDF-bestandsnaam (aanpasbaar)</label><input id="archiveFilename" maxlength="200"></div>
        <p id="archiveDuplicate" role="alert" hidden>⚠️ Mogelijk dubbele bon. Controleer de bestaande bonnen.</p>
        <label id="archiveConfirmLabel" hidden><input id="archiveConfirm" type="checkbox"> Ik heb dit gecontroleerd en wil deze bon toch opslaan.</label>
        <button id="archiveSave" class="save" type="button" onclick="saveArchiveReceipt()">PDF opslaan</button>
      </section>
      <h3>Opgeslagen tankbonnen</h3>
      <div class="field"><label for="archiveSearch">Zoeken op datum of naam</label><input type="search" id="archiveSearch" placeholder="Bijvoorbeeld Shell of 2026-10"></div>
      <a class="linkbtn" href="api/receipt-archive/export.zip" download="tankbonnenarchief.zip">⬇ Alles downloaden als ZIP</a>
      <div id="archiveList" class="known-list" aria-live="polite"></div>
    </div>`;
  document.body.appendChild(modal);
  document.getElementById('archiveCamera').addEventListener('change', e => startArchiveCameraReview(e.target.files));
  document.getElementById('archiveFiles').addEventListener('change', e => {
    cancelArchiveCamera();
    prepareArchiveReceipt(e.target.files);
  });
  document.getElementById('archiveStraighten').addEventListener('click', straightenArchiveCamera);
  document.getElementById('archiveUseStraightened').addEventListener('click', useStraightenedArchiveCamera);
  document.getElementById('archiveUseOriginal').addEventListener('click', useOriginalArchiveCamera);
  document.getElementById('archiveCancelCamera').addEventListener('click', cancelArchiveCamera);
  document.getElementById('archiveSearch').addEventListener('input', renderArchiveRows);
}

function loadArchiveCameraCanvas(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      try {
        const scale = Math.min(1, 2400 / Math.max(image.naturalWidth, image.naturalHeight));
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
        canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
        const ctx = canvas.getContext('2d');
        if (!ctx) throw new Error('De foto kan niet worden geopend.');
        ctx.fillStyle = 'white';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
        URL.revokeObjectURL(url);
        resolve(canvas);
      } catch (error) {
        URL.revokeObjectURL(url);
        reject(error);
      }
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('De iPhone kan deze foto niet verwerken. Exporteer hem eerst als JPEG.'));
    };
    image.src = url;
  });
}

async function startArchiveCameraReview(selection) {
  const file = Array.from(selection || [])[0];
  if (!file) return;
  const input = document.getElementById('archiveCamera');
  input.value = '';
  cancelArchiveCamera();
  const requestSequence = cancelArchiveCamera.sequence;
  const status = document.getElementById('archiveStatus');
  if (file.size > 12*1024*1024) {
    status.textContent = 'Bestand groter dan 12 MB.';
    return;
  }
  status.textContent = 'Foto voorbereiden…';
  try {
    if (!window.RitReceiptScanner) throw new Error('Bonscanner niet geladen. Vernieuw de app en probeer opnieuw.');
    const loadedCanvas = await loadArchiveCameraCanvas(file);
    if (cancelArchiveCamera.sequence !== requestSequence) return;
    ARCHIVE_CAMERA_SOURCE = loadedCanvas;
    ARCHIVE_CAMERA_FILE = file;
    ARCHIVE_CAMERA_EDITOR = window.RitReceiptScanner.createCornerEditor(
      ARCHIVE_CAMERA_SOURCE,
      document.getElementById('archiveCameraPreview')
    );
    document.getElementById('archiveUseStraightened').disabled = true;
    document.getElementById('archiveCameraReview').hidden = false;
    status.textContent = 'Versleep de mintgroene hoeken naar de randen van de bon.';
  } catch (error) {
    if (cancelArchiveCamera.sequence !== requestSequence) return;
    cancelArchiveCamera();
    status.textContent = error.message;
  }
}

function straightenArchiveCamera() {
  try {
    if (!ARCHIVE_CAMERA_SOURCE || !ARCHIVE_CAMERA_EDITOR) {
      throw new Error('Maak eerst een foto van de bon.');
    }
    ARCHIVE_CAMERA_STRAIGHTENED = window.RitReceiptScanner.straightenReceipt(
      ARCHIVE_CAMERA_SOURCE,
      ARCHIVE_CAMERA_EDITOR.getCorners()
    );
    const preview = document.getElementById('archiveCameraPreview');
    preview.replaceChildren(ARCHIVE_CAMERA_STRAIGHTENED);
    ARCHIVE_CAMERA_STRAIGHTENED.style.width = '100%';
    ARCHIVE_CAMERA_STRAIGHTENED.style.height = 'auto';
    ARCHIVE_CAMERA_STRAIGHTENED.style.display = 'block';
    document.getElementById('archiveUseStraightened').disabled = false;
    document.getElementById('archiveStatus').textContent = 'Bon rechtgezet. Gebruik de gecorrigeerde afbeelding of kies de originele foto.';
  } catch (error) {
    document.getElementById('archiveStatus').textContent = error.message;
  }
}

function archiveCameraCanvasFile(canvas, originalFile) {
  return new Promise((resolve, reject) => {
    canvas.toBlob(blob => {
      if (!blob) {
        reject(new Error('De gecorrigeerde foto kan niet worden opgeslagen.'));
        return;
      }
      const name = originalFile.name.replace(/\.[^.]*$/, '') + '.jpg';
      resolve(new File([blob], name, {type:'image/jpeg', lastModified:Date.now()}));
    }, 'image/jpeg', 0.94);
  });
}

async function useStraightenedArchiveCamera() {
  if (!ARCHIVE_CAMERA_STRAIGHTENED || !ARCHIVE_CAMERA_FILE) return;
  try {
    const file = await archiveCameraCanvasFile(
      ARCHIVE_CAMERA_STRAIGHTENED,
      ARCHIVE_CAMERA_FILE
    );
    cancelArchiveCamera();
    await prepareArchiveReceipt([file]);
  } catch (error) {
    document.getElementById('archiveStatus').textContent = error.message;
  }
}

async function useOriginalArchiveCamera() {
  if (!ARCHIVE_CAMERA_FILE) return;
  const file = ARCHIVE_CAMERA_FILE;
  cancelArchiveCamera();
  await prepareArchiveReceipt([file]);
}

function cancelArchiveCamera() {
  cancelArchiveCamera.sequence = (cancelArchiveCamera.sequence || 0) + 1;
  ARCHIVE_CAMERA_FILE = null;
  ARCHIVE_CAMERA_SOURCE = null;
  ARCHIVE_CAMERA_EDITOR = null;
  ARCHIVE_CAMERA_STRAIGHTENED = null;
  const review = document.getElementById('archiveCameraReview');
  if (review) review.hidden = true;
  const preview = document.getElementById('archiveCameraPreview');
  if (preview) preview.replaceChildren();
}

async function openReceiptArchive() {
  if (!document.getElementById('receiptArchiveModal')) mountReceiptArchive();
  openModal('receiptArchiveModal');
  ARCHIVE_PDF = null;
  document.getElementById('archiveDraft').hidden = true;
  document.getElementById('archiveStatus').textContent = '';
  document.getElementById('archiveCamera').value = '';
  document.getElementById('archiveFiles').value = '';
  cancelArchiveCamera();
  document.getElementById('archiveSearch').value = '';
  await refreshArchiveRows();
}

async function refreshArchiveRows() {
  try {
    ARCHIVE_ROWS = (await api('api/receipt-archive')).receipts || [];
    renderArchiveRows();
  } catch(e) {
    document.getElementById('archiveList').textContent = e.message;
  }
}

function renderArchiveRows() {
  const query = document.getElementById('archiveSearch').value.toLocaleLowerCase().trim();
  const parent = document.getElementById('archiveList');
  parent.replaceChildren();
  const rows = ARCHIVE_ROWS.filter(x => (x.filename + ' ' + x.created_at).toLocaleLowerCase().includes(query));
  if (!rows.length) {
    const empty = document.createElement('p');
    empty.textContent = 'Geen bonnen gevonden.';
    parent.append(empty);
  }
  rows.forEach(item => {
    const row = document.createElement('div');
    row.className = 'known-row archive-entry';
    const name = document.createElement('div');
    name.className = 'archive-name';
    const label = document.createElement('b');
    label.textContent = item.filename;
    const date = document.createElement('small');
    date.textContent = item.created_at.slice(0, 10) + (item.legacy ? ' · bestaande bon (origineel behouden)' : ' · PDF');
    name.append(label, date);
    const links = document.createElement('div');
    links.className = 'archive-links';
    const url = 'api/receipt-archive/' + encodeURIComponent(item.id);
    const view = document.createElement('a');
    view.className = 'linkbtn';
    view.href = url;
    view.target = '_blank';
    view.rel = 'noopener';
    view.textContent = 'Bekijken';
    const download = document.createElement('a');
    download.className = 'linkbtn';
    download.href = url + '?download=1';
    download.setAttribute('download', item.filename);
    download.textContent = 'Downloaden';
    links.append(view, download);
    row.append(name, links);
    parent.append(row);
  });
}

function fileDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(new Error('Bestand niet leesbaar.'));
    reader.readAsDataURL(file);
  });
}

function imageAsJpeg(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      try {
        const scale = Math.min(1, 2400 / Math.max(image.naturalWidth, image.naturalHeight));
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
        canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = 'white';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
        URL.revokeObjectURL(url);
        resolve(canvas.toDataURL('image/jpeg', 0.94));
      } catch(e) {
        URL.revokeObjectURL(url);
        reject(e);
      }
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('De iPhone kan deze foto niet verwerken. Exporteer hem eerst als JPEG.'));
    };
    image.src = url;
  });
}

async function prepareArchiveReceipt(selection) {
  if (ARCHIVE_BUSY) return;
  const files = Array.from(selection || []);
  if (!files.length) return;
  const status = document.getElementById('archiveStatus');
  const draft = document.getElementById('archiveDraft');
  draft.hidden = true;
  ARCHIVE_PDF = null;
  if (files.length > 4) {
    status.textContent = 'Kies maximaal vier foto’s, of één PDF.';
    return;
  }
  ARCHIVE_BUSY = true;
  status.textContent = 'PDF maken en naamgegevens controleren…';
  try {
    const upload = [];
    for (const file of files) {
      if (file.size > 12*1024*1024) throw new Error('Bestand groter dan 12 MB.');
      const pdf = file.type === 'application/pdf' || /\.pdf$/i.test(file.name);
      upload.push({data_url: pdf ? await fileDataUrl(file) : await imageAsJpeg(file)});
    }
    const result = await api('api/receipt-archive/prepare', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({files:upload})
    });
    ARCHIVE_PDF = result.pdf_data_url;
    document.getElementById('archiveFilename').value = result.suggested_filename;
    const x = result.recognized || {};
    const labels = [['Datum', x.date], ['Tankstation', x.station], ['Plaats', x.place], ['Totaalbedrag', x.total]];
    document.getElementById('archiveRecognized').textContent = labels.map(([key,val]) => key+': '+(val || 'niet herkend')).join(' · ');
    const details = result.recognized_details || {};
    const fields = [
      ['Tankstation', 'station'],
      ['Plaats', 'place'],
      ['Adres', 'address'],
      ['Datum', 'date'],
      ['Tijd', 'time'],
      ['Brandstof', 'fuel_type'],
      ['Pomp', 'pump'],
      ['Liters', 'liters'],
      ['Prijs per liter', 'price_per_liter'],
      ['Totaal', 'total'],
      ['Btw-percentage', 'vat_rate'],
      ['Btw-bedrag', 'vat_amount']
    ];

    const parent = document.getElementById('archiveDetails');
    parent.replaceChildren();

    const heading = document.createElement('h3');
    heading.textContent = 'Controleer de herkende bongegevens';
    parent.append(heading);

    for (const [label, key] of fields) {
      const wrap = document.createElement('div');
      wrap.className = 'field';

      const title = document.createElement('label');
      title.htmlFor = 'review-' + key;
      title.textContent = label;

      const input = document.createElement('input');
      input.id = 'review-' + key;
      input.dataset.reviewKey = key;
      input.value = details[key] || '';
      input.maxLength = 150;
      input.autocomplete = 'off';

      if (['liters', 'price_per_liter', 'total',
           'vat_amount'].includes(key)) {
        input.inputMode = 'decimal';
      }

      const fieldStatus = details.status?.[key];
      if (fieldStatus && fieldStatus !== 'herkend') {
        const note = document.createElement('small');
        note.textContent = 'Controle vereist: ' + fieldStatus;
        wrap.append(title, input, note);
      } else {
        wrap.append(title, input);
      }
      parent.append(wrap);
    }

    if ((details.warnings || []).length) {
      const warning = document.createElement('p');
      warning.setAttribute('role', 'alert');
      warning.textContent = details.warnings.join(' · ');
      parent.append(warning);
    }

    const reviewLabel = document.createElement('label');
    const reviewCheck = document.createElement('input');
    reviewCheck.type = 'checkbox';
    reviewCheck.id = 'archiveReviewConfirm';
    reviewCheck.checked = false;
    reviewLabel.append(
      reviewCheck,
      document.createTextNode(
        ' Ik heb de bongegevens gecontroleerd en wil ze bij de PDF bewaren.'
      )
    );
    parent.append(reviewLabel);

    const duplicate = (result.duplicates || []).length > 0;
    document.getElementById('archiveDuplicate').hidden = !duplicate;
    document.getElementById('archiveConfirmLabel').hidden = !duplicate;
    document.getElementById('archiveConfirm').checked = false;
    draft.hidden = false;
    status.textContent = 'PDF gereed. Pas ontbrekende of onjuiste gegevens in de bestandsnaam zelf aan.';
  } catch(e) {
    status.textContent = e.message;
  } finally {
    ARCHIVE_BUSY = false;
    document.getElementById('archiveCamera').value = '';
    document.getElementById('archiveFiles').value = '';
  }
}

async function saveArchiveReceipt() {
  if (ARCHIVE_BUSY || !ARCHIVE_PDF) return;
  const status = document.getElementById('archiveStatus');
  const confirm = document.getElementById('archiveConfirm').checked;
  if (!document.getElementById('archiveConfirmLabel').hidden && !confirm) {
    status.textContent = 'Bevestig eerst dat je de mogelijke dubbele bon hebt gecontroleerd.';
    return;
  }
  ARCHIVE_BUSY = true;
  document.getElementById('archiveSave').disabled = true;
  try {
    const reviewChecked =
      document.getElementById('archiveReviewConfirm')?.checked === true;
    const reviewedFields = {};
    if (reviewChecked) {
      document.querySelectorAll(
        '#archiveDetails input[data-review-key]'
      ).forEach(input => {
        reviewedFields[input.dataset.reviewKey] = input.value.trim();
      });
    }

    const result = await api('api/receipt-archive/save', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({pdf_data_url:ARCHIVE_PDF,
                           filename:document.getElementById('archiveFilename').value,
                           confirm_duplicate:confirm,
                           ...(reviewChecked ? {
                             reviewed_fields:reviewedFields,
                             review_confirmed:true
                           } : {})})
    });
    ARCHIVE_PDF = null;
    document.getElementById('archiveDraft').hidden = true;
    status.textContent = '✓ ' + result.filename + ' opgeslagen in /data/receipt_archive.';
    toast('Tankbon als PDF opgeslagen');
    await refreshArchiveRows();
  } catch(e) {
    if (e.code === 'RECEIPT_DUPLICATE') {
      document.getElementById('archiveDuplicate').hidden = false;
      document.getElementById('archiveConfirmLabel').hidden = false;
    }
    status.textContent = e.message;
  } finally {
    ARCHIVE_BUSY = false;
    document.getElementById('archiveSave').disabled = false;
  }
}


/* Release 33.11: add a receipt later, without submitting any fuel values. */
let FUEL_RECEIPT_TARGET = null;
let FUEL_RECEIPT_PDF = null;
let FUEL_RECEIPT_BUSY = false;
let FUEL_RECEIPT_DUPLICATE = false;

function mountFuelReceiptLink() {
  if (document.getElementById('fuelReceiptModal')) return;
  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'fuelReceiptModal';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.innerHTML = `
    <div class="sheet">
      <div class="sheethead"><h2>📎 Bon toevoegen</h2><button type="button" class="close" aria-label="Sluiten" onclick="closeModal('fuelReceiptModal')"><span aria-hidden="true">✕</span></button></div>
      <p id="fuelReceiptTarget"></p>
      <p id="fuelReceiptExisting" role="alert" hidden>⚠️ Er is al een bon gekoppeld. Alleen de koppeling kan worden vervangen; de oude bon blijft bewaard.</p>
      <div class="field"><label for="fuelReceiptExistingSelect">PDF uit bonnenarchief kiezen</label><select id="fuelReceiptExistingSelect"><option value="">Kies een bestaande PDF…</option></select></div>
      <p>Of voeg een nieuwe bon toe:</p>
      <div class="archive-upload">
        <label class="scan-card" for="fuelReceiptCamera">📷 Foto maken</label><input id="fuelReceiptCamera" type="file" accept="image/*" capture="environment" hidden>
        <label class="scan-card" for="fuelReceiptFile">📁 Foto/PDF uploaden</label><input id="fuelReceiptFile" type="file" accept="image/*,.heic,.heif,application/pdf" hidden>
      </div>
      <div id="fuelReceiptNew" hidden><div class="field"><label for="fuelReceiptName">Naam van de PDF</label><input id="fuelReceiptName" maxlength="200"></div></div>
      <p id="fuelReceiptStatus" role="status" aria-live="polite"></p>
      <button type="button" id="fuelReceiptSave" class="save" onclick="saveFuelReceiptLink()">Bon koppelen</button>
    </div>`;
  document.body.appendChild(modal);
  document.getElementById('fuelReceiptCamera').addEventListener('change', e => prepareFuelReceiptLink(e.target.files));
  document.getElementById('fuelReceiptFile').addEventListener('change', e => prepareFuelReceiptLink(e.target.files));
  document.getElementById('fuelReceiptExistingSelect').addEventListener('change', () => {
    FUEL_RECEIPT_PDF = null;
    FUEL_RECEIPT_DUPLICATE = false;
    document.getElementById('fuelReceiptNew').hidden = true;
    document.getElementById('fuelReceiptStatus').textContent = '';
  });
}

async function openFuelReceiptLink(id) {
  const fuel = DATA?.recent?.find(x => x.id === id && x.type === 'fuel');
  if (!fuel) { toast('Tankbeurt niet gevonden. Vernieuw het overzicht.', true); return; }
  mountFuelReceiptLink();
  FUEL_RECEIPT_TARGET = id;
  FUEL_RECEIPT_PDF = null;
  FUEL_RECEIPT_DUPLICATE = false;
  document.getElementById('fuelReceiptTarget').textContent =
    fuel.date_label + ' · ' + fmt(fuel.odometer, 0) + ' km · ' + fmt(fuel.liters, 2) + ' L';
  document.getElementById('fuelReceiptExisting').hidden = !(fuel.receipt_path || fuel.receipt_archive_id);
  document.getElementById('fuelReceiptNew').hidden = true;
  document.getElementById('fuelReceiptStatus').textContent = 'PDF-archief laden…';
  document.getElementById('fuelReceiptSave').disabled = false;
  document.getElementById('fuelReceiptFile').value = '';
  document.getElementById('fuelReceiptCamera').value = '';
  const select = document.getElementById('fuelReceiptExistingSelect');
  select.replaceChildren(new Option('Kies een bestaande PDF…', ''));
  openModal('fuelReceiptModal');
  try {
    const rows = (await api('api/receipt-archive')).receipts || [];
    if (FUEL_RECEIPT_TARGET !== id || !document.getElementById('fuelReceiptModal').classList.contains('show')) return;
    for (const row of rows.filter(x => !x.legacy)) {
      select.add(new Option(row.filename + ' · ' + row.created_at.slice(0, 10), String(row.id)));
    }
    document.getElementById('fuelReceiptStatus').textContent =
      rows.some(x => !x.legacy) ? 'Kies een PDF of upload een bon.' : 'Nog geen PDF in het archief; maak een foto of upload een PDF.';
  } catch (e) {
    document.getElementById('fuelReceiptStatus').textContent = e.message;
  }
}

async function prepareFuelReceiptLink(files) {
  if (FUEL_RECEIPT_BUSY || !files?.length) return;
  const file = files[0], status = document.getElementById('fuelReceiptStatus');
  FUEL_RECEIPT_BUSY = true;
  FUEL_RECEIPT_PDF = null;
  document.getElementById('fuelReceiptNew').hidden = true;
  status.textContent = 'Bon omzetten naar PDF…';
  try {
    if (file.size > 12 * 1024 * 1024) throw new Error('Bestand groter dan 12 MB.');
      const pdf = file.type === 'application/pdf' || /\.pdf$/i.test(file.name);
    const data = pdf ? await fileDataUrl(file) : await imageAsJpeg(file);
    const prepared = await api('api/receipt-archive/prepare', {
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({files:[{data_url:data}]})
    });
    FUEL_RECEIPT_PDF = prepared.pdf_data_url;
    FUEL_RECEIPT_DUPLICATE = (prepared.duplicates || []).length > 0;
    document.getElementById('fuelReceiptName').value = prepared.suggested_filename;
    document.getElementById('fuelReceiptExistingSelect').value = '';
    document.getElementById('fuelReceiptNew').hidden = false;
    status.textContent = FUEL_RECEIPT_DUPLICATE
      ? '⚠️ Mogelijk dubbele PDF. Bij koppelen volgt een bevestiging.'
      : 'PDF gereed. Controleer de bestandsnaam.';
  } catch (e) {
    status.textContent = e.message;
  } finally {
    FUEL_RECEIPT_BUSY = false;
    document.getElementById('fuelReceiptFile').value = '';
    document.getElementById('fuelReceiptCamera').value = '';
  }
}

async function saveFuelReceiptLink() {
  if (FUEL_RECEIPT_BUSY || FUEL_RECEIPT_TARGET === null) return;
  const status = document.getElementById('fuelReceiptStatus');
  const value = document.getElementById('fuelReceiptExistingSelect').value;
  if (!FUEL_RECEIPT_PDF && !value) { status.textContent = 'Kies eerst een PDF of maak een foto.'; return; }
  const fuel = DATA?.recent?.find(x => x.id === FUEL_RECEIPT_TARGET && x.type === 'fuel');
  if (!fuel) { status.textContent = 'Tankbeurt niet gevonden. Vernieuw het overzicht.'; return; }
  const replacing = Boolean(fuel.receipt_path || fuel.receipt_archive_id);
  if (replacing && !window.confirm('⚠️ Er is al een bon gekoppeld. Alleen de koppeling vervangen? De oude bon blijft bewaard.')) return;
  if (FUEL_RECEIPT_DUPLICATE && !window.confirm('⚠️ Deze PDF lijkt al in het archief te staan. Toch opnieuw bewaren en koppelen?')) return;
  FUEL_RECEIPT_BUSY = true;
  document.getElementById('fuelReceiptSave').disabled = true;
  try {
    const body = FUEL_RECEIPT_PDF
      ? {pdf_data_url:FUEL_RECEIPT_PDF,filename:document.getElementById('fuelReceiptName').value}
      : {archive_id:Number(value)};
    if (replacing) body.confirm_replace = true;
    if (FUEL_RECEIPT_DUPLICATE) body.confirm_duplicate = true;
    await api('api/fuel/' + FUEL_RECEIPT_TARGET + '/receipt', {
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)
    });
    FUEL_RECEIPT_PDF = null;
    FUEL_RECEIPT_TARGET = null;
    closeModal('fuelReceiptModal');
    await reloadData();
    toast('Bon gekoppeld aan bestaande tankbeurt');
  } catch(e) {
    if (e.code === 'RECEIPT_DUPLICATE') FUEL_RECEIPT_DUPLICATE = true;
    status.textContent = e.message;
  } finally {
    FUEL_RECEIPT_BUSY = false;
    document.getElementById('fuelReceiptSave').disabled = false;
  }
}
