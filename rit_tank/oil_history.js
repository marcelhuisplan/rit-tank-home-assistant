/* Motorolie-onderhoud: aparte administratie, geen rit- of tellerstandwijzigingen. */
let OIL_ENTRIES = [];
async function loadOilHistory() {
  const box = document.getElementById('oilHistoryList');
  if (!box) return;
  try {
    const result = await api('api/maintenance/oil');
    OIL_ENTRIES = result.entries || [];
    renderOilHistory();
  } catch (error) {
    box.textContent = 'Onderhoudshistorie laden mislukt: ' + error.message;
  }
}
function renderOilHistory() {
  const box = document.getElementById('oilHistoryList');
  if (!box) return;
  box.replaceChildren();
  if (!OIL_ENTRIES.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'Nog geen oliebeurten. Voeg je eerste registratie toe.';
    box.appendChild(empty);
    return;
  }
  OIL_ENTRIES.forEach(entry => {
    const card = document.createElement('div');
    card.className = 'event';
    const icon = document.createElement('div');
    icon.className = 'event-icon';
    icon.textContent = '🛢️';
    const details = document.createElement('div');
    details.className = 'oil-details';
    const label = document.createElement('strong');
    label.textContent = entry.kind === 'top_up' ? 'Olie bijgevuld' : 'Olie ververst';
    const small = document.createElement('small');
    small.textContent = [entry.performed_on.split('-').reverse().join('-'),
      entry.oil_type, entry.amount_ml + ' ml', entry.odometer.toLocaleString('nl-NL') + ' km'].join(' · ');
    details.append(label, small);
    if (entry.note) {
      const note = document.createElement('small');
      note.className = 'oil-note';
      note.textContent = entry.note;
      details.appendChild(note);
    }
    const actions = document.createElement('div');
    actions.className = 'event-actions';
    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'editbtn';
    edit.textContent = 'Bekijken / wijzigen';
    edit.setAttribute('aria-label', 'Oliebeurt wijzigen');
    edit.onclick = () => openOilEntry(entry.id);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'trash';
    remove.textContent = '🗑️';
    remove.setAttribute('aria-label', 'Oliebeurt verwijderen');
    remove.onclick = () => removeOilEntry(entry.id);
    actions.append(edit, remove);
    card.append(icon, details, actions);
    box.appendChild(card);
  });
}
function openOilEntry(id) {
  const entry = id == null ? null : OIL_ENTRIES.find(x => Number(x.id) === Number(id));
  if (id != null && !entry) {
    toast('Oliebeurt niet gevonden. Vernieuw de historie.', true);
    return;
  }
  const el = x => document.getElementById(x);
  el('oilHistoryTitle').textContent = entry ? '🛢️ Oliebeurt wijzigen' : '🛢️ Oliebeurt toevoegen';
  el('oilHistoryId').value = entry?.id || '';
  el('oilHistoryKind').value = entry?.kind || 'top_up';
  el('oilHistoryDate').value = entry?.performed_on || new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  el('oilHistoryType').value = entry?.oil_type || '';
  el('oilHistoryNote').value = entry?.note || '';
  el('oilHistoryAmount').value = entry?.amount_ml ?? '';
  el('oilHistoryOdometer').value = entry?.odometer ?? (Number.isInteger(Number(DATA?.current_odometer)) ? DATA.current_odometer : '');
  el('oilHistoryError').textContent = '';
  el('oilHistorySave').disabled = false;
  openModal('oilHistoryModal');
}
async function saveOilEntry(event) {
  event.preventDefault();
  const el = x => document.getElementById(x);
  const id = el('oilHistoryId').value;
  const payload = {
    kind: el('oilHistoryKind').value,
    performed_on: el('oilHistoryDate').value,
    oil_type: el('oilHistoryType').value.trim(),
    amount_ml: el('oilHistoryAmount').value,
    odometer: el('oilHistoryOdometer').value,
    note: el('oilHistoryNote').value.trim()
  };
  el('oilHistorySave').disabled = true;
  el('oilHistoryError').textContent = '';
  try {
    const save = () => api('api/maintenance/oil' + (id ? '/' + encodeURIComponent(id) : ''), {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
    });
    try {
      await save();
    } catch (error) {
      if (error.code !== 'OIL_ODOMETER_CONFIRMATION_REQUIRED') throw error;
      if (!confirm(error.message + '\n\nDeze afwijkende stand toch alleen in de onderhoudshistorie opslaan?')) {
        el('oilHistoryError').textContent = 'Opslaan geannuleerd. Controleer de tellerstand.';
        el('oilHistorySave').disabled = false;
        return;
      }
      payload.confirm_odometer_anomaly = true;
      await save();
    }
    closeModal('oilHistoryModal');
    await loadOilHistory();
    toast('Oliebeurt opgeslagen');
  } catch (error) {
    el('oilHistoryError').textContent = error.message;
    el('oilHistorySave').disabled = false;
  }
}
async function removeOilEntry(id) {
  const entry = OIL_ENTRIES.find(x => Number(x.id) === Number(id));
  if (!entry || !confirm('Deze oliebeurt van ' + entry.performed_on + ' (' + entry.amount_ml + ' ml) definitief verwijderen?')) return;
  try {
    await api('api/maintenance/oil/' + encodeURIComponent(id), {
      method: 'DELETE', headers: {'X-Confirm-Delete': 'yes'}
    });
    await loadOilHistory();
    toast('Oliebeurt verwijderd');
  } catch (error) {
    toast(error.message, true);
  }
}
document.addEventListener('DOMContentLoaded', loadOilHistory);
