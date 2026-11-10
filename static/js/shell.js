/* Shared page chrome: the "Filters" popover and keyboard-operable list rows
   on the job-list pages.
   Loaded before the page script. */

function toggleFilterPop(event) {
  if (event) event.stopPropagation();
  const pop = document.getElementById('filterPop');
  const btn = document.getElementById('filterBtn');
  if (!pop || !btn) return;
  pop.hidden = !pop.hidden;
  btn.setAttribute('aria-expanded', String(!pop.hidden));
  if (!pop.hidden) pop.querySelector('select, input')?.focus();
}

/* With returnFocus (Esc), focus goes back to the Filters button instead of <body> */
function closeFilterPop({ returnFocus = false } = {}) {
  const pop = document.getElementById('filterPop');
  const btn = document.getElementById('filterBtn');
  if (!pop || pop.hidden) return;
  pop.hidden = true;
  if (btn) btn.setAttribute('aria-expanded', 'false');
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
  if (e.target.closest && e.target.closest('#filterPop')) updateFilterCount();
});
