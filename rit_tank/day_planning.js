/* Screenshot dagplanning -> gecontroleerde, aparte dagafspraken. Geen ritwijzigingen. */
'use strict';
let DAY_DRAFT = null;
let DAY_ROWS = [];
let DAY_BUSY = false;

function mountDayPlanning() {
  if (document.getElementById('dayPlanningModal')) return;
  const css = document.createElement('style');
  css.textContent = `
    #dayPlanningModal .sheet { padding-bottom:calc(28px + env(safe-area-inset-bottom)); }
    #dayPlanningModal .day-row { padding:12px; margin:9px 0; border:1px solid #41534c; border-radius:14px; }
    #dayPlanningModal .day-row .field {margin:8px 0}
    #dayPlanningModal .day-controls {display:flex;gap:8px;flex-wrap:wrap;margin-top:7px}
    #dayPlanningModal .day-controls button {min-height:44px;padding:8px 13px;border-radius:10px}
    #dayPlanningModal .day-warning {color:#ffd38d}
    #dayPlanningModal .day-file {display:block;width:100%;padding:16px;min-height:56px}
    #dayPlanningModal .day-row input[type=text] {width:100%;}
    #dayPlanningModal .day-review {display:flex;gap:10px;align-items:flex-start;min-height:44px;padding-top:10px}
    #dayPlanningModal .day-review input {width:23px;height:23px}
    #dayPlanningModal .sheethead .close {flex-shrink:0}
  `;
  document.head.appendChild(css);
  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'dayPlanningModal';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.innerHTML = `
    <div class="sheet">
      <div class="sheethead"><h2>📅 Dagplanning importeren</h2><button class="close" type="button" aria-label="Sluiten" onclick="closeModal('dayPlanningModal')">✕</button></div>
      <p>Upload een screenshot met de dagplanning. Je behoudt precies de gecontroleerde bezoekvolgorde. Er worden uitsluitend Google Agenda-afspraken voor de hele dag gemaakt.</p>
      <div class="field"><label for="dayScreenshot">Screenshot (PNG, JPEG of WebP)</label><input class="day-file" id="dayScreenshot" type="file" accept="image/png,image/jpeg,image/webp"></div>
      <p id="dayStatus" role="status" aria-live="polite"></p>
      <section id="dayPreview" hidden>
        <div class="field"><label for="dayDate">Datum van de afspraken</label><input type="date" id="dayDate"></div>
        <label class="day-review" id="dayDateReviewWrap" hidden><input type="checkbox" id="dayDateReview"> Datum handmatig gecontroleerd</label>
        <p id="dayWarnings" class="day-warning" role="alert"></p>
        <h3>Bezoeken in volgorde</h3>
        <div id="dayRows"></div>
        <div class="field"><label for="dayCalendar">Doelagenda (schrijfrechten vereist)</label><select id="dayCalendar"></select></div>
        <p id="dayAuth" class="day-warning"></p>
        <label class="day-review"><input type="checkbox" id="dayConfirm"> Ik heb de datum, adressen, volgorde en doelagenda gecontroleerd en wil de afspraken aanmaken.</label>
        <button class="save" id="dayImport" type="button" onclick="submitDayPlanning()">Maak afzonderlijke dagafspraken</button>
        <p id="dayResult" role="status" aria-live="polite"></p>
      </section>
    </div>`;
  document.body.appendChild(modal);
  document.getElementById('dayScreenshot').addEventListener('change', e => previewDayPlanning(e.target.files[0]));
  document.getElementById('dayCalendar').addEventListener('change', () => { document.getElementById('dayConfirm').checked = false; });
  document.getElementById('dayDate').addEventListener('change', () => { document.getElementById('dayConfirm').checked = false; });
}

async function openDayPlanning() {
  mountDayPlanning();
  DAY_DRAFT = null;
  DAY_ROWS = [];
  document.getElementById('dayScreenshot').value = '';
  document.getElementById('dayPreview').hidden = true;
  document.getElementById('dayStatus').textContent = '';
  document.getElementById('dayResult').textContent = '';
  openModal('dayPlanningModal');
  await loadDayCalendars();
}

async function loadDayCalendars() {
  const picker = document.getElementById('dayCalendar');
  const message = document.getElementById('dayAuth');
  picker.replaceChildren();
  message.textContent = 'Google Agenda-autorisatie controleren…';
  try {
    const result = await api('api/day-planning/calendars');
    for (const calendar of result.calendars) {
      const opt = document.createElement('option');
      opt.value = calendar.id;
      opt.textContent = calendar.name + (calendar.primary ? ' (primair)' : '');
      picker.appendChild(opt);
    }
    message.textContent = result.calendars.length ? '' : 'Geen schrijfbare agenda gevonden.';
    picker.disabled = !result.calendars.length;
  } catch (error) {
    picker.disabled = true;
    message.textContent = error.message + ' Configureer een afzonderlijke Google Agenda OAuth-token in de Home Assistant add-on.';
  }
}

