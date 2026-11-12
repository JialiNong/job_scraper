/* Shared page chrome: the "Filters" popover and keyboard-operable list rows
   on the job-list pages.
   Loaded before the page script. */

function isPhoneUi() {
  return window.matchMedia('(max-width: 760px), (max-height: 520px)').matches;
}

let overlayLockY = 0;

function setOverlayLock(on) {
  const root = document.documentElement;
  const locked = root.classList.contains('ui-lock');
  if (on && !locked) {
    overlayLockY = window.scrollY || window.pageYOffset;
    root.classList.add('ui-lock');
    document.body.style.top = `-${overlayLockY}px`;
  } else if (!on && locked) {
    root.classList.remove('ui-lock');
    document.body.style.top = '';
    window.scrollTo(0, overlayLockY);
  }
}

function filterPopOpen() {
  const pop = document.getElementById('filterPop');
  return Boolean(pop && !pop.hidden);
}

function syncOverlayLock() {
  const filterLock = isPhoneUi() && filterPopOpen();
  const panelScrim = document.getElementById('panelScrim');
  const panelLock = Boolean(panelScrim && !panelScrim.hidden);
  setOverlayLock(filterLock || panelLock);
}

function ensureFilterScrim() {
  let el = document.getElementById('filterScrim');
  if (el) return el;
  el = document.createElement('div');
  el.id = 'filterScrim';
  el.className = 'pop-scrim';
  el.hidden = true;
  el.setAttribute('aria-hidden', 'true');
  el.addEventListener('click', () => closeFilterPop({ returnFocus: true }));
  document.body.appendChild(el);
  return el;
}

function syncFilterScrim() {
  if (!document.getElementById('filterPop')) return;
  const scrim = ensureFilterScrim();
  scrim.hidden = !(isPhoneUi() && filterPopOpen());
  syncOverlayLock();
}

function toggleFilterPop(event) {
  if (event) event.stopPropagation();
  const pop = document.getElementById('filterPop');
  const btn = document.getElementById('filterBtn');
  if (!pop || !btn) return;
  pop.hidden = !pop.hidden;
  btn.setAttribute('aria-expanded', String(!pop.hidden));
  syncFilterScrim();
  if (!pop.hidden && !isPhoneUi()) pop.querySelector('select, input')?.focus();
}

/* With returnFocus (Esc), focus goes back to the Filters button instead of <body> */
function closeFilterPop({ returnFocus = false } = {}) {
  const pop = document.getElementById('filterPop');
  const btn = document.getElementById('filterBtn');
  if (!pop || pop.hidden) return;
  pop.hidden = true;
  if (btn) btn.setAttribute('aria-expanded', 'false');
  syncFilterScrim();
  if (returnFocus && btn) btn.focus();
}

/** "Filters (2)": how many selects in the popover differ from their page-load default. */
function updateFilterCount() {
  const pop = document.getElementById('filterPop');
  const btn = document.getElementById('filterBtn');
  if (!pop || !btn) return;
  let changed = 0;
  pop.querySelectorAll('select').forEach(sel => {
    const def = sel.querySelector('option[selected]') || sel.options[0];
    if (def && sel.value !== def.value) changed += 1;
  });
  btn.textContent = changed ? `Filters (${changed})` : 'Filters';
}

/** Custom Time / Custom Score still need the extra fields, so keep the pop open. */
function filterChangeKeepsPopOpen(el) {
  if (!el) return false;
  if (el.id === 'timePreset' && el.value === 'custom') return true;
  if (el.id === 'scorePreset' && el.value === 'custom') return true;
  return Boolean(el.closest && el.closest('#customDateGroup, #customScoreGroup'));
}

document.addEventListener('click', (e) => {
  const pop = document.getElementById('filterPop');
  if (pop && !pop.hidden && !pop.contains(e.target)) closeFilterPop();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    closeFilterPop({ returnFocus: true });
    return;
  }
  // List rows are <div role="button">: Enter / Space open them like a click
  const row = e.target.closest && e.target.closest('.card-row[role="button"]');
  if (row && e.target === row && (e.key === 'Enter' || e.key === ' ')) {
    e.preventDefault();
    row.click();
  }
});
document.addEventListener('change', (e) => {
  const t = e.target;
  if (!(t.closest && t.closest('#filterPop'))) return;
  updateFilterCount();
  if (!filterChangeKeepsPopOpen(t)) closeFilterPop({ returnFocus: true });
});
window.matchMedia('(max-width: 760px)').addEventListener('change', syncFilterScrim);
window.matchMedia('(max-height: 520px)').addEventListener('change', syncFilterScrim);