async function previewDayPlanning(file) {
  if (DAY_BUSY || !file) return;
  const status = document.getElementById('dayStatus');
  const preview = document.getElementById('dayPreview');
  preview.hidden = true;
  DAY_DRAFT = null;
  if (!/^image\/(png|jpeg|webp)$/.test(file.type) || file.size > 12 * 1024 * 1024) {
    status.textContent = 'Gebruik een PNG, JPEG of WebP van maximaal 12 MB.';
    return;
  }
  DAY_BUSY = true;
  status.textContent = 'Screenshot lezen en adressen herkennen…';
  try {
    const data_url = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error('Screenshot lezen mislukt.'));
      reader.readAsDataURL(file);
    });
    const result = await api('api/day-planning/preview', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({image_data_url: data_url})
    });
    DAY_DRAFT = result;
    DAY_ROWS = result.visits.map(row => ({...row, reviewed: false}));
    document.getElementById('dayDate').value = result.date || '';
    document.getElementById('dayDateReviewWrap').hidden = !result.date_uncertain;
    document.getElementById('dayDateReview').checked = false;
    document.getElementById('dayConfirm').checked = false;
    document.getElementById('dayResult').textContent = '';
    document.getElementById('dayWarnings').textContent = (result.warnings || []).join(' ')
      + (result.date_uncertain ? ' Datum niet betrouwbaar herkend; vul deze zelf in. ' : '');
    renderDayRows();
    preview.hidden = false;
    status.textContent = `${DAY_ROWS.length} bezoeken gevonden. Controleer ieder adres, zeker de afgekorte regels.`;
  } catch (error) {
    status.textContent = error.message;
  } finally {
    DAY_BUSY = false;
  }
}

function renderDayRows() {
  const root = document.getElementById('dayRows');
  root.replaceChildren();
  DAY_ROWS.forEach((row, i) => {
    const outer = document.createElement('div');
    outer.className = 'day-row';
    const heading = document.createElement('b');
    heading.textContent = `${String(i + 1).padStart(2, '0')} · ${row.section === 'middag' ? 'Middag' : 'Ochtend'}`;
    outer.appendChild(heading);
    if (row.uncertain) {
      const warning = document.createElement('p');
      warning.className = 'day-warning';
      warning.textContent = '⚠️ Adres is onduidelijk of afgekapt. Vul het volledig aan en bevestig de controle.';
      outer.appendChild(warning);
    }
    const field = document.createElement('div');
    field.className = 'field';
    const label = document.createElement('label');
    label.textContent = 'Volledig adres';
    label.htmlFor = `dayAddress${row.source_id}`;
    const input = document.createElement('input');
    input.id = label.htmlFor;
    input.type = 'text';
    input.maxLength = 200;
    input.autocomplete = 'off';
    input.value = row.address;
    input.addEventListener('input', () => {
      row.address = input.value;
      row.reviewed = false;
      const review = outer.querySelector('input[type=checkbox]');
      if (review) review.checked = false;
      document.getElementById('dayConfirm').checked = false;
    });
    field.append(label, input);
    outer.appendChild(field);
    if (row.uncertain) {
      const approval = document.createElement('label');
      approval.className = 'day-review';
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = row.reviewed;
      box.addEventListener('change', () => {row.reviewed = box.checked; document.getElementById('dayConfirm').checked = false;});
      approval.append(box, document.createTextNode(' Ik heb dit adres aan de hand van de bron gecontroleerd en aangevuld.'));
      outer.appendChild(approval);
    }
    const controls = document.createElement('div');
    controls.className = 'day-controls';
    [['↑ Omhoog', i - 1], ['↓ Omlaag', i + 1]].forEach(([name, target]) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'linkbtn';
      btn.textContent = name;
      btn.disabled = target < 0 || target >= DAY_ROWS.length;
      btn.addEventListener('click', () => {
        [DAY_ROWS[i], DAY_ROWS[target]] = [DAY_ROWS[target], DAY_ROWS[i]];
        document.getElementById('dayConfirm').checked = false;
        renderDayRows();
      });
      controls.appendChild(btn);
    });
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'linkbtn';
    remove.textContent = 'Verwijderen';
    remove.addEventListener('click', () => {
      DAY_ROWS.splice(i, 1);
      document.getElementById('dayConfirm').checked = false;
      renderDayRows();
    });
    controls.appendChild(remove);
    outer.appendChild(controls);
    root.appendChild(outer);
  });
}

async function submitDayPlanning() {
  if (DAY_BUSY || !DAY_DRAFT) return;
  const resultLabel = document.getElementById('dayResult');
  if (!document.getElementById('dayConfirm').checked) {
    resultLabel.textContent = 'Bevestig eerst de gecontroleerde import.';
    return;
  }
  if (!DAY_ROWS.length || !document.getElementById('dayCalendar').value) {
    resultLabel.textContent = 'Controleer de bezoeken en kies een doelagenda.';
    return;
  }
  DAY_BUSY = true;
  document.getElementById('dayImport').disabled = true;
  resultLabel.textContent = 'Dagafspraken maken; dit kan even duren…';
  try {
    const payload = {draft_token: DAY_DRAFT.draft_token,
      date: document.getElementById('dayDate').value,
      reviewed_date: !DAY_DRAFT.date_uncertain || document.getElementById('dayDateReview').checked,
      calendar_id: document.getElementById('dayCalendar').value,
      visits: DAY_ROWS.map(x => ({source_id:x.source_id,address:x.address,reviewed:x.reviewed})),
      confirmed: true};
    const check = await api('api/day-planning/check', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload)
    });
    if (check.duplicates.length && !confirm(`${check.duplicates.length} bezoek(en) staan op dit adres en deze datum al in de agenda (regels: ${check.duplicates.join(', ')}). Deze worden overgeslagen. Doorgaan met de overige afspraken?`)) {
      resultLabel.textContent = 'Import geannuleerd; niets aangemaakt.';
      return;
    }
    const result = await api('api/day-planning/import', {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)
    });
    resultLabel.textContent = `${result.created} nieuwe dagafspraken; ${result.skipped} overgeslagen als dubbel. Er zijn geen bestaande afspraken gewijzigd.`;
    document.getElementById('dayConfirm').checked = false;
    DAY_DRAFT = null;
  } catch (error) {
    resultLabel.textContent = error.message;
  } finally {
    DAY_BUSY = false;
    document.getElementById('dayImport').disabled = false;
  }
}
